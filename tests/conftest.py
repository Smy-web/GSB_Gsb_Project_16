"""差分 / 性能测试的公共脚手架。

提供：
* :func:`generate_data` —— 用 ``tools/gen_data.py`` 固定种子造数据；
* :func:`run_legacy` / :func:`run_reportbuild` —— subprocess 各跑一遍；
* ``big_data`` fixture —— 20 万订单 + 2 万退款（session 级，只生成一次）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
GEN_DATA = ROOT / "tools" / "gen_data.py"
LEGACY_SCRIPT = ROOT / "legacy" / "report_legacy.py"

BIG_ORDERS = 200_000
BIG_REFUNDS = 20_000
BIG_SEED = 20260901


def generate_data(
    out_dir: Path, *, orders: int, refunds: int, seed: int, profile: str = "normal"
) -> tuple[Path, Path]:
    """用固定种子生成一对输入 CSV，返回 ``(orders_csv, refunds_csv)``。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            PY,
            str(GEN_DATA),
            "--out",
            str(out_dir),
            "--orders",
            str(orders),
            "--refunds",
            str(refunds),
            "--seed",
            str(seed),
            "--profile",
            profile,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"gen_data 失败：{proc.stderr[-800:]}"
    return out_dir / "orders.csv", out_dir / "refunds.csv"


def run_legacy(orders: Path, refunds: Path, out_dir: Path) -> None:
    """subprocess 跑行为基准。"""
    proc = subprocess.run(
        [PY, str(LEGACY_SCRIPT), "--orders", str(orders), "--refunds", str(refunds), "--out", str(out_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"legacy 退出码 {proc.returncode}\n{proc.stderr[-800:]}"


def run_reportbuild(orders: Path, refunds: Path, out_dir: Path) -> None:
    """subprocess 跑生产实现。"""
    proc = subprocess.run(
        [PY, "-m", "reportbuild", "--orders", str(orders), "--refunds", str(refunds), "--out", str(out_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"reportbuild 退出码 {proc.returncode}\n{proc.stderr[-800:]}"


def assert_dirs_identical(dir_a: Path, dir_b: Path) -> None:
    """断言两个输出目录的产物逐字节相同（等价于 ``diff -r`` 为空）。"""
    for name in ("monthly_region.csv", "summary.json"):
        blob_a = (dir_a / name).read_bytes()
        blob_b = (dir_b / name).read_bytes()
        assert blob_a == blob_b, f"{name} 不一致：{dir_a} vs {dir_b}"


@pytest.fixture(scope="session")
def big_data(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """20 万订单 + 2 万退款的大夹具（固定种子，整个 session 只生成一次）。"""
    data_dir = tmp_path_factory.mktemp("big_data")
    return generate_data(
        data_dir, orders=BIG_ORDERS, refunds=BIG_REFUNDS, seed=BIG_SEED, profile="normal"
    )
