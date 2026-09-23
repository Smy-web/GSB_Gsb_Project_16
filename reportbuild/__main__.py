"""``python3 -m reportbuild`` 的入口。"""

from __future__ import annotations

import sys

from .pipeline import build_parser, run

__all__ = ["main"]


def main(argv=None) -> int:
    """跑一次完整报表，返回进程退出码。"""
    args = build_parser().parse_args(argv)

    summary = run(args.orders, args.refunds, args.out)

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
    sys.exit(main())