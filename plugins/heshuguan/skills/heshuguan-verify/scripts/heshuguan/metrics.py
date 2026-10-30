"""指标计算。

口径以 kb/1-财务指标口径说明.md 为唯一依据，任何与通用算法冲突的地方都以那份文件为准。
函数一律返回 Optional，算不出来就是 None，绝不返回 0 或编一个"合理"的数。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Period

# 现金流质量的三条线，对应知识库第三节的合理区间与红旗清单 F1 的阈值。
OCF_HEALTHY = 0.8  # 经营现金流/净利润 长期高于此值为健康
OCF_WARNING = 0.5  # 低于此值进入预警
OCF_RED_FLAG = 0.3  # 低于此值命中 F1


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """除法，除数为 None 或 0 时返回 None。

    这里不做任何兜底。分母为 0 说明口径本身无意义，返回 None 让上层标缺失，
    比返回一个 0 要有用得多。
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


@dataclass
class Growth:
    """增长率结果。overflow 记录基期为负这类需要提示读者的情形。"""

    value: float | None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


def growth_rate(current: float | None, base: float | None) -> Growth:
    """增长率，公式为（本期 − 基期）/ |基期|。

    基期为负时用绝对值作分母，并提示参考意义有限，这是知识库明确要求的处理。
    基期为 0 时增长率无意义，返回 None 并提示改用绝对增减额。
    """
    if current is None or base is None:
        return Growth(None, "数据缺失")
    if base == 0:
        return Growth(None, "基期为 0，增长率无意义，应改用绝对增减额描述")

    value = (current - base) / abs(base)
    if base < 0:
        return Growth(value, "基期为负，增长率参考意义有限")
    return Growth(value)


def gross_margin(p: Period) -> float | None:
    """毛利率 =（营业收入 − 营业成本）/ 营业收入。"""
    if p.revenue is None or p.cost_of_sales is None:
        return None
    return safe_div(p.revenue - p.cost_of_sales, p.revenue)


def net_margin(p: Period) -> float | None:
    """净利率 = 归母净利润 / 营业收入。"""
    return safe_div(p.net_profit, p.revenue)


def roe(current: Period, prior: Period | None) -> float | None:
    """ROE = 归母净利润 / 平均归母净资产。

    有上期数据时取期初期末平均，只有单期数据时退化为期末净资产并无法标注，
    这种情况由调用方在底稿里说明口径。
    """
    if current.net_profit is None or current.equity is None:
        return None
    if prior is not None and prior.equity is not None:
        average_equity = (prior.equity + current.equity) / 2
    else:
        average_equity = current.equity
    return safe_div(current.net_profit, average_equity)


def debt_ratio(p: Period) -> float | None:
    """资产负债率。"""
    return safe_div(p.total_liabilities, p.total_assets)


def ocf_ratio(p: Period) -> float | None:
    """经营现金流 / 净利润。衡量利润的含金量。"""
    if p.net_profit is None or p.operating_cash_flow is None:
        return None
    if p.net_profit <= 0:
        # 亏损时这个比值没有解释意义，方向都反了
        return None
    return safe_div(p.operating_cash_flow, p.net_profit)


def free_cash_flow(p: Period) -> float | None:
    """简化口径自由现金流 = 经营现金流净额 − 购建长期资产支付的现金。"""
    if p.operating_cash_flow is None or p.capex is None:
        return None
    return p.operating_cash_flow - p.capex


def deducted_profit_ratio(p: Period) -> float | None:
    """扣非净利润 / 归母净利润。低于 1 说明利润里有非经常性损益撑场。"""
    if p.net_profit_deducted is None or p.net_profit is None:
        return None
    if p.net_profit <= 0:
        return None
    return safe_div(p.net_profit_deducted, p.net_profit)


def receivable_ratio(p: Period) -> float | None:
    """应收账款 / 营业收入。看收入有没有变成白条。"""
    return safe_div(p.accounts_receivable, p.revenue)


def cash_to_debt(p: Period) -> float | None:
    """货币资金 / 有息负债。用于识别存贷双高。"""
    if p.monetary_funds is None or p.interest_bearing_debt is None:
        return None
    if p.interest_bearing_debt <= 0:
        return None
    return safe_div(p.monetary_funds, p.interest_bearing_debt)


def cip_to_fixed_assets(p: Period) -> float | None:
    """在建工程 / 固定资产。用来看在建工程是不是长期挂账不转固。"""
    return safe_div(p.construction_in_progress, p.fixed_assets)


def depreciation_rate(p: Period, prior: Period | None) -> float | None:
    """折旧 / 期初固定资产。与同行业水平偏离过大说明折旧政策或资产规模有问题。"""
    if p.depreciation is None or p.fixed_assets is None:
        return None
    base = prior.fixed_assets if prior is not None and prior.fixed_assets else p.fixed_assets
    if base is None or base <= 0:
        return None
    return safe_div(p.depreciation, base)


def format_money(value: float | None, unit: str = "万元") -> str:
    """金额格式化。万元超过一万时自动换算成亿元，读起来省事。"""
    if value is None:
        return "缺失"
    if abs(value) >= 10000:
        return f"{value / 10000:.2f} 亿元"
    return f"{value:.2f} {unit}"


def format_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "缺失"
    return f"{value * 100:.{digits}f}%"
