"""``/todo add|list|set|check|del|bind`` 的业务逻辑。

一条待办对外有 7 个信息：``id`` / ``title`` / ``content`` / ``remind`` /
``repeat`` / ``deadline`` / ``visible``。

* ``remind``：提醒时间规则表达式；``false`` / 空 = 不提醒；
* ``repeat``：重复时间规则表达式；``false`` / 空 = 不重复；``true`` = 同
  ``remind`` 用一条表达式；
* ``deadline``：截止时间（具体时间点）；``false`` / 空 = 无截止；
* ``visible``：``false`` 不进入每日总结，列表中排在可见项后面；
* ``content``：提醒文案；空 = 配置里的 ``default_remind``。

``repeat`` 决定待办本身按什么规则重复（打卡时用它排下一次），``remind``
决定什么时候发提醒，``deadline`` 用于日报里的逾期判断。``deadline`` 与
``repeat`` 互斥：有截止时间的待办不能同时设重复规则。``list`` 只读，
``check`` 只写。
守卫（服务范围白名单）放在每个入口的第一行：被挡下时**静默返回 None**。
"""
from __future__ import annotations

from datetime import datetime

from .. import render
from ..schedule import (
    DateExprError, advance_from, first_occurrence, next_occurrence, now,
    parse_iso, to_iso,
)
from napcat.store import StoreError

from ..store import ChatRef, Todo

#: `--content` 里这些词表示“恢复默认”。
_RESET_WORDS = {"-", "默认", "default", "reset", "重置"}
#: 规则字段里表示 false 的词。
_FALSE_WORDS = {
    "-", "false", "0", "no", "off", "none", "无", "不提醒", "不重复",
    "无截止", "不截止", "关",
}
#: 规则字段里表示 true 的词（repeat=true → 与 remind 相同）。
_TRUE_WORDS = {"true", "1", "yes", "on", "是", "提醒", "重复"}


def _denied(plugin, event) -> bool:
    if plugin.check_access(event):
        return False
    plugin.log_scope_denied(event)
    return True


def _chat(plugin, event) -> ChatRef | None:
    return plugin.chat_of(event)


