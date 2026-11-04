"""每天定时发送待办日报。

日报按会话聚合：只要某个会话今天有完成记录，或还有未完成待办，就发一条。
没有可报告内容的会话不发。发送失败只记日志，不影响其它会话。
"""
from __future__ import annotations

from .. import render
from ..schedule import now
from ..store import Todo


async def send(plugin) -> int:
    """发送当天日报；返回实际发出的会话数。"""
    moment = now()
    todos = list(Todo.objects.all().order_by("id"))
    if not todos:
        return 0

    grouped: dict[str, list[Todo]] = {}
    for todo in todos:
        grouped.setdefault(todo.chat, []).append(todo)

    sent = 0
    for items in grouped.values():
        text = render.summary_text(items, moment)
        if not text:
            continue
        first = items[0]
        try:
            if first.chat_type == "group":
                await plugin.api.send_group_msg(
                    group_id=first.chat_id, message=text)
            else:
                await plugin.api.send_private_msg(
                    user_id=first.chat_id, message=text,
                    group_id=first.via_group or None)
        except Exception as exc:  # noqa: BLE001 - 单个会话失败不中断日报
            plugin.logger.warning(
                "[todo] 发送日报到 %s 失败：%s", first.chat, exc)
            continue
        sent += 1
    return sent
