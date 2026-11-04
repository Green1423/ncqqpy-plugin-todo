# 命令手册

本文是 `/todo` 的完整命令参考。时间表达式怎么写、下一次怎么算，见
[时间表达式用户手册](TIME_EXPRESSIONS.md)。

---

## 0. 权限与上下文

| 命令 | 权限 / 作用域 |
|------|----------------|
| `add` / `list` / `set` / `check` / `del` | 公开；仍受本插件服务范围白名单约束 |
| `bind` | **仅私聊 + 仅超级用户** |

服务范围口径与 `ai_chat` / `byrdocs` / `oj_bank` 一致：

- 群聊 / 群小窗：来源群在 `binding.groups` 里才可用；
- 直接私聊：QQ 号在 `binding.users` 里才可用；
- 空名单 = 谁都不用；
- 超级用户始终可用。

待办的归属：

- 群聊里添加的待办属于这个群，群里所有有权限的人都能看到 / 完成；
- 私聊（含群小窗）添加的待办只属于这个 QQ 号；
- 提醒永远发回添加它的那个会话。群小窗的待办会按临时会话发回。

---

## 1. 六个信息与参数顺序

一条待办有：

```text
id / title / content / remind / repeat / deadline
```

命令里的位置顺序固定为：

```text
/todo add  <title> [content] [remind] [repeat] [deadline]
/todo set  <id> [title] [content] [remind] [repeat] [deadline]
```

- `<...>` 必填，`[...]` 可选，尖括号 / 方括号本身不用输入；
- 含 **空格** 的表达式必须加引号，或改用 `--remind=` / `--repeat=` / `--deadline=`；
- `--键=值` 可以放在任意位置；位置参数仍按上面的顺序填。

推荐写法：

```text
/todo add 吃药 --remind="8h" --repeat="8h"
/todo add 周会 --content="周会要开始了" --remind="1w & {17h30m}"
```

---

## 2. `/todo add`：添加

```text
/todo add <title> [content] [remind] [repeat] [deadline]
```

| 参数 | 说明 |
|------|------|
| `title` | 待办标题，**必填**，最多 80 字 |
| `content` | 提醒文案，最多 500 字；省略 = 配置 `default_remind`；`-` = 恢复默认 |
| `remind` | 提醒时间规则；`false` / 省略 = 不提醒 |
| `repeat` | 重复时间规则；`false` / 省略 = 不重复；`true` = 同 `remind` |
| `deadline` | 截止时间（`YYYY-MM-DD HH:MM`；只写日期按 23:59）；`false` / 省略 = 无截止；与 `repeat` 互斥 |

### 2.1 最小用法

```text
/todo add 买牛奶
```

没有 `remind` / `repeat`，只是一条静态清单；`list` 里显示“未完成”。

### 2.2 一次性提醒

```text
/todo add 交房租 --remind="2026Y11M10D8h"
```

- 在 2026-11-10 08:00 提醒一次；
- 没有 `repeat`，完成即永久结束。

### 2.3 周期写法 `+1D`

`+1D` 来自 learn 引擎的周期集合，表示“每隔 1 天”：

```text
/todo add 吃药 --remind="+1D" --repeat="+1D"
```

- 引擎的周期序列包含当前分钟；
- 插件的首次排程取严格未来的一次，所以第一次提醒在 1 天后，不会在添加时立刻发送；
- 之后每天同一分钟提醒 / 重开；
- `+1h`、`+1M`、`+1D2h` 同理。

