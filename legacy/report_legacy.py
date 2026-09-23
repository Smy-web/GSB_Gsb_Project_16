#!/usr/bin/env python3
"""月度结算报表 —— **行为基准**（本文件禁止修改）。

这个文件是 ``reportbuild/`` 的对照物。对任意输入，它产出的
``monthly_region.csv`` 与 ``summary.json`` 就是「正确输出」的定义；重构
``reportbuild/`` 时唯一可接受的判据是**两边产物逐字节相同**（``diff -r`` 为空）。

本实现是**单遍流式**写法，只用来当基准，不追求可读性之外的东西；生产实现在
``reportbuild/`` 里，逻辑与本文件相同但性能差得多。

==========================================================================
运行方式
==========================================================================
::

    python3 legacy/report_legacy.py                        # 用 data/ 里的样例
    python3 legacy/report_legacy.py --orders O --refunds R --out DIR

``python3 -m reportbuild`` 接受**完全相同**的参数名与默认值。

==========================================================================
输入格式
==========================================================================
``orders.csv``（订单表）列：``order_id,region,ts,amount``
``refunds.csv``（退款表）列：``refund_id,order_id,amount``

两个文件都按 ``utf-8-sig`` 读（容忍 BOM），每个单元格读进来先 ``strip()``。
缺必需列直接 ``ValueError``。

==========================================================================
输出一：``monthly_region.csv``
==========================================================================
列（顺序固定，不得增删改名）::

    region,month,order_count,gross_amount,refund_amount,net_amount

* 一行 = 一个 ``(region, month)`` 分组；
* 行序 = ``sorted()`` 后的 ``(region, month)`` **字符串字典序**（不是时间序）；
* ``order_count`` 是整数，直接 ``str()``；
* 三个金额列一律是 :func:`fmt_money` 的结果。

==========================================================================
输出二：``summary.json``
==========================================================================
``json.dump(..., ensure_ascii=False, indent=2, sort_keys=True)`` 之后再补一个
换行符。字段（键序由 ``sort_keys`` 决定，不得增删改名）::

    order_rows           订单文件里的数据行数（**含**被判为坏行而跳过的）
    refund_rows          退款文件里的数据行数
    skipped_rows         ts 解析失败而被整行跳过的订单行数
    duplicate_order_ids  订单表里 order_id 重复出现的**额外**条数
    group_count          monthly_region.csv 的数据行数
    orphan_refunds       在订单表里找不到 order_id 的退款笔数
    gross_total          所有组 gross 之和（fmt_money 之后）
    refund_total         所有组 refund 之和（fmt_money 之后）
    net_total            未舍入的 gross 总和 减 未舍入的 refund 总和

==========================================================================
行为口径（重构必须一字不差地保留）
==========================================================================
1. **month**：``ts`` 用 ``datetime.fromisoformat()`` 解析，成功后取
   ``YYYY-MM``。解析不出来（含空串、``not-a-date``、``2026-13-45``）→ 该
   订单行**整行跳过**，``skipped_rows += 1``，这行的金额完全不进任何统计。
2. **订单 amount**：``float()`` 解析；空串或解析失败 → 按 ``0.0`` 计，
   **不跳行**（与 ts 的处理标准不一致，历史遗留）。
3. **region**：为空就用空串 ``""`` 参与分组，会产出一行 region 为空的记录；
   不并入 ``UNKNOWN``、也不丢弃。
4. **退款归属**：退款行按 ``order_id`` 找订单，归到那条订单的
   ``(region, month)``。订单表里同一个 ``order_id`` 出现多次时，一律归到
   **首次出现**那条的 ``(region, month)``。
5. **孤儿退款**：``order_id`` 在（未被跳过的）订单行里找不到 → 计入
   ``orphan_refunds``，不进任何分组、不进 CSV。注意：指向「ts 坏行」的退款
   也算孤儿。
6. **退款金额**：同样 ``float()`` 解析、空/坏按 ``0.0``；**负数按 0.0 计**
   （不冲减、不取绝对值）。
7. **金额落盘**：一律 :func:`fmt_money`，即 ``f"{round(v, 2):.2f}"``；
   全链路 ``float``，**禁止换成 Decimal「修正」**。
8. **求和顺序也是口径的一部分**：组内按**文件出现顺序**累加；
   ``gross_total`` / ``refund_total`` 按 ``sorted(keys)`` 的顺序累加。
   float 加法不满足结合律，顺序一变字节就可能变，重构时不许改顺序。

==========================================================================
保留的历史怪癖（故意不修，重构后必须依然存在）
==========================================================================
* **Q1 float 舍入痕迹**：``2.675`` 落盘是 ``"2.67"``（不是财务口径的
  ``2.68``），``0.145`` 是 ``"0.14"``。根因是二进制浮点 + ``round()`` 的
  round-half-even。改成 Decimal 会让下游所有历史报表对不上账。
* **Q2 负退款按 0**：``refunds.amount = -50`` 记 ``0.00``。早期用负数表示
  「撤销退款」，语义后来改了，存量数据没清洗。
* **Q3 重复 order_id 用首次归属**：同一 ``order_id`` 跨月出现两次时，它的
  全部退款都算在**第一个月**头上，后面那个月的 ``refund_amount`` 会偏小。
* **Q4 ts 坏行跳行、amount 坏值不跳行**：两套标准并存，``skipped_rows``
  只统计前者。
* **Q5 空 region 单独成行**：字典序里排在所有非空 region 之前。
* **Q6 不做时区换算**：``ts`` 带偏移量时按**字面**日历月归档，
  ``2026-09-01T00:30:00+08:00`` 归到 ``2026-09``，即使换算成 UTC 还在 8 月。
* **Q7 汇总与明细可能差一分**：``net_total`` 由**未舍入**的 float 总和相减
  得到，与 CSV 里各行 ``net_amount`` 直接相加可能差 0.01。
* **Q8 只有退款侧裁负数**：``orders.amount = -20`` 会**原样**进 gross（把
  当月毛额拉低），只有 ``refunds.amount`` 的负数才按 0 计。两侧标准不一致。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime

__all__ = [
    "DEFAULT_ORDERS",
    "DEFAULT_OUT",
    "DEFAULT_REFUNDS",
    "ORDER_COLUMNS",
    "OUTPUT_COLUMNS",
    "REFUND_COLUMNS",
    "aggregate",
    "build_parser",
    "fmt_money",
    "main",
    "parse_amount",
    "parse_month",
    "read_rows",
    "total_of",
    "write_outputs",
]

ORDER_COLUMNS = ("order_id", "region", "ts", "amount")
REFUND_COLUMNS = ("refund_id", "order_id", "amount")
OUTPUT_COLUMNS = (
    "region",
    "month",
    "order_count",
    "gross_amount",
    "refund_amount",
    "net_amount",
)

DEFAULT_ORDERS = os.path.join("data", "orders.csv")
DEFAULT_REFUNDS = os.path.join("data", "refunds.csv")
DEFAULT_OUT = "out"

CSV_FILE = "monthly_region.csv"
SUMMARY_FILE = "summary.json"


def fmt_money(value: float) -> str:
    """金额落盘口径：``round()`` 到两位再定宽格式化（见怪癖 Q1）。

    :param value: 未舍入的 float 金额。
    :returns: 形如 ``"2.67"``、``"1234.56"`` 的字符串。
    """
    return f"{round(value, 2):.2f}"


def parse_month(ts: str | None) -> str | None:
    """把 ``ts`` 解析成 ``YYYY-MM``；解析不出来返回 ``None``（该行算坏行）。

    不做任何时区换算（见怪癖 Q6）。

    :param ts: 订单行的 ``ts`` 单元格。
    :returns: ``"2026-09"`` 这样的月份串，或 ``None``。
    """
    text = (ts or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return f"{moment.year:04d}-{moment.month:02d}"


def parse_amount(text: str | None) -> float:
    """把金额字段解析成 float；空串或坏值按 ``0.0``（见口径 2、6）。

    :param text: CSV 单元格原始字符串。
    :returns: float 金额。
    """
    value = (text or "").strip()
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def read_rows(path: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    """读一个输入 CSV，返回字典列表（值已 strip）。

    :param path: CSV 路径。
    :param required: 必须存在的列名。
    :raises ValueError: 缺列时报错，信息里带上文件名与实际列。
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        missing = [c for c in required if c not in fields]
        if missing:
            raise ValueError(f"{path}: 缺少必需列 {missing}，实际列是 {fields}")
        rows: list[dict[str, str]] = []
        for raw in reader:
            rows.append({c: (raw.get(c) or "").strip() for c in required})
    return rows


