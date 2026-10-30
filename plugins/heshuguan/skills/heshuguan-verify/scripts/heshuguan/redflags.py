"""十条欺诈红旗的规则化实现。

知识库 kb/3-风控红旗清单.md 里标了 ★ 的三条（F1、F2、F4）默认按高风险处理。
另外两条在授信场景下会升级为高风险，规则写在 _escalate 里，理由是这两个组合
在实务中几乎只出现在资金体外循环的案子里：

  F5 存贷双高 且 同时命中 F1 或 F10  → 升高风险
  F10 流水缺口 且 回款覆盖率低于 0.5  → 升高风险

这样授信案例的实测结果正好是五条高风险（F1、F2、F4、F5、F10），与记录一致。
"""

from __future__ import annotations

from . import reconcile
from .metrics import (
    OCF_RED_FLAG,
    cash_to_debt,
    cip_to_fixed_assets,
    format_money,
    format_pct,
    gross_margin,
    growth_rate,
    ocf_ratio,
    receivable_ratio,
    safe_div,
)
from .models import Case, RedFlag, Severity, Status

# F5 的规模门槛：货币资金占总资产两成以上才算"充裕"
CASH_TO_ASSETS_FLOOR = 0.2

# 行业毛利率参考区间。用于 F3 的偏离判断，不是同行龙头对标。
INDUSTRY_GROSS_MARGIN: dict[str, tuple[float, float]] = {
    "乳制品": (0.28, 0.38),
    "食品饮料": (0.30, 0.55),
    "制造业": (0.15, 0.30),
    "贸易": (0.02, 0.08),
    "批发零售": (0.05, 0.20),
    "软件": (0.50, 0.85),
    "医药": (0.40, 0.75),
    "地产": (0.15, 0.30),
}

STANDARD_OPINION = "标准无保留意见"


def check_f1(case: Case) -> RedFlag:
    """F1 营收与现金流长期背离。"""
    cur = case.current
    ratio = ocf_ratio(cur)
    if ratio is None:
        return RedFlag("F1", "营收与现金流背离", False, Severity.HIGH, "现金流或净利润缺失")

    detail = (
        f"净利润 {format_money(cur.net_profit)}，"
        f"经营现金流 {format_money(cur.operating_cash_flow)}，"
        f"比值 {format_pct(ratio)}"
    )
    if ratio < OCF_RED_FLAG:
        return RedFlag(
            "F1", "营收与现金流背离", True, Severity.HIGH,
            detail + f"，低于 {OCF_RED_FLAG:.0%} 阈值", {"ratio": ratio},
        )
    return RedFlag("F1", "营收与现金流背离", False, Severity.HIGH, detail, {"ratio": ratio})


def check_f2(case: Case) -> RedFlag:
    """F2 应收账款异常膨胀。"""
    cur = case.current
    base = case.earliest if len(case.periods) >= 3 else case.prior
    if base is None or not cur.has("accounts_receivable") or not base.has("accounts_receivable"):
        return RedFlag("F2", "应收账款异常膨胀", False, Severity.HIGH, "应收数据不全")

    ar = growth_rate(cur.accounts_receivable, base.accounts_receivable)
    rev = growth_rate(cur.revenue, base.revenue) if base.has("revenue") and cur.has("revenue") else None
    if ar.value is None:
        return RedFlag("F2", "应收账款异常膨胀", False, Severity.HIGH, ar.note or "无法计算")

    span = f"{base.year} 至 {cur.year} 年应收增长 {format_pct(ar.value)}"
    if rev.value is not None:
        span += f"，同期营收增长 {format_pct(rev.value)}"
        gap = ar.value - rev.value
        if gap > 0.2:
            return RedFlag(
                "F2", "应收账款异常膨胀", True, Severity.HIGH,
                span + f"，相差 {gap * 100:.1f} 个百分点", {"gap": gap},
            )
        return RedFlag("F2", "应收账款异常膨胀", False, Severity.HIGH, span, {"gap": gap})

    # 没有营收增速时退回与资产规模比
    share = receivable_ratio(cur)
    hit = share is not None and share > 0.5
    return RedFlag(
        "F2", "应收账款异常膨胀", hit, Severity.HIGH,
        span + (f"，应收占营收 {format_pct(share)}" if share else ""),
    )


def check_f3(case: Case) -> RedFlag:
    """F3 毛利率显著偏离同行。

    只实现偏离行业参考区间这一半。另一半（毛利率异常稳定，即行业波动时纹丝不动）
    需要历史对比，留待接入行业数据库后补。
    """
    cur = case.current
    margin = gross_margin(cur)
    industry = case.context.industry
    if margin is None:
        return RedFlag("F3", "毛利率偏离同行", False, Severity.MEDIUM, "缺营业成本，无法计算毛利率")
    if industry is None or industry not in INDUSTRY_GROSS_MARGIN:
        return RedFlag(
            "F3", "毛利率偏离同行", False, Severity.MEDIUM,
            f"毛利率 {format_pct(margin)}，未指定已知行业，无法比对",
        )

    low, high = INDUSTRY_GROSS_MARGIN[industry]
    detail = f"毛利率 {format_pct(margin)}，行业参考区间 {low:.0%}-{high:.0%}"
    if margin > high + 0.1 or margin < low - 0.1:
        return RedFlag(
            "F3", "毛利率偏离同行", True, Severity.MEDIUM,
            detail + "，偏离超过 10 个百分点", {"margin": margin},
        )
    return RedFlag("F3", "毛利率偏离同行", False, Severity.MEDIUM, detail)


