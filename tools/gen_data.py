#!/usr/bin/env python3
"""生成月度结算报表的测试数据（**固定种子，同种子同产物，可复现**）。

用法::

    # 20 万订单 + 2 万退款（性能与内存验收用这个规模）
    python3 tools/gen_data.py --out /tmp/big --orders 200000 --refunds 20000 --seed 20260901

    # 小规模的极端场景（差分测试用）
    python3 tools/gen_data.py --out /tmp/d1 --orders 400 --refunds 60 --seed 1 --profile dirty
    python3 tools/gen_data.py --out /tmp/d2 --orders 300 --refunds 40 --seed 2 --profile orphan
    python3 tools/gen_data.py --out /tmp/d3 --orders 200 --refunds 30 --seed 3 --profile single
    python3 tools/gen_data.py --out /tmp/d4 --orders 200 --refunds 0  --seed 4 --profile norefunds
    python3 tools/gen_data.py --out /tmp/d5 --orders 300 --refunds 50 --seed 5 --profile dup
    python3 tools/gen_data.py --out /tmp/d6 --orders 200 --refunds 30 --seed 6 --profile tz

产出 ``<out>/orders.csv``（列 ``order_id,region,ts,amount``）与
``<out>/refunds.csv``（列 ``refund_id,order_id,amount``），LF 换行、UTF-8 无 BOM。

``--profile`` 决定数据有多「脏」，用来覆盖差分测试要求的各种极端：

``normal``
    正常数据：ts 全合法、amount 全合法、region 从固定池里取、少量重复
    order_id、少量孤儿退款、少量负退款。
``dirty``
    **全脏数据**：约 1/3 的 ts 是坏值、约 1/5 的 amount 是空串或 ``N/A``、
    约 1/10 的 region 是空串，并且大量混入 ``2.675`` / ``0.145`` / ``1.005`` /
    ``88.885`` 这类会暴露 ``float round()`` 舍入痕迹的金额。
``orphan``
    **全退款孤儿**：每一笔退款的 order_id 都不在订单表里。
``single``
    **单 region**：所有订单同一个 region（分组数退化到只有月份数）。
``norefunds``
    **空退款表**：``refunds.csv`` 只有表头，一行数据都没有。
``dup``
    大量重复 order_id：约 30% 的 order_id 会重复出现，且刻意跨月重复，
    用来放大「退款归属用首次出现」这个怪癖。
``tz``
    ts 全部带 ``+08:00`` 偏移，且刻意压在月初零点附近，用来放大
    「不做时区换算、按字面日历月归档」这个怪癖。

脚本最后会把**输入侧事实**打印到 stdout。这些数字是直接从生成过程里数出来的
（用的是和 ``legacy`` 完全相同的 ``datetime.fromisoformat`` 判定规则），
可以用来人工核对 ``summary.json`` 里的 ``order_rows`` / ``skipped_rows`` /
``group_count`` / ``duplicate_order_ids`` / ``orphan_refunds``。
金额类的 ``gross_total`` / ``refund_total`` / ``net_total`` **不打印**——
它们的口径（float 累加顺序 + ``round()``）只有 legacy 说了算，
手算没有意义，请直接用差分测试比对。
"""

from __future__ import annotations

import argparse
import calendar
import csv
import os
import random
from datetime import datetime

__all__ = ["PROFILES", "build_parser", "generate", "main", "write_csv"]

ORDER_COLUMNS = ("order_id", "region", "ts", "amount")
REFUND_COLUMNS = ("refund_id", "order_id", "amount")

REGION_POOL = ("华东", "华北", "华南", "西南", "西北", "东北", "华中", "海外")

#: 故意混进来的「会暴露 float round() 痕迹」的金额
ROUND_TRAP_AMOUNTS = ("2.675", "0.145", "1.005", "88.885", "0.005", "7.135")
PLAIN_AMOUNTS = ("10.00", "50.50", "250.75", "1000.00", "2048.10", "12345.67", "0.10")
BAD_TS_VALUES = ("", "not-a-date", "2026-13-45 99:99:99", "0000-00-00", "昨天", "1735689600")
BAD_AMOUNT_VALUES = ("", "N/A", "null", "abc", "-")
NEGATIVE_REFUNDS = ("-50.00", "-0.01", "-1000.00")

PROFILES = ("normal", "dirty", "orphan", "single", "norefunds", "dup", "tz")

