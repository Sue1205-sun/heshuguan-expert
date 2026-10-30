"""勾稽校验。

这一层不依赖任何外部数据，只用报表内部的等式关系判断数字能不能自圆其说。
凡是不成立的，不需要行业经验也能断定有问题，所以它是整个核验里最硬的部分。

检查分两组：
  通用四项 R1-R4  任何报表都能跑，对应作品文档里"勾稽检查四项通过"
  授信三项 R5-R7  需要银行流水、纳税申报等材料外数据才能跑

容差说明：报表以万元为单位、经过四舍五入，直接判等会误报。
平衡表与利润勾稽统一按资产总额的千分之一、且不低于 1 万元作为容差。
"""

from __future__ import annotations

from .metrics import (
    Growth,
    depreciation_rate,
    format_money,
    format_pct,
    growth_rate,
    safe_div,
)
from .models import Case, CheckResult, Period, Status

# 回款覆盖率低于此值视为收入与现金不匹配
RECEIPT_COVERAGE_FLOOR = 0.7

# 折旧率的合理范围。低于下限说明折旧提得不够，资产可能虚高；高于上限说明年限过短。
DEPRECIATION_FLOOR = 0.02
DEPRECIATION_CEILING = 0.25


def _tolerance(scale: float | None) -> float:
    if scale is None:
        return 1.0
    return max(1.0, abs(scale) * 0.001)


def check_balance_sheet(p: Period) -> CheckResult:
    """R1 资产 = 负债 + 所有者权益。"""
    code, name = "R1", "资产负债表平衡"
    if not p.has("total_assets", "total_liabilities", "equity"):
        return CheckResult(code, name, Status.SKIP, "资产、负债或权益科目缺失，无法校验")

    right = p.total_liabilities + p.equity
    diff = p.total_assets - right
    tol = _tolerance(p.total_assets)
    detail = (
        f"资产 {format_money(p.total_assets)}，"
        f"负债加权益 {format_money(right)}，"
        f"差额 {format_money(diff)}"
    )
    if abs(diff) <= tol:
        return CheckResult(code, name, Status.PASS, detail, {"diff": diff})
    return CheckResult(
        code, name, Status.FAIL, detail + "，超出容差，报表本身不平", {"diff": diff}
    )


def check_profit_tie_out(p: Period) -> CheckResult:
    """R2 营业利润 + 营业外收入 − 营业外支出 = 利润总额。"""
    code, name = "R2", "利润总额勾稽"
    if not p.has("operating_profit", "total_profit"):
        return CheckResult(code, name, Status.SKIP, "营业利润或利润总额缺失，无法校验")

    non_op_income = p.non_operating_income or 0.0
    non_op_expense = p.non_operating_expense or 0.0
    computed = p.operating_profit + non_op_income - non_op_expense
    diff = computed - p.total_profit
    tol = _tolerance(p.total_profit)

    detail = (
        f"营业利润 {format_money(p.operating_profit)}，"
        f"加减营业外收支后 {format_money(computed)}，"
        f"利润总额 {format_money(p.total_profit)}，差额 {format_money(diff)}"
    )
    if abs(diff) <= tol:
        return CheckResult(code, name, Status.PASS, detail, {"diff": diff})
    return CheckResult(code, name, Status.FAIL, detail, {"diff": diff})


def check_cash_profit_direction(case: Case) -> CheckResult:
    """R3 净利润与经营现金流的方向是否一致。

    查的是方向而不是金额。净利润为正而现金流为负，说明利润没变成钱，
    方向已经反了，不需要再看比值。
    """
    code, name = "R3", "净利润与经营现金流方向"
    blanks = [
        f"{p.year} 年" + "、".join(p.missing("net_profit", "operating_cash_flow"))
        for p in case.periods
        if not p.has("net_profit", "operating_cash_flow")
    ]
    if blanks:
        return CheckResult(code, name, Status.SKIP, "缺失：" + "；".join(blanks))

    diverged = [
        p.year for p in case.periods if (p.net_profit > 0) != (p.operating_cash_flow > 0)
    ]
    pairs = "，".join(
        f"{p.year} 年净利 {format_money(p.net_profit)}／现金流 "
        f"{format_money(p.operating_cash_flow)}"
        for p in case.periods
    )
    if not diverged:
        return CheckResult(code, name, Status.PASS, pairs, {"diverged_years": []})
    return CheckResult(
        code,
        name,
        Status.FAIL,
        pairs + f"；{'、'.join(str(y) for y in diverged)} 年方向相反",
        {"diverged_years": diverged},
    )


