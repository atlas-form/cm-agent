use serde_json::{Value, json};

use crate::skills::{
    SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillSpec,
};

pub fn specs() -> Vec<SkillSpec> {
    vec![
        spec(
            "accounting_cost_calc",
            "成本核算",
            "根据各项成本明细，计算单品综合成本和成本结构",
            vec![
                field("purchase_cost", "number", "采购成本（元）", true),
                field("selling_price", "number", "售价（元）", true),
                field("shipping_cost", "number", "物流成本，默认5", false),
                field("packaging_cost", "number", "包装成本，默认2", false),
                field("platform_fee_rate", "number", "平台佣金率%，默认5", false),
                field("ad_cost_per_unit", "number", "单品推广成本，默认3", false),
            ],
        ),
        spec(
            "accounting_profit_analysis",
            "利润分析",
            "按营收、退款、商品成本、运营费用和广告支出分析利润结构",
            vec![
                field("revenue", "number", "总营收（元）", false),
                field("cogs", "number", "商品成本，默认营收40%", false),
                field(
                    "operating_expenses",
                    "number",
                    "运营费用，默认营收10%",
                    false,
                ),
                field("ad_spend", "number", "广告支出", false),
                field("refund_amount", "number", "退款金额", false),
                field("days", "integer", "统计天数，默认30", false),
            ],
        ),
        spec(
            "accounting_roi_calc",
            "ROI计算",
            "计算广告或项目投入产出、利润ROI、ACOS、CPA及盈亏状态",
            vec![
                field("investment", "number", "投入金额（元）", false),
                field("revenue_generated", "number", "产出营收（元）", false),
                field("cost_of_goods", "number", "商品成本，默认营收40%", false),
                field("period", "string", "统计周期描述", false),
                field("avg_order_value", "number", "客单价，默认150", false),
            ],
        ),
        spec(
            "accounting_break_even_calc",
            "盈亏平衡分析",
            "计算盈亏平衡点、贡献利润率和价格×销量敏感性矩阵",
            vec![
                field("fixed_costs", "number", "月固定成本", false),
                field(
                    "unit_variable_cost",
                    "number",
                    "单品变动成本，默认售价55%",
                    false,
                ),
                field("selling_price", "number", "单品售价，默认100", false),
            ],
        ),
        spec(
            "accounting_budget_vs_actual",
            "预算vs实际分析",
            "逐项对比预算与实际，计算差异、完成率、执行评分和风险项",
            vec![
                field("budget_gmv", "number", "预算GMV目标", false),
                field("actual_gmv", "number", "实际GMV", false),
                field("budget_ad_spend", "number", "预算广告投入", false),
                field("actual_ad_spend", "number", "实际广告投入", false),
                field(
                    "budget_cogs_rate",
                    "number",
                    "预算商品成本率%，默认40",
                    false,
                ),
                field(
                    "actual_cogs_rate",
                    "number",
                    "实际商品成本率%，默认40",
                    false,
                ),
                field(
                    "budget_opex_rate",
                    "number",
                    "预算运营费用率%，默认10",
                    false,
                ),
                field(
                    "actual_opex_rate",
                    "number",
                    "实际运营费用率%，默认10",
                    false,
                ),
                field(
                    "budget_target_margin",
                    "number",
                    "预算目标利润率%，默认15",
                    false,
                ),
                field("actual_refund_rate", "number", "实际退款率%，默认3", false),
            ],
        ),
        spec(
            "accounting_gmv_waterfall",
            "GMV利润瀑布分析",
            "分解GMV到税后净利润，识别最大利润漏损项和核心利润率",
            vec![
                field("gmv", "number", "GMV（元）", false),
                field("ad_spend", "number", "广告投放费", false),
                field("orders", "number", "订单量", false),
                field("refund_rate", "number", "退款率%，默认3", false),
                field("cogs_rate", "number", "商品成本率%，默认40", false),
                field("logistics_rate", "number", "物流费率%，默认8", false),
                field("platform_fee_rate", "number", "平台佣金率%，默认5", false),
                field(
                    "fixed_opex",
                    "number",
                    "固定运营成本，不填按净营收3%估算",
                    false,
                ),
                field("tax_rate", "number", "综合税率%，默认3", false),
            ],
        ),
        spec(
            "accounting_scenario_analysis",
            "情景财务分析",
            "构建乐观/基准/悲观P&L，输出价格×流量敏感性矩阵和确定性风险摘要",
            vec![
                field("base_gmv", "number", "基准月GMV", true),
                field("base_margin_pct", "number", "基准毛利率%，默认30", false),
                field("base_ad_pct", "number", "广告费率%，默认8", false),
                field("base_refund_pct", "number", "退款率%，默认5", false),
                field("fixed_cost", "number", "固定成本/月", false),
                field("optimistic_growth", "number", "乐观GMV增幅%，默认30", false),
                field(
                    "pessimistic_growth",
                    "number",
                    "悲观GMV增幅%，默认-20",
                    false,
                ),
            ],
        ),
    ]
}

