# todo 插件文档

建议从插件首页 [`../README.md`](../README.md) 开始，然后按需阅读：

| 文档 | 内容 |
|------|------|
| [命令手册](COMMANDS.md) | `add` / `list` / `set` / `check` / `del` / `bind` 的完整用法、分页、完成与重开、常见问题 |
| [时间表达式用户手册](TIME_EXPRESSIONS.md) | `remind` / `repeat` 能写什么、`+1D` 间隔、集合与范围、假期集合、常见配方与坑 |
| [表达式引擎规则](DATE_EXPR_RULES.md) | 引擎的形式化规则（原样参考文档） |
| [最近时间语义](NEAREST_DATE_EXPR.md) | “下一次”如何选择、槽位与代表时间（原样参考文档） |

实现入口：

- [`../plugin.py`](../plugin.py)：生命周期、配置、服务范围、命令装配；
- [`../commands/todo.py`](../commands/todo.py)：命令逻辑；
- [`../jobs/reminder.py`](../jobs/reminder.py)：扫描、发送、完成后重开、恢复定时规则；
- [`../store.py`](../store.py)：`napcat.store` ORM 模型与旧 JSON 迁移；
- [`../schedule.py`](../schedule.py)：表达式封装与 `+数字单位` 间隔扩展；
- [`../date_expr.py`](../date_expr.py)：内置的日期表达式引擎（原样副本）。
