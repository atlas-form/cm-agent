use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec, object_params, optional_i64_param, optional_string_param,
    optional_string_vec_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("creative".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        creative_title_ctr_scorer_spec(),
        creative_content_calendar_spec(),
    ]
}

pub fn creative_title_ctr_scorer_spec() -> SkillSpec {
    SkillSpec::new(
        "creative_title_ctr_scorer",
        "标题CTR预测评分",
        "AIDA框架×情感触发词密度评分，预测商品标题点击率",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "title",
        "待评分的商品/内容标题",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音/小红书",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "target_audience",
        "目标受众描述",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "product_id",
        "商品ID，自动加载标题",
        json!({"type": "integer"}),
    ))
    .with_tag("deterministic")
}

pub struct CreativeTitleCtrScorer {
    spec: SkillSpec,
}

impl CreativeTitleCtrScorer {
    pub fn new() -> Self {
        Self {
            spec: creative_title_ctr_scorer_spec(),
        }
    }
}

impl Default for CreativeTitleCtrScorer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CreativeTitleCtrScorer {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let title = optional_string_param(&params, "title")?
            .ok_or_else(|| SkillError::missing_parameter("title"))?;
        let platform =
            optional_string_param(&params, "platform")?.unwrap_or_else(|| "淘宝".to_owned());
        let audience =
            optional_string_param(&params, "target_audience")?.unwrap_or_else(|| "通用".to_owned());
        let aida = score_aida(&title);
        let trigger = score_triggers(&title);
        let title_len = title.chars().count() as i64;
        let (lo, hi) = match platform.as_str() {
            "淘宝" => (25, 32),
            "京东" => (25, 35),
            "拼多多" => (18, 25),
            "抖音" => (12, 20),
            _ => (20, 30),
        };
        let mid = (lo + hi) / 2;
        let struct_score = if (lo ..= hi).contains(&title_len) {
            25
        } else {
            (25 - (title_len - mid).abs()).max(5)
        };
        let aida_score = (aida.weighted * 40.0).round() as i64;
        let trigger_score = (trigger.normalized * 35.0).round() as i64;
        let total = aida_score + trigger_score + struct_score;
        let base_ctr = match platform.as_str() {
            "淘宝" => 3.2,
            "京东" => 2.8,
            "拼多多" => 4.5,
            "抖音" => 5.0,
            _ => 3.5,
        };
        let predicted_ctr = ((base_ctr + (total as f64 - 50.0) * 0.03) * 100.0).round() / 100.0;
        let mut suggestions = Vec::new();
        if aida.weighted < 0.5 {
            suggestions.push("加强AIDA缺失维度：注意力、兴趣、欲望或行动号召".to_owned());
        }
        if trigger.normalized < 0.3 {
            suggestions.push("增加情感触发词（紧迫感/稀缺性/社交证明）".to_owned());
        }
        if title_len < lo {
            suggestions.push(format!("标题偏短，可补充卖点至{lo}字以上"));
        }
        if suggestions.is_empty() {
            suggestions.push("标题CTR潜力良好，可微调紧迫感词语".to_owned());
        }

        Ok(SkillOutcome::new(json!({
            "平台": platform,
            "标题": title,
            "目标受众": audience,
            "综合CTR评分": format!("{total}/100"),
            "总分": total,
            "评级": ctr_grade(total),
            "预测CTR": format!("{:.2}%（行业基准{}%）", predicted_ctr.clamp(0.5, 15.0), base_ctr),
            "分项评分": {
                "AIDA结构": {"得分": format!("{aida_score}/40"), "命中要素": aida.hits},
                "情感触发词密度": {"得分": format!("{trigger_score}/35"), "命中词": trigger.words},
                "标题结构": {"得分": format!("{struct_score}/25"), "字数": title_len, "推荐范围": format!("{lo}-{hi}字")}
            },
            "优化建议": suggestions
        })).with_summary("标题CTR预测评分已完成"))
    }
}