pub async fn execute(
    skill_name: &str,
    input: Value,
    _context: &SkillContext,
) -> Result<SkillOutcome, SkillError> {
    let value = match skill_name {
        "accounting_cost_calc" => cost_calc(&input),
        "accounting_profit_analysis" => profit_analysis(&input),
        "accounting_roi_calc" => roi_calc(&input),
        "accounting_break_even_calc" => break_even_calc(&input),
        "accounting_budget_vs_actual" => budget_vs_actual(&input),
        "accounting_gmv_waterfall" => gmv_waterfall(&input),
        "accounting_scenario_analysis" => scenario_analysis(&input),
        other => return Err(invalid_input(format!("unknown accounting skill: {other}"))),
    };
    Ok(outcome(value))
}

fn cost_calc(input: &Value) -> Value {
    let purchase = num(input, "purchase_cost", 0.0);
    let shipping = num(input, "shipping_cost", 5.0);
    let packaging = num(input, "packaging_cost", 2.0);
    let fee_rate = num(input, "platform_fee_rate", 5.0);
    let ad_cost = num(input, "ad_cost_per_unit", 3.0);
    let price = num(input, "selling_price", 100.0);
    let platform_fee = round2(price * fee_rate / 100.0);
    let total_cost = round2(purchase + shipping + packaging + platform_fee + ad_cost);
    let profit = round2(price - total_cost);
    let margin = pct(profit, price);

    json!({
        "售价": price,
        "成本明细": {
            "采购成本": purchase,
            "物流成本": shipping,
            "包装成本": packaging,
            "平台佣金": platform_fee,
            "推广成本": ad_cost,
        },
        "综合成本": total_cost,
        "单品利润": profit,
        "利润率": percent(margin, 2),
        "成本占比": {
            "采购": percent(pct(purchase, total_cost), 1),
            "物流": percent(pct(shipping, total_cost), 1),
            "包装": percent(pct(packaging, total_cost), 1),
            "平台": percent(pct(platform_fee, total_cost), 1),
            "推广": percent(pct(ad_cost, total_cost), 1),
        },
        "建议": if margin < 15.0 { "利润率偏低，建议优化采购或提价" } else { "利润率健康" },
    })
}

fn profit_analysis(input: &Value) -> Value {
    let days = num(input, "days", 30.0).round();
    let revenue = num(input, "revenue", 0.0);
    if revenue <= 0.0 {
        return json!({
            "has_data": false,
            "提示": "纯计算版本需要手动传入 revenue 参数。",
            "数据来源": "用户提供",
        });
    }
    let refund = num(input, "refund_amount", 0.0);
    let cogs = if input.get("cogs").is_some() {
        num(input, "cogs", 0.0)
    } else {
        round2(revenue * 0.40)
    };
    let opex = if input.get("operating_expenses").is_some() {
        num(input, "operating_expenses", 0.0)
    } else {
        round2(revenue * 0.10)
    };
    let ad = num(input, "ad_spend", 0.0);
    let net_revenue = round2(revenue - refund);
    let gross_profit = round2(net_revenue - cogs);
    let gross_margin = pct(gross_profit, net_revenue);
    let operating_profit = round2(gross_profit - opex - ad);
    let operating_margin = pct(operating_profit, net_revenue);

    json!({
        "统计周期": format!("最近{}天", days),
        "数据来源": "用户提供",
        "营收分析": {
            "总营收": revenue,
            "退款": refund,
            "净营收": net_revenue,
            "退款率": percent(pct(refund, revenue), 2),
        },
        "利润分析": {
            "毛利润": gross_profit,
            "毛利率": percent(gross_margin, 2),
            "营业利润": operating_profit,
            "营业利润率": percent(operating_margin, 2),
        },
        "费用结构": {
            "商品成本": cogs,
            "运营费用": opex,
            "广告支出": ad,
            "费用率": percent(pct(cogs + opex + ad, net_revenue), 2),
        },
        "健康度": {
            "毛利率": if gross_margin > 40.0 { "健康" } else if gross_margin > 20.0 { "偏低" } else { "亏损风险" },
            "营业利润率": if operating_margin > 10.0 { "健康" } else if operating_margin > 0.0 { "偏低" } else { "亏损" },
            "广告占比": if pct(ad, net_revenue) < 15.0 { "合理" } else { "偏高" },
        }
    })
}

