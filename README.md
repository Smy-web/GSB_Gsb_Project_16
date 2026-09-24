# 月度结算报表（reportbuild）

财务每个月要一份「按 region × 月份」的结算报表。仓库里有两套实现：

| 目录 | 角色 | 能不能改 |
|---|---|---|
| `legacy/report_legacy.py` | **行为基准**。单遍流式写法，产出的两个文件就是「正确输出」的定义。 | 🔴 **禁止修改** |
| `reportbuild/` | **当前生产实现**。业务逻辑与 legacy 完全相同，但慢、吃内存、结构乱。 | ✅ 本次任务就是重构它 |

硬约束一句话：**重构后，`python3 -m reportbuild` 与 `python3 legacy/report_legacy.py`
对任意输入产出的 `monthly_region.csv` 与 `summary.json` 必须逐字节相同**
（`diff -r` 输出为空）。

## 环境

* WSL2 Ubuntu 26.04，Python 3.14.4，pytest 9.0.2 已预装
* **只用标准库 + pytest**，禁止联网安装任何包

## 目录结构

```
.
├── README.md                  ← 本文件
├── legacy/
│   └── report_legacy.py       ← 行为基准（禁改）。**口径全部写在它的文件头 docstring 里**
├── reportbuild/
│   ├── __init__.py
│   ├── __main__.py            ← python3 -m reportbuild 的入口
│   └── pipeline.py            ← 单遍流式流水线：加载/归一/分组/退款归属/落盘 各一个函数
├── tools/
│   └── gen_data.py            ← 固定种子的数据生成器（含 7 种 profile）
├── data/
│   ├── README.md              ← 样例夹具逐行对照：每一行在考哪条怪癖
│   ├── orders.csv             ← 18 行手工样例
│   └── refunds.csv            ← 12 行手工样例
└── tests/
    ├── conftest.py            ← 差分/性能测试公共脚手架（含 20 万大夹具 fixture）
    ├── test_smoke.py          ← 冒烟测试
    ├── test_diff_vs_legacy.py ← 差分测试：端到端逐字节 + 纯函数级
    └── test_perf.py           ← 性能（< 4s）与内存（< 150MB）验收
```

## 怎么跑

```bash
# 1. 用自带的小样例各跑一遍
python3 legacy/report_legacy.py --orders data/orders.csv --refunds data/refunds.csv --out /tmp/legacy
python3 -m reportbuild          --orders data/orders.csv --refunds data/refunds.csv --out /tmp/new

# 2. 逐字节比对（现在应该是空的）
diff -r /tmp/legacy /tmp/new && echo "逐字节一致"

# 3. 不带参数也行：默认读 data/，默认写 out/
python3 -m reportbuild

# 4. 生成 20 万订单 + 2 万退款的大夹具（固定种子，可复现）
python3 tools/gen_data.py --out /tmp/big --orders 200000 --refunds 20000 --seed 20260901

# 5. 极端场景（差分测试要求覆盖的那些）
python3 tools/gen_data.py --out /tmp/d1 --orders 400 --refunds 60 --seed 1 --profile dirty
python3 tools/gen_data.py --out /tmp/d2 --orders 300 --refunds 40 --seed 2 --profile orphan
python3 tools/gen_data.py --out /tmp/d3 --orders 200 --refunds 30 --seed 3 --profile single
python3 tools/gen_data.py --out /tmp/d4 --orders 200 --refunds 0  --seed 4 --profile norefunds
python3 tools/gen_data.py --out /tmp/d5 --orders 300 --refunds 50 --seed 5 --profile dup
python3 tools/gen_data.py --out /tmp/d6 --orders 200 --refunds 30 --seed 6 --profile tz

# 6. 跑测试
python3 -m pytest -q
```

`--profile` 一共 7 种：`normal` / `dirty`（全脏数据）/ `orphan`（全退款孤儿）/
`single`（单 region）/ `norefunds`（空退款表）/ `dup`（大量重复 order_id）/
`tz`（ts 全带 `+08:00` 且压在月初零点）。

## 口径去哪查

**只有一个地方是权威：`legacy/report_legacy.py` 文件头的 docstring。**
里面写清了：

