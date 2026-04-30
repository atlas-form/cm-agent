use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec, object_params, optional_f64_param, optional_i64_param,
    optional_string_param, optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("creative".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        creative_video_script_spec(),
        creative_seeding_copy_spec(),
        creative_ip_branding_spec(),
        creative_content_calendar_spec(),
        creative_trend_catch_spec(),
        creative_live_script_spec(),
        creative_product_article_spec(),
        creative_title_ctr_scorer_spec(),
    ]
}

pub fn creative_video_script_spec() -> SkillSpec {
    deferred_spec(
        "creative_video_script",
        "短视频脚本",
        "根据商品和平台特点，生成短视频脚本框架；传入product_id可自动加载真实商品信息",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可从product_id自动加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "video_type",
                "视频类型：种草/测评/教程/剧情/开箱",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("duration", "目标时长（秒）", json!({"type": "integer"})),
            SkillInputField::optional(
                "platform",
                "平台：抖音/快手/小红书/B站",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息和卖点",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn creative_seeding_copy_spec() -> SkillSpec {
    deferred_spec(
        "creative_seeding_copy",
        "种草文案",
        "生成小红书/社交媒体种草文案框架；传入product_id可自动加载商品卖点",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可从product_id自动加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "selling_points",
                "核心卖点（可从product_id自动加载）",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional("target_audience", "目标人群", json!({"type": "string"})),
            SkillInputField::optional(
                "tone",
                "语气：闺蜜分享/专业测评/生活记录",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息和卖点",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn creative_ip_branding_spec() -> SkillSpec {
    deferred_spec(
        "creative_ip_branding",
        "IP定位",
        "为品牌或个人账号做IP人设定位",
        vec![
            SkillInputField::required("brand_name", "品牌/账号名称", json!({"type": "string"})),
            SkillInputField::required("category", "领域类目", json!({"type": "string"})),
            SkillInputField::optional("target_audience", "目标受众", json!({"type": "string"})),
            SkillInputField::optional("differentiator", "核心差异化点", json!({"type": "string"})),
            SkillInputField::optional(
                "platform",
                "主要运营平台，如 小红书/抖音/B站",
                json!({"type": "string"}),
            ),
        ],
    )
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

pub fn creative_trend_catch_spec() -> SkillSpec {
    deferred_spec(
        "creative_trend_catch",
        "趋势捕捉",
        "分析当前内容趋势，输出可借鉴的创意方向",
        vec![
            SkillInputField::required("category", "行业类目", json!({"type": "string"})),
            SkillInputField::optional("platform", "平台", json!({"type": "string"})),
        ],
    )
}

pub fn creative_live_script_spec() -> SkillSpec {
    deferred_spec(
        "creative_live_script",
        "直播脚本",
        "生成完整直播话术脚本，含每个阶段的真实台词；可传入product_ids自动加载商品列表",
        vec![
            SkillInputField::required(
                "duration_hours",
                "直播时长（小时）",
                json!({"type": "number"}),
            ),
            SkillInputField::optional(
                "product_count",
                "上架商品数量（无product_ids时用）",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "live_type",
                "直播类型：日常带货/大促专场/新品首发/清仓特卖",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台：抖音/快手/淘宝直播",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_ids",
                "商品ID列表，自动加载商品信息生成更真实的话术",
                json!({"type": "array", "items": {"type": "integer"}}),
            ),
        ],
    )
}

pub fn creative_product_article_spec() -> SkillSpec {
    deferred_spec(
        "creative_product_article",
        "产品软文",
        "生成完整可发布的产品软文/推广文章，含标题和正文；传入product_id自动加载商品信息",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可从product_id自动加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "article_type",
                "文章类型：测评软文/种草长文/对比评测/使用攻略/品牌故事",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "word_count",
                "目标字数，默认800字",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "platform",
                "发布平台：微信公众号/小红书/知乎/抖音",
                json!({"type": "string"}),
            ),
        ],
    )
}

fn deferred_spec(
    id: &str,
    name: &str,
    description: &str,
    inputs: Vec<SkillInputField>,
) -> SkillSpec {
    inputs.into_iter().fold(
        SkillSpec::new(id, name, description)
            .with_category(category())
            .with_priority(SkillPriority::High)
            .with_tag("deferred")
            .with_tag("content_engine"),
        SkillSpec::with_input,
    )
}

macro_rules! deferred_creative_skill {
    ($type_name:ident, $spec_fn:ident, required [$($required:literal),*], defaults {$($key:literal => $value:expr),* $(,)?}, deps [$($dep:literal),* $(,)?]) => {
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
                $(let _ = string_param(&params, $required)?;)*
                let mut normalized = serde_json::Map::new();
                $(normalized.insert($key.to_owned(), json!($value(&params)?));)*
                Ok(SkillOutcome::new(json!({
                    "status": "deferred",
                    "deferred_reason": "创意内容生成需要内容引擎、商品数据或实时趋势接入",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "deferred_dependencies": [$($dep),*],
                    "expected_outputs": ["内容框架", "可执行文案/脚本", "平台适配建议", "复盘指标"]
                })).with_summary("创意技能骨架已返回"))
            }
        }
    };
}

deferred_creative_skill!(
    CreativeVideoScript,
    creative_video_script_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "video_type" => |p: &Value| Ok(optional_string_param(p, "video_type")?.unwrap_or_else(|| "种草".to_owned())),
        "duration" => |p: &Value| Ok(optional_i64_param(p, "duration")?.unwrap_or(30)),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "抖音".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
    },
    deps ["content_engine", "product_profile", "fresh_trends"]
);
deferred_creative_skill!(
    CreativeSeedingCopy,
    creative_seeding_copy_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "selling_points" => |p: &Value| Ok(optional_string_vec_param(p, "selling_points")?.unwrap_or_default()),
        "target_audience" => |p: &Value| Ok(optional_string_param(p, "target_audience")?.unwrap_or_else(|| "年轻女性".to_owned())),
        "tone" => |p: &Value| Ok(optional_string_param(p, "tone")?.unwrap_or_else(|| "闺蜜分享".to_owned())),
    },
    deps ["content_engine", "product_profile"]
);
deferred_creative_skill!(
    CreativeIpBranding,
    creative_ip_branding_spec,
    required ["brand_name", "category"],
    defaults {
        "brand_name" => |p: &Value| string_param(p, "brand_name").map(Some),
        "category" => |p: &Value| string_param(p, "category").map(Some),
        "target_audience" => |p: &Value| Ok(optional_string_param(p, "target_audience")?.unwrap_or_else(|| "18-35岁年轻消费者".to_owned())),
        "differentiator" => |p: &Value| Ok(optional_string_param(p, "differentiator")?.unwrap_or_else(|| "专业+有温度".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "小红书/抖音".to_owned())),
    },
    deps ["content_engine"]
);
deferred_creative_skill!(
    CreativeTrendCatch,
    creative_trend_catch_spec,
    required ["category"],
    defaults {
        "category" => |p: &Value| string_param(p, "category").map(Some),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "抖音".to_owned())),
    },
    deps ["fresh_trends", "business_metrics", "content_engine"]
);
deferred_creative_skill!(
    CreativeLiveScript,
    creative_live_script_spec,
    required [],
    defaults {
        "duration_hours" => |p: &Value| Ok(optional_f64_param(p, "duration_hours")?.unwrap_or(3.0)),
        "product_count" => |p: &Value| Ok(optional_i64_param(p, "product_count")?.unwrap_or(6)),
        "live_type" => |p: &Value| Ok(optional_string_param(p, "live_type")?.unwrap_or_else(|| "日常带货".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "抖音".to_owned())),
    },
    deps ["content_engine", "product_catalog"]
);
deferred_creative_skill!(
    CreativeProductArticle,
    creative_product_article_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "article_type" => |p: &Value| Ok(optional_string_param(p, "article_type")?.unwrap_or_else(|| "测评软文".to_owned())),
        "word_count" => |p: &Value| Ok(optional_i64_param(p, "word_count")?.unwrap_or(800)),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "微信公众号".to_owned())),
    },
    deps ["content_engine", "product_profile"]
);

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
            "功能",
            "特点",
            "原因",
            "为什么",
            "如何",
            "秘密",
            "揭秘",
            "技巧",
            "方法",
            "攻略",
        ],
    ),
    (
        "Desire",
        0.25,
        &[
            "完美",
            "必买",
            "值得",
            "推荐",
            "好用",
            "高品质",
            "升级",
            "加强",
            "专业",
            "极致",
        ],
    ),
    (
        "Action",
        0.20,
        &[
            "抢",
            "买",
            "下单",
            "立即",
            "马上",
            "赶快",
            "别错过",
            "收藏",
            "加购",
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
            hits.insert((*name).to_owned(), json!(matched));
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
            "限时",
            "今天",
            "最后",
            "仅剩",
            "抢购",
            "秒杀",
            "即将",
            "截止",
            "倒计时",
            "马上",
        ],
    ),
    (
        1.6,
        &[
            "限量",
            "仅余",
            "稀缺",
            "孤品",
            "断码",
            "最后一批",
            "售完为止",
            "库存告急",
        ],
    ),
    (
        1.4,
        &[
            "爆款",
            "热卖",
            "畅销",
            "好评",
            "口碑",
            "网红",
            "明星",
            "万人",
            "口碑推荐",
            "人气",
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
            "超值",
            "划算",
            "省钱",
            "折扣",
            "优惠",
            "打折",
            "特价",
            "白菜价",
            "半价",
        ],
    ),
    (
        1.2,
        &[
            "惊喜", "感动", "完美", "心动", "必买", "真香", "安心", "放心", "幸福", "快乐",
        ],
    ),
];

fn score_triggers(title: &str) -> TriggerScore {
    let mut raw: f64 = 0.0;
    let mut words = Vec::new();
    for (weight, group) in TRIGGER_GROUPS {
        for word in *group {
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