#: 每个 profile 的各项比例。键含义见 generate()。
PROFILE_KNOBS = {
    "normal":    dict(bad_ts=0.00, bad_amount=0.00, empty_region=0.00, dup=0.02, orphan=0.02, negative_refund=0.05, trap=0.10, tz=0.00, regions=6, months=4),
    "dirty":     dict(bad_ts=0.33, bad_amount=0.20, empty_region=0.10, dup=0.08, orphan=0.10, negative_refund=0.15, trap=0.60, tz=0.10, regions=6, months=4),
    "orphan":    dict(bad_ts=0.00, bad_amount=0.00, empty_region=0.00, dup=0.00, orphan=1.00, negative_refund=0.00, trap=0.10, tz=0.00, regions=4, months=3),
    "single":    dict(bad_ts=0.00, bad_amount=0.00, empty_region=0.00, dup=0.02, orphan=0.02, negative_refund=0.05, trap=0.20, tz=0.00, regions=1, months=4),
    "norefunds": dict(bad_ts=0.02, bad_amount=0.02, empty_region=0.02, dup=0.02, orphan=0.00, negative_refund=0.00, trap=0.20, tz=0.00, regions=5, months=3),
    "dup":       dict(bad_ts=0.00, bad_amount=0.00, empty_region=0.00, dup=0.30, orphan=0.02, negative_refund=0.10, trap=0.30, tz=0.00, regions=4, months=4),
    "tz":        dict(bad_ts=0.00, bad_amount=0.00, empty_region=0.00, dup=0.02, orphan=0.02, negative_refund=0.05, trap=0.20, tz=1.00, regions=4, months=3),
}

BASE_YEAR = 2026
BASE_MONTH = 6


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(description="生成月度结算报表测试数据")
    parser.add_argument("--out", default="data", help="输出目录，默认 data")
    parser.add_argument("--orders", type=int, default=200000, help="订单行数")
    parser.add_argument("--refunds", type=int, default=20000, help="退款行数")
    parser.add_argument("--seed", type=int, default=20260901, help="随机种子")
    parser.add_argument("--profile", default="normal", choices=PROFILES, help="数据脏法")
    parser.add_argument("--regions", type=int, default=0, help="覆盖 region 个数（0=用 profile 默认）")
    parser.add_argument("--months", type=int, default=0, help="覆盖月份个数（0=用 profile 默认）")
    return parser


