"""核验引擎的单元测试。

只依赖标准库，直接跑：
    cd engine && python -m unittest discover -s tests -v

测试分三类：
  1. 边界：除零、负数基期、缺失字段，这些地方最容易出静默错误
  2. 规则：单条勾稽和红旗的判定，构造小样本正向反向各测一次
  3. 回归：华宸案例的完整结论，锁死数字，防止改代码时不知不觉改变判定
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heshuguan import Case, Period, Severity, Status  # noqa: E402
from heshuguan import reconcile, redflags, worksheet  # noqa: E402
from heshuguan.metrics import (  # noqa: E402
    growth_rate,
    ocf_ratio,
    safe_div,
)


def sample_case() -> Case:
    """最小可用样本，三年数据，全部字段齐全。"""
    return Case.from_dict(
        {
            "context": {"company": "样本公司", "industry": "制造业"},
            "periods": [
                {
                    "year": 2022,
                    "revenue": 10000,
                    "cost_of_sales": 7000,
                    "net_profit": 1000,
                    "operating_cash_flow": 900,
                    "total_assets": 20000,
                    "total_liabilities": 10000,
                    "equity": 10000,
                    "accounts_receivable": 1000,
                    "operating_profit": 1333,
                    "total_profit": 1333,
                    "fixed_assets": 5000,
                    "depreciation": 500,
                },
                {
                    "year": 2023,
                    "revenue": 12000,
                    "cost_of_sales": 8400,
                    "net_profit": 1200,
                    "operating_cash_flow": 1100,
                    "total_assets": 22000,
                    "total_liabilities": 10800,
                    "equity": 11200,
                    "accounts_receivable": 1200,
                    "operating_profit": 1600,
                    "total_profit": 1600,
                    "fixed_assets": 5200,
                    "depreciation": 520,
                },
                {
                    "year": 2024,
                    "revenue": 14000,
                    "cost_of_sales": 9800,
                    "net_profit": 1400,
                    "operating_cash_flow": 1300,
                    "total_assets": 24000,
                    "total_liabilities": 11600,
                    "equity": 12400,
                    "accounts_receivable": 1400,
                    "operating_profit": 1867,
                    "total_profit": 1867,
                    "fixed_assets": 5400,
                    "depreciation": 540,
                },
            ],
        }
    )


def load_huachen() -> Case:
    path = Path(__file__).resolve().parent.parent / "data" / "huachen.json"
    return Case.from_dict(json.loads(path.read_text(encoding="utf-8")))


class TestMetricsBoundary(unittest.TestCase):
    """除法与增长率的边界处理。这三处错了，后面所有结论都会跟着错。"""

    def test_div_by_zero_returns_none(self):
        self.assertIsNone(safe_div(10, 0))

    def test_div_by_none_returns_none(self):
        self.assertIsNone(safe_div(None, 10))
        self.assertIsNone(safe_div(10, None))

    def test_div_normal(self):
        self.assertAlmostEqual(safe_div(10, 4), 2.5)

    def test_growth_normal(self):
        result = growth_rate(120, 100)
        self.assertAlmostEqual(result.value, 0.2)
        self.assertEqual(result.note, "")

    def test_growth_negative_base_uses_absolute(self):
        """基期为负必须用绝对值作分母，否则符号会翻转。"""
        result = growth_rate(50, -100)
        # (50 - (-100)) / 100 = 1.5，若用 -100 作分母会得到 -1.5
        self.assertAlmostEqual(result.value, 1.5)
        self.assertIn("基期为负", result.note)

    def test_growth_zero_base_returns_none(self):
        result = growth_rate(50, 0)
        self.assertIsNone(result.value)
        self.assertIn("无意义", result.note)

    def test_growth_missing_data(self):
        self.assertIsNone(growth_rate(None, 100).value)
        self.assertIsNone(growth_rate(100, None).value)

    def test_ocf_ratio_ignores_loss(self):
        """亏损时经营现金流/净利润没有解释意义，必须返回 None 而不是负数比值。"""
        p = Period(year=2024, net_profit=-500, operating_cash_flow=100)
        self.assertIsNone(ocf_ratio(p))


class TestReconcile(unittest.TestCase):
    def test_balance_sheet_pass(self):
        p = Period(year=2024, total_assets=1000, total_liabilities=400, equity=600)
        self.assertIs(reconcile.check_balance_sheet(p).status, Status.PASS)

    def test_balance_sheet_fail(self):
        p = Period(year=2024, total_assets=1000, total_liabilities=400, equity=500)
        self.assertIs(reconcile.check_balance_sheet(p).status, Status.FAIL)

    def test_balance_sheet_tolerance(self):
        """千分之一容差内不算错，报表四舍五入本来就会有尾差。"""
        p = Period(year=2024, total_assets=100000, total_liabilities=40000, equity=59950)
        self.assertIs(reconcile.check_balance_sheet(p).status, Status.PASS)

    def test_balance_sheet_missing_is_skip_not_pass(self):
        """缺数据必须是缺失，绝不能默认通过。"""
        p = Period(year=2024, total_assets=1000)
        self.assertIs(reconcile.check_balance_sheet(p).status, Status.SKIP)

    def test_profit_tie_out(self):
        p = Period(year=2024, operating_profit=800, non_operating_income=50,
                   non_operating_expense=30, total_profit=820)
        self.assertIs(reconcile.check_profit_tie_out(p).status, Status.PASS)

    def test_cash_direction_fail(self):
        case = Case.from_dict({
            "periods": [
                {"year": 2023, "net_profit": 100, "operating_cash_flow": 80},
                {"year": 2024, "net_profit": 200, "operating_cash_flow": -50},
            ]
        })
        result = reconcile.check_cash_profit_direction(case)
        self.assertIs(result.status, Status.FAIL)
        self.assertEqual(result.evidence["diverged_years"], [2024])

    def test_receipt_coverage_fail(self):
        case = Case.from_dict({
            "context": {"bank_flow_total": 400},
            "periods": [{"year": 2024, "revenue": 1000}],
        })
        self.assertIs(reconcile.check_receipt_coverage(case).status, Status.FAIL)

    def test_common_checks_are_four(self):
        """通用勾稽必须是四项，作品文档里"勾稽检查四项通过"依赖这个数。"""
        self.assertEqual(len(reconcile.run_common(sample_case())), 4)
        self.assertEqual(len(reconcile.run_all(sample_case())), 7)

    def test_all_common_pass_on_clean_sample(self):
        results = reconcile.run_common(sample_case())
        self.assertTrue(all(r.status is Status.PASS for r in results),
                        [f"{r.code} {r.status}" for r in results])


class TestRedFlags(unittest.TestCase):
    def test_clean_sample_has_no_high_risk_flag(self):
        flags = redflags.run_all(sample_case())
        high = [f for f in flags if f.hit and f.severity is Severity.HIGH]
        self.assertEqual(high, [])

    def test_f1_triggers_below_threshold(self):
        case = load_huachen()
        flag = redflags.check_f1(case)
        self.assertTrue(flag.hit)
        self.assertLess(flag.values["ratio"], 0.3)

    def test_f1_not_trigger_above_threshold(self):
        case = Case.from_dict({
            "periods": [{"year": 2024, "net_profit": 1000, "operating_cash_flow": 900}]
        })
        self.assertFalse(redflags.check_f1(case).hit)

    def test_f5_escalates_when_f1_also_hits(self):
        """存贷双高基线是中风险，叠加现金流背离后升级为高风险。"""
        flags = redflags.run_all(load_huachen())
        f5 = next(f for f in flags if f.code == "F5")
        self.assertTrue(f5.hit)
        self.assertIs(f5.severity, Severity.HIGH)
        self.assertIn("体外循环", f5.evidence)

    def test_f5_stays_medium_without_escalation(self):
        case = Case.from_dict({
            "periods": [{
                "year": 2024,
                "net_profit": 1000,
                "operating_cash_flow": 900,
                "monetary_funds": 18000,
                "interest_bearing_debt": 18000,
                "total_assets": 60000,
            }]
        })
        flags = redflags.run_all(case)
        f5 = next(f for f in flags if f.code == "F5")
        self.assertTrue(f5.hit)
        self.assertIs(f5.severity, Severity.MEDIUM)

    def test_f5_not_hit_when_cash_share_is_small(self):
        """货币资金占比不高时，比值落在 0.5 到 2 之间只是正常运营储备，不构成存贷双高。"""
        case = Case.from_dict({
            "periods": [{
                "year": 2024,
                "net_profit": 1000,
                "operating_cash_flow": 900,
                "monetary_funds": 3800,
                "interest_bearing_debt": 2400,
                "total_assets": 24000,
            }]
        })
        f5 = redflags.check_f5(case)
        self.assertFalse(f5.hit)
        self.assertIn("正常运营储备", f5.evidence)

    def test_f10_escalates_on_severe_gap(self):
        flags = redflags.run_all(load_huachen())
        f10 = next(f for f in flags if f.code == "F10")
        self.assertTrue(f10.hit)
        self.assertIs(f10.severity, Severity.HIGH)

    def test_f9_skips_without_governance_data(self):
        """没有治理数据时不能凭空判命中。"""
        flag = redflags.check_f9(load_huachen())
        self.assertFalse(flag.hit)


class TestHuachenRegression(unittest.TestCase):
    """华宸案例锁死结论。改了规则导致数字变化，这里会第一时间失败。"""

    @classmethod
    def setUpClass(cls):
        cls.case = load_huachen()
        cls.flags = redflags.run_all(cls.case)

    def test_hit_count_is_eight(self):
        self.assertEqual(sum(1 for f in self.flags if f.hit), 8)

    def test_high_risk_count_is_five(self):
        high = [f.code for f in self.flags if f.hit and f.severity is Severity.HIGH]
        self.assertEqual(sorted(high), ["F1", "F10", "F2", "F4", "F5"])

    def test_level_is_high_risk(self):
        level, _ = redflags.risk_level(self.flags)
        self.assertEqual(level, "高风险")

    def test_common_four_all_pass(self):
        results = reconcile.run_common(self.case)
        self.assertEqual([r.status for r in results], [Status.PASS] * 4)

    def test_receipt_coverage_about_44_percent(self):
        result = reconcile.check_receipt_coverage(self.case)
        self.assertIs(result.status, Status.FAIL)
        self.assertAlmostEqual(result.evidence["coverage"], 0.4472, places=3)

    def test_receivable_growth_about_235_percent(self):
        result = reconcile.check_receivable_growth(self.case)
        evidence = result.evidence["gap"]
        self.assertGreater(evidence, 1.5)

    def test_missing_materials_disclosed(self):
        """材料缺失必须写进底稿，否则读者会以为没查等于查过。"""
        report = worksheet.render(self.case)
        joined = " ".join(report["missing"])
        self.assertIn("纳税申报收入", joined)


class TestCleanRegression(unittest.TestCase):
    """健康样本必须全绿。引擎如果见谁都报警，就等于没有判断力。"""

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent.parent / "data" / "clean_sample.json"
        cls.case = Case.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def test_all_seven_checks_pass(self):
        results = reconcile.run_all(self.case)
        self.assertEqual([r.status for r in results], [Status.PASS] * 7)

    def test_no_red_flag_hit(self):
        flags = redflags.run_all(self.case)
        hit = [f.code for f in flags if f.hit]
        self.assertEqual(hit, [], f"健康样本不应命中红旗，实际命中 {hit}")

    def test_level_is_low_risk(self):
        level, _ = redflags.risk_level(redflags.run_all(self.case))
        self.assertEqual(level, "低风险")


class TestWorksheet(unittest.TestCase):
    def test_markdown_contains_all_sections(self):
        text = worksheet.to_markdown(load_huachen())
        for section in ["核心指标", "勾稽检查", "红旗清单", "风险评级", "待补充资料"]:
            self.assertIn(section, text)

    def test_markdown_has_no_unrendered_placeholder(self):
        text = worksheet.to_markdown(load_huachen())
        self.assertNotIn("None", text)
        self.assertNotIn("{", text)

    def test_json_output_is_serializable(self):
        report = worksheet.render(load_huachen())
        json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["level"], "高风险")

    def test_empty_periods_raises(self):
        with self.assertRaises(ValueError):
            Case.from_dict({"periods": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