pub fn creative_content_calendar_spec() -> SkillSpec {
    SkillSpec::new(
        "creative_content_calendar",
        "内容排期",
        "根据运营节奏生成周/月内容发布计划",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::optional(
        "period",
        "周期：周/月",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "platforms",
        "发布平台列表",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_input(SkillInputField::optional(
        "content_types",
        "内容类型",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_input(SkillInputField::optional(
        "weekly_posts",
        "每周发布数量",
        json!({"type": "integer"}),
    ))
    .with_tag("deferred")
}

pub struct CreativeContentCalendar {
    spec: SkillSpec,
}

impl CreativeContentCalendar {
    pub fn new() -> Self {
        Self {
            spec: creative_content_calendar_spec(),
        }
    }
}

impl Default for CreativeContentCalendar {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CreativeContentCalendar {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let period = optional_string_param(&params, "period")?.unwrap_or_else(|| "月".to_owned());
        let platforms = optional_string_vec_param(&params, "platforms")?
            .unwrap_or_else(|| vec!["抖音".to_owned(), "小红书".to_owned()]);
        let content_types =
            optional_string_vec_param(&params, "content_types")?.unwrap_or_else(|| {
                vec![
                    "种草测评".to_owned(),
                    "干货教程".to_owned(),
                    "生活日常".to_owned(),
                    "互动话题".to_owned(),
                ]
            });
        let weekly_posts = optional_i64_param(&params, "weekly_posts")?
            .unwrap_or(5)
            .max(1);
        Ok(SkillOutcome::new(json!({
            "status": "deferred",
            "deferred_reason": "完整内容排期需要营销节点搜索、业务指标和内容引擎接入",
            "周期": period,
            "发布平台": platforms,
            "内容类型": content_types,
            "每周发布数量": weekly_posts,
            "排期骨架": ["主题规划", "平台适配", "发布时间", "内容形式", "目标指标", "复盘字段"]
        }))
        .with_summary("内容排期骨架已返回"))
    }
}

struct AidaScore {
    weighted: f64,
    hits: Value,
}

const AIDA_GROUPS: &[(&str, f64, &[&str])] = &[
    (
        "Attention",
        0.30,
        &[
            "限时", "爆款", "独家", "首发", "全网", "新品", "震撼", "惊喜", "超级",
        ],
    ),
    (
        "Interest",
        0.25,
        &[
            "功能", "特点", "原因", "为什么", "如何", "秘密", "揭秘", "技巧", "方法",
            "攻略",
        ],
    ),
    (
        "Desire",
        0.25,
        &[
            "完美", "必买", "值得", "推荐", "好用", "高品质", "升级", "加强", "专业",
            "极致",
        ],
    ),
    (
        "Action",
        0.20,
        &[
            "抢", "买", "下单", "立即", "马上", "赶快", "别错过", "收藏", "加购",
            "点击",
        ],
    ),
];

fn score_aida(title: &str) -> AidaScore {
    let mut weighted = 0.0;
    let mut hits = serde_json::Map::new();
    for (name, weight, words) in AIDA_GROUPS {
        let matched = words
            .iter()
            .filter(|word| title.contains(**word))
            .cloned()
            .collect::<Vec<_>>();
        let raw = (0.5 + matched.len() as f64 * 0.25).min(1.0);
        weighted += raw * weight;
        if !matched.is_empty() {
            hits.insert(name.to_owned(), json!(matched));
        }
    }
    AidaScore {
        weighted: (weighted * 1000.0).round() / 1000.0,
        hits: Value::Object(hits),
    }
}

struct TriggerScore {
    normalized: f64,
    words: Vec<&'static str>,
}

const TRIGGER_GROUPS: &[(f64, &[&str])] = &[
    (
        1.8,
        &[
            "限时", "今天", "最后", "仅剩", "抢购", "秒杀", "即将", "截止", "倒计时",
            "马上",
        ],
    ),
    (
        1.6,
        &[
            "限量", "仅余", "稀缺", "孤品", "断码", "最后一批", "售完为止", "库存告急",
        ],
    ),
    (
        1.4,
        &[
            "爆款", "热卖", "畅销", "好评", "口碑", "网红", "明星", "万人",
            "口碑推荐", "人气",
        ],
    ),
    (
        1.3,
        &[
            "官方", "正品", "认证", "权威", "专业", "国家", "医院", "大牌", "品牌",
        ],
    ),
    (
        1.5,
        &[
            "超值", "划算", "省钱", "折扣", "优惠", "打折", "特价", "白菜价", "半价",
        ],
    ),
    (
        1.2,
        &[
            "惊喜", "感动", "完美", "心动", "必买", "真香", "安心", "放心", "幸福",
            "快乐",
        ],
    ),
];

fn score_triggers(title: &str) -> TriggerScore {
    let mut raw: f64 = 0.0;
    let mut words = Vec::new();
    for (weight, group) in TRIGGER_GROUPS {
        for word in group {
            if title.contains(word) {
                raw += weight;
                words.push(*word);
            }
        }
    }
    TriggerScore {
        normalized: (raw / 3.0).min(1.0),
        words,
    }
}

fn ctr_grade(total: i64) -> &'static str {
    if total >= 85 {
        "S 高转化"
    } else if total >= 70 {
        "A 良好"
    } else if total >= 55 {
        "B 中等"
    } else if total >= 40 {
        "C 待改进"
    } else {
        "D 需重写"
    }
}