* 输入两张表的列名与读取规则；
* 输出 `monthly_region.csv` 的列、行序、金额格式；
* 输出 `summary.json` 的 9 个字段各是什么；
* 8 条行为口径（month 怎么算、坏行怎么处理、退款怎么归属、求和顺序为什么也是口径）；
* **8 条保留的历史怪癖（Q1~Q8）**：故意不修的行为，重构后必须依然存在。

`data/README.md` 则逐行说明样例夹具里每一行在踩哪条怪癖。

## 重构前后对照（复杂度与实测）

### 复杂度

| 维度 | 重构前 | 重构后 |
|---|---|---|
| 订单文件读取次数 | R+1 遍（R = region 数），每遍重新解析 CSV 与 ts | **1 遍**，流式逐行处理 |
| 退款归属 | 每笔退款 `list.index()` 线性扫全表：O(M×N) | 字典查 `order_id → (region, month)`：**O(M+N)** |
| 冗余计算 | R×N 的双重循环「校验」，结果从未被使用 | 已删除 |
| 内存驻留 | 订单数据同时存 3 份（`raw_rows` / `records` / `per_region`） | 只存聚合结果与首次归属表，原始行不驻留 |
| 结构 | 单个 god-function + 模块级可变状态 + 死代码 | 5 个阶段函数（加载/归一/分组/退款归属/落盘），无可变全局状态 |

### 实测（20 万订单 + 2 万退款，seed=20260901）

复现命令（WSL2 Ubuntu 26.04，Python 3.14.4，本机实测）：

```bash
python3 tools/gen_data.py --out /tmp/big --orders 200000 --refunds 20000 --seed 20260901
time python3 legacy/report_legacy.py --orders /tmp/big/orders.csv --refunds /tmp/big/refunds.csv --out /tmp/out_legacy
time python3 -m reportbuild          --orders /tmp/big/orders.csv --refunds /tmp/big/refunds.csv --out /tmp/out_new
diff -r /tmp/out_legacy /tmp/out_new && echo "逐字节一致"
python3 -m pytest tests/test_perf.py -q   # 性能 < 4s 与内存 < 150MB 两条验收
```

| 实现 | 端到端耗时 | tracemalloc 峰值内存 |
|---|---|---|
| 重构前 `reportbuild`（git HEAD 版） | 73.4 s | 334.5 MB |
| `legacy/report_legacy.py`（基准，不要求达标） | 2.25 s | — |
| **重构后 `reportbuild`** | **1.67 s** | **49.4 MB** |

加速约 **44 倍**，峰值内存降到约 **1/7**；与 legacy 的产物 `diff -r` 为空。

## 保留的历史怪癖（故意不修）

以下行为与 legacy 逐字对齐，**是口径的一部分，不是 bug**。权威描述见
`legacy/report_legacy.py` 文件头 docstring 的 Q1~Q8。

* **Q1 float 舍入痕迹**：`2.675` 落盘是 `"2.67"`（二进制浮点 + `round()`
  的 round-half-even），不是财务口径的 `"2.68"`。**不修的原因**：下游所有
  历史报表都按这个口径对账，换成 Decimal「修正」会让历史数据全部对不上。
  **将来要修的影响面**：`fmt_money` 一处改动，但所有历史报表需重算，
  且 `gross_total` / `net_total` 的差分基准全部失效。
* **Q2 负退款按 0**：`refunds.amount = -50` 记 `0.00`，不冲减也不取绝对值。
  **不修的原因**：早期用负数表示「撤销退款」，语义后来改了但存量数据没清洗，
  改成冲减会让历史月份的 `refund_amount` 变小。**影响面**：
  `attribute_refunds` 里的裁零逻辑 + 全部含负退款的存量报表。
* **Q3 重复 order_id 用首次归属**：同一 `order_id` 跨月出现两次时，它的
  全部退款都算在**第一个月**头上。**不修的原因**：这是退款归属的唯一
  确定规则，改成「按退款时间归属」需要退款表带时间戳，而退款表没有。
  **影响面**：`aggregate_orders` 的 `first_key` 登记逻辑 + 所有跨月重复
  订单的月份间退款分布。