其他 learn 语法（`now`、端点 `s/e`、偏移 `+/-`）见
[时间表达式用户手册](TIME_EXPRESSIONS.md#9-now周期端点偏移)。

### 2.4 deadline：截止时间

```text
/todo add 交房租 --remind="2026Y11M10D8h" --deadline="2026-11-10 18:00"
/todo add 体检 --deadline="2026-11-12"
```

- `deadline` 是**具体时间点**（`DateTimeField`），不是时间表达式；
- 写法：`YYYY-MM-DD HH:MM`，也接受 ISO 8601；只写日期按当天 23:59；
- 它不发送提醒，只在每日日报里用于判断“截止时间 / 已逾期”；
- **与 `repeat` 互斥**：有截止时间的待办不能同时设重复规则；要切换请先清掉一个；
- 修改：`/todo set <id> --deadline="2026-11-15 18:00"`；清除：`--deadline=false`。

### 2.5 周期性提醒 + 周期性重开

```text
/todo add 吃药 --remind="8h" --repeat="8h"
```

- 每天 08:00 提醒；
- 完成后，下一次 08:00 重新打开；
- 提醒和重开是两条独立规则，可以不同：

```text
/todo add 周会 --remind="1w & {17h30m}" --repeat="1w & {18h}"
```

每周一 17:30 提醒，完成后每周一 18:00 重开（时间点不同，仅作示例）。

### 2.6 只重复、不提醒

```text
/todo add 买菜 --repeat="1w"
```

每周一 09:00 重新打开，不发消息；完成后到下一个周一再打开。

### 2.7 位置参数与引号

```text
/todo add 吃药 "记得吃药" "8h" "8h"
```

等价于：

```text
/todo add 吃药 --content="记得吃药" --remind="8h" --repeat="8h"
```

只给 `remind`、跳过 `content` 时，用键值参数最清楚：

```text
/todo add 吃药 --remind="8h" --repeat="8h"
```

---

## 3. `/todo list`：分页查看

```text
/todo list [page]
```

- 默认第 1 页；`page` 从 1 开始；
- 每页条数由 `plugins.todo.list_page_size` 决定（默认 10，范围 1–50）；
- **列表每行只有：编号、标题、是否完成**；
- 默认包含已完成项；完成项会保留到重开或被删除；
- 页码超出范围会夹到最后一页；
- 页脚会给出上一页 / 下一页命令。

示例：

```text
/todo list
/todo list 2
/todo list --page=3
```

输出形如：

```text
📋 待办（共 12 条 · 第 1/2 页）
  #1 吃药 · 未完成
  #2 周会 · 已完成
  ...
💡 /todo check <编号> 完成 · /todo del <编号> 删除
💡 下一页 /todo list --page=2
```

`list` 不显示提醒规则、下次提醒、重复规则、截止时间；这些在 `add` / `set` 的回执和每日日报里能看到。

---

## 4. `/todo set`：修改

```text
/todo set <id> [title] [content] [remind] [repeat] [deadline]
```

所有字段都是“省略 = 不改”。只改给出的字段，未给出的保持原值。

| 参数 | 说明 |
|------|------|
| `title` | 新标题；省略 = 不改 |
| `content` | 新提醒文案；省略 = 不改；`-` = 恢复默认 |
| `remind` | 新提醒规则；省略 = 不改；`false` / `-` = 不提醒 |
| `repeat` | 新重复规则；省略 = 不改；`false` / `-` = 不重复；`true` = 同 `remind` |
| `deadline` | 新截止时间；省略 = 不改；`false` / `-` = 清除；与 `repeat` 互斥 |

示例：

```text
/todo set 1 --remind="9h" --repeat="9h"
/todo set 1 --remind=false
/todo set 1 --repeat=false
/todo set 1 新标题
/todo set 1 --content="新的提醒文案"
/todo set 1 --deadline="2026-11-15 18:00"
/todo set 1 --deadline=false
```

注意：

- 已完成的待办不能 `set`；等它因 `repeat` 重开，或先 `/todo del` 再重新添加；
- `repeat=true` 需要当前（或同一条命令里新设置的）`remind` 非空；
- 改 `remind` 会重新计算下一次提醒；改 `repeat` 会重新计算下一次重开；
- 只改标题 / 文案不会重置已有的下次时间；
- 截止时间只影响日报里的逾期判断，不改变提醒 / 重复时间；
- 同一个待办不能同时有 `deadline` 和 `repeat`。

---

## 5. `/todo check`：完成此项

```text
/todo check <id>
```

语义是**完成当前这一项**：

- `repeat` 为空 / `false`：标记完成，永久结束；下一次提醒 / 重复都清掉；
- `repeat` 有表达式：标记完成，但保留/重排 `next_repeat`；到下一次重复时间，
  扫描任务会把 `done` 重新打开，并按 `remind` 重新排提醒；
- 已完成项仍在 `list` 里显示“已完成”，直到重开或被删除；
- 对已完成的项再次 `check` 会提示“已经完成了”。

示例：

```text
/todo check 1
# ✅ 已完成待办 #1：吃药
# 下次重复：2026-11-05 08:00（周三）（到时会重新打开）
```

没有 `repeat` 时不会有“下次重复”这一行。

---

## 6. `/todo del`：删除

```text
/todo del <id>
```

- 未完成 / 已完成的都能删；
- 删除后编号不会复用；
- 删除是永久的（数据库记录删除）。

---

## 7. `/todo bind`：服务范围

```text
/todo bind [list|add|del] [group|user] [群号/QQ号]
```

| 动作 | 例子 | 作用 |
|------|------|------|
| `list` | `/todo bind list` | 查看当前群 / 用户白名单 |
| `add group` | `/todo bind add group 100200300` | 允许这个群（含该群小窗） |
| `add user` | `/todo bind add user 10001` | 允许这个人的私聊 |
| `del group` | `/todo bind del group 100200300` | 关闭这个群 |
| `del user` | `/todo bind del user 10001` | 关闭这个人的私聊 |

- 仅私聊 + 仅超级用户；
- `group` / `群`、`user` / `人` 都可以；
- 群号 / QQ 号必须是正整数；
- 空名单 = 除超级用户外谁都不用；超级用户始终豁免。

---

## 8. 每日日报

默认每天 `22:00` 按会话发送一条日报：

- 今天完成数、当前未完成数、已逾期数；
- 未完成项的编号、标题、截止时间（最多 10 条）；
- 只有今天有完成记录或还有未完成待办的会话才发送。

配置：

```json
"summary_enabled": true,
"summary_time": "22:00"
```

改完 `summary_time` 后重载插件生效；时间按本机时区解释。截止时间用
`deadline` 字段设置，参见 2.4。

## 9. 常见问题

### Q1. 为什么 `/todo check` 后还显示“已完成”？

这是预期行为。`list` 默认包含已完成项；如果该待办有 `repeat` 规则，到下一次
重复时间会自动变回“未完成”。不想要就 `/todo del`。

### Q2. 为什么完成后还在提醒？

检查 `remind` 是否写成了周期表达式（如 `8h`）。`check` 会清掉下一次提醒；
如果之后又收到，说明待办已被 `repeat` 重新打开并重新排了提醒。

### Q3. 怎么让提醒只发一次？

把 `remind` 写成只匹配一次的具体时刻，例如：

```text
/todo add 交房租 --remind="2026Y11M10D8h"
```

没有 `repeat`，提醒一次后完成即结束。

### Q4. 怎么让待办每天重开，但只在工作日提醒？

`remind` 和 `repeat` 可以不同：

```text
/todo add 打卡 --remind="[1,5]w & {8h}" --repeat="8h"
```

工作日 08:00 提醒；每天 08:00 重开（示例；按需要调整）。

### Q5. 为什么 `repeat=false` 还在每天提醒？

`repeat` 只控制“完成后是否重新打开”，不控制 `remind` 自己的周期。
`remind="8h"` 是每天 8 点提醒；想只提醒一次，把 `remind` 改成具体时刻。

### Q6. 怎么改日报时间？

改 `config.json → plugins.todo.summary_time`（`HH:MM`），然后重载插件。
`summary_enabled=false` 可以关闭。日报按本机时区，不是表达式时区。

### Q7. 截止时间到了会自动提醒吗？

不会。`deadline` 只用于日报里的“截止 / 已逾期”判断；提醒仍然由 `remind`
控制。需要提前提醒请另写 `remind`。另外 `deadline` 与 `repeat` 互斥，
不能同时设置。

### Q8. 分页每页太少 / 太多？

改 `config.json → plugins.todo.list_page_size`（1–50），然后重载插件。

### Q9. 群里没有反应？

- 群不在 `binding.groups` 里；
- 群里需要先 @机器人 再发命令；
- `bind` 只能私聊超管操作。

### Q10. 群小窗能用吗？

来源群在 `binding.groups` 里即可；待办按用户私聊归属，提醒按临时会话发回。

---

## 10. 相关文档

- [时间表达式用户手册](TIME_EXPRESSIONS.md)
- [表达式引擎规则](DATE_EXPR_RULES.md)
- [最近时间语义](NEAREST_DATE_EXPR.md)
- [插件 README](../README.md)