def total_of(mapping: dict[tuple[str, str], float]) -> float:
    """按 ``sorted(keys)`` 的顺序把各组金额加起来。

    .. warning::
       求和顺序是口径的一部分（口径 8）。float 加法不满足结合律，
       换个顺序 ``fmt_money`` 的结果就可能差一分。

    :param mapping: ``{(region, month): 金额}``。
    :returns: 未舍入的 float 总和。
    """
    total = 0.0
    for key in sorted(mapping):
        total += mapping[key]
    return total


def aggregate(order_rows, refund_rows) -> dict:
    """单遍聚合订单与退款，返回落盘所需的全部中间结果。

    :param order_rows: :func:`read_rows` 读出来的订单行。
    :param refund_rows: 同上，退款行。
    :returns: 字典，键为 ``stats`` / ``gross`` / ``refunds`` / ``counts``。
        ``gross``、``refunds`` 的值都是**未舍入**的 float；``counts`` 是整数。
    """
    gross: dict[tuple[str, str], float] = {}
    refunds: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    first_key: dict[str, tuple[str, str]] = {}

    skipped_rows = 0
    duplicate_order_ids = 0

    for row in order_rows:
        month = parse_month(row["ts"])
        if month is None:
            skipped_rows += 1
            continue
        key = (row["region"], month)
        order_id = row["order_id"]
        if order_id in first_key:
            duplicate_order_ids += 1
        else:
            first_key[order_id] = key
        gross[key] = gross.get(key, 0.0) + parse_amount(row["amount"])
        counts[key] = counts.get(key, 0) + 1

    orphan_refunds = 0
    for row in refund_rows:
        key = first_key.get(row["order_id"])
        if key is None:
            orphan_refunds += 1
            continue
        amount = parse_amount(row["amount"])
        if amount < 0.0:
            amount = 0.0
        refunds[key] = refunds.get(key, 0.0) + amount

    return {
        "stats": {
            "order_rows": len(order_rows),
            "refund_rows": len(refund_rows),
            "skipped_rows": skipped_rows,
            "duplicate_order_ids": duplicate_order_ids,
            "group_count": len(gross),
            "orphan_refunds": orphan_refunds,
        },
        "gross": gross,
        "refunds": refunds,
        "counts": counts,
    }