fn roi_calc(input: &Value) -> Value {
    let period = string(input, "period", "最近30天");
    let mut investment = num(input, "investment", 0.0);
    let revenue = num(input, "revenue_generated", 0.0);
    if investment <= 0.0 && revenue <= 0.0 {
        return json!({
            "has_data": false,
            "提示": "纯计算版本需要手动传入 investment 和 revenue_generated。",
            "数据来源": "用户提供",
        });
    }
    if investment <= 0.0 {
        investment = 1.0;
    }
    let cogs = if input.get("cost_of_goods").is_some() {
        num(input, "cost_of_goods", 0.0)
    } else {
        round2(revenue * 0.40)
    };
    let aov = num(input, "avg_order_value", 150.0).max(0.01);
    let roas = round2(revenue / investment);
    let profit_roi = round2((revenue - cogs - investment) / investment);
    let acos = pct(investment, revenue);
    let cpa = if revenue > 0.0 {
        round2(investment / (revenue / aov))
    } else {
        0.0
    };
    let net_profit = round2(revenue - cogs - investment);

    json!({
        "统计周期": period,
        "数据来源": "用户提供",
        "投入": investment,
        "产出营收": revenue,
        "ROI指标": {
            "ROAS(广告回报率)": roas,
            "真实广告ROI": roas,
            "利润ROI": profit_roi,
            "ACOS(广告成本占比)": percent(acos, 2),
            "CPA(获客成本)": cpa,
        },
        "盈亏分析": {
            "毛利润": round2(revenue - cogs),
            "净利润": net_profit,
            "是否盈利": if net_profit > 0.0 { "是" } else { "否" },
        },
        "评价": {
            "ROAS": if roas > 5.0 { "优秀" } else if roas > 3.0 { "良好" } else if roas > 1.0 { "及格" } else { "亏损" },
            "建议": if roas > 5.0 { "加大投放" } else if roas > 3.0 { "维持投放" } else if roas > 1.0 { "优化素材" } else { "暂停投放排查" },
        }
    })
}

fn break_even_calc(input: &Value) -> Value {
    let price = num(input, "selling_price", 100.0);
    let unit_var = num(input, "unit_variable_cost", round2(price * 0.55));
    let fixed = num(input, "fixed_costs", round2(price * 200.0 * 0.13));
    let contribution_per_unit = price - unit_var;
    if contribution_per_unit <= 0.0 {
        return json!({
            "error": "单品变动成本高于售价，无法达到盈亏平衡",
            "建议": "降低成本或提高售价",
        });
    }

    let bep_units = (fixed / contribution_per_unit).round();
    let bep_revenue = round2(bep_units * price);
    let margin_of_safety_pct = if price > unit_var {
        (1.0 - 1.0 / (price / unit_var)) * 100.0
    } else {
        0.0
    };
    let price_variants = [0.8, 0.9, 1.0, 1.1, 1.2].map(|r| round2(price * r));
    let sales_targets = [0.5, 0.75, 1.0, 1.5, 2.0].map(|r| (bep_units * r).round());
    let sensitivity: Vec<_> = price_variants
        .iter()
        .map(|pv| {
            let mut row = serde_json::Map::new();
            row.insert("售价".to_owned(), json!(currency(*pv)));
            for sv in sales_targets {
                let profit = round2((pv - unit_var) * sv - fixed);
                row.insert(
                    format!("{}单", sv as i64),
                    json!(format!(
                        "{}{}",
                        currency(profit),
                        if profit >= 0.0 { "✓" } else { "✗" }
                    )),
                );
            }
            Value::Object(row)
        })
        .collect();

    json!({
        "数据来源": "用户提供/默认参数",
        "定价": price,
        "单品变动成本": unit_var,
        "月固定成本": fixed,
        "单位贡献利润": round2(contribution_per_unit),
        "贡献利润率": percent(pct(contribution_per_unit, price), 1),
        "盈亏平衡点": {
            "需销售订单数": bep_units as i64,
            "需实现营收": currency(bep_revenue),
            "安全边际": percent(margin_of_safety_pct, 1),
        },
        "敏感性分析(利润矩阵)": sensitivity,
        "说明": "✓=盈利 ✗=亏损",
    })
}

