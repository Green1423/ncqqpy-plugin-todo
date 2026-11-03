"""时间规则表达式的薄封装。

表达式内核在 :mod:`date_expr`（内置实现）。本模块只补插件需要的东西：固定时区、
``next_time`` 封装、ISO 落盘格式与
展示格式。

约定
----
* 所有时间都按 ``Asia/Shanghai`` 解释并返回带时区的 ``datetime``；
* 表达式粒度是分钟，``next_occurrence()`` 返回槽位的**代表时间**（见
  ``date_expr`` 的模块说明）；
* ``advance_from()`` 在“刚触发过”之后用于严格向后找下一次，避免因为
  取整到同一分钟而原地重复触发。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .date_expr import DateExprError, next_time, parse

__all__ = [
    "TZ", "DateExprError", "now", "parse_expression", "validate_expression",
    "next_occurrence", "advance_from", "is_periodic", "is_relative",
    "to_iso", "parse_iso", "fmt_datetime",
]

#: 机器人面向国内用户，表达式内核的默认时区也是这个；这里固定成同一个。
TZ = ZoneInfo("Asia/Shanghai")

#: 触发后向后找下一次的步长。比 0 大即可：表达式的最小粒度是分钟，
#: 加一秒既能跳过“当前这一分钟”，又不会漏掉下一分钟。
_ADVANCE = timedelta(seconds=1)

_WEEKDAY_CN = "一二三四五六日"

#: 间隔表达式：`+1D` = 每隔 1 天，`+2h` = 每隔 2 小时。
_RELATIVE_RE = re.compile(r"^\+\s*(\d+)\s*([YMDwhm])$")


def now() -> datetime:
    """当前时间（带 ``Asia/Shanghai`` 时区）。"""
    return datetime.now(TZ)


def _relative_parts(expression: str) -> tuple[int, str] | None:
    match = _RELATIVE_RE.fullmatch(str(expression or "").strip())
    if not match:
        return None
    count = int(match.group(1))
    if count <= 0:
        raise DateExprError("间隔表达式的数字必须大于 0")
    return count, match.group(2)


def is_relative(expression: str) -> bool:
    """``+1D`` / ``+2h`` 这类“每隔 N 个单位”的表达式。"""
    return bool(_RELATIVE_RE.fullmatch(str(expression or "").strip()))


def _add_interval(moment: datetime, count: int, unit: str) -> datetime:
    base = moment.astimezone(TZ).replace(second=0, microsecond=0)
    if unit == "m":
        return base + timedelta(minutes=count)
    if unit == "h":
        return base + timedelta(hours=count)
    if unit in ("D", "w"):
        days = count * (7 if unit == "w" else 1)
        return base + timedelta(days=days)
    months = count * 12 if unit == "Y" else count
    total = base.year * 12 + (base.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    if not 1 <= year <= 9999:
        raise DateExprError(f"间隔表达式超出可表示年份：{year}")
    day = min(base.day, monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def parse_expression(expression: str):
    """解析日期表达式（不含 ``+1D`` 这种间隔写法）。"""
    return parse(expression)


def validate_expression(expression: str) -> None:
    """校验表达式；``+1D`` 这类间隔写法单独放行。"""
    if is_relative(expression):
        _relative_parts(expression)
        return
    parse(expression)


def next_occurrence(expression: str, after: datetime | None = None) -> datetime | None:
    """表达式在 ``after`` 之后（含）的最近一次代表时间；无匹配返回 None。

    ``+1D`` / ``+2h`` 表示“每隔 1 天 / 2 小时”：第一次是 ``after + 1D``，
    之后每次从当前时刻继续往前推一个间隔。
    """
    if not expression:
        return None
    relative = _relative_parts(expression)
    if relative is not None:
        count, unit = relative
        moment = after or now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=TZ)
        return _add_interval(moment, count, unit)
    return next_time(expression, after or now())


def advance_from(moment: datetime) -> datetime:
    """触发之后用的“严格往后”起点。"""
    return moment + _ADVANCE


def is_periodic(expression: str, first: datetime | None) -> bool:
    """``first`` 之后是否还有下一次；用于把“单次 / 周期”展示给用户。"""
    if not expression:
        return False
    if is_relative(expression):
        return True
    if first is None:
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
    """ISO 字符串 → datetime；空值/坏值返回 None（状态文件坏一条不影响整体）。"""
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
