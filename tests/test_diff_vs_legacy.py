"""差分测试：``reportbuild`` 的产物必须与 ``legacy/report_legacy.py`` 逐字节相同。

分两层：

* **端到端**：subprocess 分别跑两套实现，断言两个输出目录 diff 为空。
  覆盖 6 组 ``(seed, profile, 规模)`` 组合（全脏数据、全退款孤儿、单 region、
  空退款表、大量重复 order_id、带时区偏移），外加 20 万订单 + 2 万退款的大样本。
* **纯函数级**：小样本上直接比对两边同名函数的返回值（``fmt_money`` /
  ``parse_month`` / ``parse_amount``），不经过子进程与磁盘。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import (
    BIG_ORDERS,
    BIG_REFUNDS,
    ROOT,
    assert_dirs_identical,
    generate_data,
    run_legacy,
    run_reportbuild,
)


def _load_legacy_module():
    """把 ``legacy/report_legacy.py`` 按文件路径加载成模块对象。"""
    spec = importlib.util.spec_from_file_location("report_legacy", ROOT / "legacy" / "report_legacy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy_module()
pipeline = pytest.importorskip("reportbuild.pipeline")

# --------------------------------------------------------------------------
# 端到端差分：subprocess 两边各跑一遍，产物逐字节比对
# --------------------------------------------------------------------------

CASES = [
    pytest.param(1, "dirty", 400, 60, id="dirty-seed1"),
    pytest.param(2, "orphan", 300, 40, id="orphan-seed2"),
    pytest.param(3, "single", 200, 30, id="single-region-seed3"),
    pytest.param(4, "norefunds", 200, 0, id="norefunds-seed4"),
    pytest.param(5, "dup", 300, 50, id="dup-seed5"),
    pytest.param(6, "tz", 200, 30, id="tz-seed6"),
]


@pytest.mark.parametrize("seed,profile,orders,refunds", CASES)
def test_outputs_match_legacy(tmp_path, seed, profile, orders, refunds):
    """小规模极端场景：两个目录的产物必须逐字节相同。"""
    orders_csv, refunds_csv = generate_data(
        tmp_path / "data", orders=orders, refunds=refunds, seed=seed, profile=profile
    )
    legacy_out = tmp_path / "out_legacy"
    new_out = tmp_path / "out_new"
    run_legacy(orders_csv, refunds_csv, legacy_out)
    run_reportbuild(orders_csv, refunds_csv, new_out)
    assert_dirs_identical(legacy_out, new_out)


def test_big_outputs_match_legacy(big_data, tmp_path):
    """20 万订单 + 2 万退款：验收规模的逐字节差分。"""
    orders_csv, refunds_csv = big_data
    legacy_out = tmp_path / "out_legacy"
    new_out = tmp_path / "out_new"
    run_legacy(orders_csv, refunds_csv, legacy_out)
    run_reportbuild(orders_csv, refunds_csv, new_out)
    assert_dirs_identical(legacy_out, new_out)


# --------------------------------------------------------------------------
# 纯函数级差分：小样本直接比对返回值
# --------------------------------------------------------------------------

MONEY_VALUES = [
    0.0,
    2.675,      # Q1：落盘必须是 "2.67" 而不是 "2.68"
    0.145,
    1.005,
    88.885,
    0.005,
    7.135,
    -20.0,
    1234567.891,
    1e-9,
    0.1 + 0.2,  # 0.30000000000000004，float 痕迹必须原样保留
]

TS_VALUES = [
    "2026-09-01T10:15:00",
    "2026-08-31 23:59:59",          # 空格分隔也合法
    "2026-09-01T00:30:00+08:00",    # Q6：不做时区换算，按字面归 2026-09
    "",
    "not-a-date",
    "2026-13-45 99:99:99",
    "0000-00-00",
    "1735689600",
    "  2026-09-05T08:00:00  ",      # 首尾空白先 strip
    None,
]

AMOUNT_VALUES = ["1200.50", "", "N/A", "abc", "-", "-50.00", "0.10", " 7.135 ", None]


@pytest.mark.parametrize("value", MONEY_VALUES)
def test_fmt_money_matches_legacy(value):
    assert pipeline.fmt_money(value) == legacy.fmt_money(value)


@pytest.mark.parametrize("ts", TS_VALUES)
def test_parse_month_matches_legacy(ts):
    assert pipeline.parse_month(ts) == legacy.parse_month(ts)


@pytest.mark.parametrize("text", AMOUNT_VALUES)
def test_parse_amount_matches_legacy(text):
    assert pipeline.parse_amount(text) == legacy.parse_amount(text)


# --------------------------------------------------------------------------
# 纯函数级差分：聚合阶段（订单归一/分组 + 退款归属 + 落盘字节）
# --------------------------------------------------------------------------

def _make_rows(seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """造一小份确定性行数据，覆盖坏 ts、坏金额、空 region、重复 order_id、
    负退款、孤儿退款、float 舍入陷阱。"""
    order_rows = [
        {"order_id": "O1", "region": "华东", "ts": "2026-09-01T10:00:00", "amount": "2.675"},
        {"order_id": "O2", "region": "", "ts": "2026-09-01T00:30:00+08:00", "amount": "0.145"},
        {"order_id": "O3", "region": "华北", "ts": "not-a-date", "amount": "999.99"},
        {"order_id": "O4", "region": "华北", "ts": "2026-08-15 12:00:00", "amount": ""},
        {"order_id": "O5", "region": "华北", "ts": "2026-08-16T08:00:00", "amount": "N/A"},
        {"order_id": "O1", "region": "华东", "ts": "2026-10-01T09:00:00", "amount": "88.885"},
        {"order_id": "O6", "region": "华东", "ts": "2026-09-02T11:00:00", "amount": "-20.00"},
        {"order_id": "O7", "region": "海外", "ts": "", "amount": "1.005"},
        {"order_id": "O8", "region": "华东", "ts": "2026-09-03T12:00:00", "amount": "0.1"},
        {"order_id": "O9", "region": "华东", "ts": "2026-09-04T12:00:00", "amount": "0.2"},
    ]
    refund_rows = [
        {"refund_id": "R1", "order_id": "O1", "amount": "2.675"},
        {"refund_id": "R2", "order_id": "O1", "amount": "-50.00"},
        {"refund_id": "R3", "order_id": "O3", "amount": "10.00"},   # 指向 ts 坏行 → 孤儿
        {"refund_id": "R4", "order_id": "ZZZ", "amount": "10.00"},  # 不存在 → 孤儿
        {"refund_id": "R5", "order_id": "O2", "amount": ""},
        {"refund_id": "R6", "order_id": "O4", "amount": "abc"},
        {"refund_id": "R7", "order_id": "O6", "amount": "7.135"},
        {"refund_id": "R8", "order_id": "O8", "amount": "0.1"},
        {"refund_id": "R9", "order_id": "O9", "amount": "0.2"},
    ]
    assert seed == 0  # 固定数据，参数只为表意
    return order_rows, refund_rows


def test_aggregation_matches_legacy():
    """订单归一/分组与退款归属的中间结果必须与 legacy.aggregate 完全一致。"""
    order_rows, refund_rows = _make_rows(seed=0)
    expected = legacy.aggregate(order_rows, refund_rows)

    orders = pipeline.aggregate_orders(iter(order_rows))
    refunds = pipeline.attribute_refunds(iter(refund_rows), orders.first_key)

    assert orders.gross == expected["gross"]
    assert orders.counts == expected["counts"]
    assert refunds.refunds == expected["refunds"]
    assert {
        "order_rows": orders.order_rows,
        "refund_rows": refunds.refund_rows,
        "skipped_rows": orders.skipped_rows,
        "duplicate_order_ids": orders.duplicate_order_ids,
        "group_count": len(orders.gross),
        "orphan_refunds": refunds.orphan_refunds,
    } == expected["stats"]


def test_write_outputs_matches_legacy(tmp_path):
    """同一批中间结果，两边落盘字节必须相同。"""
    order_rows, refund_rows = _make_rows(seed=0)
    legacy_dir = tmp_path / "legacy"
    new_dir = tmp_path / "new"

    legacy_summary = legacy.write_outputs(str(legacy_dir), legacy.aggregate(order_rows, refund_rows))

    orders = pipeline.aggregate_orders(iter(order_rows))
    refunds = pipeline.attribute_refunds(iter(refund_rows), orders.first_key)
    new_summary = pipeline.write_outputs(str(new_dir), orders, refunds)

    assert new_summary == legacy_summary
    assert_dirs_identical(legacy_dir, new_dir)
