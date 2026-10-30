---
name: heshuguan-verify
description: Deterministic verification engine for financial statements and credit materials. Use when the user provides annual report figures or a credit application package and needs cross-source checks, accounting reconciliation, fraud red-flag screening, risk scoring, or a verification worksheet. Runs on the Python standard library only, no dependencies.
---

# 核数官核验引擎

这一层把核验从"模型说核对过"变成"规则算出来的结果"。同一份材料，谁跑都是同一个结论。

## 什么时候用它

- 用户上传年报或授信材料，需要核对财务数据是否自洽
- 需要判断一家企业是否存在财务造假信号
- 需要一份可以交给他人复核的核验底稿
- 需要对某个结论做复算，验证模型有没有算错

不需要用它的时候：纯合规条款问答（模式C），这类问题查 `references/4-合规FAQ要点库.txt` 即可。

## 运行方式

脚本目录：`scripts/`，全部为 Python 标准库，无需安装任何依赖。

```bash
cd scripts
python3 -m heshuguan check data/huachen.json        # 输出 markdown 底稿
python3 -m heshuguan check data/huachen.json --json # 输出结构化结果
python3 -m unittest discover -s tests               # 39 个回归用例
```

`data/huachen.json` 是有问题的样本（授信欺诈），`data/clean_sample.json` 是健康样本。两个都要跑，一个判高风险、一个判低风险，才说明引擎有判别力。

## 输入格式

一个 JSON，两个顶层字段，金额单位为万元：

```json
{
  "context": {
    "company": "企业名称",
    "industry": "行业",
    "audited": false,
    "bank_flow_total": 28400,
    "tax_reported_revenue": null,
    "top5_customer_ratio": 0.71,
    "related_party_sales_ratio": 0.28
  },
  "periods": [
    {
      "year": 2024,
      "revenue": 63500,
      "cost_of_sales": 59000,
      "net_profit": 2910,
      "net_profit_deducted": 2300,
      "operating_cash_flow": 350,
      "total_assets": 52000,
      "total_liabilities": 29328,
      "equity": 22672,
      "accounts_receivable": 20100,
      "monetary_funds": 19400,
      "interest_bearing_debt": 18000
    }
  ]
}
```

字段全部可选。**缺的字段留空或写 null，不要填 0**。填 0 会让引擎以为这是真实数据，把缺失算成结论。

`context` 里的字段在授信场景下比报表更能说明问题：银行流水、纳税申报收入、前五大客户占比、关联方销售占比、实际控制人质押比例、高管离职人数、是否被立案调查。

## 输出

markdown 底稿分五部分：核心指标（含判读）、勾稽检查、红旗清单、风险评级、缺失项。`--json` 输出同样内容的结构化版本，字段名与 `heshuguan/models.py` 一致。

底稿里每一项只有三种结果：一致、存疑、缺失。没有第四种。缺失不会被写成通过。

## 五条实现原则

这五条写在代码里，任何修改都不能破坏：

1. **缺失不补**。依赖缺失字段的检查返回"缺失"并注明原因，绝不用行业均值推算补齐。
2. **除零返回 None**。分母为零说明口径本身没意义，返回 0 会让错误一路传下去。
3. **容差写明**。资产负债表平衡按资产千分之一且不低于 1 万元取容差，因为报表按万元四舍五入。
4. **规则不凑数**。证据不足时不命中红旗，宁可少一条，也要保证命中的每条都站得住。
5. **可复算**。每个结论附带算式和取数，第三方拿同一份 JSON 能算出同一个结果。

## 参考资料

| 文件 | 内容 | 对应 |
|---|---|---|
| `references/1-财务指标口径说明.md` | 增长率、毛利率、净利率、ROE、经营现金流、自由现金流的计算口径，金融业专用口径，合理区间参考 | 模式A计算 |
| `references/2-速读报告模板.md` | 一页速读报告的固定结构：三年对比表、三个变化点、风险提示、核验底稿、免责声明 | 模式A输出 |
| `references/3-风控红旗清单.md` | 十条财务欺诈信号的判定标准与风险分级 | 模式B输出 |
| `references/4-合规FAQ要点库.txt` | 理财销售、贷款适当性、信息披露等高频合规问答，含条款编号与原文 | 模式C输出 |

计算口径以 `references/1` 为准，红旗编号以 `references/3` 为准。两份资料与本目录的测试用例互为约束，改了一处要同时改另一处。