fn budget_vs_actual(input: &Value) -> Value {
    let actual_gmv = num(input, "actual_gmv", 0.0);
    if actual_gmv <= 0.0 {
        return json!({
            "has_data": false,
            "提示": "纯计算版本需要手动传入 actual_gmv；budget_gmv 不传时会按 actual_gmv×120%生成。",
        });
    }
    let budget_gmv = num(input, "budget_gmv", round2(actual_gmv * 1.20));
    let actual_ad = num(input, "actual_ad_spend", 0.0);
    let budget_ad = num(input, "budget_ad_spend", round2(budget_gmv * 0.12));
    let budget_cogs_rate = num(input, "budget_cogs_rate", 40.0) / 100.0;
    let actual_cogs_rate = num(input, "actual_cogs_rate", 40.0) / 100.0;
    let budget_opex_rate = num(input, "budget_opex_rate", 10.0) / 100.0;
    let actual_opex_rate = num(input, "actual_opex_rate", 10.0) / 100.0;
    let actual_refund_rate = num(input, "actual_refund_rate", 3.0) / 100.0;

    let budget_net_rev = round2(budget_gmv * 0.97);
    let budget_cogs = round2(budget_gmv * budget_cogs_rate);
    let budget_opex = round2(budget_gmv * budget_opex_rate);
    let budget_gross_profit = round2(budget_net_rev - budget_cogs);
    let budget_op_profit = round2(budget_gross_profit - budget_opex - budget_ad);
    let budget_margin = pct(budget_op_profit, budget_gmv);

    let actual_net_rev = round2(actual_gmv * (1.0 - actual_refund_rate));
    let actual_cogs = round2(actual_net_rev * actual_cogs_rate);
    let actual_opex = round2(actual_gmv * actual_opex_rate);
    let actual_gross_profit = round2(actual_net_rev - actual_cogs);
    let actual_op_profit = round2(actual_gross_profit - actual_opex - actual_ad);
    let actual_margin = pct(actual_op_profit, actual_gmv);

    let variance_table = json!({
        "GMV": variance(budget_gmv, actual_gmv, true),
        "净营收": variance(budget_net_rev, actual_net_rev, true),
        "商品成本": variance(budget_cogs, actual_cogs, false),
        "广告投入": variance(budget_ad, actual_ad, false),
        "运营费用": variance(budget_opex, actual_opex, false),
        "毛利润": variance(budget_gross_profit, actual_gross_profit, true),
        "营业利润": variance(budget_op_profit, actual_op_profit, true),
        "利润率": {
            "预算": percent(budget_margin, 2),
            "实际": percent(actual_margin, 2),
            "差异": format!("{:+.1}ppt", actual_margin - budget_margin),
            "状态": if actual_margin >= budget_margin { "有利" } else { "不利" },
        },
    });

    let gmv_ach = actual_gmv / budget_gmv.max(1.0);
    let margin_ach = actual_margin / budget_margin.max(0.1);
    let ad_eff = if actual_ad > 0.0 && budget_ad > 0.0 {
        budget_ad / actual_ad
    } else {
        1.0
    };
    let exec_score =
        ((gmv_ach * 0.50 + margin_ach * 0.35 + ad_eff.min(1.5) * 0.15) * 100.0).min(100.0);
    let mut risks = Vec::new();
    if actual_gmv < budget_gmv * 0.90 {
        risks.push(format!(
            "GMV未达标（完成率{:.0}%，差{}元）",
            gmv_ach * 100.0,
            round2(budget_gmv - actual_gmv)
        ));
    }
    if budget_ad > 0.0 && actual_ad > budget_ad * 1.15 {
        risks.push(format!(
            "广告超支（超出{}元）",
            round2(actual_ad - budget_ad)
        ));
    }
    if actual_margin < budget_margin * 0.85 {
        risks.push(format!(
            "利润率低于预算（实{} vs 预算{}）",
            percent(actual_margin, 2),
            percent(budget_margin, 2)
        ));
    }

    json!({
        "has_data": true,
        "数据说明": if input.get("budget_gmv").is_some() { "用户提供预算" } else { "预算自动生成：actual_gmv×120%" },
        "执行质量评分": format!("{:.1}/100", exec_score),
        "GMV完成率": percent(gmv_ach * 100.0, 1),
        "预算vs实际对比": variance_table,
        "风险项": if risks.is_empty() { vec!["各科目执行符合预算，无重大风险".to_owned()] } else { risks },
    })
}

