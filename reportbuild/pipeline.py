"""生产实现（已重构）：单遍流式流水线，按阶段拆成命名清晰的函数。

业务口径**以 ``legacy/report_legacy.py`` 文件头的 docstring 为准**，本模块
只在结构上与 legacy 不同：

* **加载**：:func:`iter_csv_rows` 流式逐行产出，不再把整个文件读进内存、
  更不按 region 重复读文件；
* **归一 + 分组**：:func:`aggregate_orders` 单遍完成 ts 解析、金额归一、
  ``(region, month)`` 分组累加与 ``order_id`` 首次归属登记；
* **退款归属**：:func:`attribute_refunds` 用 ``order_id -> (region, month)``
  字典 O(1) 查归属，不再线性扫全表；
* **落盘**：:func:`write_outputs` 负责两个产物文件。

无模块级可变状态；组内累加顺序 = 文件出现顺序、总额累加顺序 =
``sorted(keys)``，与 legacy 完全一致（求和顺序是口径的一部分）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator

__all__ = [
    "CSV_FILE",
    "DEFAULT_ORDERS",
    "DEFAULT_OUT",
    "DEFAULT_REFUNDS",
    "ORDER_COLUMNS",
    "OUTPUT_COLUMNS",
    "REFUND_COLUMNS",
    "SUMMARY_FILE",
    "OrderAggregation",
    "RefundAggregation",
    "aggregate_orders",
    "attribute_refunds",
    "build_parser",
    "fmt_money",
    "iter_csv_rows",
    "parse_amount",
    "parse_month",
    "run",
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


# --------------------------------------------------------------------------
# 基础解析 / 格式化（口径与 legacy 逐字一致）
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# 阶段 1：加载（流式，逐行产出，不整表驻留内存）
# --------------------------------------------------------------------------
def iter_csv_rows(path: str, required: tuple[str, ...]) -> Iterator[dict[str, str]]:
    """流式读一个输入 CSV，逐行产出 ``{列名: strip 后的值}`` 字典。

    :param path: CSV 路径（按 ``utf-8-sig`` 读，容忍 BOM）。
    :param required: 必须存在的列名；产出字典只含这些列。
    :raises ValueError: 缺列时报错，信息里带上文件名与实际列。
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        missing = [c for c in required if c not in fields]
        if missing:
            raise ValueError(f"{path}: 缺少必需列 {missing}，实际列是 {fields}")
        for raw in reader:
            yield {c: (raw.get(c) or "").strip() for c in required}


# --------------------------------------------------------------------------
# 阶段 2+3：归一 + 分组（订单侧单遍聚合）
# --------------------------------------------------------------------------
@dataclass
class OrderAggregation:
    """订单侧单遍聚合的全部中间结果（金额均为**未舍入**的 float）。"""

    gross: dict[tuple[str, str], float] = field(default_factory=dict)
    counts: dict[tuple[str, str], int] = field(default_factory=dict)
    first_key: dict[str, tuple[str, str]] = field(default_factory=dict)
    order_rows: int = 0
    skipped_rows: int = 0
    duplicate_order_ids: int = 0


def aggregate_orders(rows: Iterable[dict[str, str]]) -> OrderAggregation:
    """单遍扫描订单行：ts 归一、坏行跳过、按 ``(region, month)`` 分组累加。

    行为口径（与 legacy 一致）：

    * ts 解析失败 → 整行跳过，``skipped_rows += 1``，金额不进任何统计；
    * amount 空/坏 → 按 ``0.0`` 计，**不跳行**；
    * 同一个 ``order_id`` 重复出现 → 计 ``duplicate_order_ids``，归属登记
      只保留**首次出现**的 ``(region, month)``；
    * 组内金额按**文件出现顺序**累加（求和顺序是口径的一部分）。

    :param rows: :func:`iter_csv_rows` 产出的订单行（列见 ``ORDER_COLUMNS``）。
    :returns: :class:`OrderAggregation`。
    """
    result = OrderAggregation()
    gross = result.gross
    counts = result.counts
    first_key = result.first_key
    for row in rows:
        result.order_rows += 1
        month = parse_month(row["ts"])
        if month is None:
            result.skipped_rows += 1
            continue
        key = (row["region"], month)
        order_id = row["order_id"]
        if order_id in first_key:
            result.duplicate_order_ids += 1
        else:
            first_key[order_id] = key
        gross[key] = gross.get(key, 0.0) + parse_amount(row["amount"])
        counts[key] = counts.get(key, 0) + 1
    return result