* **Q4 ts 坏行跳行、amount 坏值不跳行**：两套标准并存，`skipped_rows`
  只统计前者。**不修的原因**：`skipped_rows` 是下游监控在用的指标，
  统一标准会改变它的数值含义。**影响面**：`aggregate_orders` 的坏行
  分支 + 依赖 `skipped_rows` 的外部监控阈值。
* **Q5 空 region 单独成行**：空串 region 不并入 `UNKNOWN`、不丢弃，
  字典序里排在所有非空 region 之前。**不修的原因**：CSV 行序是产物
  契约的一部分，合并或丢弃都会改变行数与行序。**影响面**：分组键的
  归一逻辑 + 下游按行序 diff 报表的核对流程。
* **Q6 不做时区换算**：`2026-09-01T00:30:00+08:00` 按字面归 `2026-09`，
  即使换算成 UTC 还在 8 月。**不修的原因**：历史报表全部按字面月份归档，
  改口径会让跨月边界的订单集体换月。**影响面**：`parse_month` 一处，
  但所有带偏移量的 ts 的归属月份都会变。
* **Q7 汇总与明细可能差一分**：`net_total` 由**未舍入**的 float 总和
  相减得到，与 CSV 各行 `net_amount` 直接相加可能差 0.01。**不修的原因**：
  改成「明细相加」会改变 `summary.json` 的字节，破坏逐字节兼容。
  **影响面**：`write_outputs` 的汇总逻辑 + 下游对 `net_total` 的核对公式。
* **Q8 只有退款侧裁负数**：`orders.amount = -20` 原样进 gross，只有
  `refunds.amount` 的负数按 0 计。**不修的原因**：两侧标准不一致是
  历史事实，统一任何一侧都会改变 gross 或 refund 的历史数值。
  **影响面**：`aggregate_orders` / `attribute_refunds` 的裁零分支 +
  全部含负金额的存量报表。

## 你要交付什么

1. **硬约束**：`python3 -m reportbuild` 与 legacy 的两个产物**逐字节相同**。
   包括 float `round()` 的舍入痕迹（**不许换 Decimal「修正」**）、负退款按 0、
   重复 `order_id` 的退款归属用首次出现的 `(region, month)`、ts 坏行跳过并计入
   `skipped_rows`、空字段按 0、分组排序与 CSV 行序、**以及求和顺序**。
2. **性能**：20 万订单 + 2 万退款，`reportbuild` 端到端 **< 4 秒**（legacy 不要求达标）；
   `tracemalloc` 峰值内存 **< 150 MB**。两条都写成 pytest，大数据用
   `tools/gen_data.py` 固定种子生成。
3. **差分测试**：`tests/test_diff_vs_legacy.py` 用 subprocess 分别跑新旧实现，
   对**至少 5 组 `(seed, 规模)`** 组合（含全脏数据、全退款孤儿、单 region、空退款表）
   断言两目录产物 diff 为空；小样本另写**纯函数级**差分用例。
4. **结构**：把 god-function 拆成命名清晰的阶段（加载 / 归一 / 分组 / 退款归属 / 落盘）；
   公共函数补类型注解与 docstring；删除死代码与重复 IO；
   **不允许引入模块级可变状态**；CLI 用法与输出格式不变。
5. **过程纪律**：**先写差分测试并确认当前全绿，再动手重构**；每次结构性改动后重跑差分。
6. **README 要补两节**：
   * 「重构前后复杂度与实测耗时/内存对照表」——用固定机器命令复现（写清命令行）；
   * 「保留的历史怪癖」——逐条列出你发现但**故意不修**的行为（**至少 4 条**），
     说明为什么不修、将来要修的话影响面在哪。
7. **验收**：`python3 -m pytest -q` 全绿；20 万规模差分 diff 为空；
   性能与内存两条 pytest 通过。

## 一个提醒

「逐字节相同」这件事比看起来难。float 加法**不满足结合律**：
`0.1 + 0.2 + 0.3` 和 `0.3 + 0.2 + 0.1` 可能差最后一个比特，
经过 `round(v, 2)` 再 `:.2f` 之后就可能差一分钱。所以
**组内累加顺序、总额累加顺序都是口径的一部分**（legacy docstring 口径 8）。
重构时把它们改成 `sum()`、`math.fsum()`、`Decimal` 或者换个遍历顺序，
差分测试都会立刻红给你看——这是好事，别去改测试迁就实现。