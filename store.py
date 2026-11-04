"""待办的 ORM 模型（``napcat.store``）。

一条待办对外有 6 个信息：``id`` / ``title`` / ``content`` / ``remind`` /
``repeat`` / ``deadline``。``remind`` / ``repeat`` 是时间规则表达式字符串；
``deadline`` 是具体时间点（``DateTimeField``），与 ``repeat`` 互斥。
``next_remind``、``next_repeat``、``done`` 等是调度与展示用的内部列。

表由框架的 ``PluginBase.database(ensure=[Todo])`` 建在
``plugins/todo/data/todo.db``；增删改查全部走 ``Todo.objects``。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from napcat.store import (
    BooleanField, CharField, DateTimeField, IntegerField, Model, TextField,
)

from .schedule import DateExprError, parse_iso, parse_expression

__all__ = ["ChatRef", "Todo", "import_legacy_json"]


@dataclass(frozen=True)
class ChatRef:
    """一条消息所属的会话（命令写入、提醒发回都用它）。"""

    key: str
    kind: str                  # group / private
    id: str                    # 群号 / QQ 号（字符串，API 参数）
    via_group: str = ""        # 群小窗的来源群；普通会话为空


class Todo(Model):
    """一条待办。

    对外有 6 个字段；其余列是调度与状态元数据。

    * ``remind`` 为空 = 不提醒；否则是提醒时间规则表达式。
    * ``repeat`` 为空 = 不重复；否则是重复时间规则表达式。
    * ``deadline`` 为空 = 无截止；否则是一个具体时间点，且与 ``repeat`` 互斥。
    """

    class Meta:
        table = "todo"

    chat = CharField(max_length=64, index=True)
    chat_type = CharField(max_length=8, default="private")
    chat_id = CharField(max_length=32)
    via_group = CharField(max_length=32, default="")
    owner = IntegerField(default=0)

    title = CharField(max_length=80)
    content = TextField(default="")
    remind = CharField(max_length=160, default="")
    repeat = CharField(max_length=160, default="")
    deadline = DateTimeField(default="")

    created_at = CharField(max_length=40, default="")
    next_remind = CharField(max_length=40, default="")
    next_repeat = CharField(max_length=40, default="")
    last_reminded = CharField(max_length=40, default="")
    reminded = BooleanField(default=False)
    missed = BooleanField(default=False)
    missed_at = CharField(max_length=40, default="")
    done = BooleanField(default=False, index=True)
    done_at = CharField(max_length=40, default="")
    checked_at = CharField(max_length=40, default="")
    reminder_count = IntegerField(default=0)

    def save(self, *, using=None):
        # 旧库的 deadline 列是 NOT NULL DEFAULT ''；空值统一写空串，
        # 读取时 DateTimeField 会还原成 None。
        if self.deadline is None:
            self.deadline = ""
        return super().save(using=using)

    @property
    def remind_at(self):
        return parse_iso(self.next_remind)

    @property
    def repeat_at(self):
        return parse_iso(self.next_repeat)


# ---------------------------------------------------------------------------
# 旧 JSON 状态迁移
# ---------------------------------------------------------------------------

_FALSE_WORDS = {"false", "0", "no", "off", "none", "无", "不提醒", "不重复", "关", "-"}
_TRUE_WORDS = {"true", "1", "yes", "on", "是", "提醒", "重复"}


def _legacy_rule(value, fallback: str = "") -> str:
    if isinstance(value, bool):
        return fallback if value else ""
    if value is None:
        return fallback
    text = str(value).strip()
    if text.lower() in _FALSE_WORDS:
        return ""
    if text.lower() in _TRUE_WORDS:
        return fallback
    return text or fallback


def _legacy_fields(data: dict) -> dict | None:
    """把旧 ``state.json`` 的一条记录映射成 ``Todo`` 字段；坏记录返回 None。"""
    def text(key: str, default: str = "") -> str:
        value = data.get(key, default)
        return default if value is None else str(value)

    def number(key: str, default: int = 0) -> int:
        try:
            return int(data.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    def flag(key: str, default: bool = False) -> bool:
        value = data.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是", "开")
        return bool(value)

    chat = text("chat")
    chat_id = text("chat_id")
    if not chat or not chat_id:
        return None

    legacy_remind = text("remind_expr") or text("time_expr")
    remind = _legacy_rule(data.get("remind", None), legacy_remind)
    legacy_repeat = data.get("repeat", None)
    repeat = _legacy_rule(legacy_repeat, remind)
    if not repeat and isinstance(legacy_repeat, bool) and legacy_repeat:
        repeat = remind

    next_remind = (text("next_remind") or text("next_at")
                   or text("next_due"))
    next_repeat = text("next_repeat")
    if not next_repeat and (repeat or legacy_repeat is True):
        next_repeat = next_remind
    if not remind:
        next_remind = ""
    if not repeat:
        next_repeat = ""

    # 坏表达式不迁进 ORM：否则扫描任务每轮都会抛 DateExprError。
    for rule in (remind, repeat):
        if not rule:
            continue
        try:
            parse_expression(rule)
        except DateExprError:
            return None

    fields = {
        "chat": chat,
        "chat_type": text("chat_type", "private") or "private",
        "chat_id": chat_id,
        "via_group": text("via_group"),
        "owner": number("owner"),
        "title": text("title") or "待办",
        "content": text("content"),
        "remind": remind,
        "repeat": repeat,
        "created_at": text("created_at"),
        "next_remind": next_remind,
        "next_repeat": next_repeat,
        "last_reminded": text("last_reminded"),
        "reminded": flag("reminded", False),
        "missed": flag("missed", False),
        "missed_at": text("missed_at"),
        "done": flag("done", False),
        "done_at": text("done_at"),
        "checked_at": text("checked_at"),
        "reminder_count": number("reminder_count"),
    }
    legacy_id = number("id")
    if legacy_id > 0:
        fields["id"] = legacy_id
    return fields


def import_legacy_json(path: str | Path, *, logger=None) -> int:
    """把旧的 ``data/state.json`` 导入 ORM（仅在 ORM 表为空时调用）。

    导入成功后把文件改名为 ``state.json.migrated``，避免以后删光 ORM 记录后
    再次把旧数据灌回来。返回导入条数。
    """
    log = logger or logging.getLogger(__name__)
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("[todo] 旧状态文件读取失败：%s", exc)
        return 0
    raw = data.get("todos") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        _mark_migrated(source, log)
        return 0

    imported = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fields = _legacy_fields(entry)
        if fields is None:
            log.warning("[todo] 跳过一条无法迁移的旧待办：%r", entry.get("id"))
            continue
        try:
            Todo.objects.create(**fields)
        except Exception as exc:  # noqa: BLE001 - 单条失败不挡其它记录
            log.warning("[todo] 迁移旧待办 %r 失败：%s", entry.get("id"), exc)
            continue
        imported += 1
    if imported:
        _mark_migrated(source, log)
    return imported


def _mark_migrated(source: Path, log) -> None:
    target = source.with_name(source.name + ".migrated")
    try:
        source.replace(target)
    except OSError as exc:
        log.warning("[todo] 旧状态文件改名失败（下次仍会尝试）：%s", exc)