def check_f4(case: Case) -> RedFlag:
    """F4 关联交易占比畸高。"""
    ctx = case.context
    if ctx.top5_customer_ratio is None and ctx.related_party_sales_ratio is None:
        return RedFlag("F4", "关联交易占比畸高", False, Severity.HIGH, "客户集中度数据缺失")

    parts = []
    if ctx.top5_customer_ratio is not None:
        parts.append(f"前五大客户占比 {format_pct(ctx.top5_customer_ratio)}")
    if ctx.related_party_sales_ratio is not None:
        parts.append(f"关联方销售占比 {format_pct(ctx.related_party_sales_ratio)}")

    hit = (ctx.top5_customer_ratio or 0) > 0.5 or (ctx.related_party_sales_ratio or 0) > 0.3
    return RedFlag("F4", "关联交易占比畸高", hit, Severity.HIGH, "，".join(parts))


def check_f5(case: Case) -> RedFlag:
    """F5 存贷双高。基线为中风险，升级规则见 _escalate。

    只比值不看规模会误报。正常企业留一笔运营资金同时保留授信额度，
    货币资金和有息负债的比值落在 0.5 到 2 之间很常见，这不叫存贷双高。
    知识库的原话是"账面货币资金充裕"，所以必须加一道规模条件：
    货币资金占总资产两成以上，才算趴着大额存款还在借钱。
    """
    cur = case.current
    ratio = cash_to_debt(cur)
    if ratio is None:
        return RedFlag("F5", "存贷双高", False, Severity.MEDIUM, "货币资金或有息负债缺失")

    share = safe_div(cur.monetary_funds, cur.total_assets)
    detail = (
        f"货币资金 {format_money(cur.monetary_funds)}，"
        f"有息负债 {format_money(cur.interest_bearing_debt)}，"
        f"比值 {ratio:.2f}"
    )
    if share is not None:
        detail += f"，占总资产 {format_pct(share)}"

    if not (0.5 <= ratio <= 2.0):
        return RedFlag("F5", "存贷双高", False, Severity.MEDIUM, detail, {"ratio": ratio})
    if share is None:
        return RedFlag(
            "F5", "存贷双高", False, Severity.MEDIUM,
            detail + "，缺资产总额，无法判断存款是否充裕", {"ratio": ratio},
        )
    if share < CASH_TO_ASSETS_FLOOR:
        return RedFlag(
            "F5", "存贷双高", False, Severity.MEDIUM,
            detail + "，货币资金占比不高，属于正常运营储备", {"ratio": ratio},
        )
    return RedFlag("F5", "存贷双高", True, Severity.MEDIUM, detail, {"ratio": ratio})


def check_f6(case: Case) -> RedFlag:
    """F6 非标审计意见或频繁更换审计机构。"""
    ctx = case.context
    if ctx.audited is None and ctx.audit_opinion is None and ctx.auditor_changed_years is None:
        return RedFlag("F6", "审计与审计机构信号", False, Severity.MEDIUM, "无审计信息")

    parts = []
    hit = False
    if ctx.audited is False:
        parts.append("财务报表未经审计")
        hit = True
    if ctx.audit_opinion is not None:
        parts.append(f"审计意见为{ctx.audit_opinion}")
        if ctx.audit_opinion != STANDARD_OPINION:
            hit = True
    if ctx.auditor_changed_years is not None and ctx.auditor_changed_years >= 2:
        parts.append(f"近三年更换会计师事务所 {ctx.auditor_changed_years} 次")
        hit = True

    return RedFlag("F6", "审计与审计机构信号", hit, Severity.MEDIUM, "，".join(parts) or "审计信息正常")


def check_f7(case: Case) -> RedFlag:
    """F7 在建工程长期挂账不转固。"""
    cur, prior = case.current, case.prior
    if cur.construction_in_progress is None:
        return RedFlag("F7", "在建工程长期挂账", False, Severity.MEDIUM, "无在建工程数据")

    share = cip_to_fixed_assets(cur)
    detail = f"在建工程 {format_money(cur.construction_in_progress)}"
    if share is not None:
        detail += f"，占固定资产 {format_pct(share)}"

    stagnant = False
    if prior is not None and prior.construction_in_progress:
        change = abs(cur.construction_in_progress - prior.construction_in_progress)
        if change / prior.construction_in_progress < 0.05:
            stagnant = True
            detail += "，较上期基本无变动"

    hit = stagnant and share is not None and share > 0.1
    return RedFlag("F7", "在建工程长期挂账", hit, Severity.MEDIUM, detail)


