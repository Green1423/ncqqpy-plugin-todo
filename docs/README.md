# todo 插件文档

建议从插件首页 [`../README.md`](../README.md) 开始，然后按需阅读：

| 文档 | 内容 |
|------|------|
| [命令手册](COMMANDS.md) | `add` / `list` / `set` / `check` / `del` / `bind` 的完整用法、deadline（与 repeat 互斥）、日报、分页、常见问题 |
| [AI 工具](TOOLS.md) | 给模型调用的 `todo_add` / `todo_list` / `todo_set` / `todo_check` / `todo_del`、参数与回收 |
| [时间表达式用户手册](TIME_EXPRESSIONS.md) | `remind` / `repeat` 能写什么、`now` / `+1D` / 端点 / 偏移、集合与范围、假期集合、常见配方与坑；`deadline` 是具体时间点 |
| [表达式引擎规则](DATE_EXPR_RULES.md) | 引擎的形式化规则（原样参考文档） |
| [最近时间语义](NEAREST_DATE_EXPR.md) | “下一次”如何选择、槽位与代表时间（原样参考文档） |

实现入口：

- [`../plugin.py`](../plugin.py)：生命周期、配置、服务范围、命令装配；
- [`../commands/todo.py`](../commands/todo.py)：命令逻辑；
- [`../jobs/reminder.py`](../jobs/reminder.py)：扫描、发送、完成后重开、恢复定时规则；
- [`../jobs/summary.py`](../jobs/summary.py)：每日日报聚合与发送；
- [`../tools.py`](../tools.py)：AI 工具注册与命令层复用；
- [`../store.py`](../store.py)：`napcat.store` ORM 模型与旧 JSON 迁移；
- [`../schedule.py`](../schedule.py)：表达式封装与首次排程策略；
- [`../date_expr.py`](../date_expr.py)：内置的日期表达式引擎（原样副本）。