def write_outputs(out_dir: str, result: dict) -> dict:
    """把 ``monthly_region.csv`` 与 ``summary.json`` 写进 ``out_dir``。

    :param out_dir: 输出目录，不存在会自动创建。
    :param result: :func:`aggregate` 的返回值。
    :returns: 写进 ``summary.json`` 的那个字典。
    """
    os.makedirs(out_dir, exist_ok=True)
    stats = result["stats"]
    gross = result["gross"]
    refunds = result["refunds"]
    counts = result["counts"]

    keys = sorted(gross)
    with open(os.path.join(out_dir, CSV_FILE), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(list(OUTPUT_COLUMNS))
        for key in keys:
            region, month = key
            g = gross[key]
            r = refunds.get(key, 0.0)
            writer.writerow(
                [
                    region,
                    month,
                    str(counts[key]),
                    fmt_money(g),
                    fmt_money(r),
                    fmt_money(g - r),
                ]
            )

    gross_sum = total_of(gross)
    refund_sum = total_of(refunds)
    summary = {
        "order_rows": stats["order_rows"],
        "refund_rows": stats["refund_rows"],
        "skipped_rows": stats["skipped_rows"],
        "duplicate_order_ids": stats["duplicate_order_ids"],
        "group_count": stats["group_count"],
        "orphan_refunds": stats["orphan_refunds"],
        "gross_total": fmt_money(gross_sum),
        "refund_total": fmt_money(refund_sum),
        "net_total": fmt_money(gross_sum - refund_sum),
    }
    with open(os.path.join(out_dir, SUMMARY_FILE), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return summary


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器（参数名与默认值属于对外兼容范围）。"""
    parser = argparse.ArgumentParser(
        prog="report_legacy",
        description="月度结算报表（行为基准，禁止修改）",
    )
    parser.add_argument("--orders", default=DEFAULT_ORDERS, help="订单 CSV 路径")
    parser.add_argument("--refunds", default=DEFAULT_REFUNDS, help="退款 CSV 路径")
    parser.add_argument("--out", default=DEFAULT_OUT, help="产物输出目录")
    return parser


def main(argv=None) -> int:
    """跑一次完整报表，返回进程退出码。"""
    args = build_parser().parse_args(argv)

    order_rows = read_rows(args.orders, ORDER_COLUMNS)
    refund_rows = read_rows(args.refunds, REFUND_COLUMNS)

    summary = write_outputs(args.out, aggregate(order_rows, refund_rows))

    print(
        "报表完成：groups={group_count} order_rows={order_rows} "
        "refund_rows={refund_rows} skipped_rows={skipped_rows} "
        "duplicate_order_ids={duplicate_order_ids} orphan_refunds={orphan_refunds} "
        "gross_total={gross_total} refund_total={refund_total} net_total={net_total}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())