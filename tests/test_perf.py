"""性能与内存验收：20 万订单 + 2 万退款（固定种子，见 conftest.big_data）。

* 端到端（``python3 -m reportbuild`` 子进程墙钟）< 4 秒；
* ``tracemalloc`` 峰值内存 < 150 MB（进程内跑 ``pipeline.run`` 测量）。
"""

from __future__ import annotations

import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

from conftest import ROOT, run_legacy  # noqa: F401  (run_legacy 仅表意，不在此用)

PY = sys.executable

TIME_BUDGET_SECONDS = 4.0
MEMORY_BUDGET_BYTES = 150 * 1024 * 1024


def test_end_to_end_under_4_seconds(big_data, tmp_path):
    """reportbuild 端到端 < 4 秒（legacy 不要求达标，不在此计时）。"""
    orders_csv, refunds_csv = big_data
    out_dir = tmp_path / "out"
    start = time.perf_counter()
    proc = subprocess.run(
        [
            PY,
            "-m",
            "reportbuild",
            "--orders",
            str(orders_csv),
            "--refunds",
            str(refunds_csv),
            "--out",
            str(out_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    assert proc.returncode == 0, f"reportbuild 退出码 {proc.returncode}\n{proc.stderr[-800:]}"
    assert (out_dir / "monthly_region.csv").is_file()
    assert elapsed < TIME_BUDGET_SECONDS, f"端到端耗时 {elapsed:.2f}s，超过 {TIME_BUDGET_SECONDS}s 预算"


def test_peak_memory_under_150mb(big_data, tmp_path):
    """tracemalloc 峰值内存 < 150 MB（进程内测量，排除解释器自身开销）。"""
    from reportbuild import pipeline

    orders_csv, refunds_csv = big_data
    out_dir = tmp_path / "out"
    tracemalloc.start()
    try:
        pipeline.run(str(orders_csv), str(refunds_csv), str(out_dir))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (out_dir / "summary.json").is_file()
    assert peak < MEMORY_BUDGET_BYTES, (
        f"tracemalloc 峰值 {peak / 1024 / 1024:.1f} MB，超过 150 MB 预算"
    )
