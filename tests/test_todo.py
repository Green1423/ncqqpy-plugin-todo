"""todo 插件验收：表达式、ORM、分页、提醒、定时器恢复、白名单。

跑法：
    ./venv-arm/bin/python -B tests/test_todo.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from napcat.client import GroupBinding
from napcat.command import CommandManager
from napcat.config import ConfigManager
from napcat.events import EventBus, TimerManager
from napcat.file_manager import FileManager
from napcat.models import MessageEvent
from napcat.permission import PermissionManager
from napcat.plugin import PluginManager
from napcat.store import Database

from plugins.todo import schedule
from plugins.todo.store import Todo, import_legacy_json

PASSED = 0
FAILED = 0
SUPERUSER = 10001


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


class FakeApi:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_group_msg(self, group_id, message, **kwargs):
        self.sent.append(("group", str(group_id), str(message)))

    async def send_private_msg(self, user_id, message, group_id=None, **kwargs):
        self.sent.append(("private", str(user_id), str(message),
                          str(group_id) if group_id else None))


class Notified:
    def set_plugin_resolver(self, resolver):
        pass

    async def notify_operators(self, error, *, where=""):
        print(f"  [notify] {where}: {error}")


class Env:
    def __init__(self, *, page_size: int = 10):
        self.tmp = Path(tempfile.mkdtemp(prefix="todo-test-"))
        self.plugins_dir = self.tmp / "plugins"
        shutil.copytree(
            ROOT / "plugins" / "todo", self.plugins_dir / "todo",
            ignore=shutil.ignore_patterns("__pycache__", "data", ".junk-*"))
        (self.plugins_dir / "todo" / "data").mkdir()

        logger = logging.getLogger("test.todo")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL)

        self.bus = EventBus()
        self.timer = TimerManager()
        self.timer.start()
        self.perm = PermissionManager(superusers=[SUPERUSER])
        self.cmds = CommandManager(prefixes=["/"], perm_manager=self.perm)
        self.cfg = ConfigManager(str(self.tmp / "config.json"))
        self.cfg.set("plugins.todo.list_page_size", page_size)
        self.api = FakeApi()
        self.mgr = PluginManager(
            str(self.plugins_dir), api=self.api, event_bus=self.bus,
            timer=self.timer, cmd_manager=self.cmds, perm_manager=self.perm,
            file_manager=FileManager(api=None), logger=logger,
            groups=GroupBinding(bound=set(), all_groups=True), cfg=self.cfg,
            client=SimpleNamespace(plugins=None), error_reporter=Notified())
        self.mgr._client.plugins = self.mgr
        self.cmds._plugins_ref = self.mgr
        self.plugin = None

    def model(self):
        """插件加载器里的 Todo 类（与测试直接 import 的不是同一个模块对象）。"""
        module = sys.modules.get("napcat_plugin_todo.store")
        if module is None:
            raise AssertionError("todo.store 还没有被插件加载器导入")
        return module.Todo

    def jobs(self):
        module = sys.modules.get("napcat_plugin_todo.jobs.reminder")
        if module is None:
            raise AssertionError("todo.jobs.reminder 还没有被插件加载器导入")
        return module

    async def start(self):
        self.plugin = await self.mgr.load("todo")
        check("插件加载成功", self.plugin is not None)
        await self.mgr.enable("todo")
        check("插件启用成功", bool(getattr(self.plugin, "enabled", False)))
        return self.plugin

    async def ask(self, text: str, user: int = SUPERUSER, group: int | None = None,
                  temp_group: int | None = None) -> str:
        if group is not None:
            event = MessageEvent(message_type="group", user_id=user,
                                 group_id=group, message=text)
        elif temp_group is not None:
            event = MessageEvent(message_type="private", user_id=user,
                                 group_id=temp_group, sub_type="group",
                                 message=text)
        else:
            event = MessageEvent(message_type="private", user_id=user,
                                 message=text)
        result = await self.cmds.handle(event)
        return str(result or "")

    async def close(self):
        try:
            await self.mgr.disable("todo")
        finally:
            await self.mgr.unload("todo")
            self.timer.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)


# ==================== 1. 表达式 ====================

def test_expressions() -> None:
    print("\n[1] 时间规则表达式（nearest_date_expr 语义）")
    from datetime import datetime

    pivot = datetime(2026, 11, 3, 22, 56, tzinfo=schedule.TZ)

    def at(expr: str) -> str:
        moment = schedule.next_occurrence(expr, pivot)
        return moment.strftime("%Y-%m-%d %H:%M") if moment else ""

    check("8h = 明天 08:00", at("8h") == "2026-11-04 08:00", at("8h"))
    check("1w = 下周一 09:00", at("1w") == "2026-11-09 09:00", at("1w"))
    check("1D = 下月 1 日 09:00", at("1D") == "2026-12-01 09:00", at("1D"))
    check("{1,5}D = 每月 1/5 日", at("{1,5}D") == "2026-11-05 09:00",
          at("{1,5}D"))
    check("[1,5]D = 1–5 日连续范围", at("[1,5]D") == "2026-11-04 09:00",
          at("[1,5]D"))
    check("eM = 本月最后一天 09:00", at("eM") == "2026-11-30 09:00", at("eM"))
    check("1w & {8h} = 下周一 08:00", at("1w & {8h}") == "2026-11-09 08:00",
          at("1w & {8h}"))
    check("精确到分钟只发生一次", at("2026Y11M5D8h") == "2026-11-05 08:00",
          at("2026Y11M5D8h"))
    check("空集返回 None", schedule.next_occurrence("{10h5m}!{10h}", pivot) is None)
    check("周期判定：8h 是周期", schedule.is_periodic(
        "8h", schedule.next_occurrence("8h", pivot)))
    check("周期判定：具体时刻不是周期", not schedule.is_periodic(
        "2026Y11M5D8h", schedule.next_occurrence("2026Y11M5D8h", pivot)))
    try:
        schedule.parse_expression("not-an-expression")
        raised = False
    except schedule.DateExprError:
        raised = True
    check("非法表达式抛 DateExprError", raised)

    check("+1D = 每隔 1 天", at("+1D") == "2026-11-04 22:56", at("+1D"))
    check("+2h = 每隔 2 小时", at("+2h") == "2026-11-04 00:56", at("+2h"))
    check("+1w = 每隔 1 周", at("+1w") == "2026-11-10 22:56", at("+1w"))
    check("+1M = 每隔 1 个月", at("+1M") == "2026-12-03 22:56", at("+1M"))
    check("+1D 是周期规则", schedule.is_periodic("+1D", pivot))
    try:
        schedule.validate_expression("+0D")
        raised = False
    except schedule.DateExprError:
        raised = True
    check("+0D 被拒绝", raised)
    schedule.validate_expression("+1D")
    check("+1D 通过校验", True)


# ==================== 2. ORM ====================

def test_orm() -> None:
    print("\n[2] ORM（napcat.store）")
    tmp = Path(tempfile.mkdtemp(prefix="todo-orm-"))
    db = Database(str(tmp / "todo.db"))
    db.register(Todo)
    check("建表成功", db.table_exists(Todo._table))

    todo = Todo.objects.create(
        chat="user:1", chat_type="private", chat_id="1",
        title="买牛奶", content="记得买", remind="8h", repeat="1w",
        next_remind="2026-11-04T08:00:00+08:00",
        next_repeat="2026-11-09T09:00:00+08:00")
    check("create 回填主键", todo.pk == 1)
    check("按会话查询", Todo.objects.filter(chat="user:1").count() == 1)
    loaded = Todo.objects.get(pk=1)
    check("字段类型往返", (loaded.title, loaded.remind, loaded.repeat)
          == ("买牛奶", "8h", "1w"))
    loaded.title = "买酸奶"
    loaded.save()
    check("save 更新", Todo.objects.get(pk=1).title == "买酸奶")

    for index in range(5):
        Todo.objects.create(chat="user:2", chat_type="private", chat_id="2",
                            title=f"分页{index}", content="", remind="",
                            repeat="")
    page = list(Todo.objects.filter(chat="user:2").order_by("id")
                .offset(2).limit(2))
    check("QuerySet offset/limit 分页", [item.title for item in page]
          == ["分页2", "分页3"], str([item.title for item in page]))
    check("count 只算当前过滤", Todo.objects.filter(chat="user:2").count() == 5)

    loaded.delete()
    check("delete 生效", Todo.objects.filter(chat="user:1").count() == 0)

    # 旧 JSON 状态迁移（只在 ORM 表为空时执行）。
    Todo.objects.all().delete()
    legacy = tmp / "state.json"
    legacy.write_text(json.dumps({"version": 1, "todos": [{
        "id": 1, "chat": "user:9", "chat_type": "private", "chat_id": "9",
        "title": "旧状态", "content": "旧文案", "time_expr": "8h",
        "repeat": True, "next_due": "2026-11-04T08:00:00+08:00",
    }]}, ensure_ascii=False), encoding="utf-8")
    imported = import_legacy_json(legacy)
    migrated = Todo.objects.get(pk=1)
    check("旧 JSON 迁移条数", imported == 1, str(imported))
    check("旧 time_expr 映射 remind", migrated.remind == "8h", migrated.remind)
    check("旧 repeat=True 映射 repeat=remind",
          migrated.repeat == "8h", migrated.repeat)
    check("迁移后旧文件改名", not legacy.exists()
          and (tmp / "state.json.migrated").exists())
    fresh = Todo.objects.create(chat="user:10", chat_type="private",
                                chat_id="10", title="迁移后新增", content="",
                                remind="", repeat="")
    check("迁移后主键仍唯一且继续增长", fresh.pk > 1, str(fresh.pk))
    db.close()


# ==================== 3. 命令链路 ====================

async def test_commands(env: Env) -> None:
    print("\n[3] add / list / set / check / del")
    text = await env.ask('/todo add 吃药 --remind="8h" --repeat="8h"')
    check("添加成功并分配编号", "已添加待办 #1" in text, text)
    check("提醒/重复规则都写入", "提醒规则：8h" in text
          and "重复规则：8h" in text, text)

    text = await env.ask('/todo add 周会 "周会要开始了" '
                        '"1w & {17h30m}" "1w & {18h}"')
    check("位置顺序 title/content/remind/repeat", "已添加待办 #2" in text
          and "提醒文案：周会要开始了" in text
          and "提醒规则：1w & {17h30m}" in text
          and "重复规则：1w & {18h}" in text, text)

    text = await env.ask('/todo add 喝水')
    check("省略两条规则 = 不提醒/不重复", "提醒规则：不提醒" in text
          and "重复规则：不重复" in text, text)

    text = await env.ask('/todo add 买菜 --remind=false --repeat=false')
    check("remind/repeat=false 都清空", "提醒规则：不提醒" in text
          and "重复规则：不重复" in text, text)

    future = schedule.now() + timedelta(days=2)
    exact = f"{future.year}Y{future.month}M{future.day}D8h"
    text = await env.ask(f'/todo add 交房租 --remind="{exact}"')
    check("一次性提醒：repeat 为空", "重复规则：不重复" in text, text)

    text = await env.ask('/todo add 重复真 --remind="8h" --repeat=true')
    check("repeat=true 等于 remind", "重复规则：8h" in text, text)
    text = await env.ask('/todo add 重复真没提醒 --repeat=true')
    check("repeat=true 缺 remind 时报错", "repeat=true 需要" in text, text)

    text = await env.ask('/todo add 坏提醒 --remind="not-an-expression"')
    check("非法提醒表达式被拒绝", "无效" in text, text)
    text = await env.ask('/todo add 坏重复 --repeat="not-an-expression"')
    check("非法重复表达式被拒绝", "无效" in text, text)

    text = await env.ask("/todo list")
    check("list 有分页表头", "共 6 条 · 第 1/1 页" in text, text)
    check("list 只展示编号/标题/是否完成",
          "#1 吃药 · 未完成" in text and "提醒规则" not in text
          and "无时间" not in text and "规则" not in text, text)
    text = await env.ask("/todo check")
    check("check 必须带编号，不再兼职列表", "用法不正确" in text, text)

    text = await env.ask("/todo check 1")
    check("check 语义 = 完成此项", "已完成待办 #1" in text, text)
    check("重复项完成时保留下次重复并提示重开",
          "下次重复" in text and "重新打开" in text, text)
    first = env.model().objects.get(pk=1)
    check("完成后 done=True", first.done is True)
    check("完成后仍排了下一次重复", first.next_repeat != "", first.next_repeat)

    text = await env.ask("/todo set 2 --repeat=false")
    check("set --repeat=false 清除重复", "重复规则：不重复" in text, text)
    text = await env.ask("/todo check 2")
    check("不重复待办打卡直接完成", "已完成待办 #2" in text, text)
    check("完成后不再有下次提醒", env.model().objects.get(pk=2).next_remind == "")
    check("不重复项完成后没有下次重复", env.model().objects.get(pk=2).next_repeat == "")

    text = await env.ask('/todo set 3 --remind="8h" --repeat="8h"')
    check("无规则待办可补两条规则", "提醒规则：8h" in text
          and "重复规则：8h" in text, text)
    text = await env.ask("/todo set 3 --remind=false")
    check("set --remind=false 只清提醒", "提醒规则：不提醒" in text
          and "重复规则：8h" in text, text)
    text = await env.ask("/todo check 3")
    check("有重复规则时完成但排重开", "已完成待办 #3" in text
          and "下次重复" in text, text)
    check("完成后重复时间仍在", env.model().objects.get(pk=3).next_repeat != "")

    one_shot = env.model().objects.filter(title="交房租").first()
    text = await env.ask(f"/todo check {one_shot.pk}")
    check("一次性提醒打卡后完成", "已完成待办" in text, text)
    check("一次性提醒不再有下一次",
          env.model().objects.get(pk=one_shot.pk).next_remind == "")

    text = await env.ask("/todo list")
    check("完成项仍在列表里显示状态",
          "#1 吃药 · 已完成" in text and "#2 周会 · 已完成" in text, text)
    check("列表仍分页统计全部", "共 6 条 · 第 1/1 页" in text, text)

    text = await env.ask(f"/todo del {one_shot.pk}")
    check("删除成功", "已删除待办" in text, text)
    check("删除后查不到", env.model().objects.filter(pk=one_shot.pk).first() is None)

    text = await env.ask("/todo del 99999")
    check("删除不存在的编号有提示", "没有待办" in text, text)


# ==================== 4. 分页 ====================

async def test_pagination() -> None:
    print("\n[4] list 分页")
    env = Env(page_size=3)
    try:
        await env.start()
        for index in range(1, 6):
            await env.ask(f"/todo add 第{index}条")
        text = await env.ask("/todo list")
        check("第 1 页显示 3 条", "第 1/2 页" in text
              and "#1" in text and "#3" in text and "#4" not in text, text)
        check("第 1 页有下一页提示", "--page=2" in text, text)
        text = await env.ask("/todo list 2")
        check("第 2 页显示剩余 2 条", "第 2/2 页" in text
              and "#4" in text and "#5" in text and "#1" not in text, text)
        check("第 2 页有上一页提示", "--page=1" in text, text)
        text = await env.ask("/todo list --page=9")
        check("超出页码夹到最后一页", "第 2/2 页" in text, text)

        await env.ask("/todo check 1")
        text = await env.ask("/todo list")
        check("完成项仍参与分页并显示状态", "共 5 条 · 第 1/2 页" in text
              and "#1 第1条 · 已完成" in text, text)
        check("分页链接仍可用", "--page=2" in text, text)
    finally:
        await env.close()


# ==================== 5. 服务范围 ====================

async def test_subscription(env: Env) -> None:
    print("\n[5] 服务范围（napcat.subscribe）")
    user = 20002
    text = await env.ask("/todo list", user=user)
    check("空名单：普通用户被静默拒绝", text == "", repr(text))
    text = await env.ask("/todo list", user=SUPERUSER)
    check("超级用户豁免", "待办" in text, text)

    text = await env.ask(f"/todo bind add user {user}", user=user)
    check("非超管不能改名单", "服务范围" not in text, text)

    text = await env.ask(f"/todo bind add user {user}", user=SUPERUSER)
    check("超管把用户加入名单", f"用户：{user}" in text, text)
    text = await env.ask("/todo list", user=user)
    check("名单内私聊可用", "待办" in text, text)
    text = await env.ask(f"/todo bind del user {user}", user=SUPERUSER)
    check("超管把用户移出名单", f"用户：{user}" not in text, text)
    text = await env.ask("/todo list", user=user)
    check("移出后私聊再次拒绝", text == "", repr(text))

    group = 123456
    text = await env.ask("/todo list", user=user, group=group)
    check("未开通的群仍不可用", text == "", repr(text))
    text = await env.ask(f"/todo bind add group {group}", user=SUPERUSER)
    check("超管把群加入名单", str(group) in text, text)
    text = await env.ask("/todo list", user=user, group=group)
    check("名单内群聊可用", "待办" in text, text)
    text = await env.ask("/todo list", user=user, temp_group=group)
    check("群小窗按来源群放行", "待办" in text, text)
    text = await env.ask(f"/todo bind del group {group}", user=SUPERUSER)
    check("超管移出群", str(group) not in text.split("群：", 1)[-1].split("\n", 1)[0], text)
    text = await env.ask("/todo list", user=user, group=group)
    check("移出后群聊再次拒绝", text == "", repr(text))


# ==================== 6. 提醒扫描 ====================

async def test_tick(env: Env) -> None:
    print("\n[6] 定时扫描 / 发送 / 错过宽限")
    plugin = env.plugin
    moment = schedule.now()
    chat = "user:10001"

    todo = env.model().objects.create(
        chat=chat, chat_type="private", chat_id="10001",
        title="喝水", content="", remind="8h", repeat="8h",
        next_remind=schedule.to_iso(moment - timedelta(seconds=5)),
        next_repeat=schedule.to_iso(moment - timedelta(seconds=5)))
    env.api.sent.clear()
    await env.jobs().tick(plugin)
    check("到点发送私聊提醒", any(item[0] == "private" and item[1] == "10001"
                                for item in env.api.sent), str(env.api.sent))
    check("提醒文案替换了标题", "喝水" in env.api.sent[-1][2], env.api.sent[-1][2])
    check("提醒里带编号", f"#{todo.pk}" in env.api.sent[-1][2], env.api.sent[-1][2])
    todo = env.model().objects.get(pk=todo.pk)
    check("发送后推进下一次提醒", todo.next_remind != "", todo.next_remind)
    check("发送后推进下一次重复", todo.next_repeat != "", todo.next_repeat)
    check("发送后标记已提醒", todo.reminded is True)

    todo.next_remind = schedule.to_iso(moment - timedelta(hours=1))
    todo.save()
    env.api.sent.clear()
    await env.jobs().tick(plugin)
    check("超过宽限不补发", env.api.sent == [], str(env.api.sent))
    check("错过会打标记", env.model().objects.get(pk=todo.pk).missed is True)

    repeat_only = env.model().objects.create(
        chat=chat, chat_type="private", chat_id="10001",
        title="只重复", content="", remind="", repeat="8h",
        next_repeat=schedule.to_iso(moment - timedelta(seconds=5)))
    env.api.sent.clear()
    await env.jobs().tick(plugin)
    check("只有 repeat 不发提醒", env.api.sent == [], str(env.api.sent))
    check("只有 repeat 也会推进下一次",
          env.model().objects.get(pk=repeat_only.pk).next_repeat != "")

    past = schedule.now() - timedelta(days=2)
    exact = f"{past.year}Y{past.month}M{past.day}D8h"
    one_shot = env.model().objects.create(
        chat=chat, chat_type="private", chat_id="10001",
        title="一次性", content="", remind=exact, repeat="",
        next_remind=schedule.to_iso(moment - timedelta(seconds=5)))
    env.api.sent.clear()
    await env.jobs().tick(plugin)
    check("一次性提醒会发送", any(item[0] == "private" for item in env.api.sent),
          str(env.api.sent))
    check("一次性提醒发送后没有下一次",
          env.model().objects.get(pk=one_shot.pk).next_remind == "")

    quiet = env.model().objects.create(
        chat=chat, chat_type="private", chat_id="10001",
        title="无规则", content="", remind="", repeat="",
        next_remind="", next_repeat="")
    await env.jobs().tick(plugin)
    quiet = env.model().objects.get(pk=quiet.pk)
    check("两条规则都为空时什么都不排", quiet.next_remind == ""
          and quiet.next_repeat == "")

    reopen = env.model().objects.create(
        chat=chat, chat_type="private", chat_id="10001",
        title="完成后重开", content="", remind="", repeat="8h",
        done=True, done_at=schedule.to_iso(moment - timedelta(minutes=1)),
        next_remind="",
        next_repeat=schedule.to_iso(moment - timedelta(seconds=5)))
    await env.jobs().tick(plugin)
    reopen = env.model().objects.get(pk=reopen.pk)
    check("已完成重复项到点重新打开", reopen.done is False, str(reopen.done))
    check("重新打开后仍排下一次重复", reopen.next_repeat != "",
          reopen.next_repeat)

    group_todo = env.model().objects.create(
        chat="group:123456", chat_type="group", chat_id="123456",
        title="群任务", content="", remind="8h", repeat="8h",
        next_remind=schedule.to_iso(moment - timedelta(seconds=5)),
        next_repeat=schedule.to_iso(moment - timedelta(seconds=5)))
    env.api.sent.clear()
    await env.jobs().tick(plugin)
    check("群待办发到群里", any(item[0] == "group" and item[1] == "123456"
                              for item in env.api.sent), str(env.api.sent))
    check("群提醒后仍保留", env.model().objects.get(pk=group_todo.pk).done is False)


# ==================== 7. 恢复 / 持久化 ====================

async def test_recovery(env: Env) -> None:
    print("\n[7] 每次 enable 恢复断掉的定时器")
    todo = env.model().objects.filter(title="重复真").first()
    check("测试待办存在", todo is not None)
    todo.next_remind = ""
    todo.next_repeat = ""
    todo.save()
    await env.mgr.disable("todo")
    await env.mgr.enable("todo")
    todo = env.model().objects.get(pk=todo.pk)
    check("enable 后补回 next_remind", todo.next_remind != "", todo.next_remind)
    check("enable 后补回 next_repeat", todo.next_repeat != "", todo.next_repeat)
    check("enable 后扫描任务重新注册", any(
        item["id"] == "todo.tick" for item in env.timer.list_all()))

    before = env.model().objects.count()
    await env.mgr.disable("todo")
    await env.mgr.unload("todo")
    env.plugin = await env.mgr.load("todo")
    await env.mgr.enable("todo")
    check("重载后 ORM 记录还在", env.model().objects.count() == before)
    check("重载后命令可用", "待办" in await env.ask("/todo list"))


# ==================== main ====================

async def main() -> int:
    test_expressions()
    test_orm()

    env = Env()
    try:
        await env.start()
        await test_commands(env)
        await test_subscription(env)
        await test_tick(env)
        await test_recovery(env)
    finally:
        await env.close()

    await test_pagination()

    print(f"\n{'=' * 44}\n通过 {PASSED} / 失败 {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