fn gmv_waterfall(input: &Value) -> Value {
    let gmv = num(input, "gmv", 0.0);
    if gmv <= 0.0 {
        return json!({
            "has_data": false,
            "提示": "纯计算版本需要手动传入 gmv。",
        });
    }
    let ad_spend = num(input, "ad_spend", 0.0);
    let orders = num(input, "orders", 0.0);
    let refund_rate = num(input, "refund_rate", 3.0) / 100.0;
    let cogs_rate = num(input, "cogs_rate", 40.0) / 100.0;
    let logistics_rate = num(input, "logistics_rate", 8.0) / 100.0;
    let platform_fee_rate = num(input, "platform_fee_rate", 5.0) / 100.0;
    let tax_rate = num(input, "tax_rate", 3.0) / 100.0;

    let step1_gmv = round2(gmv);
    let step2_refund = round2(gmv * refund_rate);
    let step3_net_rev = round2(step1_gmv - step2_refund);
    let step4_cogs = round2(step3_net_rev * cogs_rate);
    let step5_gross_profit = round2(step3_net_rev - step4_cogs);
    let step6_logistics = round2(step3_net_rev * logistics_rate);
    let step7_platform_fee = round2(step1_gmv * platform_fee_rate);
    let step8_ad = round2(ad_spend);
    let step9_contribution =
        round2(step5_gross_profit - step6_logistics - step7_platform_fee - step8_ad);
    let step10_fixed = if input.get("fixed_opex").is_some() {
        num(input, "fixed_opex", 0.0)
    } else {
        round2(step3_net_rev * 0.03)
    };
    let step11_ebitda = round2(step9_contribution - step10_fixed);
    let step12_tax = round2((step11_ebitda * tax_rate).max(0.0));
    let step13_net_profit = round2(step11_ebitda - step12_tax);

    let waterfall = vec![
        wf("① GMV（总销售额）", step1_gmv, step1_gmv, "基准", step1_gmv),
        wf(
            "  (-) 退款/退货",
            -step2_refund,
            step1_gmv,
            "扣减",
            step1_gmv,
        ),
        wf("② 净营收", step3_net_rev, step1_gmv, "小计", step1_gmv),
        wf(
            "  (-) 商品成本(COGS)",
            -step4_cogs,
            step1_gmv,
            "扣减",
            step1_gmv,
        ),
        wf("③ 毛利润", step5_gross_profit, step1_gmv, "小计", step1_gmv),
        wf(
            "  (-) 物流/包装费",
            -step6_logistics,
            step1_gmv,
            "扣减",
            step1_gmv,
        ),
        wf(
            "  (-) 平台佣金",
            -step7_platform_fee,
            step1_gmv,
            "扣减",
            step1_gmv,
        ),
        wf("  (-) 广告投放费", -step8_ad, step1_gmv, "扣减", step1_gmv),
        wf(
            "④ 贡献利润(CM)",
            step9_contribution,
            step1_gmv,
            "小计",
            step1_gmv,
        ),
        wf(
            "  (-) 固定运营成本",
            -step10_fixed,
            step1_gmv,
            "扣减",
            step1_gmv,
        ),
        wf("⑤ EBITDA", step11_ebitda, step1_gmv, "小计", step1_gmv),
        wf("  (-) 税费", -step12_tax, step1_gmv, "扣减", step1_gmv),
        wf(
            "⑥ 税后净利润",
            step13_net_profit,
            step1_gmv,
            "终值",
            step1_gmv,
        ),
    ];

    let mut deductions = vec![
        ("退款", step2_refund),
        ("商品成本", step4_cogs),
        ("物流", step6_logistics),
        ("平台佣金", step7_platform_fee),
        ("广告", step8_ad),
        ("固定成本", step10_fixed),
    ];
    deductions.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let top_leak = deductions[0];
    let net_margin = pct(step13_net_profit, step1_gmv);
    let gross_margin = pct(step5_gross_profit, step1_gmv);
    let cm_margin = pct(step9_contribution, step1_gmv);

    json!({
        "has_data": true,
        "数据来源": "用户提供 + 参数估算",
        "GMV利润瀑布": waterfall,
        "核心利润率": {
            "毛利率": percent(gross_margin, 2),
            "贡献利润率": percent(cm_margin, 2),
            "净利率": percent(net_margin, 2),
            "广告ROI": if ad_spend > 0.0 { json!(round2(gmv / ad_spend)) } else { json!("无广告数据") },
            "客单价": if orders > 0.0 { json!(currency(gmv / orders)) } else { json!("N/A") },
        },
        "健康度诊断": {
            "净利率": percent(net_margin, 2),
            "评级": if net_margin >= 15.0 { "优秀" } else if net_margin >= 5.0 { "正常" } else if net_margin < 0.0 { "亏损" } else { "偏低" },
            "最大漏损项": format!("{}（占GMV {}）", top_leak.0, percent(pct(top_leak.1, step1_gmv), 1)),
            "改善建议": leak_advice(top_leak.0, top_leak.1, step1_gmv),
        }
    })
}

