# AI 工具

todo 把常用操作注册成 **AI 工具**（`register_tool`）。工具由模型调用，结果
**回给模型**，由模型组织答复；不是命令映射。

工具都作用于**当前聊天**：群聊操作群待办，私聊操作用户自己的待办，不能跨会话
读写。服务范围（`binding.groups/users`）仍在工具内部校验，即使模型看到了工具，
没开通的聊天也会拿到拒绝说明。

## 工具清单

| 工具 | 暴露名 | 作用 |
|------|--------|------|
| `todo.add` | `todo_add` | 添加待办 |
| `todo.list` | `todo_list` | 查看当前会话待办（含编号、状态、截止、规则） |
| `todo.show` | `todo_show` | 查看单条待办的完整信息 |
| `todo.set` | `todo_set` | 修改待办 |
| `todo.check` | `todo_check` | 完成此项 |
| `todo.del` | `todo_del` | 删除待办 |

## 参数

### `todo_add`

| 参数 | 必填 | 说明 |
|------|------|------|
| `title` | 是 | 标题 |
| `content` | 否 | 提醒文案；省略用配置默认 |
| `remind` | 否 | 提醒表达式，如 `8h`、`1w & {18h}`、`+1D` |
| `repeat` | 否 | 重复表达式；与 `deadline` 互斥 |
| `deadline` | 否 | 具体时间，如 `2026-11-10 18:00`；与 `repeat` 互斥 |
| `visible` | 否 | 是否显示在列表/日报；`false` 隐藏（仍提醒），默认 true |

### 时间表达式

`todo_add` / `todo_set` 的**工具提示**里带了表达式速查，模型可以直接照写
`remind` / `repeat`，不需要先查文档。提示覆盖：

- 每天 / 每周 / 每月 / 每周几：`8h`、`1w`、`1D`、`[1,5]w & {8h}`
- 最后一天：`eM`、`eM & {20h}`；直接写 `eM`，**不要写 `{eM}`**
- 周期：`+1D`、`+6h`、`+1D & {7h}`
- 一次性：`2026Y12M31D23h59m`
- 集合 / 范围 / 运算：`{...}` `[...]` `&` `|` `!`
- 端点：`(hol|lil)e`（每段官方假期最后一天）、`(hol|lil)s`（第一天）；端点只能写在
  括号后。纯时间集合如 `{10h,15h}` 日期无限，`e` 无结果，需要限定日期：
  `(2026Y11M10D & {10h,15h})e`
- 假期：`hol` / `hol|lil` / `wil`；普通周末用 `[6,7]w`

`deadline` 不是表达式，而是具体时间（`YYYY-MM-DD HH:MM`，只写日期按当天结束），
并且与 `repeat` 互斥。

完整规则见 [TIME_EXPRESSIONS.md](TIME_EXPRESSIONS.md)。

### `todo_list`

| 参数 | 必填 | 说明 |
|------|------|------|
| `page` | 否 | 页码，默认 1；每页条数由 `list_page_size` 控制 |

工具版列表比命令版多返回 `截止=`、`提醒=`、`重复=`；隐藏项还会标
`可见=否`，排序同样是可见项在前。

### `todo_show`

| 参数 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 待办编号（先通过 `todo_list` 获取） |

返回标题、状态、提醒文案、提醒/重复/截止规则、下次时间和创建/完成时间。

### `todo_set`

`id` 必填；其余字段只传要改的：

`title`、`content`、`remind`、`repeat`、`deadline`、`visible`。

`deadline=false` 清除截止；`repeat=false` 清除重复。二者互斥。
`visible=false` 隐藏此条（不进入日报，列表排在可见项之后），仍会提醒。

### `todo_check` / `todo_del`

`id` 必填。编号先通过 `todo_list` 获取。

## 示例

模型可以这样调用（示意）：

```json
{"name": "todo_add", "arguments": {
  "title": "交房租",
  "remind": "2026Y11M10D8h",
  "deadline": "2026-11-10 18:00"
}}
```

```json
{"name": "todo_list", "arguments": {"page": 1}}
```

```json
{"name": "todo_show", "arguments": {"id": 3}}
```

```json
{"name": "todo_check", "arguments": {"id": 3}}
```

## 与命令的差异

- 工具参数是 JSON，不经过命令行的引号 / 转义；
- 工具结果给模型，命令结果显示给用户；
- 业务规则完全复用命令层：服务范围、`deadline` / `repeat` 互斥、分页、
  `check` 完成与重复重开都一致；
- `todo.list` 是 AI 专用的详细列表，命令 `/todo list` 仍只显示编号、标题、
  是否完成三列。

## 注册与回收

- 插件加载时注册；
- 插件禁用 / 卸载 / 热重载时由框架按插件名整批撤销；
- 工具声明 `permission=""` / `scope="all"`，细粒度服务范围在 handler 里用
  `plugin.check_access(ctx 对应的事件)` 判。
