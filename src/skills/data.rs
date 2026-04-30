use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec,
};

fn category() -> SkillCategory {
    SkillCategory::Data
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        spec(
            "query_store_metrics",
            "查询店铺指标",
            "查询店铺经营指标，Rust迁移阶段返回确定性延迟计划",
            SkillPriority::High,
            vec![
                field("days", "integer", "查询最近N天，默认30", false),
                field(
                    "platform",
                    "string",
                    "平台筛选：taobao/jd/pdd/douyin，留空查全部",
                    false,
                ),
                field(
                    "include_anomalies",
                    "boolean",
                    "是否同时检测异常，默认true",
                    false,
                ),
            ],
            &["deferred"],
        ),
        spec(
            "data_funnel_analysis",
            "漏斗分析",
            "分析电商转化漏斗各阶段转化率并定位流失环节",
            SkillPriority::Critical,
            vec![
                field("impressions", "integer", "曝光量", false),
                field("clicks", "integer", "点击量", false),
                field("add_to_cart", "integer", "加购量", false),
                field("orders", "integer", "下单量", false),
                field("payments", "integer", "付款量", false),
                field("platform", "string", "平台", false),
                field("days", "integer", "查询天数，默认30", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_anomaly_diagnosis",
            "异常诊断",
            "基于Z-Score检测指标时序异常点",
            SkillPriority::Critical,
            vec![
                field("metric_name", "string", "指标名称，默认未知指标", false),
                field("days", "integer", "分析天数，默认14", false),
                field("values", "array<number>", "手动提供的时序数据", false),
                field("threshold", "number", "Z-Score阈值，默认2.0", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_trend_forecast",
            "趋势预测",
            "基于线性趋势预测GMV、UV、订单等核心指标",
            SkillPriority::Critical,
            vec![
                field(
                    "metric_name",
                    "string",
                    "指标：gmv/orders/uv/conversion_rate，默认gmv",
                    false,
                ),
                field("days", "integer", "历史天数，默认30", false),
                field("forecast_periods", "integer", "预测期数，默认7天", false),
                field("platform", "string", "平台筛选", false),
                field("values", "array<number>", "手动提供的历史序列", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_dashboard",
            "数据看板",
            "生成店铺核心经营指标看板，Rust迁移阶段返回延迟计划",
            SkillPriority::High,
            vec![
                field("days", "integer", "统计天数，默认30", false),
                field("platform", "string", "平台筛选，留空查全部", false),
            ],
            &["deferred"],
        ),
        spec(
            "data_customer_segmentation",
            "RFM客户分层",
            "基于RFM模型输出客户分群、规模和运营策略",
            SkillPriority::Critical,
            vec![
                field("total_customers", "integer", "总客户数", false),
                field("avg_order_value", "number", "平均客单价", false),
                field("avg_orders_per_customer", "number", "人均订单数", false),
                field("days", "integer", "数据范围，默认90天", false),
                field("recency_days", "number", "平均近期购买间隔天数", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_competitor_analysis",
            "竞品分析",
            "输入竞品信息，输出竞品对比矩阵和策略建议",
            SkillPriority::Critical,
            vec![
                field("our_product", "string", "我方商品名称", true),
                field("our_price", "number", "我方价格", false),
                field(
                    "competitors",
                    "array<object>",
                    "竞品列表：{name,price,monthly_sales,rating}",
                    false,
                ),
            ],
            &["deterministic"],
        ),
        spec(
            "data_multi_period_trend",
            "多周期趋势分析",
            "多周期环比、移动平均和趋势动能分析",
            SkillPriority::Critical,
            vec![
                field(
                    "metric",
                    "string",
                    "指标名：gmv/orders/uv/conversion_rate/ad_spend，默认gmv",
                    false,
                ),
                field(
                    "periods",
                    "array<integer>",
                    "对比周期天数列表，默认[7,14,30]",
                    false,
                ),
                field("ma_window", "integer", "移动平均窗口天数，默认7", false),
                field("values", "array<number>", "手动提供的时序数据", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_channel_roi",
            "渠道ROI归因",
            "分析各渠道广告ROI、流量效率和GMV贡献",
            SkillPriority::High,
            vec![
                field("days", "integer", "分析天数，默认30", false),
                field(
                    "include_organic",
                    "boolean",
                    "是否包含自然流量估算，默认true",
                    false,
                ),
                field(
                    "channels",
                    "array<object>",
                    "渠道列表：{name,gmv,ad_spend,orders,uv}",
                    false,
                ),
            ],
            &["deferred", "deterministic_fallback"],
        ),
        spec(
            "data_ltv_calculator",
            "LTV客户价值计算",
            "计算客户生命周期价值、CAC健康度和回收周期",
            SkillPriority::Critical,
            vec![
                field("days", "integer", "分析天数，默认90", false),
                field(
                    "churn_period_days",
                    "integer",
                    "流失判定天数，默认90",
                    false,
                ),
                field("cac", "number", "获客成本CAC", false),
                field("avg_order_value", "number", "客单价，默认120", false),
                field("purchase_frequency", "number", "月购买频次，默认1.6", false),
                field("gross_margin", "number", "毛利率%，默认35", false),
                field("monthly_retention", "number", "月留存率%，默认35", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_cohort_analysis",
            "队列留存分析",
            "生成队列留存曲线并识别关键流失节点",
            SkillPriority::Critical,
            vec![
                field("days", "integer", "分析总天数，默认90", false),
                field(
                    "cohort_unit",
                    "string",
                    "队列单位：week/month，默认week",
                    false,
                ),
                field("new_customers", "integer", "首期新客数，默认120", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_ab_test_analyzer",
            "A/B测试分析",
            "计算A/B测试转化率差异、卡方值、置信度和推全建议",
            SkillPriority::Critical,
            vec![
                field("control_visitors", "integer", "对照组访客数", true),
                field("control_conversions", "integer", "对照组转化数", true),
                field("test_visitors", "integer", "实验组访客数", true),
                field("test_conversions", "integer", "实验组转化数", true),
                field("test_name", "string", "测试名称", false),
                field("metric_name", "string", "测试指标名称", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_attribution_analysis",
            "渠道归因分析",
            "多触点渠道归因模型，Rust迁移阶段返回延迟计划",
            SkillPriority::High,
            vec![
                field("days", "integer", "分析天数，默认30", false),
                field(
                    "attribution_model",
                    "string",
                    "last_touch/linear/time_decay/position/all，默认all",
                    false,
                ),
            ],
            &["deferred"],
        ),
        spec(
            "data_refund_decomposition",
            "退款帕累托分析",
            "按渠道/类目/原因分解退款来源并识别关键问题项",
            SkillPriority::High,
            vec![
                field("days", "integer", "分析天数，默认30", false),
                field("top_n", "integer", "展示前N个问题项，默认5", false),
                field(
                    "items",
                    "array<object>",
                    "退款项：{name,refund_amount,refund_rate,gmv}",
                    false,
                ),
            ],
            &["deferred", "deterministic_fallback"],
        ),
        spec(
            "data_seasonal_decompose",
            "季节性趋势分解",
            "STL-lite趋势、季节和残差分解",
            SkillPriority::Critical,
            vec![
                field(
                    "metric",
                    "string",
                    "分解指标：gmv/orders/uv，默认gmv",
                    false,
                ),
                field("days", "integer", "分析天数，默认56", false),
                field("period", "integer", "季节周期长度，默认7", false),
                field("values", "array<number>", "手动提供的时序数据", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_price_elasticity",
            "价格弹性分析",
            "用弧弹性公式计算需求弹性和最优价格参考",
            SkillPriority::Critical,
            vec![
                field(
                    "price_points",
                    "array<object>",
                    "历史价格-销量对：{price,quantity}",
                    false,
                ),
                field("cost", "number", "单品成本", false),
                field("current_price", "number", "当前售价", false),
                field("days", "integer", "分析天数，默认60", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_demand_forecast",
            "需求预测",
            "简单指数平滑预测未来需求并输出备货建议",
            SkillPriority::Critical,
            vec![
                field(
                    "metric",
                    "string",
                    "预测指标：gmv/orders/uv，默认orders",
                    false,
                ),
                field("days", "integer", "历史数据天数，默认60", false),
                field(
                    "forecast_days",
                    "array<integer>",
                    "预测未来N天，默认[7,14,30]",
                    false,
                ),
                field("values", "array<number>", "手动提供的历史序列", false),
            ],
            &["deterministic"],
        ),
        spec(
            "data_comprehensive_diagnosis",
            "综合业务诊断",
            "全维度业务诊断编排，Rust迁移阶段仅提供延迟执行计划",
            SkillPriority::Normal,
            vec![
                field("days", "integer", "分析周期，默认30", false),
                field(
                    "focus",
                    "string",
                    "重点方向：growth/profit/retention/risk/all，默认all",
                    false,
                ),
            ],
            &["deferred"],
        ),
    ]
}

pub struct DataSkill {
    spec: SkillSpec,
}

impl DataSkill {
    pub fn new(skill_id: &str) -> Result<Self, SkillError> {
        let spec = specs()
            .into_iter()
            .find(|spec| spec.id == skill_id)
            .ok_or_else(|| SkillError::invalid_parameter("skill_id", "known data skill id"))?;
        Ok(Self { spec })
    }
}

#[async_trait]
impl Skill for DataSkill {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, context: SkillContext) -> SkillResult {
        execute(&self.spec.id, params, &context).await
    }
}

pub async fn execute(skill_name: &str, input: Value, _context: &SkillContext) -> SkillResult {
    ensure_object(&input)?;
    let output = match skill_name {
        "query_store_metrics" => deferred(
            skill_name,
            "需要接入 store_metrics 读取真实店铺指标",
            &input,
        ),
        "data_funnel_analysis" => funnel_analysis(&input),
        "data_anomaly_diagnosis" => anomaly_diagnosis(&input),
        "data_trend_forecast" => trend_forecast(&input),
        "data_dashboard" => deferred(
            skill_name,
            "需要接入 store_metrics 汇总、环比和异常检测",
            &input,
        ),
        "data_customer_segmentation" => customer_segmentation(&input),
        "data_competitor_analysis" => competitor_analysis(&input)?,
        "data_multi_period_trend" => multi_period_trend(&input),
        "data_channel_roi" => channel_roi(&input),
        "data_ltv_calculator" => ltv_calculator(&input),
        "data_cohort_analysis" => cohort_analysis(&input),
        "data_ab_test_analyzer" => ab_test_analyzer(&input)?,
        "data_attribution_analysis" => {
            deferred(skill_name, "需要真实多平台触点和渠道路径数据", &input)
        }
        "data_refund_decomposition" => refund_decomposition(&input),
        "data_seasonal_decompose" => seasonal_decompose(&input),
        "data_price_elasticity" => price_elasticity(&input),
        "data_demand_forecast" => demand_forecast(&input),
        "data_comprehensive_diagnosis" => {
            deferred(skill_name, "需要编排多个数据技能和综合报告生成", &input)
        }
        other => return Err(SkillError::invalid_parameter(other, "known data skill")),
    };
    Ok(SkillOutcome::new(output).with_summary("data skill executed"))
}

fn spec(
    id: &str,
    name: &str,
    description: &str,
    priority: SkillPriority,
    inputs: Vec<SkillInputField>,
    tags: &[&str],
) -> SkillSpec {
    let mut skill = SkillSpec::new(id, name, description)
        .with_category(category())
        .with_priority(priority);
    for input in inputs {
        skill = skill.with_input(input);
    }
    for tag in tags {
        skill = skill.with_tag(*tag);
    }
    skill
}

fn field(name: &str, kind: &str, description: &str, required: bool) -> SkillInputField {
    let schema = match kind {
        "string" => json!({"type": "string"}),
        "integer" => json!({"type": "integer"}),
        "number" => json!({"type": "number"}),
        "boolean" => json!({"type": "boolean"}),
        "array<number>" => json!({"type": "array", "items": {"type": "number"}}),
        "array<integer>" => json!({"type": "array", "items": {"type": "integer"}}),
        "array<object>" => json!({"type": "array", "items": {"type": "object"}}),
        _ => json!({"type": "object"}),
    };
    if required {
        SkillInputField::required(name, description, schema)
    } else {
        SkillInputField::optional(name, description, schema)
    }
}

fn ensure_object(input: &Value) -> Result<(), SkillError> {
    if input.is_object() {
        Ok(())
    } else {
        Err(SkillError::invalid_parameter("$", "object"))
    }
}

fn deferred(skill: &str, reason: &str, input: &Value) -> Value {
    json!({
        "status": "deferred",
        "skill": skill,
        "deferred_reason": reason,
        "received_params": input,
        "side_effects": "none",
        "expected_outputs": ["结构化指标", "诊断摘要", "可执行建议"]
    })
}

fn funnel_analysis(input: &Value) -> Value {
    let impressions = i(input, "impressions", 0).max(i(input, "clicks", 0));
    let clicks = i(input, "clicks", 0);
    let orders = i(input, "orders", 0);
    let add_to_cart = i(input, "add_to_cart", (orders as f64 * 2.5).round() as i64);
    let payments = i(input, "payments", (orders as f64 * 0.95).round() as i64);
    if impressions <= 0 && clicks <= 0 {
        return json!({"error": "暂无数据", "hint": "请提供 impressions/clicks/orders 等漏斗数据"});
    }

    let stages = [
        (
            "曝光→点击",
            impressions,
            clicks,
            5.0,
            "优化主图和标题关键词，提升点击率",
        ),
        (
            "点击→加购",
            clicks,
            add_to_cart,
            30.0,
            "改善详情页、增强价格竞争力",
        ),
        (
            "加购→下单",
            add_to_cart,
            orders,
            35.0,
            "设置限时优惠和库存紧迫感",
        ),
        (
            "下单→付款",
            orders,
            payments,
            90.0,
            "简化支付流程，增加支付方式",
        ),
    ];
    let mut bottleneck = "无明显瓶颈";
    let mut worst_gap = f64::MIN;
    let funnel = stages
        .iter()
        .filter(|(_, upper, _, _, _)| *upper > 0)
        .map(|(name, upper, lower, benchmark, _)| {
            let rate = pct(*lower as f64, *upper as f64);
            let gap = benchmark - rate;
            if gap > worst_gap {
                worst_gap = gap;
                bottleneck = name;
            }
            json!({
                "阶段": name,
                "转化率": percent(rate, 2),
                "基准值": percent(*benchmark, 1),
                "差距": percent(gap, 2),
                "状态": if gap <= 0.0 { "正常" } else if gap < 5.0 { "需关注" } else { "异常" }
            })
        })
        .collect::<Vec<_>>();
    let suggestion = stages
        .iter()
        .find(|(name, _, _, _, _)| *name == bottleneck)
        .map(|(_, _, _, _, suggestion)| *suggestion)
        .unwrap_or("持续监控各环节");

    json!({
        "数据来源": "用户提供",
        "漏斗分析": funnel,
        "整体转化率": percent(pct(payments as f64, impressions.max(1) as f64), 3),
        "瓶颈环节": bottleneck,
        "优化建议": suggestion
    })
}

fn anomaly_diagnosis(input: &Value) -> Value {
    let values = nums(input, "values");
    if values.len() < 3 {
        return json!({"error": "无真实数据且未提供足够时序数据", "required": "values至少3个数字"});
    }
    let threshold = n(input, "threshold", 2.0).abs().max(0.1);
    let mean = average(&values);
    let std = stddev(&values).max(1e-9);
    let anomalies = values
        .iter()
        .enumerate()
        .filter_map(|(idx, value)| {
            let z = (*value - mean) / std;
            (z.abs() >= threshold).then(|| {
                json!({"第几天": idx + 1, "数值": round2(*value), "Z-Score": round2(z), "方向": if z > 0.0 { "激增" } else { "骤降" }})
            })
        })
        .collect::<Vec<_>>();
    json!({
        "数据来源": "用户提供",
        "指标": s(input, "metric_name", "未知指标"),
        "样本数": values.len(),
        "均值": round2(mean),
        "标准差": round2(std),
        "异常点数": anomalies.len(),
        "异常详情": anomalies,
        "建议": if anomalies.is_empty() { "指标运行正常" } else { "立即排查异常原因" }
    })
}

fn trend_forecast(input: &Value) -> Value {
    let values = nums(input, "values");
    if values.len() < 2 {
        return json!({"error": "数据不足，无法预测", "hint": "请提供 values 至少2个数据点"});
    }
    let periods = i(input, "forecast_periods", 7).max(1) as usize;
    let (slope, intercept) = linear_fit(&values);
    let forecasts = (0..periods)
        .map(|idx| json!({"期数": format!("第{}天", idx + 1), "预测值": round2((intercept + slope * (values.len() + idx) as f64).max(0.0))}))
        .collect::<Vec<_>>();
    let avg_growth = avg_growth(&values);
    let trend = if slope > average(&values).abs() * 0.01 {
        "上升"
    } else if slope < -average(&values).abs() * 0.01 {
        "下降"
    } else {
        "平稳"
    };
    json!({
        "数据来源": "用户提供",
        "指标": metric_display(&s(input, "metric_name", "gmv")),
        "历史数据点数": values.len(),
        "最新值": round2(*values.last().unwrap_or(&0.0)),
        "趋势方向": trend,
        "平均日增长率": percent(avg_growth, 2),
        "斜率": round4(slope),
        "预测结果": forecasts,
        "置信说明": "基于线性回归，反映近期趋势，不含季节性因素"
    })
}

fn customer_segmentation(input: &Value) -> Value {
    let total = i(input, "total_customers", 1000).max(1);
    let aov = n(input, "avg_order_value", 120.0);
    let freq = n(input, "avg_orders_per_customer", 1.8);
    let recency = n(input, "recency_days", 30.0);
    let high_value = ((total as f64) * (0.12 + (freq / 20.0).min(0.08))).round() as i64;
    let loyal = ((total as f64) * 0.18).round() as i64;
    let attention = ((total as f64) * if recency <= 30.0 { 0.30 } else { 0.24 }).round() as i64;
    let lost = (total - high_value - loyal - attention).max(0);
    json!({
        "数据来源": "用户提供/默认估算",
        "客户总数": total,
        "RFM输入": {"平均客单价": round2(aov), "人均订单数": round2(freq), "平均购买间隔": round2(recency)},
        "分群": [
            {"客群标签": "Champions（冠军）", "客户数": high_value, "占比": percent(pct(high_value as f64, total as f64), 1), "策略": "专属折扣奖励，新品优先体验"},
            {"客群标签": "Loyal（忠诚客户）", "客户数": loyal, "占比": percent(pct(loyal as f64, total as f64), 1), "策略": "会员积分升级，推荐关联商品"},
            {"客群标签": "Need Attention（需关注）", "客户数": attention, "占比": percent(pct(attention as f64, total as f64), 1), "策略": "限时活动唤醒，个性化push"},
            {"客群标签": "Lost（已流失）", "客户数": lost, "占比": percent(pct(lost as f64, total as f64), 1), "策略": "低成本召回测试，无响应则停止触达"}
        ],
        "加权RFM说明": "R×0.15 + F×0.28 + M×0.57，当前为聚合输入估算"
    })
}

fn competitor_analysis(input: &Value) -> Result<Value, SkillError> {
    let product = required_string(input, "our_product")?;
    let our_price = n(input, "our_price", 100.0).max(0.01);
    let competitors = input
        .get("competitors")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_else(|| {
            vec![
                json!({"name": "竞品A", "price": our_price * 0.89, "monthly_sales": 5000, "rating": 4.7}),
                json!({"name": "竞品B", "price": our_price * 1.10, "monthly_sales": 3000, "rating": 4.5}),
            ]
        });
    let prices = competitors
        .iter()
        .map(|item| n(item, "price", our_price))
        .collect::<Vec<_>>();
    let avg_price = if prices.is_empty() {
        our_price
    } else {
        average(&prices)
    };
    let matrix = competitors
        .iter()
        .map(|item| {
            let price = n(item, "price", our_price).max(0.01);
            let diff = pct(our_price - price, price);
            json!({
                "竞品": s(item, "name", "未知"),
                "价格": round2(price),
                "月销量": i(item, "monthly_sales", 0),
                "评分": n(item, "rating", 0.0),
                "价格差异": signed_percent(diff, 1),
                "我方优势": if diff < 0.0 { "价格更低" } else { "需要用卖点或服务支撑溢价" }
            })
        })
        .collect::<Vec<_>>();
    json_ok(json!({
        "我方商品": product,
        "我方价格": round2(our_price),
        "竞品矩阵": matrix,
        "价格定位": if our_price < avg_price * 0.9 { "低价位" } else if our_price < avg_price * 1.1 { "中价位" } else { "高价位" },
        "竞品均价": round2(avg_price),
        "策略建议": ["明确差异化卖点", "监控最低价竞品", "用赠品/服务降低直接比价"]
    }))
}

fn multi_period_trend(input: &Value) -> Value {
    let values = nums(input, "values");
    if values.len() < 4 {
        return json!({"error": "数据不足", "hint": "请提供 values 至少4个数据点"});
    }
    let periods = ints(input, "periods", vec![7, 14, 30]);
    let ma_window = i(input, "ma_window", 7).max(1) as usize;
    let comparisons = periods
        .into_iter()
        .filter(|period| (*period as usize) * 2 <= values.len())
        .map(|period| {
            let p = period as usize;
            let recent = average(&values[values.len() - p..]);
            let previous = average(&values[values.len() - p * 2..values.len() - p]);
            json!({"周期": format!("{period}天"), "近期均值": round2(recent), "前期均值": round2(previous), "环比": signed_percent(pct(recent - previous, previous), 2)})
        })
        .collect::<Vec<_>>();
    let ma = moving_average(&values, ma_window);
    let (slope, _) = linear_fit(&values);
    json!({
        "指标": metric_display(&s(input, "metric", "gmv")),
        "数据点数": values.len(),
        "最新值": round2(*values.last().unwrap_or(&0.0)),
        "移动平均(最近5天)": ma.into_iter().rev().take(5).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>(),
        "多周期环比": comparisons,
        "趋势动能": if slope > 0.0 { "上行" } else if slope < 0.0 { "下行" } else { "平稳" },
        "斜率": round4(slope)
    })
}

fn channel_roi(input: &Value) -> Value {
    let channels = input
        .get("channels")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if channels.is_empty() {
        return deferred(
            "data_channel_roi",
            "需要真实渠道GMV、广告花费、订单和UV数据",
            input,
        );
    }
    let mut rows = channels
        .iter()
        .map(|channel| {
            let gmv = n(channel, "gmv", 0.0);
            let ad = n(channel, "ad_spend", 0.0);
            let orders = i(channel, "orders", 0).max(1) as f64;
            let uv = i(channel, "uv", 0).max(1) as f64;
            let roi = if ad > 0.0 { gmv / ad } else { 0.0 };
            json!({
                "渠道": s(channel, "name", "未知渠道"),
                "GMV贡献": round2(gmv),
                "广告花费": round2(ad),
                "广告ROI": round2(roi),
                "获客成本(CPO)": round2(ad / orders),
                "点击成本(CPC)": round4(ad / uv)
            })
        })
        .collect::<Vec<_>>();
    rows.sort_by(|a, b| n(b, "广告ROI", 0.0).total_cmp(&n(a, "广告ROI", 0.0)));
    json!({"has_data": true, "渠道ROI排名": rows, "优化建议": ["加码高ROI渠道", "ROI低于2的渠道优先优化创意和人群"]})
}

fn ltv_calculator(input: &Value) -> Value {
    let aov = n(input, "avg_order_value", 120.0);
    let freq = n(input, "purchase_frequency", 1.6);
    let margin = n(input, "gross_margin", 35.0) / 100.0;
    let retention = (n(input, "monthly_retention", 35.0) / 100.0).clamp(0.01, 0.95);
    let cac = n(input, "cac", 35.0);
    let ltv = aov * freq * margin / (1.0 - retention);
    json!({
        "数据来源": "用户提供/默认估算",
        "LTV": round2(ltv),
        "月贡献毛利": round2(aov * freq * margin),
        "CAC": round2(cac),
        "LTV/CAC": round2(ltv / cac.max(0.01)),
        "回收周期(月)": round2(cac / (aov * freq * margin).max(0.01)),
        "健康度": if ltv / cac.max(0.01) >= 3.0 { "健康" } else if ltv / cac.max(0.01) >= 1.5 { "可优化" } else { "风险" }
    })
}

fn cohort_analysis(input: &Value) -> Value {
    let unit = s(input, "cohort_unit", "week");
    let label = if unit == "month" { "月" } else { "周" };
    let new_customers = i(input, "new_customers", 120).max(1);
    let base = [1.0, 0.35, 0.22, 0.15, 0.10, 0.07];
    let cohorts = (0..4)
        .map(|idx| {
            let acquired = (new_customers as f64 * (0.9_f64).powi(idx)).round() as i64;
            let curve = base
                .iter()
                .take(6 - idx as usize)
                .enumerate()
                .map(|(period, rate)| json!({format!("第{period}{label}"): format!("{:.1}% ({}人)", rate * 100.0, (acquired as f64 * rate).round() as i64)}))
                .collect::<Vec<_>>();
            json!({"队列": format!("第{}{label}队列", idx + 1), "获取新客": acquired, "留存曲线": curve})
        })
        .collect::<Vec<_>>();
    json!({
        "数据来源": "指数留存模型",
        "队列留存矩阵": cohorts,
        "关键流失节点": [{"节点": format!("第1{label}后"), "流失率": "约65%", "原因": "首次体验不满意或无复购触点"}],
        "关键发现": {"首期留存率": "约35%", "最高价值窗口": format!("首次购买后第1-2{label}")}
    })
}

fn ab_test_analyzer(input: &Value) -> Result<Value, SkillError> {
    let ctrl_n = required_i(input, "control_visitors")?;
    let ctrl_c = required_i(input, "control_conversions")?;
    let test_n = required_i(input, "test_visitors")?;
    let test_c = required_i(input, "test_conversions")?;
    if ctrl_n <= 0 || test_n <= 0 {
        return Err(SkillError::invalid_parameter(
            "visitors",
            "positive integer",
        ));
    }
    if ctrl_c < 0 || test_c < 0 || ctrl_c > ctrl_n || test_c > test_n {
        return Err(SkillError::invalid_parameter(
            "conversions",
            "0 <= conversions <= visitors",
        ));
    }
    let ctrl_rate = ctrl_c as f64 / ctrl_n as f64;
    let test_rate = test_c as f64 / test_n as f64;
    let pooled = (ctrl_c + test_c) as f64 / (ctrl_n + test_n) as f64;
    let se = (pooled * (1.0 - pooled) * (1.0 / ctrl_n as f64 + 1.0 / test_n as f64)).sqrt();
    let z = if se > 0.0 {
        (test_rate - ctrl_rate) / se
    } else {
        0.0
    };
    let confidence = normal_confidence(z.abs());
    json_ok(json!({
        "测试名称": s(input, "test_name", "A/B测试"),
        "指标": s(input, "metric_name", "转化率"),
        "对照组转化率": percent(ctrl_rate * 100.0, 2),
        "实验组转化率": percent(test_rate * 100.0, 2),
        "相对提升": signed_percent(pct(test_rate - ctrl_rate, ctrl_rate), 2),
        "Z值": round4(z),
        "置信度": percent(confidence * 100.0, 2),
        "结论": if confidence >= 0.95 { "差异显著，可考虑推全" } else if confidence >= 0.90 { "接近显著，建议扩大样本" } else { "差异不显著，继续观察" }
    }))
}

fn refund_decomposition(input: &Value) -> Value {
    let items = input
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if items.is_empty() {
        return deferred(
            "data_refund_decomposition",
            "需要退款金额、退款率或渠道退款明细",
            input,
        );
    }
    let mut rows = items
        .iter()
        .map(|item| {
            let amount = n(
                item,
                "refund_amount",
                n(item, "gmv", 0.0) * n(item, "refund_rate", 0.03),
            );
            (amount, item)
        })
        .collect::<Vec<_>>();
    rows.sort_by(|a, b| b.0.total_cmp(&a.0));
    let total = rows.iter().map(|(amount, _)| *amount).sum::<f64>().max(1.0);
    let mut cumulative = 0.0;
    let top_n = i(input, "top_n", 5).max(1) as usize;
    let pareto = rows
        .iter()
        .take(top_n)
        .map(|(amount, item)| {
            let share = *amount / total * 100.0;
            cumulative += share;
            json!({"项目": s(item, "name", "未知"), "退款金额": round2(*amount), "占总退款比": percent(share, 1), "累计占比": percent(cumulative.min(100.0), 1), "是否80%内": cumulative <= 80.0})
        })
        .collect::<Vec<_>>();
    json!({"has_data": true, "总退款估算": round2(total), "帕累托分析": pareto, "整改建议": ["优先处理累计80%内的问题项", "复核详情页描述、质检和物流包装"]})
}

fn seasonal_decompose(input: &Value) -> Value {
    let series = nums(input, "values");
    let period = i(input, "period", 7).max(2) as usize;
    if series.len() < period * 2 {
        return json!({"has_data": false, "提示": format!("数据点不足（需≥{}个），当前仅{}个", period * 2, series.len())});
    }
    let ma = moving_average(&series, period);
    let trend_start = ma.first().copied().unwrap_or(series[0]);
    let trend_end = ma
        .last()
        .copied()
        .unwrap_or(*series.last().unwrap_or(&series[0]));
    let trend_growth = pct(trend_end - trend_start, trend_start);
    let mut indices = Vec::new();
    for p in 0 .. period {
        let vals = series
            .iter()
            .enumerate()
            .filter_map(|(idx, value)| (idx % period == p).then_some(*value))
            .collect::<Vec<_>>();
        indices.push(average(&vals) / average(&series).max(1e-9));
    }
    let strength = stddev(&indices) * 100.0;
    json!({
        "has_data": true,
        "指标": metric_display(&s(input, "metric", "gmv")),
        "季节周期": format!("{period}天"),
        "趋势分析": {"趋势起点": round2(trend_start), "趋势终点": round2(trend_end), "趋势增长率": signed_percent(trend_growth, 2)},
        "季节性分析": {"季节性强度": percent(strength, 1), "季节指数": indices.iter().map(|v| round4(*v)).collect::<Vec<_>>()},
        "运营建议": if trend_growth >= 0.0 { "趋势向上，峰值周期前加大备货和投放" } else { "趋势下行，优先排查流量、价格和竞品冲击" }
    })
}

fn price_elasticity(input: &Value) -> Value {
    let points = input
        .get("price_points")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_else(|| {
            vec![
                json!({"price": 90.0, "quantity": 560.0}),
                json!({"price": 110.0, "quantity": 440.0}),
            ]
        });
    let pairs = points
        .iter()
        .filter_map(|point| {
            let price = n(point, "price", 0.0);
            let qty = n(point, "quantity", 0.0);
            (price > 0.0 && qty > 0.0).then_some((price, qty))
        })
        .collect::<Vec<_>>();
    if pairs.len() < 2 {
        return json!({"error": "至少需要2个有效 price_points"});
    }
    let mut elasticities = Vec::new();
    for pair in pairs.windows(2) {
        let (p1, q1) = pair[0];
        let (p2, q2) = pair[1];
        let pct_q = (q2 - q1) / ((q1 + q2) / 2.0);
        let pct_p = (p2 - p1) / ((p1 + p2) / 2.0);
        if pct_p.abs() > 1e-9 {
            elasticities.push(pct_q / pct_p);
        }
    }
    let e = average(&elasticities);
    let cost = n(input, "cost", pairs[0].0 * 0.45);
    let current = n(
        input,
        "current_price",
        pairs.last().map(|(p, _)| *p).unwrap_or(100.0),
    );
    let optimal = if e < -1.0 {
        cost * e / (e + 1.0)
    } else {
        current
    };
    json!({
        "价格弹性系数": round4(e),
        "敏感度分类": if e.abs() > 1.5 { "高弹性" } else if e.abs() > 0.8 { "中弹性" } else { "低弹性" },
        "当前售价": round2(current),
        "Lerner最优价参考": round2(optimal.max(cost * 1.05)),
        "建议": if e < -1.0 { "需求对价格敏感，降价促量需同步核算利润" } else { "需求弹性较低，可测试小幅提价" }
    })
}

fn demand_forecast(input: &Value) -> Value {
    let values = nums(input, "values");
    if values.len() < 3 {
        return json!({"error": "数据不足", "hint": "请提供 values 至少3个数据点"});
    }
    let horizons = ints(input, "forecast_days", vec![7, 14, 30]);
    let alphas = [0.2, 0.4, 0.6, 0.8];
    let mut best_alpha = 0.2;
    let mut best_mape = f64::INFINITY;
    for alpha in alphas {
        let preds = ses_fitted(&values, alpha);
        let score = mape(&values[1 ..], &preds);
        if score < best_mape {
            best_mape = score;
            best_alpha = alpha;
        }
    }
    let level = ses_level(&values, best_alpha);
    let sigma = stddev(&values);
    let forecasts = horizons
        .into_iter()
        .map(|days| json!({"未来天数": days, "预测总量": round2(level * days as f64), "日均预测": round2(level), "置信区间": [round2((level - 1.96 * sigma).max(0.0)), round2(level + 1.96 * sigma)]}))
        .collect::<Vec<_>>();
    json!({
        "指标": metric_display(&s(input, "metric", "orders")),
        "历史数据点数": values.len(),
        "最优alpha": best_alpha,
        "MAPE": percent(best_mape, 2),
        "预测结果": forecasts,
        "备货建议": format!("按日均{}的预测水平备货，并预留约{}安全缓冲", round2(level), round2(1.65 * sigma))
    })
}

fn json_ok(value: Value) -> Result<Value, SkillError> {
    Ok(value)
}

fn required_string(input: &Value, key: &str) -> Result<String, SkillError> {
    input
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| SkillError::missing_parameter(key))
}

fn required_i(input: &Value, key: &str) -> Result<i64, SkillError> {
    input
        .get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| SkillError::missing_parameter(key))
}

fn s(input: &Value, key: &str, default: &str) -> String {
    input
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_owned()
}

fn i(input: &Value, key: &str, default: i64) -> i64 {
    input.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn n(input: &Value, key: &str, default: f64) -> f64 {
    input.get(key).and_then(Value::as_f64).unwrap_or(default)
}

fn nums(input: &Value, key: &str) -> Vec<f64> {
    input
        .get(key)
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(Value::as_f64).collect())
        .unwrap_or_default()
}

fn ints(input: &Value, key: &str, default: Vec<i64>) -> Vec<i64> {
    input
        .get(key)
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(Value::as_i64).collect())
        .filter(|values: &Vec<i64>| !values.is_empty())
        .unwrap_or(default)
}

fn average(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    }
}

fn stddev(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return 0.0;
    }
    let mean = average(values);
    (values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / values.len() as f64).sqrt()
}

fn linear_fit(values: &[f64]) -> (f64, f64) {
    let len = values.len() as f64;
    let x_mean = (len - 1.0) / 2.0;
    let y_mean = average(values);
    let numerator = values
        .iter()
        .enumerate()
        .map(|(idx, value)| (idx as f64 - x_mean) * (value - y_mean))
        .sum::<f64>();
    let denominator = (0 .. values.len())
        .map(|idx| (idx as f64 - x_mean).powi(2))
        .sum::<f64>();
    let slope = if denominator.abs() > 1e-9 {
        numerator / denominator
    } else {
        0.0
    };
    (slope, y_mean - slope * x_mean)
}

fn moving_average(values: &[f64], window: usize) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    (0 .. values.len())
        .map(|idx| {
            let start = (idx + 1).saturating_sub(window);
            round2(average(&values[start ..= idx]))
        })
        .collect()
}

fn avg_growth(values: &[f64]) -> f64 {
    let rates = values
        .windows(2)
        .filter_map(|pair| {
            (pair[0].abs() > 1e-9).then_some((pair[1] - pair[0]) / pair[0].abs() * 100.0)
        })
        .collect::<Vec<_>>();
    average(&rates)
}

fn ses_level(values: &[f64], alpha: f64) -> f64 {
    let mut level = values[0];
    for value in &values[1 ..] {
        level = alpha * *value + (1.0 - alpha) * level;
    }
    level
}

fn ses_fitted(values: &[f64], alpha: f64) -> Vec<f64> {
    let mut level = values[0];
    let mut fitted = Vec::with_capacity(values.len().saturating_sub(1));
    for value in &values[1 ..] {
        fitted.push(level);
        level = alpha * *value + (1.0 - alpha) * level;
    }
    fitted
}

fn mape(actual: &[f64], predicted: &[f64]) -> f64 {
    let errors = actual
        .iter()
        .zip(predicted)
        .filter_map(|(actual, predicted)| {
            (actual.abs() > 1e-9).then_some(((actual - predicted) / actual).abs() * 100.0)
        })
        .collect::<Vec<_>>();
    average(&errors)
}

fn normal_confidence(z_abs: f64) -> f64 {
    if z_abs >= 2.576 {
        0.99
    } else if z_abs >= 1.96 {
        0.95
    } else if z_abs >= 1.645 {
        0.90
    } else {
        (0.5 + z_abs / 3.29).min(0.89)
    }
}

fn metric_display(metric: &str) -> &str {
    match metric {
        "gmv" => "GMV",
        "orders" => "订单数",
        "uv" => "访客数(UV)",
        "conversion_rate" => "转化率",
        "ad_spend" => "广告花费",
        _ => metric,
    }
}

fn pct(num: f64, den: f64) -> f64 {
    if den.abs() < 1e-9 {
        0.0
    } else {
        num / den * 100.0
    }
}

fn percent(value: f64, decimals: usize) -> String {
    format!("{value:.decimals$}%")
}

fn signed_percent(value: f64, decimals: usize) -> String {
    format!("{value:+.decimals$}%")
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10000.0).round() / 10000.0
}