fn scenario_analysis(input: &Value) -> Value {
    let base_gmv = num(input, "base_gmv", 0.0);
    if base_gmv <= 0.0 {
        return json!({"error": "请提供基准GMV（月销售额）"});
    }
    let base_margin = num(input, "base_margin_pct", 30.0) / 100.0;
    let base_ad = num(input, "base_ad_pct", 8.0) / 100.0;
    let base_refund = num(input, "base_refund_pct", 5.0) / 100.0;
    let fixed_cost = num(input, "fixed_cost", 0.0);
    let opt_growth = num(input, "optimistic_growth", 30.0) / 100.0;
    let pes_growth = num(input, "pessimistic_growth", -20.0) / 100.0;

    let scenarios = vec![
        calc_scenario(
            base_gmv * (1.0 + opt_growth),
            format!("乐观 ({:+.0}% GMV)", opt_growth * 100.0),
            base_margin,
            base_ad,
            base_refund,
            fixed_cost,
        ),
        calc_scenario(
            base_gmv,
            "基准 (当前水平)".to_owned(),
            base_margin,
            base_ad,
            base_refund,
            fixed_cost,
        ),
        calc_scenario(
            base_gmv * (1.0 + pes_growth),
            format!("悲观 ({:+.0}% GMV)", pes_growth * 100.0),
            base_margin,
            base_ad,
            base_refund,
            fixed_cost,
        ),
    ];

    let price_changes = [-0.20, -0.10, 0.0, 0.10, 0.20];
    let traffic_changes = [-0.20, -0.10, 0.0, 0.10, 0.20];
    let sensitivity_matrix: Vec<_> = price_changes
        .iter()
        .map(|pc| {
            traffic_changes
                .iter()
                .map(|tc| {
                    let adj_gmv = base_gmv * (1.0 + pc) * (1.0 + tc);
                    let row = calc_scenario(
                        adj_gmv,
                        String::new(),
                        base_margin,
                        base_ad,
                        base_refund,
                        fixed_cost,
                    );
                    json!({
                        "价格变动": signed_percent(pc * 100.0, 0),
                        "流量变动": signed_percent(tc * 100.0, 0),
                        "净利润": row["税后净利润"],
                        "净利率": row["净利率"],
                    })
                })
                .collect::<Vec<_>>()
        })
        .collect();

    let net_contribution_rate = (base_margin - base_ad - 0.04 - 0.06) * (1.0 - base_refund);
    let bep_gmv = if net_contribution_rate > 0.0 {
        fixed_cost / net_contribution_rate
    } else {
        0.0
    };

    let samples =
        deterministic_risk_samples(base_gmv, base_margin, base_ad, base_refund, fixed_cost);
    let mut sorted = samples.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = sorted.len();
    let expected = average(&samples);
    let stddev = (samples.iter().map(|x| (x - expected).powi(2)).sum::<f64>() / n as f64).sqrt();
    let mc_summary = json!({
        "模拟次数": n,
        "P10（最差10%情景净利润）": percentile(&sorted, 10.0).round(),
        "P25（下四分位净利润）": percentile(&sorted, 25.0).round(),
        "P50（中位净利润）": percentile(&sorted, 50.0).round(),
        "P75（上四分位净利润）": percentile(&sorted, 75.0).round(),
        "P90（最优10%情景净利润）": percentile(&sorted, 90.0).round(),
        "亏损概率": percent(samples.iter().filter(|x| **x < 0.0).count() as f64 / n as f64 * 100.0, 1),
        "期望净利润": expected.round(),
        "净利润标准差": stddev.round(),
    });

    json!({
        "三情景P&L对比": scenarios,
        "盈亏平衡GMV": bep_gmv.round(),
        "敏感性矩阵（价格×流量）": sensitivity_matrix,
        "蒙特卡洛风险分析": mc_summary,
        "AI情景解读": "纯计算版本已完成三情景、敏感性和确定性风险模拟；未调用LLM。",
        "分析参数": {
            "基准毛利率": percent(base_margin * 100.0, 0),
            "广告费率": percent(base_ad * 100.0, 0),
            "退款率": percent(base_refund * 100.0, 0),
            "固定成本/月": fixed_cost.round(),
        },
    })
}

