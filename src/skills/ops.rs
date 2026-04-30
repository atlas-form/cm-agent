use serde_json::{Value, json};

use crate::skills::{
    SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillSpec,
};

pub fn specs() -> Vec<SkillSpec> {
    vec![
        spec(
            "ops_pricing_strategy",
            "定价策略",
            "根据成本、竞品价格和平台，输出建议定价、利润率及竞品对比",
            vec![
                field("cost", "number", "单位成本（元）", true),
                field("competitor_prices", "array<number>", "竞品价格列表", true),
                field("platform", "string", "销售平台，默认淘宝", false),
            ],
        ),
        spec(
            "ops_promo_planning",
            "促销策划",
            "根据商品、预算和平台，生成促销活动阶段、KPI和预算分配",
            vec![
                field("product", "string", "商品名称或类目", true),
                field("budget", "number", "总预算（元）", true),
                field(
                    "platform",
                    "string",
                    "平台：淘宝/京东/拼多多/抖音/快手",
                    true,
                ),
            ],
        ),
        spec(
            "ops_channel_strategy",
            "渠道策略",
            "根据商品类型、预算和目标人群，输出渠道预算分配和内容计划",
            vec![
                field(
                    "product_type",
                    "string",
                    "商品类型：美妆/食品/数码/服饰/家居",
                    true,
                ),
                field("budget", "number", "月预算（元）", true),
                field("target_audience", "string", "目标人群描述", false),
            ],
        ),
        spec(
            "ops_inventory_planning",
            "库存规划",
            "根据日均销量和供货周期，计算安全库存、补货点和建议订货量",
            vec![
                field("avg_daily_demand", "number", "日均销量（件）", true),
                field("lead_time_days", "integer", "供货周期（天）", true),
                field("demand_std", "number", "需求标准差，默认日均需求20%", false),
                field(
                    "service_level",
                    "number",
                    "服务水平：0.90/0.95/0.99，默认0.95",
                    false,
                ),
            ],
        ),
        spec(
            "ops_listing_copy",
            "上架文案",
            "根据商品名称、卖点和平台，生成标题、卖点描述、关键词和问答",
            vec![
                field("product_name", "string", "商品名称", false),
                field("features", "array<string>", "商品卖点列表", false),
                field("platform", "string", "上架平台，默认淘宝", false),
                field("product_id", "integer", "商品ID，仅用于透传标识", false),
                field("competitor_titles", "array<string>", "竞品标题列表", false),
            ],
        ),
        spec(
            "ops_assortment_planning",
            "选品规划",
            "根据类目和选品预算，输出商品组合、测品预算和风险控制",
            vec![
                field("category", "string", "商品类目", true),
                field("budget", "number", "选品预算（元）", true),
            ],
        ),
        spec(
            "ops_execution_plan",
            "运营执行计划",
            "根据运营目标和阶段，生成30天行动计划、KPI体系和风险预案",
            vec![
                field("goal", "string", "运营目标", true),
                field("stage", "string", "阶段：冷启动/成长期/成熟期/衰退期", true),
                field("product_id", "integer", "主推商品ID，可选", false),
            ],
        ),
        spec(
            "ops_smart_pricing",
            "智能定价引擎",
            "基于价格弹性模型和竞品约束，计算最优利润定价区间和涨降价建议",
            vec![
                field("cost", "number", "单位成本（元）", true),
                field("current_price", "number", "当前售价（元）", true),
                field(
                    "current_monthly_sales",
                    "integer",
                    "当前月销量，默认500",
                    false,
                ),
                field("competitor_prices", "array<number>", "竞品价格列表", false),
                field("platform", "string", "平台，默认淘宝", false),
                field("elasticity", "number", "价格弹性系数，默认-1.5", false),
                field("stock_days", "integer", "当前库存天数", false),
            ],
        ),
        spec(
            "ops_inventory_optimizer",
            "库存优化计算",
            "按SKU计算EOQ、安全库存、再订货点、库存健康和ABC分类",
            vec![
                field(
                    "skus",
                    "array<object>",
                    "SKU列表：{name, monthly_sales, cost, stock_qty}",
                    false,
                ),
                field("ordering_cost", "number", "单次订货成本，默认200", false),
                field("holding_rate", "number", "年库存持有成本率%，默认25", false),
                field("lead_time_days", "integer", "供应商交货周期，默认14", false),
                field(
                    "service_level",
                    "string",
                    "服务水平：90%/95%/98%/99%，默认95%",
                    false,
                ),
            ],
        ),
        spec(
            "ops_ad_fatigue_detector",
            "广告疲劳检测",
            "检测CTR衰减、曝光频次和ROAS健康度，输出疲劳评分与预算重分配",
            vec![
                field(
                    "channels",
                    "array<object>",
                    "渠道列表：{name, ctr_baseline, ctr_recent, impressions_per_user, budget, \
                     roas}",
                    false,
                ),
                field("total_budget", "number", "总广告预算", false),
                field("platform", "string", "平台，默认淘宝", false),
            ],
        ),
        spec(
            "ops_abc_xyz_classifier",
            "ABC-XYZ库存分类",
            "ABC按GMV价值、XYZ按需求变异系数CV生成9格库存策略矩阵",
            vec![
                field(
                    "skus",
                    "array<object>",
                    "SKU列表：{sku_id, gmv, sales_history:[...]}",
                    false,
                ),
                field("platform", "string", "平台，默认淘宝", false),
                field("top_n", "integer", "展示前N个高价值SKU，默认20", false),
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
        "ops_pricing_strategy" => pricing_strategy(&input),
        "ops_promo_planning" => promo_planning(&input),
        "ops_channel_strategy" => channel_strategy(&input),
        "ops_inventory_planning" => inventory_planning(&input),
        "ops_listing_copy" => listing_copy(&input),
        "ops_assortment_planning" => assortment_planning(&input),
        "ops_execution_plan" => execution_plan(&input),
        "ops_smart_pricing" => smart_pricing(&input),
        "ops_inventory_optimizer" => inventory_optimizer(&input),
        "ops_ad_fatigue_detector" => ad_fatigue_detector(&input),
        "ops_abc_xyz_classifier" => abc_xyz_classifier(&input),
        other => return Err(invalid_input(format!("unknown ops skill: {other}"))),
    };
    Ok(outcome(value))
}

fn pricing_strategy(input: &Value) -> Value {
    let cost = num(input, "cost", 0.0);
    let competitors = nums(input, "competitor_prices");
    let platform = string(input, "platform", "淘宝");
    let avg_comp = if competitors.is_empty() {
        cost * 3.0
    } else {
        average(&competitors)
    };
    let min_comp = competitors
        .iter()
        .copied()
        .reduce(f64::min)
        .unwrap_or(cost * 2.0);
    let max_comp = competitors
        .iter()
        .copied()
        .reduce(f64::max)
        .unwrap_or(cost * 4.0);
    let commission_rate = commission_rate(&platform);
    let recommended = round2((avg_comp * 0.95).max(cost * 1.3));
    let commission = round2(recommended * commission_rate);
    let profit = round2(recommended - cost - commission);
    let margin = pct(profit, recommended);

    json!({
        "成本": cost,
        "平台": platform,
        "建议零售价": recommended,
        "平台佣金": commission,
        "单品利润": profit,
        "利润率": percent(margin, 2),
        "竞品分析": {
            "竞品均价": round2(avg_comp),
            "竞品最低价": round2(min_comp),
            "竞品最高价": round2(max_comp),
            "价格竞争力": if recommended < avg_comp { "高" } else { "中" },
        }
    })
}

fn promo_planning(input: &Value) -> Value {
    let product = string(input, "product", "通用商品");
    let budget = num(input, "budget", 10_000.0).max(0.0);
    let platform = string(input, "platform", "淘宝");
    let phases = vec![
        promo_phase(
            "预热期",
            3,
            budget,
            0.15,
            "加购+收藏",
            &["短视频种草", "优惠券预发放"],
        ),
        promo_phase(
            "爆发期",
            2,
            budget,
            0.55,
            "冲销量",
            &["限时折扣", "满减叠加", "直播带货"],
        ),
        promo_phase(
            "续航期",
            5,
            budget,
            0.20,
            "长尾转化",
            &["返场优惠", "老客复购"],
        ),
        promo_phase(
            "复盘期",
            2,
            budget,
            0.10,
            "数据复盘",
            &["ROI分析", "用户反馈收集"],
        ),
    ];
    let target_gmv = round2(budget * 5.0);
    json!({
        "has_data": true,
        "数据来源": "用户输入 + 确定性预算模型",
        "商品": product,
        "平台": platform,
        "总预算": currency(budget),
        "活动阶段": phases,
        "KPI": {
            "目标GMV": currency(target_gmv),
            "目标ROI": round1(target_gmv / budget.max(1.0)),
            "预估UV": (budget * 2.0).round() as i64,
            "预估转化率": "3.5%",
            "预算消耗节奏": "预热15% / 爆发55% / 续航20% / 复盘10%",
        },
        "风险规则": [
            "爆发期首日ROI低于目标70%时，暂停低转化素材并转投高点击素材",
            "库存可售天数低于7天时，下调券面力度并切换为预约/预售",
            "退款率连续2天高于8%时，停止扩大流量并排查商品承诺"
        ],
    })
}

fn channel_strategy(input: &Value) -> Value {
    let product_type = string(input, "product_type", "通用");
    let budget = num(input, "budget", 10_000.0).max(0.0);
    let audience = string(input, "target_audience", "泛兴趣人群");
    let weights = if product_type.contains("美妆") || product_type.contains("服饰") {
        vec![
            ("抖音/快手短视频", 0.35, "种草与转化"),
            ("小红书/内容种草", 0.25, "信任背书"),
            ("淘宝/京东搜索", 0.25, "承接成交"),
            ("私域复购", 0.15, "老客激活"),
        ]
    } else if product_type.contains("数码") || product_type.contains("家电") {
        vec![
            ("淘宝/京东搜索", 0.40, "高意向成交"),
            ("测评内容", 0.25, "参数解释"),
            ("抖音信息流", 0.20, "新品曝光"),
            ("私域复购", 0.15, "配件和延保"),
        ]
    } else {
        vec![
            ("平台搜索", 0.35, "成交承接"),
            ("短视频信息流", 0.30, "需求激发"),
            ("内容种草", 0.20, "信任建设"),
            ("私域/会员", 0.15, "复购维护"),
        ]
    };
    let mix: Vec<_> = weights
        .iter()
        .map(|(channel, weight, role)| {
            json!({
                "渠道": channel,
                "预算": currency(round2(budget * weight)),
                "占比": percent(weight * 100.0, 0),
                "角色": role,
                "核心KPI": if channel.contains("搜索") { "ROI/转化率" } else if channel.contains("私域") { "复购率/客单价" } else { "CTR/加购率" },
            })
        })
        .collect();
    json!({
        "has_data": true,
        "数据来源": "用户输入 + 类目渠道规则",
        "商品类型": product_type,
        "目标人群": audience,
        "月预算": currency(budget),
        "渠道组合": mix,
        "内容节奏": [
            {"周期": "第1周", "重点": "人群测试", "动作": "每渠道至少3组素材，筛CTR和加购率"},
            {"周期": "第2-3周", "重点": "预算放大", "动作": "将预算转向ROI前40%的渠道/素材"},
            {"周期": "第4周", "重点": "复盘沉淀", "动作": "沉淀关键词、素材脚本和复购触达包"}
        ],
    })
}

fn inventory_planning(input: &Value) -> Value {
    let avg_demand = num(input, "avg_daily_demand", 100.0);
    let lead_time = num(input, "lead_time_days", 7.0).max(0.0);
    let demand_std = num(input, "demand_std", avg_demand * 0.2).max(0.0);
    let service_level = num(input, "service_level", 0.95);
    let z = z_score(service_level);
    let safety_stock = (z * demand_std * lead_time.sqrt()).round();
    let reorder_point = (avg_demand * lead_time + safety_stock).round();
    let eoq = (2.0 * avg_demand * 365.0 * 50.0 / 2.0).sqrt().round();
    let turnover_days = if avg_demand > 0.0 {
        round1(eoq / avg_demand)
    } else {
        0.0
    };

    json!({
        "日均销量": avg_demand,
        "供货周期": format!("{}天", lead_time.round()),
        "安全库存": safety_stock,
        "补货点": reorder_point,
        "建议订货量": eoq,
        "预计周转天数": turnover_days,
        "月度采购预算参考": (avg_demand * 30.0).round(),
        "风险提示": if turnover_days > 30.0 { "库存周转 > 30天建议优化供应链" } else { "库存周转正常" },
    })
}

fn listing_copy(input: &Value) -> Value {
    let product_name = string(input, "product_name", "");
    if product_name.is_empty() && input.get("product_id").is_none() {
        return json!({
            "has_data": false,
            "提示": "请提供 product_name；纯计算版本不会按 product_id 自动读取商品信息。",
        });
    }
    let name = if product_name.is_empty() {
        format!("商品ID {}", num(input, "product_id", 0.0).round() as i64)
    } else {
        product_name
    };
    let platform = string(input, "platform", "淘宝");
    let mut features = strings(input, "features");
    if features.is_empty() {
        features = vec![
            "高性价比".to_owned(),
            "品质稳定".to_owned(),
            "适合日常使用".to_owned(),
        ];
    }
    let keywords = listing_keywords(&name, &features, &platform);
    json!({
        "has_data": true,
        "数据来源": "用户输入 + 确定性文案模板",
        "平台": platform,
        "商品": name,
        "标题版本": [
            format!("{} {} 官方同款 高性价比", name, features[0]),
            format!("{} {} 新品热卖 现货速发", name, features.join(" ")),
            format!("{} 旗舰品质 家用/送礼优选", name),
        ],
        "五点描述": features.iter().take(5).enumerate().map(|(idx, f)| {
            json!({"序号": idx + 1, "卖点": f, "表达": format!("突出「{}」，降低决策成本并强化使用场景", f)})
        }).collect::<Vec<_>>(),
        "关键词矩阵": keywords,
        "常见问答": [
            {"问": "适合什么场景？", "答": "适合日常使用、礼赠和新手入门，具体以商品规格为准。"},
            {"问": "发货和售后如何保障？", "答": "建议在详情页明确发货时效、退换规则和质保承诺。"}
        ],
    })
}

fn assortment_planning(input: &Value) -> Value {
    let category = string(input, "category", "通用");
    let budget = num(input, "budget", 50_000.0).max(0.0);
    let hero = round2(budget * 0.45);
    let test = round2(budget * 0.30);
    let profit = round2(budget * 0.15);
    let clearance = round2(budget * 0.10);
    json!({
        "has_data": true,
        "数据来源": "用户输入 + 确定性选品组合模型",
        "类目": category,
        "选品预算": currency(budget),
        "商品组合": [
            {"角色": "引流款", "预算": currency(hero), "SKU数": 2, "目标": "获取曝光与搜索权重", "毛利要求": "15%-25%"},
            {"角色": "测品款", "预算": currency(test), "SKU数": 4, "目标": "验证卖点/价格带", "毛利要求": "25%-35%"},
            {"角色": "利润款", "预算": currency(profit), "SKU数": 2, "目标": "承接复购和套装", "毛利要求": "35%+"},
            {"角色": "清仓/备用", "预算": currency(clearance), "SKU数": 1, "目标": "应对活动和库存风险", "毛利要求": "不低于现金成本"}
        ],
        "测品规则": {
            "单SKU首批采购": currency(round2(test / 4.0)),
            "放量门槛": "7天点击率>行业均值且转化率>2.5%",
            "淘汰门槛": "连续7天无加购或退款率>10%",
        },
        "风险提醒": ["避免同价格带SKU过密", "首批采购不超过预算30%", "优先选择可补货周期小于14天的供应商"],
    })
}

fn execution_plan(input: &Value) -> Value {
    let goal = string(input, "goal", "提升GMV");
    let stage = string(input, "stage", "成长期");
    json!({
        "has_data": true,
        "数据来源": "用户输入 + 确定性执行框架",
        "目标": goal,
        "阶段": stage,
        "30天执行计划": [
            {"周期": "第1周", "主题": "诊断与基线", "行动": ["确认GMV/转化/ROI基线", "梳理主推SKU和库存", "搭建日更看板"], "交付物": "基线表+问题清单"},
            {"周期": "第2周", "主题": "素材与流量测试", "行动": ["上线3组价格/利益点测试", "投放小预算渠道实验", "优化标题和详情首屏"], "交付物": "测试结论+预算调整表"},
            {"周期": "第3周", "主题": "放量与转化", "行动": ["放大ROI达标渠道", "配置优惠券/满减", "跟进客服转化话术"], "交付物": "放量计划+库存预警"},
            {"周期": "第4周", "主题": "复盘与固化", "行动": ["复盘活动ROI", "固化高转化素材", "制定下月预算"], "交付物": "复盘报告+下月计划"}
        ],
        "AARRR指标": {
            "Acquisition": "UV、CTR、渠道ROI",
            "Activation": "加购率、收藏率、详情页停留",
            "Retention": "复购率、会员触达率",
            "Revenue": "GMV、客单价、净利率",
            "Referral": "评价率、晒单率、内容转发"
        },
        "风险预案": [
            "ROI低于目标70%连续2天：收缩预算并回滚素材",
            "库存低于7天销量：暂停大券并切换预售",
            "退款率高于8%：暂停放量并排查品控/承诺"
        ],
    })
}

fn smart_pricing(input: &Value) -> Value {
    let cost = num(input, "cost", 0.0);
    let current_price = num(input, "current_price", 0.0);
    if cost <= 0.0 || current_price <= 0.0 {
        return json!({"error": "请提供有效的成本和当前售价"});
    }

    let current_sales = num(input, "current_monthly_sales", 500.0).max(1.0);
    let competitors = nums(input, "competitor_prices");
    let platform = string(input, "platform", "淘宝");
    let elasticity = num(input, "elasticity", -1.5);
    let stock_days = num(input, "stock_days", 0.0);
    let commission_rate = commission_rate(&platform);
    let current_profit_per_unit = current_price - cost - current_price * commission_rate;
    let current_margin = pct(current_profit_per_unit, current_price);
    let current_monthly_profit = current_profit_per_unit * current_sales;

    let effective_cost = cost + current_price * commission_rate;
    let lerner_optimal = if elasticity < -1.0 {
        effective_cost * elasticity / (elasticity + 1.0)
    } else {
        effective_cost * 2.0
    };

    let (avg_comp, min_comp, max_comp, price_floor, price_ceiling) = if competitors.is_empty() {
        (
            current_price,
            current_price * 0.8,
            current_price * 1.3,
            cost * 1.15,
            current_price * 1.4,
        )
    } else {
        let avg = average(&competitors);
        let min = competitors
            .iter()
            .copied()
            .reduce(f64::min)
            .unwrap_or(current_price);
        let max = competitors
            .iter()
            .copied()
            .reduce(f64::max)
            .unwrap_or(current_price);
        (avg, min, max, (cost * 1.1).max(min * 0.9), max * 1.2)
    };

    let stock_pressure_price = if stock_days > 90.0 {
        let discount = if stock_days <= 120.0 {
            0.05
        } else if stock_days <= 180.0 {
            0.10
        } else {
            0.15
        };
        Some(round2(price_floor.max(current_price * (1.0 - discount))))
    } else {
        None
    };

    let mut price_points = vec![
        (current_price * 0.90).max(price_floor),
        (current_price * 0.95).max(price_floor),
        current_price,
        (current_price * 1.05).min(price_ceiling),
        (current_price * 1.10).min(price_ceiling),
        lerner_optimal.max(price_floor).min(price_ceiling),
    ];
    if let Some(p) = stock_pressure_price {
        price_points.push(p);
    }
    price_points.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    price_points.dedup_by(|a, b| (*a - *b).abs() < 0.01);

    let mut candidates = Vec::new();
    let mut best_price = current_price;
    let mut best_profit = f64::NEG_INFINITY;
    for p in price_points {
        let demand_ratio = (p / current_price).powf(elasticity);
        let projected_sales = (current_sales * demand_ratio).round().max(1.0);
        let unit_profit = p - cost - p * commission_rate;
        let monthly_profit = unit_profit * projected_sales;
        let uplift_pct = (monthly_profit - current_monthly_profit)
            / current_monthly_profit.abs().max(1.0)
            * 100.0;
        let mut entry = json!({
            "定价": round2(p),
            "预估月销量": projected_sales as i64,
            "单品利润": round2(unit_profit),
            "利润率": percent(pct(unit_profit, p), 2),
            "月度总利润": round2(monthly_profit),
            "vs当前利润": signed_percent(uplift_pct, 1),
            "竞争定位": price_position(p, avg_comp),
        });
        if let Some(sp) = stock_pressure_price {
            if (round2(p) - sp).abs() < 0.01 {
                entry["标签"] = json!(format!("库存压力价（{}天库存）", stock_days.round()));
            }
        }
        if monthly_profit > best_profit {
            best_profit = monthly_profit;
            best_price = round2(p);
        }
        candidates.push(entry);
    }
    candidates.sort_by(|a, b| {
        b["月度总利润"]
            .as_f64()
            .partial_cmp(&a["月度总利润"].as_f64())
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let price_gap_vs_comp = pct(current_price - avg_comp, avg_comp);
    let mut result = json!({
        "当前经营状态": {
            "成本": currency(cost),
            "当前售价": currency(current_price),
            "当前利润率": percent(current_margin, 2),
            "当前月利润": currency(current_monthly_profit),
            "市场定位": if price_gap_vs_comp < -10.0 { "低价位" } else if price_gap_vs_comp > 10.0 { "高价位" } else { "中价位" },
            "vs竞品均价": signed_percent(price_gap_vs_comp, 1),
        },
        "价格弹性": {
            "弹性系数": elasticity,
            "弹性类型": elasticity_label(elasticity),
            "理论最优价格(Lerner)": currency(lerner_optimal.max(price_floor).min(price_ceiling)),
        },
        "定价方案对比": candidates,
        "推荐定价": currency(best_price),
        "推荐理由": format!("在当前弹性({elasticity})和竞品约束下，{}预期月利润最大化", currency(best_price)),
        "竞品价格区间": if competitors.is_empty() { json!("无竞品数据") } else { json!({"最低": currency(min_comp), "均价": currency(avg_comp), "最高": currency(max_comp)}) },
    });

    if let Some(sp) = stock_pressure_price {
        result["库存压力告警"] = json!({
            "当前库存天数": stock_days.round(),
            "风险等级": if stock_days > 180.0 { "高" } else if stock_days > 120.0 { "中" } else { "低" },
            "建议清仓价": currency(sp),
            "说明": format!("库存周转超过{}天，建议优先考虑库存压力价{}加速去库", stock_days.round(), currency(sp)),
        });
    }
    result
}

fn inventory_optimizer(input: &Value) -> Value {
    let skus = input["skus"].as_array().cloned().unwrap_or_default();
    if skus.is_empty() {
        return json!({
            "has_data": false,
            "提示": "请提供 skus 参数；纯计算版本不会自动读取店铺数据。",
            "示例参数": {"skus": [{"name": "SKU1", "monthly_sales": 300, "cost": 40, "stock_qty": 500}]}
        });
    }

    let ordering_cost = num(input, "ordering_cost", 200.0).max(0.01);
    let holding_rate = num(input, "holding_rate", 25.0) / 100.0;
    let lead_time = num(input, "lead_time_days", 14.0).max(1.0);
    let service_level = string(input, "service_level", "95%");
    let z = match service_level.as_str() {
        "90%" => 1.282,
        "98%" => 2.054,
        "99%" => 2.326,
        _ => 1.645,
    };

    let mut sku_results = Vec::new();
    let mut gmv_list = Vec::new();
    let mut total_inv_value = 0.0;
    let mut annual_sales_cost = 0.0;
    for (idx, sku) in skus.iter().enumerate() {
        let name = sku["name"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| format!("SKU{}", idx + 1));
        let monthly_sales = val_num(sku, "monthly_sales", 1.0).max(1.0);
        let unit_cost = val_num(sku, "cost", 1.0).max(0.01);
        let stock_qty = val_num(sku, "stock_qty", 0.0).max(0.0);
        let daily_demand = monthly_sales / 30.0;
        let annual_demand = monthly_sales * 12.0;
        let annual_holding_cost = (unit_cost * holding_rate).max(0.01);
        let eoq = (2.0 * annual_demand * ordering_cost / annual_holding_cost)
            .sqrt()
            .round()
            .max(1.0);
        let safety_stock = (z * daily_demand * 0.30 * lead_time.sqrt())
            .round()
            .max(1.0);
        let rop = (daily_demand * lead_time + safety_stock).round();
        let optimal_cycle = (eoq / daily_demand.max(0.01)).round();
        let current_stock_days = (stock_qty / daily_demand.max(0.01)).round();
        let inv_value = round2(stock_qty * unit_cost);
        total_inv_value += inv_value;
        annual_sales_cost += monthly_sales * unit_cost * 12.0;
        let health = if current_stock_days < lead_time {
            "缺货风险"
        } else if current_stock_days < lead_time + 7.0 {
            "临近再订货点"
        } else if current_stock_days > 90.0 {
            "库存积压"
        } else if current_stock_days > 60.0 {
            "偏高"
        } else {
            "健康"
        };
        sku_results.push(json!({
            "SKU": name,
            "月销量": monthly_sales.round(),
            "单位成本": currency(unit_cost),
            "当前库存": stock_qty.round(),
            "当前库存天数": format!("{}天", current_stock_days),
            "库存健康": health,
            "库存价值": currency(inv_value),
            "EOQ经济订货量": format!("{}件", eoq),
            "安全库存": format!("{}件（服务水平{}）", safety_stock, service_level),
            "再订货点(ROP)": format!("{}件", rop),
            "最优订货周期": format!("每{}天补货一次", optimal_cycle),
            "建议": match health {
                "缺货风险" => "立即补货",
                "临近再订货点" => "准备下单",
                "库存积压" => "清库促销",
                _ => "正常维护",
            }
        }));
        gmv_list.push((name, monthly_sales * unit_cost * 2.5));
    }

    gmv_list.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let total_gmv: f64 = gmv_list.iter().map(|(_, g)| *g).sum();
    let mut cumulative = 0.0;
    let mut abc_result = Vec::new();
    let mut counts = (0, 0, 0);
    for (name, gmv) in &gmv_list {
        cumulative += *gmv / total_gmv.max(1.0) * 100.0;
        let class = if cumulative <= 80.0 {
            counts.0 += 1;
            "A"
        } else if cumulative <= 95.0 {
            counts.1 += 1;
            "B"
        } else {
            counts.2 += 1;
            "C"
        };
        abc_result.push(json!({
            "SKU": name,
            "月GMV估算": currency(*gmv),
            "分类": class,
            "累计GMV占比": percent(cumulative.min(100.0), 1),
        }));
    }

    let turnover_ratio = round2(annual_sales_cost / total_inv_value.max(1.0));
    let turnover_days = (365.0 / turnover_ratio.max(0.01)).round();
    json!({
        "has_data": true,
        "数据来源": "用户提供",
        "服务水平": service_level,
        "供应商交货期": format!("{}天", lead_time.round()),
        "SKU库存优化明细": sku_results,
        "ABC分类汇总": [
            {"分类": "A", "SKU数": counts.0, "价值占比": 80, "策略": "重点保障，不允许缺货"},
            {"分类": "B", "SKU数": counts.1, "价值占比": 15, "策略": "正常补货，EOQ控制"},
            {"分类": "C", "SKU数": counts.2, "价值占比": 5, "策略": "缩减库存，清库促销"},
        ],
        "ABC分类明细": abc_result.into_iter().take(10).collect::<Vec<_>>(),
        "整体库存健康": {
            "总库存价值": currency(total_inv_value),
            "库存周转率": format!("{turnover_ratio}次/年"),
            "库存周转天数": format!("{turnover_days}天"),
            "健康状态": if turnover_days < 30.0 { "优秀" } else if turnover_days < 60.0 { "正常" } else { "偏高" },
            "行业基准": "电商库存周转<30天为优秀",
        }
    })
}

fn ad_fatigue_detector(input: &Value) -> Value {
    let channels = input["channels"].as_array().cloned().unwrap_or_default();
    if channels.is_empty() {
        return json!({
            "has_data": false,
            "提示": "请提供 channels 参数；纯计算版本不会自动读取广告数据。",
            "示例参数": {"channels": [{"name": "直通车", "ctr_baseline": 3.2, "ctr_recent": 2.4, "impressions_per_user": 6, "budget": 5000, "roas": 3.2}]}
        });
    }
    let platform = string(input, "platform", "淘宝");
    let mut total_budget = num(input, "total_budget", 0.0);
    if total_budget == 0.0 {
        total_budget = channels.iter().map(|c| val_num(c, "budget", 0.0)).sum();
    }

    let mut channel_results = Vec::new();
    let mut raw_scores = Vec::new();
    let mut fatigued_count = 0;
    for ch in &channels {
        let name = ch["name"].as_str().unwrap_or("未知渠道");
        let ctr_base = val_num(ch, "ctr_baseline", 1.0);
        let ctr_recent = val_num(ch, "ctr_recent", 1.0);
        let freq = val_num(ch, "impressions_per_user", 0.0);
        let budget = val_num(ch, "budget", 0.0);
        let roas = val_num(ch, "roas", 0.0);
        let decay_rate = if ctr_base > 0.0 {
            (ctr_base - ctr_recent) / ctr_base * 100.0
        } else {
            0.0
        };
        let mut fatigue_score = 0.0;
        if decay_rate > 15.0 {
            fatigue_score += (decay_rate * 2.0).min(50.0);
        }
        if freq >= 8.0 {
            fatigue_score += ((freq - 7.0) * 10.0).min(30.0);
        }
        if decay_rate > 25.0 {
            fatigue_score += 20.0;
        }
        fatigue_score = fatigue_score.min(100.0).round();
        let status = if fatigue_score >= 70.0 {
            fatigued_count += 1;
            "严重疲劳（立即换素材）"
        } else if fatigue_score >= 40.0 {
            fatigued_count += 1;
            "中度疲劳（本周更新创意）"
        } else if fatigue_score >= 15.0 {
            "轻微疲劳（持续监控）"
        } else {
            "正常"
        };
        let roas_health = if roas == 0.0 {
            "未知"
        } else if roas >= 4.0 {
            "高效"
        } else if roas >= 2.0 {
            "正常"
        } else {
            "低效（建议削减预算）"
        };
        raw_scores.push(fatigue_score);
        channel_results.push(json!({
            "渠道": name,
            "基线CTR": percent(ctr_base, 2),
            "近期CTR": percent(ctr_recent, 2),
            "CTR衰减率": signed_percent(decay_rate, 1),
            "曝光频次": if freq > 0.0 { format!("{:.1}次/用户", freq) } else { "未知".to_owned() },
            "疲劳评分": format!("{}/100", fatigue_score as i64),
            "疲劳状态": status,
            "广告预算": if budget > 0.0 { currency(budget) } else { "未知".to_owned() },
            "ROAS": if roas > 0.0 { format!("{roas:.2}") } else { "未知".to_owned() },
            "ROAS健康": roas_health,
        }));
    }

    let mut result = json!({
        "数据来源": "用户提供",
        "总广告预算": if total_budget > 0.0 { currency(total_budget) } else { "未知".to_owned() },
        "平台": platform,
        "行业疲劳基准": "CTR衰减>15%（连续7天）= 疲劳信号；频次≥8次/用户 = 受众疲劳",
        "渠道疲劳分析": channel_results,
        "疲劳渠道数": format!("{}/{}", fatigued_count, channels.len()),
    });

    if total_budget > 0.0 && channels.len() > 1 {
        let mut weights = Vec::new();
        for (idx, ch) in channels.iter().enumerate() {
            let roas = val_num(ch, "roas", 2.0).max(0.5);
            weights.push((roas * (1.0 - raw_scores[idx] / 150.0)).max(0.1));
        }
        let total_weight: f64 = weights.iter().sum();
        let mut reallocation = Vec::new();
        for (idx, ch) in channels.iter().enumerate() {
            let old_budget = val_num(ch, "budget", 0.0);
            let new_budget = (total_budget * weights[idx] / total_weight).round();
            reallocation.push(json!({
                "渠道": ch["name"].as_str().unwrap_or(""),
                "当前预算": currency(old_budget),
                "建议预算": currency(new_budget),
                "变化": signed_currency(new_budget - old_budget),
            }));
        }
        result["建议预算分配"] = json!(reallocation);
    }
    result
}

fn abc_xyz_classifier(input: &Value) -> Value {
    let mut skus = input["skus"].as_array().cloned().unwrap_or_default();
    if skus.is_empty() {
        skus = demo_skus();
    }
    let data_source = if input["skus"].as_array().map_or(false, |a| !a.is_empty()) {
        "用户输入"
    } else {
        "演示数据（30个确定性模拟SKU）"
    };
    let platform = string(input, "platform", "淘宝");
    let top_n = num(input, "top_n", 20.0).round().max(1.0) as usize;

    let total_gmv: f64 = skus
        .iter()
        .map(|s| val_num(s, "gmv", 0.0))
        .sum::<f64>()
        .max(1.0);
    skus.sort_by(|a, b| {
        val_num(b, "gmv", 0.0)
            .partial_cmp(&val_num(a, "gmv", 0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut classified = Vec::new();
    let mut cumulative = 0.0;
    for sku in skus {
        let gmv = val_num(&sku, "gmv", 0.0);
        cumulative += gmv / total_gmv;
        let abc = if cumulative <= 0.80 {
            "A"
        } else if cumulative <= 0.95 {
            "B"
        } else {
            "C"
        };
        let history = value_nums(&sku["sales_history"]);
        let (cv, xyz) = cv_xyz(&history);
        let sku_id = sku["sku_id"]
            .as_str()
            .or_else(|| sku["name"].as_str())
            .unwrap_or("SKU");
        classified.push(json!({
            "sku_id": sku_id,
            "gmv": gmv,
            "gmv_pct": gmv / total_gmv,
            "abc": abc,
            "xyz": xyz,
            "cv": cv,
        }));
    }

    let mut matrix = matrix_cells();
    for item in &classified {
        let cell = format!(
            "{}{}",
            item["abc"].as_str().unwrap_or("C"),
            item["xyz"].as_str().unwrap_or("Z")
        );
        if let Some((count, gmv)) = matrix.get_mut(&cell) {
            *count += 1;
            *gmv += item["gmv"].as_f64().unwrap_or(0.0);
        }
    }
    let matrix_display: serde_json::Map<String, Value> = matrix
        .iter()
        .filter(|(_, (count, _))| *count > 0)
        .map(|(cell, (count, gmv))| {
            (
                cell.clone(),
                json!({
                    "SKU数量": count,
                    "GMV贡献": percent(*gmv / total_gmv * 100.0, 1),
                    "策略": matrix_strategy(cell).0,
                    "库存倍率": matrix_strategy(cell).1,
                }),
            )
        })
        .collect();

    let top_display: Vec<_> = classified
        .iter()
        .take(top_n)
        .map(|item| {
            let class = format!(
                "{}{}",
                item["abc"].as_str().unwrap_or("?"),
                item["xyz"].as_str().unwrap_or("?")
            );
            json!({
                "SKU": item["sku_id"],
                "GMV": currency(item["gmv"].as_f64().unwrap_or(0.0)),
                "GMV占比": percent(item["gmv_pct"].as_f64().unwrap_or(0.0) * 100.0, 1),
                "CV": item["cv"],
                "分类": class,
                "策略": matrix_strategy(&class).0,
            })
        })
        .collect();

    json!({
        "数据来源": data_source,
        "平台": platform,
        "分析SKU总数": classified.len(),
        "9格矩阵": matrix_display,
        "矩阵说明": "ABC: A=前80%GMV / B=80-95% / C=尾部; XYZ: X=CV<0.2稳定 / Y=CV0.2-0.5 / Z=CV>0.5高波动",
        format!("高价值SKU(前{}个)", top_display.len()): top_display,
        "关键洞察": {
            "AX稳定高价值": matrix.get("AX").map(|v| v.0).unwrap_or(0),
            "AZ高价值高波动": matrix.get("AZ").map(|v| v.0).unwrap_or(0),
            "CZ建议清仓": matrix.get("CZ").map(|v| v.0).unwrap_or(0),
        }
    })
}

fn spec(
    name: &str,
    display_name: &str,
    description: &str,
    input_fields: Vec<SkillInputField>,
) -> SkillSpec {
    let mut spec = SkillSpec::new(name, display_name, description)
        .with_category(SkillCategory::Custom("ops".to_owned()))
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
        "array<string>" => json!({"type": "array", "items": {"type": "string"}}),
        "array<object>" => json!({"type": "array", "items": {"type": "object"}}),
        _ => json!({"type": "string"}),
    }
}

fn num(input: &Value, key: &str, default: f64) -> f64 {
    val_num(input, key, default)
}

fn val_num(value: &Value, key: &str, default: f64) -> f64 {
    value[key]
        .as_f64()
        .or_else(|| value[key].as_i64().map(|v| v as f64))
        .or_else(|| value[key].as_u64().map(|v| v as f64))
        .unwrap_or(default)
}

fn nums(input: &Value, key: &str) -> Vec<f64> {
    value_nums(&input[key])
}

fn strings(input: &Value, key: &str) -> Vec<String> {
    input[key]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn value_nums(value: &Value) -> Vec<f64> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_f64().or_else(|| v.as_i64().map(|n| n as f64)))
                .collect()
        })
        .unwrap_or_default()
}

fn string(input: &Value, key: &str, default: &str) -> String {
    input[key].as_str().unwrap_or(default).to_owned()
}

fn average(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len().max(1) as f64
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
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

fn signed_currency(value: f64) -> String {
    if value >= 0.0 {
        format!("+{}", currency(value))
    } else {
        format!("-{}", currency(value.abs()))
    }
}

fn z_score(service_level: f64) -> f64 {
    if (service_level - 0.90).abs() < 0.001 {
        1.28
    } else if (service_level - 0.99).abs() < 0.001 {
        2.33
    } else {
        1.65
    }
}

fn promo_phase(
    name: &str,
    days: i64,
    budget: f64,
    ratio: f64,
    target: &str,
    actions: &[&str],
) -> Value {
    json!({
        "阶段": name,
        "天数": days,
        "预算": currency(round2(budget * ratio)),
        "预算占比": percent(ratio * 100.0, 0),
        "目标": target,
        "动作": actions,
    })
}

fn listing_keywords(name: &str, features: &[String], platform: &str) -> Value {
    let feature_words = features.iter().take(5).cloned().collect::<Vec<_>>();
    json!({
        "核心词": [name, platform],
        "卖点词": feature_words,
        "场景词": ["家用", "送礼", "日常", "新品"],
        "转化词": ["现货", "官方", "高性价比", "售后保障"],
    })
}

fn commission_rate(platform: &str) -> f64 {
    match platform {
        "京东" => 0.08,
        "拼多多" => 0.03,
        "淘宝" | "抖音" | "快手" => 0.05,
        _ => 0.05,
    }
}

fn price_position(price: f64, avg_comp: f64) -> &'static str {
    if price < avg_comp * 0.95 {
        "低价"
    } else if price > avg_comp * 1.05 {
        "高价"
    } else {
        "中价"
    }
}

fn elasticity_label(elasticity: f64) -> &'static str {
    if elasticity <= -2.5 {
        "高弹性（价格敏感型买家，降价显著拉量）"
    } else if elasticity <= -1.5 {
        "中弹性（均衡定价，适度调整有效）"
    } else if elasticity <= -0.8 {
        "低弹性（品牌溢价空间，可适度提价）"
    } else {
        "超低弹性（刚需产品，价格影响有限）"
    }
}

fn cv_xyz(history: &[f64]) -> (f64, &'static str) {
    if history.len() < 2 {
        return (0.0, "Z");
    }
    let mean = average(history);
    if mean == 0.0 {
        return (0.0, "Z");
    }
    let variance = history.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / history.len() as f64;
    let cv = round2((variance.sqrt() / mean) * 100.0) / 100.0;
    let xyz = if cv < 0.20 {
        "X"
    } else if cv < 0.50 {
        "Y"
    } else {
        "Z"
    };
    (cv, xyz)
}

fn matrix_cells() -> std::collections::BTreeMap<String, (usize, f64)> {
    ["AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ"]
        .into_iter()
        .map(|cell| (cell.to_owned(), (0, 0.0)))
        .collect()
}

fn matrix_strategy(cell: &str) -> (&'static str, f64) {
    match cell {
        "AX" => ("持续补货，自动化补货触发，维持最低安全库存", 1.2),
        "AY" => ("提前预测补货，安全库存1.5倍需求，定期评估", 1.5),
        "AZ" => ("柔性供应链，小批量高频补货，紧密监控", 2.0),
        "BX" => ("标准补货流程，定期审查", 1.3),
        "BY" => ("适度库存缓冲，季节性调整", 1.5),
        "BZ" => ("按需补货，避免积压", 1.8),
        "CX" => ("批量采购降成本，低频补货", 1.0),
        "CY" => ("清理尾货，考虑停售低利润款", 1.0),
        _ => ("建议停售或大促清仓", 0.5),
    }
}

fn demo_skus() -> Vec<Value> {
    (0..30)
        .map(|i| {
            let gmv = (30 - i) as f64 * (30 - i) as f64 * 420.0;
            let history: Vec<f64> = (0..12)
                .map(|m| {
                    let base = (100.0 - i as f64 * 2.0).max(10.0);
                    let wave = ((m as f64 + 1.0) * (i as f64 + 3.0)).sin() * (6.0 + i as f64);
                    (base + wave).max(0.0)
                })
                .collect();
            json!({"sku_id": format!("SKU{:03}", i + 1), "gmv": round2(gmv), "sales_history": history})
        })
        .collect()
}
