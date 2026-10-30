"""核数官核验引擎。

把"交叉核验"从模型的一句话变成一段可执行的判断：
指标怎么算、勾稽怎么对、红旗怎么判，全部按 kb/ 里的口径固定下来，
同一个输入任何时候都得到同一个结论。

典型用法：
    from heshuguan import Case, to_markdown
    case = Case.from_dict(payload)
    print(to_markdown(case))
"""

from __future__ import annotations

from .models import Case, CheckResult, Context, Period, RedFlag, Severity, Status
from .worksheet import render, to_markdown

__version__ = "1.0.0"

__all__ = [
    "Case",
    "Context",
    "Period",
    "CheckResult",
    "RedFlag",
    "Severity",
    "Status",
    "render",
    "to_markdown",
    "__version__",
]
