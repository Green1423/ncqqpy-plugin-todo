"""给 AI 用的工具（`register_tool`）—— 结果**回给模型**，由模型组织答复。

与命令的区别：工具直接收 JSON Schema 参数，不经过命令解析器；业务上仍复用
``commands/todo.py``，所以服务范围、deadline/repeat 互斥、分页、完成语义与
用户手敲命令完全一致。

工具清单：

| 工具 | 用途 |
|------|------|
| `todo.add`   | 添加待办 |
| `todo.list`  | 查看当前会话待办（含 deadline / 规则，便于模型判断） |
| `todo.set`   | 修改待办 |
| `todo.check` | 完成此项 |
| `todo.del`   | 删除待办 |

所有工具都按当前会话隔离；`permission` / `scope` 只负责粗粒度暴露，真正的
服务范围在 handler 里用 ``plugin.check_access()`` 判。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from napcat.command import SchemaError
from . import render
from .commands import todo as todo_cmd
from .schedule import fmt_datetime
from .schemas import AddSchema, CheckSchema, DelSchema, SetSchema
from .store import Todo

#: 写进系统提示的时间表达式速查（模型据此生成 remind / repeat）
TIME_RULES = (
    "时间表达式规则（remind / repeat）："
    "`8h`=每天08:00，`1w`=每周一09:00，`1D`=每月1日09:00；"
    "`[1,5]w & {8h}`=工作日08:00，`[6,7]w`=周末09:00，`eM`=每月最后一天；"
    "`2026Y12M31D23h59m`=只发生一次。"
    "`&`交集、`|`并集、`!`差集；`{...}`列举、`[...]`范围；"
    "`+1D`=从当前起每隔1天，`+6h`=每6小时，`+1D & {7h}`=每天07:00；"
    "`hol`=官方放假天，`hol|lil`=所有放假天，`wil`=调休上班，"
    "普通周末要写 `[6,7]w`。"
    "remind 到点后按同一规则续排；repeat 到点把已完成的待办重新打开并重排提醒，"
    "`repeat=true` 同 remind；false/空=不重复；"
    "deadline 是具体时间且与 repeat 互斥。"
)


def _coerce(args: Any) -> dict[str, str]:
    """JSON 参数 → Schema 需要的字符串参数。"""
    if not isinstance(args, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[str(key)] = "true" if value else "false"
        elif isinstance(value, datetime):
            out[str(key)] = value.isoformat()
        else:
            out[str(key)] = str(value)
    return out


def _event(ctx):
    """ToolContext → 命令用的最小事件。

    ai_chat 的上下文是鸭子类型；这里只取命令真正用到的字段。群小窗
    （private + group_id）保留 ``is_temp_session``，让提醒 / 日报按临时会话回。
    """
    if ctx is None:
        return None
    in_group = bool(getattr(ctx, "in_group", False))
    user_id = int(getattr(ctx, "user_id", 0) or 0)
    group_id = int(getattr(ctx, "group_id", 0)
                   or getattr(ctx, "chat_id", 0) or 0)
    if in_group:
        return SimpleNamespace(message_type="group", user_id=user_id,
                               group_id=group_id)
    via = int(getattr(ctx, "group_id", 0) or 0)
    return SimpleNamespace(message_type="private", user_id=user_id,
                           group_id=via or None,
                           is_temp_session=bool(via))


def _denied(ctx=None) -> str:
    if ctx is None:
        return "⛔ 这个工具需要在聊天上下文中调用。"
    if getattr(ctx, "in_group", False):
        gid = int(getattr(ctx, "group_id", 0) or getattr(ctx, "chat_id", 0) or 0)
        return (f"⛔ 本群（{gid}）没有开通待办服务：让管理员在私聊发 "
                f"/todo bind add group {gid} 开通后重试。")
    uid = int(getattr(ctx, "user_id", 0) or 0)
    return (f"⛔ 你（{uid}）还没有开通待办服务：让管理员在私聊发 "
            f"/todo bind add user {uid} 开通后重试。")


async def _call(plugin, ctx, func, params) -> str:
    event = _event(ctx)
    result = await func(plugin, params, event)
    if result is None:
        return _denied(ctx)
    if isinstance(result, tuple) and len(result) == 2 and result[0] == "_schema_error":
        return f"⚠️ 用法不正确：{result[1]}"
    return str(result)


def _validate(schema, args: Any):
    return schema().validate([], _coerce(args))


async def _add(plugin, args, ctx=None) -> str:
    try:
        params = _validate(AddSchema, args)
    except SchemaError as exc:
        return f"⚠️ 参数不正确：{exc}"
    return await _call(plugin, ctx, todo_cmd.add, params)


async def _set(plugin, args, ctx=None) -> str:
    try:
        params = _validate(SetSchema, args)
    except SchemaError as exc:
        return f"⚠️ 参数不正确：{exc}"
    return await _call(plugin, ctx, todo_cmd.update, params)


async def _check(plugin, args, ctx=None) -> str:
    try:
        params = _validate(CheckSchema, args)
    except SchemaError as exc:
        return f"⚠️ 参数不正确：{exc}"
    return await _call(plugin, ctx, todo_cmd.check, params)


async def _del(plugin, args, ctx=None) -> str:
    try:
        params = _validate(DelSchema, args)
    except SchemaError as exc:
        return f"⚠️ 参数不正确：{exc}"
    return await _call(plugin, ctx, todo_cmd.delete, params)


async def _list(plugin, args, ctx=None) -> str:
    """AI 版列表：在命令的三列之外补上 deadline / 规则，方便模型判断。"""
    event = _event(ctx)
    if event is None:
        return _denied(ctx)
    if not plugin.check_access(event):
        return _denied(ctx)
    chat = plugin.chat_of(event)
    if chat is None:
        return "⚠️ 无法确定当前聊天。"
    data = args if isinstance(args, dict) else {}
    try:
        page = int(data.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    page_size = plugin.list_page_size()
    query = Todo.objects.filter(chat=chat.key)
    total = query.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    items = list(query.order_by("id")
                 .offset((page - 1) * page_size)
                 .limit(page_size))
    if not items:
        return "当前会话没有待办。"

    lines = [f"待办共 {total} 条（第 {page}/{pages} 页）："]
    for todo in items:
        state = "已完成" if todo.done else "未完成"
        parts = [f"#{todo.id}", f"[{state}]", render.clip(todo.title, 30)]
        deadline = todo.deadline if isinstance(todo.deadline, datetime) else None
        if deadline is not None:
            parts.append(f"截止={fmt_datetime(deadline, short=True)}")
        if todo.remind:
            parts.append(f"提醒={todo.remind}")
        if todo.repeat:
            parts.append(f"重复={todo.repeat}")
        lines.append(" | ".join(parts))
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(f"上一页 page={page - 1}")
        if page < pages:
            nav.append(f"下一页 page={page + 1}")
        lines.append("分页：" + "，".join(nav))
    return "\n".join(lines)


def register(plugin) -> None:
    """把 todo 工具注册到插件上（on_load 调用一次）。"""
    reg = plugin.register_tool

    @reg(
        "add",
        description=(
            "在当前会话添加一条待办。title 必填；content 是提醒文案；"
            "remind/repeat 用时间表达式（规则已随工具提示给出）；"
            "deadline 是具体时间（YYYY-MM-DD HH:MM，deadline 与 repeat 互斥）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "待办标题（必填）"},
                "content": {"type": "string", "description": "提醒文案；省略用默认文案"},
                "remind": {"type": "string",
                           "description": "提醒表达式，如 8h（每天8点）/ [1,5]w & {8h}（工作日8点）/ +1D（每隔1天）；false 或省略 = 不提醒"},
                "repeat": {"type": "string",
                           "description": "重复表达式，写法同 remind；false 或省略 = 不重复；true = 同 remind；与 deadline 互斥"},
                "deadline": {"type": "string",
                             "description": "截止时间，如 2026-11-10 18:00；只写日期按 23:59；与 repeat 互斥"},
            },
            "required": ["title"],
        },
        scope="all",
        max_chars=3000,
        prompt_hint=("用户让你记待办、设提醒、加截止时间时，调用 todo_add。"
                     + TIME_RULES),
    )
    async def add(args, ctx=None):
        return await _add(plugin, args, ctx)

    @reg(
        "list",
        description=(
            "查看当前会话的待办，返回编号、标题、完成状态、截止时间、提醒和重复规则。"
            "适合在想完成/修改某条待办前先拿编号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1,
                         "description": "页码，默认 1"},
            },
        },
        scope="all",
        max_chars=4000,
        prompt_hint="需要知道当前会话有哪些待办、编号或截止时间时，调用 todo_list。",
    )
    async def list_tool(args, ctx=None):
        return await _list(plugin, args, ctx)

    @reg(
        "set",
        description=(
            "修改当前会话的一条待办。id 必填；只传要改的字段。"
            "remind/repeat 的写法与 todo_add 相同；"
            "deadline 与 repeat 互斥；deadline 用具体时间，不是表达式。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "minimum": 1,
                       "description": "待办编号（先调 todo_list）"},
                "title": {"type": "string", "description": "新标题"},
                "content": {"type": "string", "description": "新提醒文案；- = 恢复默认"},
                "remind": {"type": "string", "description": "新提醒表达式（写法同 todo_add）；false = 不提醒"},
                "repeat": {"type": "string", "description": "新重复表达式（写法同 todo_add）；false = 不重复；与 deadline 互斥"},
                "deadline": {"type": "string",
                             "description": "新截止时间；false = 清除；与 repeat 互斥"},
            },
            "required": ["id"],
        },
        scope="all",
        max_chars=3000,
        prompt_hint="用户要改待办标题、提醒、重复或截止时间时，调用 todo_set；表达式规则见 todo_add。",
    )
    async def set_tool(args, ctx=None):
        return await _set(plugin, args, ctx)

    @reg(
        "check",
        description="把当前会话的一条待办标记为完成；有 repeat 规则时到点会重新打开。",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "minimum": 1,
                       "description": "待办编号（先调 todo_list）"},
            },
            "required": ["id"],
        },
        scope="all",
        max_chars=2000,
        prompt_hint="用户说某条待办完成了、打卡、做完了时，调用 todo_check。",
    )
    async def check_tool(args, ctx=None):
        return await _check(plugin, args, ctx)

    @reg(
        "del",
        description="删除当前会话的一条待办（已完成也能删）。",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "minimum": 1,
                       "description": "待办编号（先调 todo_list）"},
            },
            "required": ["id"],
        },
        scope="all",
        max_chars=2000,
        prompt_hint="用户明确要删除某条待办时，调用 todo_del。",
    )
    async def del_tool(args, ctx=None):
        return await _del(plugin, args, ctx)