# --------------------------------------------------------------------------
# 阶段 4：退款归属（字典 O(1) 查找，不再线性扫全表）
# --------------------------------------------------------------------------
@dataclass
class RefundAggregation:
    """退款侧单遍聚合的全部中间结果（金额均为**未舍入**的 float）。"""

    refunds: dict[tuple[str, str], float] = field(default_factory=dict)
    refund_rows: int = 0
    orphan_refunds: int = 0


def attribute_refunds(
    rows: Iterable[dict[str, str]], first_key: dict[str, tuple[str, str]]
) -> RefundAggregation:
    """单遍扫描退款行，把每笔退款归到其订单**首次出现**的 ``(region, month)``。

    行为口径（与 legacy 一致）：

    * ``order_id`` 在未被跳过的订单行里找不到 → 计 ``orphan_refunds``，
      不进任何分组（指向 ts 坏行的退款也算孤儿）；
    * 退款金额空/坏按 ``0.0``；**负数按 0.0 计**（见怪癖 Q2）；
    * 组内金额按退款文件出现顺序累加。

    :param rows: :func:`iter_csv_rows` 产出的退款行（列见 ``REFUND_COLUMNS``）。
    :param first_key: :class:`OrderAggregation` 里的 ``order_id`` 首次归属表。
    :returns: :class:`RefundAggregation`。
    """
    result = RefundAggregation()
    refunds = result.refunds
    for row in rows:
        result.refund_rows += 1
        key = first_key.get(row["order_id"])
        if key is None:
            result.orphan_refunds += 1
            continue
        amount = parse_amount(row["amount"])
        if amount < 0.0:
            amount = 0.0
        refunds[key] = refunds.get(key, 0.0) + amount
    return result


# --------------------------------------------------------------------------
# 阶段 5：落盘
# --------------------------------------------------------------------------
def write_outputs(
    out_dir: str, orders: OrderAggregation, refunds_agg: RefundAggregation
) -> dict:
    """把 ``monthly_region.csv`` 与 ``summary.json`` 写进 ``out_dir``。

    行序为 ``sorted()`` 后的 ``(region, month)`` 字符串字典序；金额一律
    :func:`fmt_money`；``gross_total`` / ``refund_total`` 按 ``sorted(keys)``
    的顺序累加（口径 8）；``net_total`` 用**未舍入**的总和相减（见怪癖 Q7）。

    :param out_dir: 输出目录，不存在会自动创建。
    :param orders: :func:`aggregate_orders` 的返回值。
    :param refunds_agg: :func:`attribute_refunds` 的返回值。
    :returns: 写进 ``summary.json`` 的那个字典。
    """
    os.makedirs(out_dir, exist_ok=True)
    gross = orders.gross
    counts = orders.counts
    refunds = refunds_agg.refunds

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
        "order_rows": orders.order_rows,
        "refund_rows": refunds_agg.refund_rows,
        "skipped_rows": orders.skipped_rows,
        "duplicate_order_ids": orders.duplicate_order_ids,
        "group_count": len(gross),
        "orphan_refunds": refunds_agg.orphan_refunds,
        "gross_total": fmt_money(gross_sum),
        "refund_total": fmt_money(refund_sum),
        "net_total": fmt_money(gross_sum - refund_sum),
    }
    with open(os.path.join(out_dir, SUMMARY_FILE), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return summary


# --------------------------------------------------------------------------
# 流水线入口
# --------------------------------------------------------------------------
def run(orders_path: str, refunds_path: str, out_dir: str) -> dict:
    """跑完整条报表流水线，把两个产物写进 ``out_dir``，返回 summary 字典。

    :param orders_path: 订单 CSV 路径。
    :param refunds_path: 退款 CSV 路径。
    :param out_dir: 产物输出目录。
    """
    orders = aggregate_orders(iter_csv_rows(orders_path, ORDER_COLUMNS))
    refunds = attribute_refunds(iter_csv_rows(refunds_path, REFUND_COLUMNS), orders.first_key)
    return write_outputs(out_dir, orders, refunds)


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器。参数名与默认值和 legacy 完全一致，不得变更。"""
    parser = argparse.ArgumentParser(
        prog="reportbuild",
        description="月度结算报表（当前生产实现）",
    )
    parser.add_argument("--orders", default=DEFAULT_ORDERS, help="订单 CSV 路径")
    parser.add_argument("--refunds", default=DEFAULT_REFUNDS, help="退款 CSV 路径")
    parser.add_argument("--out", default=DEFAULT_OUT, help="产物输出目录")
    return parser
