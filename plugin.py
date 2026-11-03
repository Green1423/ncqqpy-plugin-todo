"""todo —— 待办与周期提醒插件。

核心设计
--------
* **5 个信息**：``id`` / ``title`` / ``content`` / ``remind`` / ``repeat``。
  ``remind`` 是提醒时间规则表达式（``false`` / 空 = 不提醒），
  ``repeat`` 是重复时间规则表达式（``false`` / 空 = 不重复，``true`` = 同
  ``remind``）；两者都是字符串，空即 false。
* **表达式内核**：直接使用 ``date_expr`` 的表达式语言（内置引擎的设计），
  因此“周期待办”不需要额外的
  cron 语法：``8h`` 是每天 08:00，``1w & {18h}`` 是每周一 18:00，
  ``2026Y11M5D18h`` 是只发生一次的具体时刻。
* **集中扫描**：插件只注册一个 ``todo.tick`` 定时任务，按落盘的
  ``next_remind`` / ``next_repeat`` 扫描；重启后不会丢提醒，也不会为每条
  待办各留一个定时器。
* **服务范围**：使用框架的 ``napcat.subscribe.Subscription`` 白名单，
  与 ai_chat / byrdocs / oj_bank 同一口径（群聊按来源群、私聊按用户、
  空名单 = 谁都不用、超级用户豁免）。维护入口是 ``/todo bind``
  （``add`` / ``del`` 都支持）。
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

from napcat.plugin import PluginBase
from napcat.store import StoreError

try:  # 新框架
    from napcat.subscribe import Subscription
except ImportError:  # 旧框架的兼容名
    from napcat.scope import Scope as Subscription

from . import render
from .commands import todo as todo_cmd
from .jobs import reminder as reminder_job
from .schedule import now, parse_iso
from .schemas import (
    AddSchema, BindSchema, CheckSchema, DelSchema, ListSchema, SetSchema,
)
from .store import ChatRef, Todo, import_legacy_json


class TodoPlugin(PluginBase):
    """待办 + 周期提醒。"""

    name = "todo"
    version = "2.0.0"
    description = "待办与周期提醒（时间规则表达式 / 服务范围白名单）"
    author = "ncqqpy"

    # ------------------------------------------------------------------ 配置

    def config_template(self) -> dict:
        """出厂默认配置（框架构造期写入 ``config.json → plugins.todo``）。"""
        return {
            "enabled": True,
            "tick_seconds": 20,
            "misfire_grace_seconds": 600,
            "default_remind": "⏰ 待办提醒 #{id}：{title}\n时间：{time}",
            "max_todos_per_chat": 50,
            "list_page_size": 10,
            "done_keep_days": 30,
            # 服务范围（口径见 napcat.subscribe）：空 = 谁都不用，不回落
            # 框架的 bound_groups（那是“监听哪些群”，语义正好相反）。
            # 超级用户始终可用；给其他人/群开通用 /todo bind。
            "binding": {"groups": [], "users": []},
        }

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self):
        # ORM：建表 + 绑定；库默认在 plugins/todo/data/todo.db。
        self.database(ensure=[Todo])
        base = Path(self.plugin_dir) if self.plugin_dir else Path(__file__).resolve().parent
        legacy = base / "data" / "state.json"
        if legacy.exists() and Todo.objects.count() == 0:
            imported = import_legacy_json(legacy, logger=self.logger)
            if imported:
                self.logger.info("[todo] 已从旧 state.json 迁移 %d 条待办", imported)
        removed = self._prune_done(now())
        if removed:
            self.logger.info("[todo] 启动清理：删除 %d 条过旧的已完成待办", removed)
        self._visibility_filter = None
        self._command_names: list[str] = []
        self._denied: dict[int, tuple[float, int]] = {}
        self.logger.info("[todo] 已加载（ORM 表 %s）", Todo._table)

    async def on_enable(self):
        self._register_commands()
        self._install_visibility_filter()
        self._start_tick()

        # 每次 enable 都把断掉的“下一次”补回来：定时器本身是内存态，
        # 进程/插件停用后只剩数据库里的 next_remind / next_repeat。
        try:
            recovered = reminder_job.recover(self)
        except StoreError as exc:
            recovered = 0
            self.logger.warning("[todo] 恢复定时规则失败：%s", exc)
        if recovered:
            self.logger.info("[todo] 已恢复 %d 条待办的定时规则", recovered)

        subscription = self.subscription()
        groups, users = subscription.groups, subscription.users
        self.logger.info(
            "[todo] 已启用：扫描间隔 %d 秒，服务范围 %d 群 / %d 人",
            self.tick_seconds(), len(groups), len(users))
        if not groups and not users:
            self.logger.warning(
                "[todo] 服务范围为空：只有超级用户能用。"
                "用 /todo bind add group <群号> 或 /todo bind add user <QQ号> 开通")

    async def on_disable(self):
        self._remove_visibility_filter()
        self._unregister_commands()
        self._stop_tick()
        await super().on_disable()

    async def on_unload(self):
        self._remove_visibility_filter()

    # ------------------------------------------------------------------ 装配

    def _register_commands(self) -> None:
        """只做登记；业务在 ``commands/todo.py``。"""
        self._unregister_commands()

        def reg(name: str, **kwargs):
            self._command_names.append(name)
            return self.register_command(name, **kwargs)

        @reg("todo", aliases=["待办"], permission="", scope="all",
             description="待办与周期提醒：add / list / set / check / del")
        async def cmd_root(packet):
            # 命令组本体不会被执行：裸调由框架重定向到 /help todo。
            return None

        @reg("todo.add", schema=AddSchema, permission="", scope="all",
             description="添加待办：标题 + 可选文案 / 提醒规则 / 重复规则")
        async def cmd_add(params, event):
            return await todo_cmd.add(self, params, event)

        @reg("todo.list", schema=ListSchema, permission="", scope="all",
             description="分页查看待办列表；-a / --all=true 连已完成的一起看")
        async def cmd_list(params, event):
            return await todo_cmd.list_todos(self, params, event)

        @reg("todo.set", schema=SetSchema, permission="", scope="all",
             description="修改待办的标题 / 文案 / 提醒规则 / 重复规则")
        async def cmd_set(params, event):
            return await todo_cmd.update(self, params, event)

        @reg("todo.check", schema=CheckSchema, permission="", scope="all",
             description="打卡：完成单次待办，或跳过周期待办的这一次")
        async def cmd_check(params, event):
            return await todo_cmd.check(self, params, event)

        @reg("todo.del", schema=DelSchema, permission="", scope="all",
             description="删除待办")
        async def cmd_del(params, event):
            return await todo_cmd.delete(self, params, event)

        @reg("todo.bind", schema=BindSchema, permission="superuser", scope="private",
             description="维护服务范围白名单（仅私聊 · 仅超管）")
        async def cmd_bind(params, event):
            return await todo_cmd.bind(self, params, event)

    def _unregister_commands(self) -> None:
        for name in reversed(getattr(self, "_command_names", [])):
            try:
                self.unregister_command(name)
            except Exception:  # noqa: BLE001 - 回收阶段尽力而为
                self.logger.warning("[todo] 注销命令 %s 失败", name, exc_info=True)
        self._command_names = []

    def _start_tick(self) -> None:
        interval = self.tick_seconds()
        try:
            self.timer.cancel("todo.tick")
        except Exception:  # noqa: BLE001 - 不存在/已停用都不影响重新注册
            pass

        @self.schedule(interval, name="todo.tick", description="扫描待办并发送提醒")
        async def _tick():
            await reminder_job.tick(self)

    def _stop_tick(self) -> None:
        try:
            self.timer.cancel("todo.tick")
        except Exception:  # noqa: BLE001
            pass
        self._timers = [name for name in getattr(self, "_timers", [])
                        if name != "todo.tick"]

    # ------------------------------------------------------------------ 服务范围

    def binding(self) -> dict:
        """``config.binding`` 的副本（非 dict 按空处理）。"""
        config = self.config
        value = config.get("binding") if config is not None else {}
        return dict(value) if isinstance(value, dict) else {}

    def subscription(self):
        return Subscription.of(self.binding())

    def check_access(self, event) -> bool:
        """当前事件是否在本插件服务范围内；超级用户豁免。"""
        if event is None:
            return True                       # 帮助渲染 / 脚本直调
        try:
            user_id = int(getattr(event, "user_id", 0) or 0)
        except (TypeError, ValueError):
            return False
        perm = getattr(self, "perm", None)
        try:
            if perm is not None and perm.is_superuser(user_id):
                return True
        except Exception:  # noqa: BLE001 - 权限对象异常时退回白名单判定
            pass
        return self.subscription().event_ok(event)

    def log_scope_denied(self, event) -> None:
        """拒绝日志（按用户 60 秒节流，避免刷屏）。"""
        if event is None:
            return
        try:
            user_id = int(getattr(event, "user_id", 0) or 0)
        except (TypeError, ValueError):
            return
        moment = time.monotonic()
        denied = getattr(self, "_denied", None)
        if denied is None:
            denied = self._denied = {}
        last, count = denied.get(user_id, (0.0, 0))
        if moment - last < 60:
            denied[user_id] = (last, count + 1)
            return
        if len(denied) >= 256:
            denied.clear()
        denied[user_id] = (moment, 0)
        source = (f"，来源群 {event.group_id}"
                  if getattr(event, "is_temp_session", False) else "")
        self.logger.info(
            "[todo] 拒绝服务范围外的调用：用户 %s%s（白名单见 plugins.todo.binding）",
            user_id, source)

    def _install_visibility_filter(self) -> None:
        """让 ``/help`` 与执行同口径：范围外的人看不到 ``/todo``。"""
        cmds = getattr(self, "cmds", None)
        if cmds is None or not hasattr(cmds, "add_visibility_filter"):
            return
        if getattr(self, "_visibility_filter", None) is not None:
            try:
                cmds.remove_visibility_filter(self._visibility_filter)
            except Exception:  # noqa: BLE001
                pass

        def _visible(handler, event) -> bool:
            if getattr(handler, "plugin", "") != self.name:
                return True
            return self.check_access(event)

        self._visibility_filter = _visible
        cmds.add_visibility_filter(_visible)

    def _remove_visibility_filter(self) -> None:
        flt = getattr(self, "_visibility_filter", None)
        cmds = getattr(self, "cmds", None)
        if flt is not None and cmds is not None:
            try:
                cmds.remove_visibility_filter(flt)
            except Exception:  # noqa: BLE001
                pass
        self._visibility_filter = None

    # ------------------------------------------------------------------ 会话与发送

    def chat_of(self, event) -> ChatRef | None:
        """事件 → 归属会话。群小窗按用户归（提醒按临时会话发回）。"""
        if event is None:
            return None
        message_type = str(getattr(event, "message_type", "") or "")
        if message_type == "group":
            group_id = getattr(event, "group_id", None)
            try:
                group_id = int(group_id)
            except (TypeError, ValueError):
                return None
            if group_id <= 0:
                return None
            return ChatRef(f"group:{group_id}", "group", str(group_id))
        try:
            user_id = int(getattr(event, "user_id", 0) or 0)
        except (TypeError, ValueError):
            return None
        if user_id <= 0:
            return None
        via_group = ""
        if getattr(event, "is_temp_session", False) and getattr(event, "group_id", None):
            try:
                via_group = str(int(event.group_id))
            except (TypeError, ValueError):
                via_group = ""
        return ChatRef(f"user:{user_id}", "private", str(user_id), via_group)

    async def send_reminder(self, todo, when) -> None:
        """按待办所属会话发送提醒。"""
        template = todo.content or self.default_remind()
        text = render.reminder_text(todo, when, template)
        if todo.chat_type == "group":
            await self.api.send_group_msg(group_id=todo.chat_id, message=text)
        else:
            await self.api.send_private_msg(
                user_id=todo.chat_id, message=text,
                group_id=todo.via_group or None)

    # ------------------------------------------------------------------ 清理

    def _prune_done(self, moment) -> int:
        """删掉过旧/过多的已完成记录；返回删除条数。"""
        cutoff = moment - timedelta(days=self.done_keep_days())
        removed = 0
        kept: dict[str, int] = {}
        done = list(Todo.objects.filter(done=True))
        done.sort(key=lambda todo: todo.done_at or todo.checked_at
                  or todo.created_at or "", reverse=True)
        for todo in done:
            # 已完成但还在等下一次重复的条目不能清：到点要重新打开。
            if todo.next_repeat:
                continue
            finished = parse_iso(todo.done_at or todo.checked_at or todo.created_at)
            if finished is not None and finished < cutoff:
                todo.delete()
                removed += 1
                continue
            count = kept.get(todo.chat, 0)
            if count >= 100:
                todo.delete()
                removed += 1
                continue
            kept[todo.chat] = count + 1
        return removed

    # ------------------------------------------------------------------ 配置读取

    def _int_cfg(self, key: str, default: int, *, minimum: int = 0) -> int:
        config = self.config
        value = config.get(key, default) if config is not None else default
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, number)

    def default_remind(self) -> str:
        config = self.config
        value = str(config.get("default_remind") if config is not None else "").strip()
        return value or "⏰ 待办提醒 #{id}：{title}"

    def reminders_enabled(self) -> bool:
        config = self.config
        return bool(config.get("enabled", True)) if config is not None else True

    def tick_seconds(self) -> int:
        return self._int_cfg("tick_seconds", 20, minimum=5)

    def list_page_size(self) -> int:
        return min(50, self._int_cfg("list_page_size", 10, minimum=1))

    def misfire_grace_seconds(self) -> int:
        return self._int_cfg("misfire_grace_seconds", 600, minimum=0)

    def max_todos_per_chat(self) -> int:
        return self._int_cfg("max_todos_per_chat", 50, minimum=1)

    def done_keep_days(self) -> int:
        return self._int_cfg("done_keep_days", 30, minimum=1)
