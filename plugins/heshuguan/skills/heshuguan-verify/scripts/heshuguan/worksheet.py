"""核验底稿生成。

底稿的作用不是让报告好看，是让没核实的东西藏不住。每一项都显式标出
一致、存疑还是缺失，读者不需要读完全文就知道哪些数字能信。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import redflags, reconcile
from .metrics import (
    debt_ratio,
    deducted_profit_ratio,
    format_money,
    format_pct,
    gross_margin,
    growth_rate,
    net_margin,
    ocf_ratio,
    receivable_ratio,
    roe,
)
from .models import Case, Status

FIELD_NAMES = {
    "revenue": "营业收入",
    "cost_of_sales": "营业成本",
    "net_profit": "归母净利润",
    "net_profit_deducted": "扣非净利润",
    "operating_profit": "营业利润",
    "non_operating_income": "营业外收入",
    "non_operating_expense": "营业外支出",
    "total_profit": "利润总额",
    "operating_cash_flow": "经营现金流",
    "capex": "购建长期资产支出",
    "total_assets": "资产总额",
    "total_liabilities": "负债总额",
    "equity": "所有者权益",
    "accounts_receivable": "应收账款",
    "inventory": "存货",
    "monetary_funds": "货币资金",
    "interest_bearing_debt": "有息负债",
    "construction_in_progress": "在建工程",
    "fixed_assets": "固定资产",
    "depreciation": "折旧摊销",
}

# 报表之外的材料。缺了这些不是"没影响"，而是对应的红旗根本判不了。
CONTEXT_NAMES = {
    "bank_flow_total": "银行流水合计",
    "tax_reported_revenue": "纳税申报收入",
    "top5_customer_ratio": "前五大客户占比",
    "related_party_sales_ratio": "关联方销售占比",
    "audit_opinion": "审计意见",
    "controller_pledge_ratio": "实控人质押比例",
    "executives_left_12m": "近一年高管离职情况",
    "under_investigation": "立案调查情况",
}


def _metric_table(case: Case) -> str:
    """三年指标对比表。每行一个指标，每列一年。"""
    years = [p.year for p in case.periods]
    head = "| 指标 | " + " | ".join(f"{y} 年" for y in years) + " | 判读 |"
    sep = "|" + "---|" * (len(years) + 2)

    rows: list[str] = []
    cur, prior = case.current, case.prior

    def add(label: str, values: list[str], note: str = "") -> None:
        rows.append("| " + label + " | " + " | ".join(values) + f" | {note} |")

    add("营业收入", [format_money(p.revenue) for p in case.periods])
    add("归母净利润", [format_money(p.net_profit) for p in case.periods])
    add("扣非净利润", [format_money(p.net_profit_deducted) for p in case.periods])
    add("毛利率", [format_pct(gross_margin(p)) for p in case.periods])
    add("净利率", [format_pct(net_margin(p)) for p in case.periods])
    add(
        "ROE",
        [
            format_pct(roe(p, case.periods[i - 1] if i > 0 else None))
            for i, p in enumerate(case.periods)
        ],
        "平均净资产口径",
    )
    add("经营现金流", [format_money(p.operating_cash_flow) for p in case.periods])

    ocf = ocf_ratio(cur)
    ocf_note = ""
    if ocf is not None:
        if ocf < 0.3:
            ocf_note = "利润含金量差"
        elif ocf < 0.5:
            ocf_note = "低于预警线"
        elif ocf < 0.8:
            ocf_note = "尚可"
        else:
            ocf_note = "健康"
    add("经营现金流/净利润", [format_pct(ocf_ratio(p)) for p in case.periods], ocf_note)

    add("资产负债率", [format_pct(debt_ratio(p)) for p in case.periods])
    add("应收占营收", [format_pct(receivable_ratio(p)) for p in case.periods])

    ded = deducted_profit_ratio(cur)
    add(
        "扣非/归母",
        [format_pct(deducted_profit_ratio(p)) for p in case.periods],
        "低于 1 说明利润含非经常性损益" if ded is not None and ded < 1 else "",
    )

    # 增长率单独一块，因为基期为负时需要额外提示
    if prior is not None:
        for label, growth in (
            ("营收同比", growth_rate(cur.revenue, prior.revenue)),
            ("净利同比", growth_rate(cur.net_profit, prior.net_profit)),
        ):
            values = ["—"] * len(years)
            values[-1] = format_pct(growth.value)
            add(label, values, growth.note or "—")

    return "\n".join([head, sep] + rows)


def _reconcile_table(results: list) -> str:
    head = "| 编号 | 检查项 | 结果 | 说明 |"
    sep = "|---|---|---|---|"
    rows = [
        f"| {r.code} | {r.name} | {r.status} | {r.detail} |" for r in results
    ]
    return "\n".join([head, sep] + rows)


def _flag_table(flags: list) -> str:
    hits = [f for f in flags if f.hit]
    if not hits:
        return "未命中任何红旗。"
    head = "| 编号 | 信号 | 等级 | 触发依据 |"
    sep = "|---|---|---|---|"
    rows = [
        f"| {f.code} | {f.name} | {f.severity} | {f.evidence} |" for f in hits
    ]
    return "\n".join([head, sep] + rows)


def _missing_items(case: Case) -> list[str]:
    """列出缺失字段。这是底稿里最容易被忽略、但最该有的一块。

    分两段：报表内的科目，以及报表之外的材料。
    后者常被当成"没有就算了"，实际上缺了流水和纳税数据，F10 和 R6 根本判不了，
    必须让读者知道哪几条结论是因为缺数据才没下。
    """
    items: list[str] = []
    for p in case.periods:
        blanks = [
            FIELD_NAMES.get(name, name)
            for name in Period_fields()
            if getattr(p, name, None) is None
        ]
        if blanks:
            items.append(f"{p.year} 年报表缺：{'、'.join(blanks)}")

    ctx = case.context
    ctx_blanks = []
    for field_name, label in CONTEXT_NAMES.items():
        # 未审计的报表本来就没有审计意见，这一项不算缺
        if field_name == "audit_opinion" and ctx.audited is False:
            continue
        if getattr(ctx, field_name, None) is None:
            ctx_blanks.append(label)
    if ctx_blanks:
        items.append(f"材料缺：{'、'.join(ctx_blanks)}")
    return items


def Period_fields() -> list[str]:
    from .models import Period

    return [n for n in Period.__annotations__ if n != "year"]


def render(case: Case) -> dict[str, Any]:
    """执行全部检查并返回结构化结果，供 CLI 与智能体调用。"""
    checks = reconcile.run_all(case)
    flags = redflags.run_all(case)
    level, reason = redflags.risk_level(flags)

    return {
        "company": case.context.company or "未命名对象",
        "industry": case.context.industry,
        "level": level,
        "reason": reason,
        "checks": [
            {"code": c.code, "name": c.name, "status": str(c.status), "detail": c.detail}
            for c in checks
        ],
        "flags": [
            {
                "code": f.code,
                "name": f.name,
                "severity": str(f.severity),
                "evidence": f.evidence,
            }
            for f in flags
            if f.hit
        ],
        "hit_count": sum(1 for f in flags if f.hit),
        "missing": _missing_items(case),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def to_markdown(case: Case) -> str:
    report = render(case)
    checks = reconcile.run_all(case)
    flags = redflags.run_all(case)

    parts = [
        f"# 核验底稿：{report['company']}",
        "",
        f"生成时间 {report['generated_at']}　行业 {report['industry'] or '未指定'}",
        "",
        "## 一、核心指标",
        "",
        _metric_table(case),
        "",
        "## 二、勾稽检查",
        "",
        _reconcile_table(checks),
        "",
        "## 三、红旗清单",
        "",
        _flag_table(flags),
        "",
        "## 四、风险评级",
        "",
        f"**{report['level']}**。{report['reason']}。",
        "",
    ]

    missing = report["missing"]
    if missing:
        parts += ["## 五、待补充资料", ""]
        parts += [f"- {m}" for m in missing]
        parts += ["", "以上科目未取得数据，相关检查未执行，不得以任何方式推算补齐。", ""]
    else:
        parts += ["## 五、待补充资料", "", "本期数据完整，无缺失科目。", ""]

    passed = sum(1 for c in checks if c.status is Status.PASS)
    failed = sum(1 for c in checks if c.status is Status.FAIL)
    skipped = sum(1 for c in checks if c.status is Status.SKIP)
    parts += [
        "## 六、底稿说明",
        "",
        f"勾稽检查 {len(checks)} 项，一致 {passed} 项，存疑 {failed} 项，因数据缺失未校验 {skipped} 项。"
        f"红旗命中 {report['hit_count']} 条。",
        "",
        "本底稿由规则引擎按固定口径生成，不依赖大模型判断，可复算。"
        "结论仅覆盖可量化部分，定性判断需结合实地核查。",
        "",
        "本报告仅供参考，不构成投资建议或授信决策依据。",
    ]

    return "\n".join(parts)