fn spec(
    name: &str,
    display_name: &str,
    description: &str,
    input_fields: Vec<SkillInputField>,
) -> SkillSpec {
    let mut spec = SkillSpec::new(name, display_name, description)
        .with_category(SkillCategory::Custom("accounting".to_owned()))
        .with_priority(SkillPriority::Critical)
        .with_tag("deterministic")
        .with_tag("p0")
        .with_version("0.1.0");
    for input in input_fields {
        spec = spec.with_input(input);
    }
    spec
}

fn field(name: &str, field_type: &str, description: &str, required: bool) -> SkillInputField {
    SkillInputField::new(name, description, required, field_schema(field_type))
}

fn outcome(value: Value) -> SkillOutcome {
    SkillOutcome::new(value)
}

fn invalid_input(message: String) -> SkillError {
    SkillError::execution(message)
}

fn field_schema(field_type: &str) -> Value {
    match field_type {
        "string" => json!({"type": "string"}),
        "number" => json!({"type": "number"}),
        "integer" => json!({"type": "integer"}),
        "array<number>" => json!({"type": "array", "items": {"type": "number"}}),
        "array<object>" => json!({"type": "array", "items": {"type": "object"}}),
        _ => json!({"type": "string"}),
    }
}

fn num(input: &Value, key: &str, default: f64) -> f64 {
    input[key]
        .as_f64()
        .or_else(|| input[key].as_i64().map(|v| v as f64))
        .or_else(|| input[key].as_u64().map(|v| v as f64))
        .unwrap_or(default)
}

