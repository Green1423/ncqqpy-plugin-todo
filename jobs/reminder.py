"""周期扫描与定时器恢复。

扫描由插件在 ``on_enable()`` 里注册的 ``todo.tick`` 定时任务驱动（默认 20 秒）。
不每条待办各挂一个定时器：待办数量会变、改规则要重排，集中扫描的边角情况更少。

``remind`` 和 ``repeat`` 是两条独立的规则：

* ``remind`` 为空 = 不提醒；否则到 ``next_remind`` 时发消息并排下一次；
* ``repeat`` 为空 = 不重复；否则 ``next_repeat`` 过期后按表达式往后排。

定时器是内存态：插件停用 / 进程重启后只剩数据库里的 ``next_remind`` /
``next_repeat``。所以每次 ``on_enable()`` 先调用 :func:`recover` 把断掉的
“下一次”补回来，再启动扫描任务。
"""
from __future__ import annotations

from napcat.store import StoreError

from ..schedule import (
    DateExprError, advance_from, next_occurrence, now, parse_iso, to_iso,
    validate_expression,
)
from ..store import Todo


def recover(plugin) -> int:
    """补齐 / 清理所有待办的下一次时间；返回实际修复的条数。

    只补“缺失/坏掉”的时间，不把已经到期的未来时间往后推：
    到期的提醒交给 ``tick()`` 处理（宽限期内补发，超过宽限跳过并排下一次）。
    已完成的重复待办也要恢复 ``next_repeat``，到点由 ``tick()`` 重新打开。
    """
    moment = now()
    repaired = 0
    for todo in Todo.objects.all().order_by("id"):
        changed = False
        try:
            if todo.repeat:
                if not todo.next_repeat or parse_iso(todo.next_repeat) is None:
                    todo.next_repeat = to_iso(
                        next_occurrence(todo.repeat, moment))
                    changed = True
            elif todo.next_repeat:
                todo.next_repeat = ""
                changed = True

            if todo.done:
                # 完成期间不提醒；重新打开时 tick() 会重新排 next_remind。
                if todo.next_remind:
                    todo.next_remind = ""
                    changed = True
            elif todo.remind:
                if not todo.next_remind or parse_iso(todo.next_remind) is None:
                    todo.next_remind = to_iso(
                        next_occurrence(todo.remind, moment))
                    changed = True
            elif todo.next_remind:
                todo.next_remind = ""
                changed = True
        except DateExprError as exc:
            plugin.logger.warning(
                "[todo] 待办 #%s 的规则无法恢复：%s", todo.id, exc)
            continue
        if changed:
            try:
                todo.save()
            except StoreError as exc:
                plugin.logger.warning(
                    "[todo] 恢复待办 #%s 的定时规则失败：%s", todo.id, exc)
                continue
            repaired += 1
    return repaired


async def tick(plugin) -> None:
    """扫一遍所有未完成待办；只有实际变化的记录才逐条保存。"""
    if not plugin.reminders_enabled():
        return

    moment = now()
    grace = plugin.misfire_grace_seconds()
    changed = False

    for todo in Todo.objects.all().order_by("id"):
        try:
            if todo.remind:
                validate_expression(todo.remind)
            if todo.repeat:
                validate_expression(todo.repeat)
        except DateExprError as exc:
            plugin.logger.warning(
                "[todo] 待办 #%s 的规则无效，跳过：%s", todo.id, exc)
            continue

        # ---- 重复规则：推进“下一次”；到点的已完成项重新打开 ----
        repeat_at = parse_iso(todo.next_repeat)
        if todo.repeat and repeat_at is not None and repeat_at <= moment:
            todo.next_repeat = to_iso(
                next_occurrence(todo.repeat, advance_from(moment)))
            if todo.done:
                todo.done = False
                todo.done_at = ""
                todo.reminded = False
                todo.missed = False
                todo.next_remind = to_iso(
                    next_occurrence(todo.remind, moment)) if todo.remind else ""
            todo.save()
            changed = True
        elif not todo.repeat and todo.next_repeat:
            todo.next_repeat = ""
            todo.save()
            changed = True

        # ---- 提醒规则：只对未完成项发消息 ----
        if todo.done:
            if todo.next_remind:
                todo.next_remind = ""
                todo.save()
                changed = True
            continue
        if not todo.remind:
            if todo.next_remind:
                todo.next_remind = ""
                todo.save()
                changed = True
            continue
        remind = parse_iso(todo.next_remind)
        if remind is None or remind > moment:
            continue

        late = (moment - remind).total_seconds()
        if late > grace:
            # 机器人离线/插件停用期间错过的提醒不补发：重启后连发一串旧消息
            # 比漏一条更糟。记日志并把它标成“已错过”，下一次照常排。
            plugin.logger.warning(
                "[todo] 待办 #%s 的提醒已错过 %.0f 秒（宽限 %d 秒），跳过本次",
                todo.id, late, grace)
            todo.missed = True
            todo.missed_at = to_iso(moment)
        else:
            try:
                await plugin.send_reminder(todo, remind)
            except Exception as exc:  # noqa: BLE001 - 单条发送失败不该中断整轮
                plugin.logger.warning(
                    "[todo] 发送待办 #%s 的提醒失败：%s", todo.id, exc)
                continue
            todo.last_reminded = to_iso(moment)
            todo.reminder_count += 1
            todo.reminded = True
            todo.missed = False

        todo.next_remind = to_iso(
            next_occurrence(todo.remind, advance_from(moment)))
        try:
            todo.save()
        except StoreError as exc:
            plugin.logger.warning(
                "[todo] 待办 #%s 保存失败：%s", todo.id, exc)
        changed = True

    if changed:
        plugin._prune_done(moment)
