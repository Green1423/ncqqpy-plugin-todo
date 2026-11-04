# 日期表达式与最近时间查询

> **插件说明**：本文件是 `learn/nearest_date_expr.py` 的参考副本。插件内置假期数据只覆盖官方假期区间，`hol` **不包含普通周末**；普通周末请用 `[6,7]w`。插件用户手册见 [`TIME_EXPRESSIONS.md`](TIME_EXPRESSIONS.md)。


完整规则、形式化语义、复杂度与性能优化见
[DATE_EXPR_RULES.md](DATE_EXPR_RULES.md)。

实现文件：

- 本插件内置副本：[date_expr.py](../date_expr.py)
- 插件验收测试：[tests/test_todo.py](../tests/test_todo.py)
- 上游引擎的独立测试不在插件包内

只使用 Python 标准库，不依赖目录里的其他脚本。

## 快速使用

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from nearest_date_expr import nearest, next_time, matches

now = datetime(2026, 11, 3, 20, 36, tzinfo=ZoneInfo("Asia/Shanghai"))

nearest("5D5m", now)
# 2026-11-05 09:05:00+08:00

next_time("[8,9]h", now)
# 2026-11-04 08:00:00+08:00

nearest("{10h5m}!{10h}", now)
# None  （空集）

matches("hol", datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
# True
```

`nearest()` 默认取“绝对最近”的槽位，可能落在现在之前或之后。要取下一次，使用
`next_time()` 或 `direction="next"`；要取上一次，使用 `previous_time()` 或
`direction="previous"`。

## 语义

### 单元与默认补全

字段从大到小为 `Y M D h m`，`w` 是星期（1=周一，7=周日，兼容 0=周日）。

- 比最大已写单位更大的单位保持“任意”。
- 比最大已写单位更小的未写单位，用默认值补全：`2008Y1M1D9h0m`。

所以：

| 表达式 | 含义 |
|---|---|
| `5D5m` | 任意年、任意月、5 日 09:05 |
| `5D` | 任意年、任意月、5 日 09:00 |
| `5m` | 任意年/月/日/小时，每小时的第 5 分钟 |
| `2026Y` | 2026-01-01 09:00 |
| `2026Y5M6D10h7m` | 2026-05-06 10:07 |
| `1w` | 每个周一 09:00 |
| `eY` | 每年 12 月 31 日 09:00，D 精度 |
| `eM` | 每月最后一天 09:00，D 精度 |
| `now` | 查询基准时间，分钟粒度 |
| `+1D` | 从当前时间起，每 1 天一个周期点 |
| `1w & {10h}` | 每个周一 10:00 |

包的单位是“槽位粒度”。例如 `{10h}` 覆盖整个 10 点小时，但它的代表时间是
10:00；`{10h5m}` 覆盖 10:05 这一分钟。这个区别正是下面差集为空的原因。

```python
nearest("{10h5m}!{10h}")   # None：10h 的槽位包含 10h5m
```

### 包、区间、步长

```text
{1,3,5}D           # 1、3、5 日
[8,9]h             # 8 点、9 点
[1,10]2D           # 1、3、5、7、9 日
[8,9](30m)h        # 08:00、08:30、09:00
[1,2](12h)D        # 1 日 00:00、12:00，2 日 00:00
{1,2,3,4}2D        # 集合按序号取第 1、3 个元素
```

包的单位可省略，默认取集合/区间内最细的单位。步长可写成 `2D`、`(2D)`、
`D2`、`(2D)D` 等形式。若步长单位比包单位更细且属于 `D/h/m/w`，会提升到更细
单位计算；其他跨单位步长要求能整除换算。

### 强制类型转换

`(表达式)单位` 会把结果转换到目标单位：

```python
({10h5m})h   # 10:00
(hol)h       # 节假日按小时展开：00:00 ... 23:00
```

原子包转换为更粗单位时做截断；转换为更细的 `D/h/m` 时做投影。复合表达式的
转换目前支持 `D/h/m`。

### 运算与优先级

优先级从高到低：

```text
()  >  !  >  |  >  &
```

注意 `&` 的优先级最低，这与 C 语言相反。例如：

```text
a | b & c   ==   (a | b) & c
a & b | c   ==   a & (b | c)
```

- `&`：交集
- `|`：并集
- `!`：差集（左边有、右边没有）
- `!` 右侧包的更细单位被包含在右侧包中，所以 `{10h5m}!{10h}` 为空。
- 差集保留粗槽位中未被删掉的部分，例如 `{10h}!{10h0m}` 会得到 10:01–10:59，
  最近时间可能是 10:01。

### 内置节假日集合

| 名称 | 含义 |
|---|---|
| `fes` | 法定节假日当天 |
| `hol` | 非调休的放假天（包括节假日当天和自然周末） |
| `lil` | 调休得到的放假天 |
| `wil` | 调休上班天 |

`fes ⊆ hol`，`hol` 与 `lil` 合起来是整个放假区间。

内置数据覆盖：

- 2025 年、2026 年国务院办公厅公布的完整安排；
- 2027 年元旦 1 月 1 日至 3 日。

其他年份可通过 `HolidayCalendar.add_period()` / `add_day()` 扩展：

```python
from datetime import date
from nearest_date_expr import HolidayCalendar, nearest

cal = HolidayCalendar()          # 带内置数据
cal.add_period(
    date(2030, 1, 1),
    date(2030, 1, 3),
    fes={date(2030, 1, 1)},
    lil={date(2030, 1, 2)},
    wil={date(2030, 1, 5)},
)
nearest("hol", calendar=cal)
```

## API

```python
nearest(expression, now=None, *, direction="nearest",
        horizon_years=8, calendar=None, tz=None) -> datetime | None
next_time(expression, now=None, **kwargs) -> datetime | None
previous_time(expression, now=None, **kwargs) -> datetime | None
matches(expression, when, *, calendar=None, tz=None) -> bool
parse(expression, *, calendar=None) -> Expr
```

- 默认时区：`Asia/Shanghai`。
- 返回 `datetime` 带时区；空集或无匹配返回 `None`。
- `horizon_years` 是默认搜索窗口；表达式里显式写出的年份会自动扩展窗口。
- 表达式字符串可重复传给 `matches`/`nearest`，也可先 `parse()` 后复用。

## 测试

```bash
python3 -m unittest -v test_nearest_date_expr
```

当前 85 个测试覆盖：默认补全、星期、集合/区间/步长、类型转换、运算优先级、
差集语义、四个节假日集合、时区、显式远年份和错误输入。


## 节假日数据来源

- 2025 年安排：[新华社受权发布](http://www.news.cn/politics/20241112/46dcf0de740844dd824ff54b5be5af2e/c.html)
- 2026 年安排：[国务院办公厅通知](https://www.shanghaiinvest.com/cn/viewfile.php?id=21364)
