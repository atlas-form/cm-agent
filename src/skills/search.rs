use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, object_params, optional_i64_param, optional_string_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("search".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        search_trends_spec(),
        search_competitor_spec(),
        search_market_info_spec(),
        search_product_reviews_spec(),
        search_platform_policy_spec(),
        search_industry_benchmarks_spec(),
    ]
}

pub fn search_trends_spec() -> SkillSpec {
    search_spec(
        "search_trends",
        "市场趋势搜索",
        "实时搜索热卖商品、爆款话题、消费趋势、流行风格",
        vec![
            SkillInputField::required(
                "query",
                "搜索关键词，如商品类目、品类名称、热门话题",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台（淘宝/抖音/小红书/京东/拼多多，可选）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "days",
                "搜索最近N天内信息（默认30天）",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn search_competitor_spec() -> SkillSpec {
    search_spec(
        "search_competitor",
        "竞品信息搜索",
        "实时搜索竞争对手商品信息、定价策略、促销活动、用户评价和差异化卖点",
        vec![
            SkillInputField::required(
                "query",
                "竞品关键词，如商品名称、品牌名、品类名",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台（淘宝/京东/拼多多/抖音，可选）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "focus",
                "分析重点：price/promotion/reviews/features",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn search_market_info_spec() -> SkillSpec {
    search_spec(
        "search_market_info",
        "市场行情搜索",
        "实时搜索行业市场报告、政策法规变化、平台规则更新、行业新闻和市场数据",
        vec![
            SkillInputField::required(
                "query",
                "行业/话题关键词，如行业名、政策名、市场主题",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "topic",
                "搜索类型：general/news/finance",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "days",
                "搜索最近N天内信息（默认7天）",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn search_product_reviews_spec() -> SkillSpec {
    search_spec(
        "search_product_reviews",
        "用户评价搜索",
        "实时搜索指定商品或品牌的真实用户评价、口碑反馈、好差评分布",
        vec![
            SkillInputField::required(
                "query",
                "商品名称、品牌名或品类关键词",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台（淘宝/京东/拼多多/抖音，可选）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "focus",
                "关注点：positive/negative/overall",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn search_platform_policy_spec() -> SkillSpec {
    search_spec(
        "search_platform_policy",
        "平台政策搜索",
        "实时搜索电商平台最新规则变化、算法调整、流量政策、佣金费率、大促规则",
        vec![
            SkillInputField::required(
                "platform",
                "平台名称（淘宝/京东/拼多多/抖音/小红书等）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "policy_type",
                "政策类型：algorithm/fee/promotion/content/general",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "days",
                "搜索最近N天内信息（默认30天）",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn search_industry_benchmarks_spec() -> SkillSpec {
    search_spec(
        "search_industry_benchmarks",
        "行业基准搜索",
        "实时搜索行业转化率、客单价、NPS、DSR、退款率、广告ROI等KPI基准数据",
        vec![
            SkillInputField::required(
                "industry",
                "行业类目（如：美妆/服装/3C/食品/家居等）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "metric",
                "关注指标：conversion/aov/nps/refund/roi/general",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台（可选，不填则返回全平台基准）",
                json!({"type": "string"}),
            ),
        ],
    )
}

fn search_spec(id: &str, name: &str, description: &str, inputs: Vec<SkillInputField>) -> SkillSpec {
    inputs.into_iter().fold(
        SkillSpec::new(id, name, description)
            .with_category(category())
            .with_priority(SkillPriority::High)
            .with_tag("deferred")
            .with_tag("fresh_data"),
        SkillSpec::with_input,
    )
}

macro_rules! deferred_search_skill {
    ($type_name:ident, $spec_fn:ident, $required:literal, $builder:expr) => {
        pub struct $type_name {
            spec: SkillSpec,
        }

        impl $type_name {
            pub fn new() -> Self {
                Self { spec: $spec_fn() }
            }
        }

        impl Default for $type_name {
            fn default() -> Self {
                Self::new()
            }
        }

        #[async_trait]
        impl Skill for $type_name {
            fn spec(&self) -> &SkillSpec {
                &self.spec
            }

            async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
                object_params(&params)?;
                let required_value = string_param(&params, $required)?;
                let query = $builder(&params, &required_value)?;
                Ok(SkillOutcome::new(json!({
                    "status": "deferred",
                    "deferred_reason": "搜索后端尚未接入Rust skill runtime",
                    "skill": self.spec.id,
                    "required_input": required_value,
                    "planned_query": query,
                    "expected_outputs": ["分析摘要", "结构化要点", "sources", "_search_engine"]
                })).with_summary("搜索技能骨架已返回"))
            }
        }
    };
}

deferred_search_skill!(
    SearchTrends,
    search_trends_spec,
    "query",
    build_trends_query
);
deferred_search_skill!(
    SearchCompetitor,
    search_competitor_spec,
    "query",
    build_competitor_query
);
deferred_search_skill!(
    SearchMarketInfo,
    search_market_info_spec,
    "query",
    build_market_info_query
);
deferred_search_skill!(
    SearchProductReviews,
    search_product_reviews_spec,
    "query",
    build_reviews_query
);
deferred_search_skill!(
    SearchPlatformPolicy,
    search_platform_policy_spec,
    "platform",
    build_policy_query
);
deferred_search_skill!(
    SearchIndustryBenchmarks,
    search_industry_benchmarks_spec,
    "industry",
    build_benchmark_query
);

fn build_trends_query(params: &Value, query: &str) -> Result<String, crate::skills::SkillError> {
    let platform = optional_string_param(params, "platform")?.unwrap_or_else(|| "电商".to_owned());
    let days = optional_i64_param(params, "days")?.unwrap_or(30);
    Ok(format!(
        "{query} {platform} 热销 趋势 爆款 2026 最近{days}天"
    ))
}

fn build_competitor_query(
    params: &Value,
    query: &str,
) -> Result<String, crate::skills::SkillError> {
    let platform = optional_string_param(params, "platform")?.unwrap_or_default();
    let focus = optional_string_param(params, "focus")?.unwrap_or_default();
    let focus_text = match focus.as_str() {
        "price" => "价格 定价 价位 售价",
        "promotion" => "促销 活动 优惠 折扣 限时",
        "reviews" => "用户评价 口碑 差评 真实体验",
        "features" => "卖点 功能特点 优势 核心参数",
        _ => "价格 卖点 评价 优惠 竞品对比",
    };
    Ok(format!("{query} {platform} {focus_text} 2026"))
}

fn build_market_info_query(
    params: &Value,
    query: &str,
) -> Result<String, crate::skills::SkillError> {
    let days = optional_i64_param(params, "days")?.unwrap_or(7);
    let recency = if days <= 7 {
        "最新".to_owned()
    } else {
        format!("{days}天内")
    };
    Ok(format!("{query} {recency} 行业动态 市场分析 2026"))
}

fn build_reviews_query(params: &Value, query: &str) -> Result<String, crate::skills::SkillError> {
    let platform = optional_string_param(params, "platform")?.unwrap_or_default();
    let focus = optional_string_param(params, "focus")?.unwrap_or_else(|| "overall".to_owned());
    let focus_text = match focus.as_str() {
        "positive" => "好评 亮点 优点 推荐 满意 值得买",
        "negative" => "差评 缺点 投诉 退货 踩雷 槽点",
        _ => "用户评价 口碑 真实反馈 购买体验 评测",
    };
    Ok(format!("{query} {platform} {focus_text} 2026"))
}

fn build_policy_query(params: &Value, platform: &str) -> Result<String, crate::skills::SkillError> {
    let policy_type =
        optional_string_param(params, "policy_type")?.unwrap_or_else(|| "general".to_owned());
    let type_text = match policy_type.as_str() {
        "algorithm" => "搜索算法 流量规则 排名机制 权重调整",
        "fee" => "佣金 费率 扣点 服务费 收费标准",
        "promotion" => "大促规则 活动报名 双11 618 商家政策",
        "content" => "内容规范 违禁词 审核标准 发布规定 违规处罚",
        _ => "规则变化 政策更新 新规 公告",
    };
    Ok(format!("{platform} {type_text} 最新 2026"))
}

fn build_benchmark_query(
    params: &Value,
    industry: &str,
) -> Result<String, crate::skills::SkillError> {
    let platform = optional_string_param(params, "platform")?.unwrap_or_else(|| "电商".to_owned());
    let metric = optional_string_param(params, "metric")?.unwrap_or_else(|| "general".to_owned());
    let metric_text = match metric.as_str() {
        "conversion" => "转化率 成交转化 CVR 行业均值 基准",
        "aov" => "客单价 平均订单金额 消费水平 均值",
        "nps" => "NPS 净推荐值 客户满意度 行业标准 平均分",
        "refund" => "退款率 退货率 售后率 行业水平 均值",
        "roi" => "广告ROI 投产比 ROAS 营销效率 基准",
        _ => "行业数据 KPI基准 平均水平 运营指标 数据报告",
    };
    Ok(format!("{industry} {platform} {metric_text} 2025 2026"))
}
