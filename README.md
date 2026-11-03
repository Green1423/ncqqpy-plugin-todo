# todo —— 待办与周期提醒

待办与周期提醒：自然语言时间表达式、重复规则、服务范围白名单、重启不丢提醒。

这是 [ncqqpy](https://github.com/Green1423/ncqqpy) 的插件，**独立仓库**：
框架按 `plugin_dir`（默认 `./plugins`）扫描子目录加载插件，所以把它 clone 到
框架的 `plugins/` 下即可。

## 安装

```bash
cd /path/to/ncqqpy
git clone https://github.com/Green1423/ncqqpy-plugin-todo plugins/todo
# 重新加载（QQ 内，超级用户 · 仅私聊）
#   /reload todo
```

要求：框架 [ncqqpy](https://github.com/Green1423/ncqqpy)（`napcat` 包需可导入）。

## 配置

插件被**构造**时（`on_load()` 之前）会把自己的**全部**配置项写进
`config.json` 的 `plugins.todo` 段（只补缺键、不覆盖已有值、幂等），所以打开
文件就能看到所有开关和默认值。模板如下：

```json
{
  "enabled": true,
  "tick_seconds": 20,
  "misfire_grace_seconds": 600,
  "default_remind": "⏰ 待办提醒 #{id}：{title}\n时间：{time}",
  "max_todos_per_chat": 50,
  "list_page_size": 10,
  "done_keep_days": 30,
  "binding": {
    "groups": [],
    "users": []
  }
}
```

环境变量（优先级高于文件）：

| 变量 | 说明 |
|------|------|
| `（无）` | 全部配置在 `config.json → plugins.todo` |


## 测试

```bash
# 独立仓库里跑：框架需可导入（PYTHONPATH 指向框架仓库，或 pip install -e）
PYTHONPATH=/path/to/ncqqpy python -B tests/test_todo.py
```

套件：`tests/test_todo.py`。测试会在 `tests/.test-tmp/` 下建临时目录，不碰真实数据。

## 注意

- 完整手册在仓库 `docs/README.md`（命令 / 时间表达式 / 引擎规则）。
- `data/todo.db` 是运行期数据库，**不要**提交。

## License

MIT，见 [LICENSE](LICENSE)。