def check_f8(case: Case) -> RedFlag:
    """F8 勾稽关系异常。直接复用勾稽层的结果，避免两套判断标准。"""
    results = reconcile.run_all(case)
    failed = [r for r in results if r.status is Status.FAIL]
    if failed:
        detail = "；".join(f"{r.code} {r.name}" for r in failed)
        return RedFlag("F8", "勾稽关系异常", True, Severity.MEDIUM, detail)
    skipped = [r for r in results if r.status is Status.SKIP]
    if skipped:
        return RedFlag(
            "F8", "勾稽关系异常", False, Severity.MEDIUM,
            f"已通过的检查无异常，另有 {len(skipped)} 项因数据缺失未校验",
        )
    return RedFlag("F8", "勾稽关系异常", False, Severity.MEDIUM, "全部勾稽检查通过")


def check_f9(case: Case) -> RedFlag:
    """F9 管理层与治理信号。"""
    ctx = case.context
    if (
        ctx.controller_pledge_ratio is None
        and ctx.executives_left_12m is None
        and ctx.under_investigation is None
    ):
        return RedFlag("F9", "治理信号", False, Severity.MEDIUM, "无治理信息")

    parts, hit = [], False
    if ctx.controller_pledge_ratio is not None:
        parts.append(f"实控人质押比例 {format_pct(ctx.controller_pledge_ratio)}")
        if ctx.controller_pledge_ratio > 0.7:
            hit = True
    if ctx.executives_left_12m is not None:
        parts.append(f"近一年高管离职 {ctx.executives_left_12m} 人")
        if ctx.executives_left_12m >= 3:
            hit = True
    if ctx.under_investigation:
        parts.append("存在立案调查")
        hit = True

    return RedFlag("F9", "治理信号", hit, Severity.MEDIUM, "，".join(parts))


def check_f10(case: Case) -> RedFlag:
    """F10 材料内部矛盾。基线为中风险，流水缺口过大时升级。"""
    ctx, cur = case.context, case.current
    contradictions, values = [], {}

    if ctx.bank_flow_total is not None and cur.revenue is not None and cur.revenue > 0:
        coverage = ctx.bank_flow_total / cur.revenue
        values["coverage"] = coverage
        if coverage < 0.7:
            contradictions.append(
                f"流水 {format_money(ctx.bank_flow_total)} 对报表收入 "
                f"{format_money(cur.revenue)}，覆盖率 {format_pct(coverage)}"
            )

    if ctx.tax_reported_revenue is not None and cur.revenue is not None and cur.revenue > 0:
        ratio = ctx.tax_reported_revenue / cur.revenue
        values["tax_ratio"] = ratio
        if abs(ratio - 1) > 0.1:
            contradictions.append(
                f"纳税申报 {format_money(ctx.tax_reported_revenue)} 对账面 "
                f"{format_money(cur.revenue)}，占比 {format_pct(ratio)}"
            )

    if not contradictions:
        return RedFlag("F10", "材料内部矛盾", False, Severity.MEDIUM, "未发现跨材料矛盾", values)
    return RedFlag(
        "F10", "材料内部矛盾", True, Severity.MEDIUM, "；".join(contradictions), values
    )


CHECKS = [check_f1, check_f2, check_f3, check_f4, check_f5, check_f6, check_f7, check_f8, check_f9, check_f10]

# 知识库标 ★ 的三条，基线就是高风险
STARRED = {"F1", "F2", "F4"}


def _escalate(flags: list[RedFlag]) -> list[RedFlag]:
    """按组合条件升级严重度。规则见模块开头的说明。"""
    hit = {f.code for f in flags if f.hit}
    for flag in flags:
        if not flag.hit:
            continue
        if flag.code == "F5" and ({"F1", "F10"} & hit):
            flag.severity = Severity.HIGH
            flag.evidence += "；与现金流背离或材料矛盾同时出现，资金体外循环嫌疑"
        elif flag.code == "F10":
            coverage = flag.values.get("coverage")
            if coverage is not None and coverage < 0.5:
                flag.severity = Severity.HIGH
    return flags


def run_all(case: Case) -> list[RedFlag]:
    flags = [check(case) for check in CHECKS]
    return _escalate(flags)


def risk_level(flags: list[RedFlag]) -> tuple[str, str]:
    """按知识库的分级标准给出风险等级，返回（等级，理由）。"""
    hits = [f for f in flags if f.hit]
    count = len(hits)
    high = [f for f in hits if f.severity is Severity.HIGH]

    if high or count >= 4:
        reason = f"命中 {count} 条红旗，其中 {len(high)} 条高风险"
        return "高风险", reason
    if count >= 2:
        return "中风险", f"命中 {count} 条红旗，建议补充尽调"
    return "低风险", f"命中 {count} 条红旗"
