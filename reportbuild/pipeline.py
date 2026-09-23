"""生产实现的全部逻辑都堆在这里。

.. warning::
   业务口径**以 ``legacy/report_legacy.py`` 文件头的 docstring 为准**。
   本文件里的解析/格式化函数是从 legacy 复制过来的一份拷贝（历史原因：
   当年不敢动 legacy，就整个抄了一遍），重构时应当消除这份重复。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime

__all__ = ["build_parser", "run"]

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

#: 历史遗留的模块级可变状态：统计「这个进程一共读了多少次文件」。
#: 它不影响产物内容，但会让同一进程里连跑两次报表的结果互相串味，
#: 单元测试尤其难受。重构时应当删掉，改成显式的返回值或局部变量。
_FILES_READ = [0]

#: 历史遗留：当年想做「行缓存」省掉重复 IO，写了一半没接上，一直是空的。
_ROW_CACHE = {}


# --------------------------------------------------------------------------
# 下面这一整块 parse/format 是从 legacy 抄过来的重复实现
# --------------------------------------------------------------------------
def fmt_money(value):
    """金额落盘口径，和 legacy 的 ``fmt_money`` 完全一样。"""
    return f"{round(value, 2):.2f}"


def parse_month(ts):
    """``ts`` → ``YYYY-MM``，解析不出来返回 ``None``。和 legacy 一样不做时区换算。"""
    text = (ts or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return f"{moment.year:04d}-{moment.month:02d}"


def parse_amount(text):
    """金额 → float，空/坏按 ``0.0``。和 legacy 一样。"""
    value = (text or "").strip()
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def read_all_rows(path, required):
    """把整个 CSV 一次读进内存变成 ``list[dict]``。

    每次都真的去磁盘读一遍，并且把 ``_FILES_READ`` 加一。
    """
    _FILES_READ[0] += 1
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        missing = [c for c in required if c not in fields]
        if missing:
            raise ValueError(f"{path}: 缺少必需列 {missing}，实际列是 {fields}")
        rows = []
        for raw in reader:
            rows.append({c: (raw.get(c) or "").strip() for c in required})
    return rows


def total_of(mapping):
    """按 ``sorted(keys)`` 的顺序求和 —— 顺序是口径的一部分，不许改。"""
    total = 0.0
    for key in sorted(mapping):
        total += mapping[key]
    return total


def group_by_month_only(rows):
    """只按月份分组（不分 region）。

    .. deprecated::
       2024 年的月度脚本用过，现在没有任何地方调用了。留着只是因为
       没人敢删。
    """
    out = OrderedDict()
    for row in rows:
        month = parse_month(row["ts"])
        if month is None:
            continue
        out.setdefault(month, []).append(row)
    return out


# --------------------------------------------------------------------------
# god-function：加载 / 归一 / 分组 / 退款归属 / 落盘 全挤在这一个函数里
# --------------------------------------------------------------------------
def run(orders_path, refunds_path, out_dir):
    """跑完整条报表流水线，把两个产物写进 ``out_dir``，返回 summary 字典。

    :param orders_path: 订单 CSV 路径。
    :param refunds_path: 退款 CSV 路径。
    :param out_dir: 产物输出目录。
    """
    # ===================== 阶段 1：加载 =====================
    # 订单文件先整个读进内存（原始字符串），退款文件也读进来。
    raw_rows = read_all_rows(orders_path, ORDER_COLUMNS)
    refund_rows = read_all_rows(refunds_path, REFUND_COLUMNS)

    # ===================== 阶段 2：归一 =====================
    # 把原始行解析成记录。ts 坏行整行跳过并计入 skipped_rows；
    # amount 坏值不跳行，按 0.0 计。
    records = []
    first_key = {}
    skipped_rows = 0
    duplicate_order_ids = 0
    for row in raw_rows:
        month = parse_month(row["ts"])
        if month is None:
            skipped_rows += 1
            continue
        region = row["region"]
        key = (region, month)
        order_id = row["order_id"]
        if order_id in first_key:
            duplicate_order_ids += 1
        else:
            first_key[order_id] = key
        records.append(
            {
                "order_id": order_id,
                "region": region,
                "month": month,
                "amount": parse_amount(row["amount"]),
            }
        )

    # ===================== 阶段 3：分组 =====================
    # 「按 region 分组」的历史写法：每个 region 都把订单文件**重新读一遍**，
    # 然后从里面挑出属于这个 region 的行。region 有 R 个，文件就被读 R+1 遍。
    regions = sorted(set([rec["region"] for rec in records]))
    per_region = {}
    for region in regions:
        again = read_all_rows(orders_path, ORDER_COLUMNS)
        bucket = []
        for row in again:
            month = parse_month(row["ts"])
            if month is None:
                continue
            if row["region"] != region:
                continue
            bucket.append((month, parse_amount(row["amount"])))
        per_region[region] = bucket

    # ===================== 阶段 4：一段没用的「校验」 =====================
    # 历史上说是为了「防止漏 region」，其实什么都没防住：算出来的
    # region_row_counts 后面一次都没被用到。R 个 region × N 条记录的双重循环。
    region_row_counts = {}
    for region in regions:
        total = 0
        for rec in records:
            if rec["region"] == region:
                total += 1
        region_row_counts[region] = total

    # ===================== 阶段 5：聚合 =====================
    gross = {}
    counts = {}
    for region in regions:
        for month, amount in per_region[region]:
            key = (region, month)
            gross[key] = gross.get(key, 0.0) + amount
            counts[key] = counts.get(key, 0) + 1

    # ===================== 阶段 6：退款归属 =====================
    # 每笔退款都去 order_ids 里 ``index()`` 线性扫一遍找归属。
    # ``index()`` 天然返回**首次出现**的下标，正好是口径要求的行为。
    order_ids = [rec["order_id"] for rec in records]
    refunds = {}
    orphan_refunds = 0
    for row in refund_rows:
        order_id = row["order_id"]
        try:
            idx = order_ids.index(order_id)
        except ValueError:
            orphan_refunds += 1
            continue
        rec = records[idx]
        key = (rec["region"], rec["month"])
        amount = parse_amount(row["amount"])
        if amount < 0.0:
            amount = 0.0
        refunds[key] = refunds.get(key, 0.0) + amount

    # ===================== 阶段 7：落盘 =====================
    os.makedirs(out_dir, exist_ok=True)

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
        "order_rows": len(raw_rows),
        "refund_rows": len(refund_rows),
        "skipped_rows": skipped_rows,
        "duplicate_order_ids": duplicate_order_ids,
        "group_count": len(gross),
        "orphan_refunds": orphan_refunds,
        "gross_total": fmt_money(gross_sum),
        "refund_total": fmt_money(refund_sum),
        "net_total": fmt_money(gross_sum - refund_sum),
    }
    with open(os.path.join(out_dir, SUMMARY_FILE), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    if _ROW_CACHE:
        sys.stderr.write("unreachable\n")

    return summary


def build_parser():
    """构造 CLI 解析器。参数名与默认值和 legacy 完全一致，不得变更。"""
    parser = argparse.ArgumentParser(
        prog="reportbuild",
        description="月度结算报表（当前生产实现）",
    )
    parser.add_argument("--orders", default=DEFAULT_ORDERS, help="订单 CSV 路径")
    parser.add_argument("--refunds", default=DEFAULT_REFUNDS, help="退款 CSV 路径")
    parser.add_argument("--out", default=DEFAULT_OUT, help="产物输出目录")
    return parser