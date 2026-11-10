"""待办与提醒的文本渲染（命令回执 / 定时提醒共用）。"""
from __future__ import annotations

from datetime import datetime

from .schedule import TZ, fmt_datetime, parse_iso
from .store import Todo

__all__ = [
    "clip", "todo_line", "list_text", "detail_text", "show_text",
    "reminder_text", "summary_text", "binding_text",
]


def _deadline(todo: Todo) -> datetime | None:
    """deadline 正常是 datetime；历史脏数据/空串统一当无截止。"""
    value = todo.deadline
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def clip(text: str, limit: int = 36) -> str:
    """单行化并按字符数截断（中文按字计，够用且不引入宽度依赖）。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"


def todo_line(todo: Todo) -> str:
    """列表只展示：编号、标题、是否完成。"""
    status = "已完成" if todo.done else "未完成"
    return f"#{todo.id} {clip(todo.title or '待办')} · {status}"


def list_text(todos: list[Todo], *, page: int = 1, pages: int = 1,
              total: int = 0) -> str:
    if total <= 0:
        return "\n".join([
            "📋 还没有待办",
            '💡 添加：/todo add 吃药 --remind="8h" --repeat="8h"',
            '   或：/todo add 周会 --repeat="1w & {18h}"',
        ])

    lines = [f"📋 待办（共 {total} 条 · 第 {page}/{pages} 页）"]
    lines.extend(f"  {todo_line(todo)}" for todo in todos)
    lines.append("💡 /todo check <编号> 完成 · /todo del <编号> 删除")
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(f"上一页 /todo list --page={page - 1}")
        if page < pages:
            nav.append(f"下一页 /todo list --page={page + 1}")
        if nav:
            lines.append("💡 " + " · ".join(nav))
    return "\n".join(lines)


def detail_text(todo: Todo, *, verb: str = "已添加") -> str:
    """单条回执（7 个信息 + 派生出的下一次时间）。"""
    lines = [
        f"✅ {verb}待办 #{todo.id}",
        f"标题：{todo.title or '待办'}",
    ]
    if todo.content:
        lines.append(f"提醒文案：{todo.content}")
    lines.append(f"提醒规则：{todo.remind or '不提醒'}")
    lines.append(f"重复规则：{todo.repeat or '不重复'}")
    deadline = _deadline(todo)
    lines.append("截止时间：" + (fmt_datetime(deadline) if deadline else "无"))
    lines.append(f"可见：{'是' if todo.visible else '否'}")
    if todo.next_remind:
        lines.append(f"下次提醒：{fmt_datetime(todo.remind_at)}")
    if todo.next_repeat:
        lines.append(f"下次重复：{fmt_datetime(todo.repeat_at)}")
    return "\n".join(lines)


def show_text(todo: Todo) -> str:
    """``/todo show`` 的单条完整信息。"""
    deadline = _deadline(todo)
    lines = [
        f"📋 待办 #{todo.id}",
        f"标题：{todo.title or '待办'}",
        f"状态：{'已完成' if todo.done else '未完成'}",
        f"可见：{'是' if todo.visible else '否'}",
    ]
    lines.append("提醒文案：" + (todo.content or "（默认）"))
    lines.append(f"提醒规则：{todo.remind or '不提醒'}")
    lines.append(f"重复规则：{todo.repeat or '不重复'}")
    lines.append("截止时间：" + (fmt_datetime(deadline) if deadline else "无"))
    lines.append(f"下次提醒：{fmt_datetime(todo.remind_at)}"
                 if todo.next_remind else "下次提醒：无")
    lines.append(f"下次重复：{fmt_datetime(todo.repeat_at)}"
                 if todo.next_repeat else "下次重复：无")
    created = parse_iso(todo.created_at)
    if created is not None:
        lines.append(f"创建时间：{fmt_datetime(created)}")
    if todo.done:
        finished = parse_iso(todo.done_at or todo.checked_at)
        if finished is not None:
            lines.append(f"完成时间：{fmt_datetime(finished)}")
    return "\n".join(lines)


def reminder_text(todo: Todo, when, template: str) -> str:
    """把提醒模板里的占位符替换成当前这条待办的值。

    只替换已知占位符，不调用 ``str.format``：用户文案里出现 ``{`` / ``}``
    （代码、正则、模板）时不应该报错，也不应该被当成未知字段。
    """
    deadline = _deadline(todo)
    values = {
        "id": str(todo.id),
        "title": todo.title or "待办",
        "content": todo.content or todo.title or "待办",
        "time": fmt_datetime(when),
        "expr": todo.remind,
        "remind": todo.remind,
        "repeat": todo.repeat,
        "deadline": fmt_datetime(deadline) if deadline else "无截止",
    }
    template = (template or "⏰ 待办提醒 #{id}：{title}").strip()
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    text = text or f"⏰ 待办提醒：{todo.title or '待办'}"
    if "{id}" not in template:
        # 提醒必须能让用户对上号：模板没写 {id} 时补一行编号。
        text = f"待办 #{todo.id}\n{text}"
    return text


def summary_text(todos: list[Todo], moment) -> str:
    """当天完成情况日报；没有可报告的内容时返回空串。"""
    todos = [todo for todo in todos if todo.visible]
    active = [todo for todo in todos if not todo.done]
    completed_today: list[Todo] = []
    for todo in todos:
        if not todo.done:
            continue
        finished = parse_iso(todo.done_at or todo.checked_at)
        if finished is not None and finished.date() == moment.date():
            completed_today.append(todo)

    if not active and not completed_today:
        return ""

    overdue = [todo for todo in active
               if _deadline(todo) is not None and _deadline(todo) <= moment]

    lines = [f"🌙 待办日报 {moment:%m-%d}"]
    done_line = f"✅ 今日完成 {len(completed_today)} 条"
    if completed_today:
        shown = "、".join(
            f"#{todo.id} {clip(todo.title, 16)}"
            for todo in completed_today[:5])
        done_line += f"：{shown}"
        if len(completed_today) > 5:
            done_line += f" 等 {len(completed_today)} 条"
    lines.append(done_line)
    lines.append(f"⏳ 未完成 {len(active)} 条")
    if overdue:
        lines.append(f"⚠️ 已逾期 {len(overdue)} 条")

    if active:
        lines.append("📌 未完成：")
        for todo in active[:10]:
            suffix = ""
            deadline = _deadline(todo)
            if deadline is not None:
                suffix = f"（截止 {deadline:%m-%d %H:%M}"
                if deadline <= moment:
                    suffix += "，已逾期"
                suffix += "）"
            lines.append(f"  #{todo.id} {clip(todo.title, 20)}{suffix}")
        if len(active) > 10:
            lines.append(f"  …另有 {len(active) - 10} 条")
    else:
        lines.append("🎉 当前没有未完成待办")
    return "\n".join(lines)


def binding_text(groups: set[int], users: set[int]) -> str:
    """``/todo bind list`` 的输出。"""
    def shown(values: set[int]) -> str:
        return "、".join(str(value) for value in sorted(values)) if values else "（空）"

    lines = [
        "🔐 服务范围（空 = 谁都不用；超级用户始终可用）",
        f"群：{shown(groups)}",
        f"用户：{shown(users)}",
    ]
    if not groups and not users:
        lines.append("💡 开通：/todo bind add group <群号> 或 /todo bind add user <QQ号>")
    else:
        lines.append("💡 关闭：/todo bind del group <群号> 或 /todo bind del user <QQ号>")
    return "\n".join(lines)
