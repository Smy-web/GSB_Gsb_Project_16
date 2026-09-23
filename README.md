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
│   └── pipeline.py            ← god-function 全在这里
├── tools/
│   └── gen_data.py            ← 固定种子的数据生成器（含 7 种 profile）
├── data/
│   ├── README.md              ← 样例夹具逐行对照：每一行在考哪条怪癖
│   ├── orders.csv             ← 18 行手工样例
│   └── refunds.csv            ← 12 行手工样例
└── tests/
    └── test_smoke.py          ← 冒烟测试（当前全绿）
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

## 已知的性能问题（`reportbuild/pipeline.py`）

1. **按 region 重复读订单文件**：有 R 个 region，订单文件就被完整读 R+1 遍，
   每遍都重新解析一次 CSV 和 ts。
2. **每笔退款线性扫全表**：`order_ids.index(order_id)`，M 笔退款 × N 条订单。
3. **一段无意义的双重循环**：阶段 4 那个「校验」把 R × N 次比较算了个
   `region_row_counts`，**算完一次都没用到**。
4. 主流程是一个塞了 7 个阶段的 god-function；模块级可变状态（`_FILES_READ`、
   `_ROW_CACHE`）；死代码（`group_by_month_only`、没用上的 `sys` / `OrderedDict`
   导入）；一整套和 legacy 重复的 parse/format 函数；订单数据在内存里存了
   三份拷贝（`raw_rows` / `records` / `per_region`）。

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