def _month_list(count: int) -> list[tuple[int, int]]:
    """从 2026-06 开始数 ``count`` 个自然月。"""
    out = []
    year, month = BASE_YEAR, BASE_MONTH
    for _ in range(max(1, count)):
        out.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def _valid_month(ts: str) -> str | None:
    """和 ``legacy.parse_month`` 用**完全相同**的规则判定 ts 好坏。

    只用于生成结束后数一数「有多少坏行」，不参与任何金额计算。
    """
    text = (ts or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return f"{moment.year:04d}-{moment.month:02d}"


def _make_ts(rng: random.Random, year: int, month: int, knobs: dict) -> str:
    """造一个落在指定年月的 ts 字符串。"""
    last_day = calendar.monthrange(year, month)[1]
    if knobs["tz"] and rng.random() < knobs["tz"]:
        # 刻意压在月初零点附近：字面月份 == 指定月份，但换算成 UTC 会掉回上个月
        day = 1
        hour = rng.randrange(0, 2)
        minute = rng.randrange(0, 60)
        second = rng.randrange(0, 60)
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+08:00"
    day = rng.randrange(1, last_day + 1)
    hour = rng.randrange(0, 24)
    minute = rng.randrange(0, 60)
    second = rng.randrange(0, 60)
    if rng.random() < 0.25:
        return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"


def _make_amount(rng: random.Random, knobs: dict) -> str:
    """造一个订单金额字符串。"""
    if knobs["bad_amount"] and rng.random() < knobs["bad_amount"]:
        return rng.choice(BAD_AMOUNT_VALUES)
    if rng.random() < knobs["trap"]:
        return rng.choice(ROUND_TRAP_AMOUNTS)
    return rng.choice(PLAIN_AMOUNTS)


def _make_refund_amount(rng: random.Random, knobs: dict) -> str:
    """造一个退款金额字符串（含负数与坏值）。"""
    if knobs["bad_amount"] and rng.random() < knobs["bad_amount"]:
        return rng.choice(BAD_AMOUNT_VALUES)
    if rng.random() < knobs["negative_refund"]:
        return rng.choice(NEGATIVE_REFUNDS)
    if rng.random() < knobs["trap"]:
        return rng.choice(ROUND_TRAP_AMOUNTS)
    return rng.choice(PLAIN_AMOUNTS)


def generate(orders: int, refunds: int, seed: int, profile: str, regions=0, months=0):
    """按种子生成两张表，返回 ``(order_rows, refund_rows, 输入侧事实字典)``。"""
    knobs = dict(PROFILE_KNOBS[profile])
    if regions:
        knobs["regions"] = regions
    if months:
        knobs["months"] = months

    rng = random.Random(seed)
    region_names = list(REGION_POOL[: max(1, knobs["regions"])])
    month_pairs = _month_list(knobs["months"])

    order_rows: list[dict[str, str]] = []
    order_ids: list[str] = []          # 与 order_rows 平行，含坏行
    live_ids: list[str] = []           # 只含 ts 合法的行的 order_id
    live_keys: dict[str, tuple[str, str]] = {}   # order_id -> 首次出现的 (region, month)
    dup_extra = 0
    skipped = 0

    for i in range(orders):
        order_id = f"O{i + 1:08d}"
        if knobs["dup"] and order_ids and rng.random() < knobs["dup"]:
            # 复用之前某个 order_id（月份是随机的，所以大概率跨月重复）
            order_id = rng.choice(order_ids)

        if knobs["empty_region"] and rng.random() < knobs["empty_region"]:
            region = ""
        else:
            region = rng.choice(region_names)

        if knobs["bad_ts"] and rng.random() < knobs["bad_ts"]:
            ts = rng.choice(BAD_TS_VALUES)
            month = None
        else:
            year, mm = rng.choice(month_pairs)
            ts = _make_ts(rng, year, mm, knobs)
            month = _valid_month(ts)

        amount = _make_amount(rng, knobs)

        order_rows.append({"order_id": order_id, "region": region, "ts": ts, "amount": amount})
        order_ids.append(order_id)

        if month is None:
            skipped += 1
        else:
            live_ids.append(order_id)
            if order_id in live_keys:
                dup_extra += 1
            else:
                live_keys[order_id] = (region, month)

    refund_rows: list[dict[str, str]] = []
    orphan = 0
    negative = 0
    for j in range(refunds):
        if knobs["orphan"] and rng.random() < knobs["orphan"]:
            target = f"ZZZ{j + 1:08d}"
        elif live_ids and rng.random() < 0.98:
            target = rng.choice(live_ids if rng.random() < 0.85 else order_ids)
        elif live_ids:
            target = rng.choice(live_ids)
        else:
            target = f"ZZZ{j + 1:08d}"
        amount = _make_refund_amount(rng, knobs)
        refund_rows.append({"refund_id": f"RF{j + 1:08d}", "order_id": target, "amount": amount})
        if target not in live_keys:
            orphan += 1
        if amount.startswith("-"):
            negative += 1

    facts = {
        "profile": profile,
        "seed": seed,
        "order_rows": len(order_rows),
        "refund_rows": len(refund_rows),
        "skipped_rows": skipped,
        "group_count": len(set(live_keys.values())),
        "duplicate_order_ids": dup_extra,
        "distinct_order_ids_in_live_rows": len(live_keys),
        "orphan_refunds": orphan,
        "negative_refund_rows": negative,
        "regions": region_names,
        "months": [f"{y:04d}-{m:02d}" for y, m in month_pairs],
    }
    return order_rows, refund_rows, facts


def write_csv(path: str, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    """把记录写成 CSV（LF 换行、UTF-8 无 BOM）。"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv=None) -> int:
    """生成数据并把输入侧事实打印出来。"""
    args = build_parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    order_rows, refund_rows, facts = generate(
        args.orders, args.refunds, args.seed, args.profile, args.regions, args.months
    )
    orders_path = os.path.join(args.out, "orders.csv")
    refunds_path = os.path.join(args.out, "refunds.csv")
    write_csv(orders_path, order_rows, ORDER_COLUMNS)
    write_csv(refunds_path, refund_rows, REFUND_COLUMNS)

    print(f"已生成：{orders_path}（{facts['order_rows']} 行数据）")
    print(f"已生成：{refunds_path}（{facts['refund_rows']} 行数据）")
    print(f"profile={facts['profile']} seed={facts['seed']}")
    print(f"regions={len(facts['regions'])} 个：{'、'.join(facts['regions']) or '(空)'}")
    print(f"months={len(facts['months'])} 个：{'、'.join(facts['months'])}")
    print("输入侧事实（可用来核对 summary.json 的对应字段）：")
    for key in (
        "order_rows",
        "refund_rows",
        "skipped_rows",
        "group_count",
        "duplicate_order_ids",
        "orphan_refunds",
        "negative_refund_rows",
    ):
        print(f"  {key} = {facts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())