"""冒烟测试：确认两套实现都能跑起来、产物文件都在、对外契约没跑偏。

这里**故意不**比较 legacy 与 reportbuild 的产物内容——差分测试
（``tests/test_diff_vs_legacy.py``）是本次任务要你自己写的，而且按要求
必须**先写它、先确认它当前全绿**，再动手重构。

跑法（仓库根目录）::

    python3 -m pytest -q
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

LEGACY = ["legacy/report_legacy.py"]
NEW = ["-m", "reportbuild"]

OUTPUT_COLUMNS = ["region", "month", "order_count", "gross_amount", "refund_amount", "net_amount"]
SUMMARY_KEYS = {
    "order_rows",
    "refund_rows",
    "skipped_rows",
    "duplicate_order_ids",
    "group_count",
    "orphan_refunds",
    "gross_total",
    "refund_total",
    "net_total",
}


def run(target, out_dir, orders="data/orders.csv", refunds="data/refunds.csv"):
    """跑一次报表，返回 CompletedProcess。"""
    proc = subprocess.run(
        [PY, *target, "--orders", orders, "--refunds", refunds, "--out", str(out_dir)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{target} 退出码 {proc.returncode}\n{proc.stderr[-1200:]}"
    return proc


def read_csv(out_dir):
    with open(Path(out_dir) / "monthly_region.csv", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        return header, [row for row in reader]


def read_summary(out_dir):
    return json.loads((Path(out_dir) / "summary.json").read_text(encoding="utf-8"))


def test_legacy_produces_both_files(tmp_path):
    out = tmp_path / "legacy"
    run(LEGACY, out)
    assert (out / "monthly_region.csv").is_file()
    assert (out / "summary.json").is_file()


def test_reportbuild_produces_both_files(tmp_path):
    out = tmp_path / "new"
    run(NEW, out)
    assert (out / "monthly_region.csv").is_file()
    assert (out / "summary.json").is_file()


def test_csv_header_is_the_contract(tmp_path):
    out = tmp_path / "h"
    run(NEW, out)
    header, _ = read_csv(out)
    assert header == OUTPUT_COLUMNS


def test_summary_has_every_required_key(tmp_path):
    out = tmp_path / "s"
    run(NEW, out)
    summary = read_summary(out)
    assert set(summary) == SUMMARY_KEYS


def test_summary_counts_are_ints_and_money_has_two_decimals(tmp_path):
    out = tmp_path / "n"
    run(NEW, out)
    summary = read_summary(out)
    for key in ("order_rows", "refund_rows", "skipped_rows", "duplicate_order_ids", "group_count", "orphan_refunds"):
        assert isinstance(summary[key], int), f"{key} 应该是 int"
        assert summary[key] >= 0
    for key in ("gross_total", "refund_total", "net_total"):
        assert isinstance(summary[key], str)
        assert Decimal(summary[key]) == Decimal(summary[key]).quantize(Decimal("0.01"))
        assert len(summary[key].split(".")[-1]) == 2, f"{key} 不是两位小数：{summary[key]}"


def test_sample_fixture_row_counts(tmp_path):
    """data/ 样例的输入侧事实（见 data/README.md）。"""
    out = tmp_path / "c"
    run(NEW, out)
    summary = read_summary(out)
    assert summary["order_rows"] == 18
    assert summary["refund_rows"] == 12
    assert summary["skipped_rows"] == 3
    assert summary["duplicate_order_ids"] == 1
    assert summary["group_count"] == 7
    assert summary["orphan_refunds"] == 2


def test_csv_data_row_count_equals_group_count(tmp_path):
    out = tmp_path / "g"
    run(NEW, out)
    _, rows = read_csv(out)
    assert len(rows) == read_summary(out)["group_count"]


def test_csv_rows_are_sorted_by_region_then_month(tmp_path):
    out = tmp_path / "sort"
    run(NEW, out)
    _, rows = read_csv(out)
    keys = [(r[0], r[1]) for r in rows]
    assert keys == sorted(keys), "CSV 行序必须是 (region, month) 的字符串字典序"


def test_cli_defaults_point_at_data_dir(tmp_path, monkeypatch):
    """不带任何参数也要能跑（默认读 data/，默认写 out/）。"""
    monkeypatch.chdir(ROOT)
    for target in (LEGACY, NEW):
        proc = subprocess.run([PY, *target, "--out", str(tmp_path / "def")], cwd=str(ROOT), capture_output=True, text=True)
        assert proc.returncode == 0, f"{target} 默认参数跑不通：{proc.stderr[-800:]}"
        assert (tmp_path / "def" / "summary.json").is_file()


def test_cli_help_lists_the_three_params(tmp_path):
    for target in (LEGACY, NEW):
        proc = subprocess.run([PY, *target, "--help"], cwd=str(ROOT), capture_output=True, text=True)
        assert proc.returncode == 0
        for flag in ("--orders", "--refunds", "--out"):
            assert flag in proc.stdout, f"{target} 的 {flag} 参数被改名或删掉了"


def test_outputs_are_utf8_without_bom_and_lf_only(tmp_path):
    out = tmp_path / "enc"
    run(NEW, out)
    for name in ("monthly_region.csv", "summary.json"):
        raw = (out / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 带了 BOM"
        assert b"\r" not in raw, f"{name} 里出现了 CR，换行必须是 LF"
        raw.decode("utf-8")


def test_missing_column_raises(tmp_path):
    bad = tmp_path / "bad_orders.csv"
    bad.write_text("order_id,region,ts\nO1,华东,2026-09-01T00:00:00\n", encoding="utf-8")
    for target in (LEGACY, NEW):
        proc = subprocess.run(
            [PY, *target, "--orders", str(bad), "--refunds", "data/refunds.csv", "--out", str(tmp_path / "x")],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        assert proc.returncode != 0, f"{target} 缺列时应该报错"
        assert "缺少必需列" in proc.stderr


def test_rerun_is_idempotent(tmp_path):
    """同一份输入连跑两次，两个产物逐字节相同。"""
    blobs = []
    for tag in ("r1", "r2"):
        out = tmp_path / tag
        run(NEW, out)
        blobs.append((
            (out / "monthly_region.csv").read_bytes(),
            (out / "summary.json").read_bytes(),
        ))
    assert blobs[0] == blobs[1]