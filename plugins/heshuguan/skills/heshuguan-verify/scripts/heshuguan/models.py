"""核数官核验引擎的数据结构。

设计原则：所有财务字段一律可选。缺失就是缺失，不允许在计算里被当成 0，
也不允许用行业均值或同期数据推算补齐。凡是依赖缺失字段的检查，
结果必须是 SKIP 并注明原因，而不是 PASS。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """核对结果。三态之外没有第四种可能。"""

    PASS = "一致"
    FAIL = "存疑"
    SKIP = "缺失"

    def __str__(self) -> str:  # 便于直接写进 markdown 表格
        return self.value


class Severity(str, Enum):
    HIGH = "高风险"
    MEDIUM = "中风险"
    LOW = "低风险"

    def __str__(self) -> str:
        return self.value


@dataclass
class Period:
    """单一年度的财务数据。单位统一为万元，比率为小数（0.35 表示 35%）。"""

    year: int

    # 利润表
    revenue: float | None = None
    cost_of_sales: float | None = None
    net_profit: float | None = None
    net_profit_deducted: float | None = None  # 扣除非经常性损益后的净利润
    operating_profit: float | None = None
    non_operating_income: float | None = None
    non_operating_expense: float | None = None
    total_profit: float | None = None

    # 现金流量表
    operating_cash_flow: float | None = None
    capex: float | None = None

    # 资产负债表
    total_assets: float | None = None
    total_liabilities: float | None = None
    equity: float | None = None
    accounts_receivable: float | None = None
    inventory: float | None = None
    monetary_funds: float | None = None
    interest_bearing_debt: float | None = None
    construction_in_progress: float | None = None
    fixed_assets: float | None = None
    depreciation: float | None = None

    def has(self, *names: str) -> bool:
        """检查一组字段是否都有值。用于决定某条检查能不能跑。"""
        return all(getattr(self, n, None) is not None for n in names)

    def missing(self, *names: str) -> list[str]:
        return [n for n in names if getattr(self, n, None) is None]


@dataclass
class Context:
    """报表之外的辅助材料。授信场景下这些比报表更能说明问题。"""

    company: str = ""
    industry: str | None = None
    audited: bool | None = None
    audit_opinion: str | None = None
    auditor_changed_years: int | None = None

    # 授信材料特有：报表之外的另一套数字
    bank_flow_total: float | None = None  # 银行流水合计
    tax_reported_revenue: float | None = None  # 纳税申报收入

    # 客户与关联方
    top5_customer_ratio: float | None = None
    related_party_sales_ratio: float | None = None

    # 治理信号
    controller_pledge_ratio: float | None = None
    executives_left_12m: int | None = None
    under_investigation: bool | None = None

    def has(self, *names: str) -> bool:
        return all(getattr(self, n, None) is not None for n in names)


@dataclass
class CheckResult:
    """一条核验项的结论。code 用来对应知识库里的清单编号。"""

    code: str
    name: str
    status: Status
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, str]:
        return {
            "编号": self.code,
            "检查项": self.name,
            "结果": str(self.status),
            "说明": self.detail,
        }


@dataclass
class RedFlag:
    """一条命中的红旗。hit 为 False 时不进入输出，但保留判定过程便于复核。"""

    code: str
    name: str
    hit: bool
    severity: Severity
    evidence: str
    values: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, str]:
        return {
            "编号": self.code,
            "信号": self.name,
            "等级": str(self.severity),
            "触发依据": self.evidence,
        }


@dataclass
class Case:
    """一个完整的待核验对象。periods 按年份升序，最后一年是当期。"""

    context: Context
    periods: list[Period]

    def __post_init__(self) -> None:
        if not self.periods:
            raise ValueError("至少需要一个年度的数据")
        self.periods.sort(key=lambda p: p.year)

    @property
    def current(self) -> Period:
        return self.periods[-1]

    @property
    def prior(self) -> Period | None:
        return self.periods[-2] if len(self.periods) >= 2 else None

    @property
    def earliest(self) -> Period:
        return self.periods[0]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Case":
        ctx_raw = payload.get("context", {}) or {}
        ctx = Context(**{k: v for k, v in ctx_raw.items() if k in Context.__annotations__})
        periods = []
        for item in payload.get("periods", []):
            filtered = {k: v for k, v in item.items() if k in Period.__annotations__}
            periods.append(Period(**filtered))
        return cls(context=ctx, periods=periods)
