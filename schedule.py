"""时间规则表达式的薄封装。

表达式内核完全来自 :mod:`date_expr`（即 ``learn/nearest_date_expr.py`` 的
原样实现），本模块只补插件需要的东西：固定时区、``next_time`` 封装、ISO
落盘格式与展示格式。**不新增任何表达式语法。**

约定
----
* 所有时间都按 ``Asia/Shanghai`` 解释并返回带时区的 ``datetime``；
* 表达式粒度是分钟，``next_occurrence()`` 返回槽位的**代表时间**（见
  ``date_expr`` 的模块说明）；
* ``advance_from()`` 在“刚触发过”之后用于严格向后找下一次，避免因为
  取整到同一分钟而原地重复触发。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .date_expr import DateExprError, next_time, parse

__all__ = [
    "TZ", "DateExprError", "now", "parse_expression", "next_occurrence",
    "first_occurrence", "advance_from", "is_periodic", "to_iso", "parse_iso",
    "fmt_datetime",
]

#: 机器人面向国内用户，表达式内核的默认时区也是这个；这里固定成同一个。
TZ = ZoneInfo("Asia/Shanghai")

#: 触发后向后找下一次的步长。比 0 大即可：表达式的最小粒度是分钟，
#: 加一秒既能跳过“当前这一分钟”，又不会漏掉下一分钟。
_ADVANCE = timedelta(seconds=1)

_WEEKDAY_CN = "一二三四五六日"


def now() -> datetime:
    """当前时间（带 ``Asia/Shanghai`` 时区）。"""
    return datetime.now(TZ)


def parse_expression(expression: str):
    """解析表达式；语法错误会抛 :class:`DateExprError`。"""
    return parse(expression)


def next_occurrence(expression: str, after: datetime | None = None) -> datetime | None:
    """表达式在 ``after`` 之后（含）的最近一次代表时间；无匹配返回 None。"""
    if not expression:
        return None
    return next_time(expression, after or now())


def first_occurrence(expression: str,
                     moment: datetime | None = None) -> datetime | None:
    """首次排程用的“严格未来”时间。

    ``learn`` 的周期表达式 ``+1D`` 序列包含起点（当前分钟）；插件是提醒器，
    首次排程取严格未来的一次，避免“刚添加就立刻提醒”。普通日历表达式
    行为不变；``now`` 这类单点也不跳过。
    """
    moment = moment or now()
    if str(expression or "").lstrip().startswith("+"):
        return next_time(expression, advance_from(moment))
    return next_time(expression, moment)


def advance_from(moment: datetime) -> datetime:
    """触发之后用的“严格往后”起点。"""
    return moment + _ADVANCE


def is_periodic(expression: str, first: datetime | None) -> bool:
    """``first`` 之后是否还有下一次；用于把“单次 / 周期”展示给用户。"""
    if not expression or first is None:
        return False
    try:
        return next_occurrence(expression, advance_from(first)) is not None
    except DateExprError:
        return False


def to_iso(moment: datetime | None) -> str:
    """datetime → 落盘用的 ISO 字符串（秒精度，带时区）。"""
    if moment is None:
        return ""
    return moment.astimezone(TZ).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """ISO 字符串 → datetime；空值/坏值返回 None。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def fmt_datetime(moment: datetime | None, *, short: bool = False) -> str:
    """展示用：``2026-11-09 09:00（周一）`` / ``11-09 09:00（周一）``。"""
    if moment is None:
        return "—"
    weekday = _WEEKDAY_CN[moment.weekday()]
    if short:
        return f"{moment:%m-%d %H:%M}（周{weekday}）"
    return f"{moment:%Y-%m-%d %H:%M}（周{weekday}）"
