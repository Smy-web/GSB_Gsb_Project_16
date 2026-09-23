"""月度结算报表 —— **当前生产实现**（待重构）。

这个包和 ``legacy/report_legacy.py`` 的业务逻辑完全相同，产出的
``monthly_region.csv`` 与 ``summary.json`` 必须与 legacy **逐字节一致**；
区别只在于它慢、吃内存、结构乱：

* 按 region **重复读订单文件**（有 N 个 region 就把文件读 N+1 遍）；
* 每笔退款都去 ``list.index()`` **线性扫全表**找归属；
* 有一段**无意义的双重循环**在重复统计已经算过的东西；
* 主流程是一个几百行的 god-function（:func:`pipeline.run`）；
* 有模块级可变状态、死代码、和 legacy 重复的一套解析函数。

行为口径（金额怎么舍入、坏行怎么处理、退款怎么归属、顺序怎么排）**以
``legacy/report_legacy.py`` 文件头的 docstring 为准**，重构时不许改口径，
只许改结构与性能。

用法::

    python3 -m reportbuild
    python3 -m reportbuild --orders O --refunds R --out DIR
"""

from __future__ import annotations

__version__ = "2.3.1"

__all__ = ["__version__"]