def check_depreciation(case: Case) -> CheckResult:
    """R4 折旧摊销与固定资产规模是否匹配。

    折旧率异常低通常意味着折旧提不足，固定资产账面价值虚高，
    利润也就跟着虚高。这一条是 F8 里最容易被忽略的一项。
    """
    code, name = "R4", "折旧与固定资产匹配"
    cur, prior = case.current, case.prior
    rate = depreciation_rate(cur, prior)
    if rate is None:
        return CheckResult(code, name, Status.SKIP, "缺折旧或固定资产数据")

    base = prior.fixed_assets if prior is not None and prior.fixed_assets else cur.fixed_assets
    detail = (
        f"折旧 {format_money(cur.depreciation)}，"
        f"期初固定资产 {format_money(base)}，"
        f"折旧率 {format_pct(rate)}"
    )
    if rate < DEPRECIATION_FLOOR:
        return CheckResult(
            code, name, Status.FAIL, detail + "，低于一般水平，折旧可能计提不足", {"rate": rate}
        )
    if rate > DEPRECIATION_CEILING:
        return CheckResult(
            code, name, Status.FAIL, detail + "，高于一般水平，需核实折旧年限", {"rate": rate}
        )
    return CheckResult(code, name, Status.PASS, detail, {"rate": rate})


def check_receipt_coverage(case: Case) -> CheckResult:
    """R5 银行流水与报表收入的匹配度。授信场景必查。"""
    code, name = "R5", "收入与回款匹配"
    ctx, cur = case.context, case.current
    if ctx.bank_flow_total is None or cur.revenue is None:
        return CheckResult(
            code, name, Status.SKIP, "缺银行流水或营业收入，应要求补充完整流水"
        )

    coverage = safe_div(ctx.bank_flow_total, cur.revenue)
    gap = cur.revenue - ctx.bank_flow_total
    detail = (
        f"流水 {format_money(ctx.bank_flow_total)}，"
        f"报表收入 {format_money(cur.revenue)}，"
        f"覆盖率 {format_pct(coverage)}，缺口 {format_money(gap)}"
    )
    if coverage is not None and coverage >= RECEIPT_COVERAGE_FLOOR:
        return CheckResult(code, name, Status.PASS, detail, {"coverage": coverage})
    return CheckResult(
        code, name, Status.FAIL, detail + "，回款不足以支撑报表收入", {"coverage": coverage}
    )


def check_tax_vs_book(case: Case) -> CheckResult:
    """R6 纳税申报收入与账面收入。差距过大意味着要么少缴税，要么虚增收入。"""
    code, name = "R6", "纳税申报与账面收入"
    ctx, cur = case.context, case.current
    if ctx.tax_reported_revenue is None or cur.revenue is None:
        return CheckResult(code, name, Status.SKIP, "缺纳税申报数据，无法比对")

    ratio = safe_div(ctx.tax_reported_revenue, cur.revenue)
    diff = cur.revenue - ctx.tax_reported_revenue
    detail = (
        f"申报 {format_money(ctx.tax_reported_revenue)}，"
        f"账面 {format_money(cur.revenue)}，"
        f"占比 {format_pct(ratio)}，差额 {format_money(diff)}"
    )
    if ratio is not None and abs(ratio - 1) <= 0.1:
        return CheckResult(code, name, Status.PASS, detail, {"ratio": ratio})
    return CheckResult(code, name, Status.FAIL, detail + "，差异超过一成", {"ratio": ratio})


def check_receivable_growth(case: Case) -> CheckResult:
    """R7 应收账款增速与营业收入增速是否同步。"""
    code, name = "R7", "应收账款增速与营收增速"
    cur = case.current
    if not cur.has("accounts_receivable", "revenue"):
        return CheckResult(code, name, Status.SKIP, "缺应收账款或营业收入")

    # 三年以上用最早一年做基期看长期趋势，两年数据用上一年
    base = case.earliest if len(case.periods) >= 3 else case.prior
    if base is None or not base.has("accounts_receivable", "revenue"):
        return CheckResult(code, name, Status.SKIP, "基期数据不全，无法计算增速")

    ar: Growth = growth_rate(cur.accounts_receivable, base.accounts_receivable)
    rev: Growth = growth_rate(cur.revenue, base.revenue)
    if ar.value is None or rev.value is None:
        return CheckResult(code, name, Status.SKIP, f"增速无法计算：{ar.note or rev.note}")

    gap = ar.value - rev.value
    detail = (
        f"应收 {format_pct(ar.value)}，营收 {format_pct(rev.value)}，"
        f"相差 {gap * 100:.2f} 个百分点（{base.year} 至 {cur.year}）"
    )
    if gap <= 0.2:
        return CheckResult(code, name, Status.PASS, detail, {"gap": gap})
    return CheckResult(
        code, name, Status.FAIL, detail + "，应收增速明显超出营收", {"gap": gap}
    )


def run_common(case: Case) -> list[CheckResult]:
    """通用四项。投研场景跑这四条，与文档中"勾稽四项"对应。"""
    return [
        check_balance_sheet(case.current),
        check_profit_tie_out(case.current),
        check_cash_profit_direction(case),
        check_depreciation(case),
    ]


def run_credit(case: Case) -> list[CheckResult]:
    """授信三项。只有拿到流水、纳税申报等材料外数据才有意义。"""
    return [
        check_receipt_coverage(case),
        check_tax_vs_book(case),
        check_receivable_growth(case),
    ]


def run_all(case: Case) -> list[CheckResult]:
    """全部七项，顺序固定，方便底稿逐条对照。"""
    return run_common(case) + run_credit(case)
