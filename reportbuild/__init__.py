"""月度结算报表 —— 当前生产实现（已重构为单遍流式分阶段流水线）。

这个包和 ``legacy/report_legacy.py`` 的业务逻辑完全相同，产出的
``monthly_region.csv`` 与 ``summary.json`` 与 legacy **逐字节一致**；
区别只在于结构与性能：加载 / 归一 / 分组 / 退款归属 / 落盘各是一个
命名清晰的函数（见 :mod:`reportbuild.pipeline`），无模块级可变状态。

行为口径（金额怎么舍入、坏行怎么处理、退款怎么归属、顺序怎么排）**以
``legacy/report_legacy.py`` 文件头的 docstring 为准**。

用法::

    python3 -m reportbuild
    python3 -m reportbuild --orders O --refunds R --out DIR
"""

from __future__ import annotations

__version__ = "2.3.1"

__all__ = ["__version__"]