fn string(input: &Value, key: &str, default: &str) -> String {
    input[key].as_str().unwrap_or(default).to_owned()
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn pct(numerator: f64, denominator: f64) -> f64 {
    if denominator.abs() > f64::EPSILON {
        numerator / denominator * 100.0
    } else {
        0.0
    }
}

fn percent(value: f64, decimals: usize) -> String {
    format!("{value:.decimals$}%")
}

fn signed_percent(value: f64, decimals: usize) -> String {
    format!("{value:+.decimals$}%")
}

fn currency(value: f64) -> String {
    format!("¥{:.2}", round2(value))
}

fn variance(budget: f64, actual: f64, revenue_metric: bool) -> Value {
    let abs_var = round2(actual - budget);
    let pct_var = pct(abs_var, budget.abs().max(1.0));
    let favorable = if revenue_metric {
        abs_var >= 0.0
    } else {
        abs_var <= 0.0
    };
    json!({
        "预算": round2(budget),
        "实际": round2(actual),
        "差异": abs_var,
        "差异率": signed_percent(pct_var, 1),
        "状态": if favorable { "有利" } else { "不利" },
    })
}

fn wf(step: &str, amount: f64, denominator: f64, direction: &str, _gmv: f64) -> Value {
    json!({
        "步骤": step,
        "金额": currency(amount),
        "占GMV比": if direction == "扣减" { signed_percent(pct(amount, denominator), 1) } else { percent(pct(amount, denominator), 1) },
        "方向": direction,
    })
}

fn leak_advice(name: &str, amount: f64, gmv: f64) -> String {
    let prefix = format!(
        "当前最大成本耗损来自「{}」（{}，占GMV {}）；",
        name,
        currency(amount),
        percent(pct(amount, gmv), 1)
    );
    let advice = match name {
        "广告" => "建议优先优化广告ROI，降低单次获客成本",
        "商品成本" => "建议与供应链谈判降低采购成本或提升商品附加值",
        "物流" => "建议提升包装效率或与物流谈量价",
        "退款" => "建议排查退款根因，优化商品描述和品控",
        other => return format!("{prefix}建议精细化管控{other}支出"),
    };
    format!("{prefix}{advice}")
}

fn calc_scenario(
    gmv: f64,
    label: String,
    base_margin: f64,
    base_ad: f64,
    base_refund: f64,
    fixed_cost: f64,
) -> Value {
    let net_revenue = gmv * (1.0 - base_refund);
    let gross_profit = net_revenue * base_margin;
    let ad_spend = gmv * base_ad;
    let platform_fee = gmv * 0.04;
    let logistics = gmv * 0.06;
    let contribution = gross_profit - ad_spend - platform_fee - logistics;
    let operating_profit = contribution - fixed_cost;
    let tax = (operating_profit * 0.25).max(0.0);
    let net_profit = operating_profit - tax;
    json!({
        "情景": label,
        "GMV": gmv.round(),
        "退款后净收入": net_revenue.round(),
        "毛利润": gross_profit.round(),
        "广告费": ad_spend.round(),
        "平台佣金": platform_fee.round(),
        "物流成本": logistics.round(),
        "贡献利润": contribution.round(),
        "固定成本": fixed_cost.round(),
        "营业利润": operating_profit.round(),
        "税后净利润": net_profit.round(),
        "净利率": percent(pct(net_profit, gmv), 1),
        "盈亏状态": if net_profit > 0.0 { "盈利" } else { "亏损" },
    })
}

fn deterministic_risk_samples(
    base_gmv: f64,
    base_margin: f64,
    base_ad: f64,
    base_refund: f64,
    fixed_cost: f64,
) -> Vec<f64> {
    let mut samples = Vec::with_capacity(1000);
    for i in 0 .. 1000 {
        let t = i as f64;
        let gmv_factor = (1.0 + 0.15 * (t * 12.9898).sin()).max(0.1);
        let margin = (base_margin + 0.04 * (t * 78.233).cos()).max(0.01);
        let ad = (base_ad + 0.02 * (t * 37.719).sin()).max(0.0);
        let refund = (base_refund + 0.02 * (t * 19.191).cos()).max(0.0);
        let gmv = base_gmv * gmv_factor;
        let net_revenue = gmv * (1.0 - refund);
        let gross = net_revenue * margin;
        let contribution = gross - gmv * ad - gmv * 0.04 - gmv * 0.06;
        let operating_profit = contribution - fixed_cost;
        let tax = (operating_profit * 0.25).max(0.0);
        samples.push(operating_profit - tax);
    }
    samples
}

fn average(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len().max(1) as f64
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() - 1) as f64 * p / 100.0).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}