def _owner(event) -> int:
    try:
        return int(getattr(event, "user_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _title(raw: str) -> str:
    return str(raw or "").strip()


def _content_value(raw: str) -> str:
    value = str(raw or "").strip()
    return "" if value in _RESET_WORDS else value


def _rule_value(raw: str) -> str:
    value = str(raw or "").strip()
    return "" if value.lower() in _FALSE_WORDS else value


def _is_true_rule(raw: str) -> bool:
    return str(raw or "").strip().lower() in _TRUE_WORDS


def _resolve_rule(expression: str, label: str,
                  moment: datetime) -> tuple[str, datetime | None, str]:
    """校验时间规则表达式并求下一次；空表达式 = 不启用。"""
    expr = str(expression or "").strip()
    if not expr:
        return "", None, ""
    try:
        upcoming = first_occurrence(expr, moment)
    except DateExprError as exc:
        return "", None, f"{label}无效：{exc}"
    if upcoming is None:
        return "", None, f"{label}没有未来的匹配时间（可能是空集，或时间已经过去）"
    return expr, upcoming, ""


async def add(plugin, params, event):
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    title = _title(str(params.title or ""))
    if not title:
        return ("_schema_error",
                "待办标题不能为空，例如 /todo add 吃药 --remind=\"8h\" --repeat=\"8h\"")

    moment = now()
    remind = _rule_value(str(params.remind or ""))
    next_remind = None
    if remind:
        remind, next_remind, error = _resolve_rule(remind, "提醒时间规则表达式", moment)
        if error:
            return f"⚠️ {error}"

    raw_repeat = str(params.repeat or "").strip()
    if raw_repeat and _is_true_rule(raw_repeat):
        repeat = remind
        if not repeat:
            return ("_schema_error",
                    "repeat=true 需要同时给 remind 表达式；不重复请写 repeat=false")
    else:
        repeat = _rule_value(raw_repeat)
    next_repeat = None
    if repeat:
        repeat, next_repeat, error = _resolve_rule(repeat, "重复时间规则表达式", moment)
        if error:
            return f"⚠️ {error}"

    deadline = (params.deadline
                if isinstance(params.deadline, datetime) else "")
    visible = bool(params.visible) if params.visible is not None else True
    if repeat and deadline:
        return "⚠️ deadline 和 repeat 互斥：有截止时间的待办不能同时设置重复规则"

    active = Todo.objects.filter(chat=chat.key, done=False).count()
    if active >= plugin.max_todos_per_chat():
        return (f"⚠️ 这个会话的待办已达上限（{plugin.max_todos_per_chat()} 条）；"
                "先 /todo del <编号> 清理一些")

    try:
        todo = Todo.objects.create(
            chat=chat.key,
            chat_type=chat.kind,
            chat_id=chat.id,
            via_group=chat.via_group,
            owner=_owner(event),
            title=title,
            content=_content_value(str(params.content or "")),
            remind=remind,
            repeat=repeat,
            deadline=deadline,
            visible=visible,
            created_at=to_iso(moment),
            next_remind=to_iso(next_remind),
            next_repeat=to_iso(next_repeat),
        )
    except StoreError as exc:
        plugin.logger.error("[todo] 保存待办失败：%s", exc)
        return "⚠️ 待办没有保存成功（数据库写入失败），请稍后再试"

    return render.detail_text(todo, verb="已添加")


async def update(plugin, params, event):
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    todo = Todo.objects.filter(chat=chat.key, id=int(params.id)).first()
    if todo is None:
        return f"❌ 当前会话没有待办 #{params.id}（用 /todo list 查看编号）"
    if todo.done:
        return f"✅ 待办 #{todo.id} 已经完成了，不能再修改；需要的话重新 /todo add"

    moment = now()
    changed: list[str] = []

    new_title = todo.title
    raw_title = _title(str(params.title or ""))
    if raw_title:
        new_title = raw_title
        if new_title != todo.title:
            changed.append("title")

    new_content = todo.content
    raw_content = str(params.content or "").strip()
    if raw_content:
        new_content = _content_value(raw_content)
        if new_content != todo.content:
            changed.append("content")

    # ---- remind ----
    new_remind = todo.remind
    next_remind = parse_iso(todo.next_remind)
    raw_remind = str(params.remind or "").strip()
    if raw_remind:
        normalized = _rule_value(raw_remind)
        if normalized:
            expression, next_remind, error = _resolve_rule(
                normalized, "提醒时间规则表达式", moment)
            if error:
                return f"⚠️ {error}"
            new_remind = expression
        else:
            new_remind = ""
            next_remind = None
        if new_remind != todo.remind:
            changed.append("remind")
            todo.reminded = False
            todo.missed = False

    # ---- repeat ----
    new_repeat = todo.repeat
    next_repeat = parse_iso(todo.next_repeat)
    raw_repeat = str(params.repeat or "").strip()
    if raw_repeat:
        if _is_true_rule(raw_repeat):
            target = new_remind
            if not target:
                return ("_schema_error",
                        "repeat=true 需要同时有 remind 表达式；不重复请写 repeat=false")
            expression, next_repeat, error = _resolve_rule(
                target, "重复时间规则表达式", moment)
            if error:
                return f"⚠️ {error}"
            new_repeat = expression
        else:
            normalized = _rule_value(raw_repeat)
            if normalized:
                expression, next_repeat, error = _resolve_rule(
                    normalized, "重复时间规则表达式", moment)
                if error:
                    return f"⚠️ {error}"
                new_repeat = expression
            else:
                new_repeat = ""
                next_repeat = None
        if new_repeat != todo.repeat:
            changed.append("repeat")

    # ---- deadline ----
    new_deadline = todo.deadline or ""
    if params.deadline is not None:
        parsed_deadline = (params.deadline
                           if isinstance(params.deadline, datetime) else "")
        if parsed_deadline != new_deadline:
            new_deadline = parsed_deadline
            changed.append("deadline")

    if new_repeat and new_deadline:
        return "⚠️ deadline 和 repeat 互斥：有截止时间的待办不能同时设置重复规则"

    new_visible = todo.visible
    if params.visible is not None:
        new_visible = bool(params.visible)
        if new_visible != todo.visible:
            changed.append("visible")

    if not changed:
        return "ℹ️ 没有要修改的字段（可改：title / content / remind / repeat / deadline / visible）"

    todo.title = new_title
    todo.content = new_content
    todo.remind = new_remind
    todo.repeat = new_repeat
    todo.deadline = new_deadline or ""
    todo.visible = new_visible
    todo.next_remind = to_iso(next_remind)
    todo.next_repeat = to_iso(next_repeat)
    try:
        todo.save()
    except StoreError as exc:
        plugin.logger.error("[todo] 保存待办失败：%s", exc)
        return "⚠️ 修改没有保存成功（数据库写入失败），请稍后再试"
    return render.detail_text(todo, verb="已更新")


async def delete(plugin, params, event):
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    todo = Todo.objects.filter(chat=chat.key, id=int(params.id)).first()
    if todo is None:
        return f"❌ 当前会话没有待办 #{params.id}（用 /todo list 查看编号）"
    title = todo.title or "待办"
    try:
        todo.delete()
    except StoreError as exc:
        plugin.logger.error("[todo] 删除待办失败：%s", exc)
        return "⚠️ 删除没有保存成功（数据库写入失败），请稍后再试"
    return f"🗑️ 已删除待办 #{params.id}：{render.clip(title, 40)}"


async def list_todos(plugin, params, event):
    """只读：分页列出当前会话的待办（含已完成，每行只显示编号/标题/状态）。"""
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    page_size = plugin.list_page_size()
    requested = max(1, int(getattr(params, "page", 1) or 1))
    query = Todo.objects.filter(chat=chat.key)
    total = query.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(requested, pages)
    items = list(query.order_by("-visible", "id")
                 .offset((page - 1) * page_size)
                 .limit(page_size))
    return render.list_text(items, page=page, pages=pages, total=total)


async def show(plugin, params, event):
    """查看当前会话单条待办的完整信息。"""
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    todo = Todo.objects.filter(chat=chat.key, id=int(params.id)).first()
    if todo is None:
        return f"❌ 当前会话没有待办 #{params.id}（用 /todo list 查看编号）"
    return render.show_text(todo)


async def check(plugin, params, event):
    if _denied(plugin, event):
        return None
    chat = _chat(plugin, event)
    if chat is None:
        return "⚠️ 无法确定会话（拿不到群号 / QQ 号），请从群聊或私聊里重试"

    todo_id = int(params.id)
    todo = Todo.objects.filter(chat=chat.key, id=todo_id).first()
    if todo is None:
        return f"❌ 当前会话没有待办 #{todo_id}（用 /todo list 查看编号）"
    if todo.done:
        return f"✅ 待办 #{todo.id} 已经完成了"

    moment = now()

    # check = 完成当前这一项。有 repeat 规则时保留/重排下一次重复，
    # 到点由 tick() 把 done 重新打开；没有 repeat 规则就永久完成。
    if todo.repeat:
        upcoming = parse_iso(todo.next_repeat)
        if upcoming is None or upcoming <= moment:
            upcoming = next_occurrence(todo.repeat, advance_from(moment))
        todo.next_repeat = to_iso(upcoming)
    else:
        todo.next_repeat = ""

    todo.done = True
    todo.done_at = to_iso(moment)
    todo.checked_at = to_iso(moment)
    todo.next_remind = ""
    todo.missed = False
    try:
        todo.save()
    except StoreError as exc:
        plugin.logger.error("[todo] 保存待办失败：%s", exc)
        return "⚠️ 打卡没有保存成功（数据库写入失败），请稍后再试"

    text = f"✅ 已完成待办 #{todo.id}：{render.clip(todo.title, 40)}"
    if todo.next_repeat:
        text += f"\n下次重复：{render.fmt_datetime(todo.repeat_at)}（到时会重新打开）"
    return text


def _target_id(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


async def bind(plugin, params, event):
    """维护服务范围（``napcat.subscribe`` 的群/用户白名单）。

    支持 ``add`` / ``del`` / ``list`` 三个动作，群和用户都走同一条命令。
    """
    if _denied(plugin, event):
        return None

    action = str(params.action or "list").lower()
    kind = str(params.kind or "group").lower()
    kind = "group" if kind in ("group", "群") else "user"

    # 用 Subscription 归一后的名单（parse_ids 会丢掉 bool / 0 / 非数字），
    # 避免手改配置里出现字符串时与整数混排导致排序失败。
    subscription = plugin.subscription()
    groups = set(subscription.groups)
    users = set(subscription.users)
    if action == "list":
        return render.binding_text(groups, users)

    target = _target_id(str(params.target or ""))
    if target is None:
        return ("_schema_error",
                "要填正整数群号 / QQ 号，例如 /todo bind add group 100200300")

    if action == "add":
        (groups if kind == "group" else users).add(target)
    elif action == "del":
        (groups if kind == "group" else users).discard(target)
    else:
        return ("_schema_error", "action 只能是 list / add / del")

    plugin.config.set("binding", {
        "groups": sorted(int(value) for value in groups),
        "users": sorted(int(value) for value in users),
    })
    plugin.logger.info("[todo] 服务范围更新：%s %s %s", action, kind, target)
    return render.binding_text(groups, users)
