"""``/todo`` 子命令的参数 Schema。

一条待办对外有 7 个信息：``id`` / ``title`` / ``content`` / ``remind`` /
``repeat`` / ``deadline`` / ``visible``。位置顺序与它一致：

    /todo add <title> [content] [remind] [repeat] [deadline] [visible]
    /todo set <id> [title] [content] [remind] [repeat] [deadline] [visible]

``remind`` / ``repeat`` 是时间规则表达式；``deadline`` 是具体时间
（``YYYY-MM-DD HH:MM``，``DatetimeField``），与 ``repeat`` 互斥；``visible=false``
不进入每日总结，列表中排在可见项之后；``repeat=true`` 表示与 ``remind``
用同一条表达式。
"""
from __future__ import annotations

from napcat.command import (
    BoolField, ChoiceField, CommandSchema, DatetimeField, IntField, StrField,
)
from .schedule import TZ

__all__ = [
    "AddSchema", "ListSchema", "SetSchema", "DelSchema", "CheckSchema",
    "ShowSchema", "BindSchema",
]


class AddSchema(CommandSchema):
    title = StrField(required=True, max_length=80,
                     description="待办标题")
    content = StrField(default="", max_length=500,
                       description="提醒文案（省略 = 用配置的默认文案；- = 恢复默认）")
    remind = StrField(default="", max_length=160,
                      description="提醒时间规则表达式；false / 省略 = 不提醒")
    repeat = StrField(default="", max_length=160,
                      description="重复时间规则表达式；false / 省略 = 不重复；true = 同 remind")
    deadline = DatetimeField(default=None, tz=TZ,
                             description="截止时间（YYYY-MM-DD HH:MM；false / 省略 = 无截止）")
    visible = BoolField(default=True, short="v",
                        description="是否显示在列表和每日总结；false = 隐藏（仍会提醒）")


class ListSchema(CommandSchema):
    page = IntField(default=1, min=1, description="页码（每页条数见 list_page_size）")


class SetSchema(CommandSchema):
    id = IntField(required=True, min=1, description="待办编号（见 /todo list）")
    title = StrField(default="", max_length=80,
                     description="新的待办标题（省略 = 不改）")
    content = StrField(default="", max_length=500,
                       description="新的提醒文案（省略 = 不改；- = 恢复默认）")
    remind = StrField(default="", max_length=160,
                      description="新的提醒规则（省略 = 不改；false / - = 不提醒）")
    repeat = StrField(default="", max_length=160,
                      description="新的重复规则（省略 = 不改；false / - = 不重复；true = 同 remind）")
    deadline = DatetimeField(default=None, tz=TZ,
                             description="新的截止时间（YYYY-MM-DD HH:MM；省略 = 不改；false / - = 清除）")
    visible = BoolField(default=None, short="v",
                        description="新的可见性（省略 = 不改；false = 隐藏）")


class ShowSchema(CommandSchema):
    id = IntField(required=True, min=1,
                  description="要查看的待办编号（见 /todo list）")


class DelSchema(CommandSchema):
    id = IntField(required=True, min=1, description="待办编号（见 /todo list）")


class CheckSchema(CommandSchema):
    id = IntField(required=True, min=1,
                  description="要打卡的待办编号（查看列表用 /todo list）")


class BindSchema(CommandSchema):
    action = ChoiceField(["list", "add", "del"], default="list",
                         description="list = 查看 / add = 开通 / del = 关闭")
    kind = ChoiceField(["group", "user", "群", "人"], default="group",
                       description="group/群 或 user/人")
    target = StrField(default="", max_length=32,
                      description="群号或 QQ 号（add / del 时必填）")
