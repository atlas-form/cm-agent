use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec, object_params, optional_f64_param, optional_i64_param,
    optional_string_param, optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("web".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        web_seo_optimize_spec(),
        web_title_generator_spec(),
        web_page_conversion_spec(),
        web_store_design_spec(),
        web_keyword_research_spec(),
        web_product_description_spec(),
        web_title_seo_scorer_spec(),
    ]
}

pub fn web_seo_optimize_spec() -> SkillSpec {
    deferred_spec(
        "web_seo_optimize",
        "SEO优化方案",
        "AI生成完整SEO方案（关键词矩阵/标题优化/内容改写清单/技术SEO检查/提排路径），\
         自动关联真实商品和平台数据",
        vec![
            SkillInputField::optional(
                "target_keywords",
                "目标关键词列表（可从product_id自动生成）",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional(
                "current_rank",
                "当前排名（可选）",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息生成针对性SEO方案",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn web_title_generator_spec() -> SkillSpec {
    deferred_spec(
        "web_title_generator",
        "标题生成",
        "根据商品信息生成多种风格的SEO友好标题；传入product_id可自动加载真实商品",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可自动从product_id加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "keywords",
                "核心关键词（可从商品卖点自动生成）",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional(
                "style",
                "风格：营销型/信息型/品牌型",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，用于自动加载商品信息",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "platform",
                "目标平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn web_title_seo_scorer_spec() -> SkillSpec {
    SkillSpec::new(
        "web_title_seo_scorer",
        "标题SEO评分",
        "对电商商品标题进行SEO质量评分：关键词密度、主关键词位置、字符长度和可读性",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "title",
        "待评分的商品标题",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "target_keywords",
        "目标关键词列表（主关键词放第一位）",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "category",
        "商品类目",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "product_id",
        "商品ID，自动加载标题和关键词",
        json!({"type": "integer"}),
    ))
    .with_tag("deterministic")
}

pub struct WebTitleSeoScorer {
    spec: SkillSpec,
}

impl WebTitleSeoScorer {
    pub fn new() -> Self {
        Self {
            spec: web_title_seo_scorer_spec(),
        }
    }
}

impl Default for WebTitleSeoScorer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for WebTitleSeoScorer {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let title = optional_string_param(&params, "title")?
            .ok_or_else(|| SkillError::missing_parameter("title"))?;
        let keywords = optional_string_vec_param(&params, "target_keywords")?.unwrap_or_default();
        let platform =
            optional_string_param(&params, "platform")?.unwrap_or_else(|| "淘宝".to_owned());
        let len = title.chars().count() as i64;
        let (lo, hi) = match platform.as_str() {
            "淘宝" => (28, 34),
            "京东" => (28, 36),
            "拼多多" => (20, 28),
            "抖音" => (15, 22),
            _ => (24, 34),
        };
        let mut deductions = Vec::new();
        let len_score = if (lo ..= hi).contains(&len) {
            25
        } else if len < lo {
            let score = (25 - (lo - len) * 2).max(0);
            deductions.push(format!(
                "标题偏短({len}字 < 推荐{lo}字)，损失{}分",
                25 - score
            ));
            score
        } else {
            let score = (25 - (len - hi)).max(5);
            deductions.push(format!(
                "标题偏长({len}字 > 推荐{hi}字)，损失{}分",
                25 - score
            ));
            score
        };

        let primary = keywords.first().cloned().unwrap_or_default();
        let (position_score, pos_bonus) = if primary.is_empty() {
            (15, 0)
        } else {
            let bonus = keyword_position_score(&title, &primary);
            if bonus < 0 {
                deductions.push(format!("主关键词「{primary}」未在标题中出现，扣20分"));
            } else if bonus < 20 {
                deductions.push(format!("主关键词「{primary}」位置靠后，建议移至前30%"));
            }
            (
                (if title.contains(&primary) { 10 } else { 0 } + bonus).clamp(0, 30),
                bonus,
            )
        };

        let (kw_score, coverage_pct, missing) = if keywords.is_empty() {
            (15, 0.0, Vec::new())
        } else {
            let covered = keywords
                .iter()
                .filter(|kw| title.to_lowercase().contains(&kw.to_lowercase()))
                .count();
            let coverage = covered as f64 / keywords.len() as f64;
            let missing = keywords
                .iter()
                .filter(|kw| !title.to_lowercase().contains(&kw.to_lowercase()))
                .cloned()
                .collect::<Vec<_>>();
            if coverage < 0.6 {
                deductions.push(format!(
                    "关键词覆盖率{:.0}%，未包含: {}",
                    coverage * 100.0,
                    missing
                        .iter()
                        .take(3)
                        .cloned()
                        .collect::<Vec<_>>()
                        .join(", ")
                ));
            }
            ((coverage * 25.0).round() as i64, coverage, missing)
        };

        let special = title
            .chars()
            .filter(|c| "！？!?★☆▶◆●■□[]【】《》<>~～".contains(*c))
            .count() as i64;
        let repeat_penalty = repeated_char_penalty(&title);
        let readable_score = (20 - special * 2 - repeat_penalty).max(0);
        if special > 3 {
            deductions.push(format!("特殊符号过多({special}个)，影响可读性"));
        }
        let total = len_score + position_score + kw_score + readable_score;
        let suggestions = build_seo_suggestions(&primary, pos_bonus, &missing, len, lo, hi);

        Ok(SkillOutcome::new(json!({
            "平台": platform,
            "标题": title,
            "综合SEO评分": format!("{total}/100"),
            "总分": total,
            "评级": seo_grade(total),
            "分项评分": {
                "字符长度": {"得分": len_score, "满分": 25, "实际字数": len, "推荐范围": format!("{lo}-{hi}字")},
                "主关键词位置": {"得分": position_score, "满分": 30, "关键词": primary, "位置分": pos_bonus},
                "关键词覆盖": {"得分": kw_score, "满分": 25, "覆盖率": format!("{:.0}%", coverage_pct * 100.0)},
                "可读性": {"得分": readable_score, "满分": 20, "特殊符号数": special}
            },
            "扣分项": if deductions.is_empty() { vec!["无明显扣分项".to_owned()] } else { deductions },
            "优化建议": suggestions
        })).with_summary("标题SEO评分已完成"))
    }
}

pub fn web_page_conversion_spec() -> SkillSpec {
    SkillSpec::new(
        "web_page_conversion",
        "页面转化优化",
        "分析页面转化瓶颈，输出优化方案；可接入真实转化率数据",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::required(
        "page_type",
        "页面类型：首页/商品详情/购物车/结算/列表页",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "current_conversion",
        "当前转化率(%)",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "bounce_rate",
        "跳出率(%)",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "avg_stay_time",
        "平均停留时间(秒)",
        json!({"type": "number"}),
    ))
    .with_tag("deferred")
}

pub fn web_store_design_spec() -> SkillSpec {
    deferred_spec(
        "web_store_design",
        "店铺装修方案",
        "AI生成完整店铺装修设计方案（视觉定位/模块结构/尺寸规格/文案方向/素材清单/验收标准），\
         基于店铺真实数据个性化定制",
        vec![
            SkillInputField::required(
                "store_type",
                "店铺类型：旗舰店/专营店/个人店",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("category", "主营类目", json!({"type": "string"})),
            SkillInputField::optional("platform", "平台", json!({"type": "string"})),
            SkillInputField::optional("brand_tone", "品牌调性", json!({"type": "string"})),
        ],
    )
}

pub fn web_keyword_research_spec() -> SkillSpec {
    deferred_spec(
        "web_keyword_research",
        "关键词研究",
        "AI拓展关键词矩阵（核心词/长尾词/蓝海词/场景词），分析搜索意图、竞争度和出价策略，\
         可自动关联真实商品",
        vec![
            SkillInputField::optional(
                "seed_keyword",
                "种子关键词（可从product_id自动提取）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("category", "商品类目", json!({"type": "string"})),
            SkillInputField::optional(
                "platform",
                "平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动以商品名为种子词",
                json!({"type": "integer"}),
            ),
        ],
    )
}

pub fn web_product_description_spec() -> SkillSpec {
    deferred_spec(
        "web_product_description",
        "详情页文案",
        "生成完整的详情页营销文案（首屏卖点/痛点/解决方案/亮点/行动号召）；\
         传入product_id自动加载商品信息",
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
                "platform",
                "目标平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "style",
                "文案风格：营销型/信息型/故事型",
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

macro_rules! deferred_web_skill {
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
                    "deferred_reason": "Web/SEO技能需要内容引擎、商品数据、搜索数据或店铺指标接入",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "deferred_dependencies": [$($dep),*],
                    "expected_outputs": ["关键词/内容策略", "页面结构建议", "实验清单", "复盘指标"]
                })).with_summary("Web技能骨架已返回"))
            }
        }
    };
}

deferred_web_skill!(
    WebSeoOptimize,
    web_seo_optimize_spec,
    required [],
    defaults {
        "target_keywords" => |p: &Value| Ok(optional_string_vec_param(p, "target_keywords")?.unwrap_or_else(|| vec!["电商".to_owned()])),
        "current_rank" => |p: &Value| Ok(optional_i64_param(p, "current_rank")?.unwrap_or(0)),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
    },
    deps ["content_engine", "product_profile", "fresh_search_data"]
);
deferred_web_skill!(
    WebTitleGenerator,
    web_title_generator_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "keywords" => |p: &Value| Ok(optional_string_vec_param(p, "keywords")?.unwrap_or_default()),
        "style" => |p: &Value| Ok(optional_string_param(p, "style")?.unwrap_or_else(|| "营销型".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
    },
    deps ["content_engine", "product_profile"]
);
deferred_web_skill!(
    WebStoreDesign,
    web_store_design_spec,
    required ["store_type"],
    defaults {
        "store_type" => |p: &Value| string_param(p, "store_type").map(Some),
        "category" => |p: &Value| Ok(optional_string_param(p, "category")?.unwrap_or_else(|| "通用".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "brand_tone" => |p: &Value| Ok(optional_string_param(p, "brand_tone")?.unwrap_or_else(|| "专业".to_owned())),
    },
    deps ["store_metrics", "design_engine"]
);
deferred_web_skill!(
    WebKeywordResearch,
    web_keyword_research_spec,
    required [],
    defaults {
        "seed_keyword" => |p: &Value| optional_string_param(p, "seed_keyword"),
        "category" => |p: &Value| Ok(optional_string_param(p, "category")?.unwrap_or_else(|| "通用".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
    },
    deps ["fresh_search_data", "product_profile", "content_engine"]
);
deferred_web_skill!(
    WebProductDescription,
    web_product_description_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "style" => |p: &Value| Ok(optional_string_param(p, "style")?.unwrap_or_else(|| "营销型".to_owned())),
    },
    deps ["content_engine", "product_profile"]
);

pub struct WebPageConversion {
    spec: SkillSpec,
}

impl WebPageConversion {
    pub fn new() -> Self {
        Self {
            spec: web_page_conversion_spec(),
        }
    }
}

impl Default for WebPageConversion {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for WebPageConversion {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        let page_type = string_param(&params, "page_type")?;
        let conv = optional_f64_param(&params, "current_conversion")?.unwrap_or(3.0);
        let bounce = optional_f64_param(&params, "bounce_rate")?.unwrap_or(55.0);
        let stay = optional_f64_param(&params, "avg_stay_time")?.unwrap_or(45.0);
        let benchmark = match page_type.as_str() {
            "首页" => 5.0,
            "商品详情" => 4.0,
            "购物车" => 65.0,
            "结算" => 80.0,
            "列表页" => 8.0,
            _ => 5.0,
        };
        Ok(SkillOutcome::new(json!({
            "status": "deferred",
            "deferred_reason": "完整页面转化方案需要内容引擎、真实指标或搜索增强接入",
            "页面类型": page_type,
            "当前数据": {"转化率": format!("{conv}%"), "跳出率": format!("{bounce}%"), "停留时间": format!("{stay}秒")},
            "行业基准转化率": format!("{benchmark}%"),
            "初步判断": if conv >= benchmark { "当前转化率达到或超过基准，优先做AB测试微调" } else { "当前转化率低于基准，优先检查首屏信息、信任背书、CTA和价格呈现" },
            "建议后续": ["接入近30天漏斗数据", "按页面模块拆分点击与流失", "生成可执行AB测试清单"]
        })).with_summary("页面转化优化骨架已返回"))
    }
}

fn keyword_position_score(title: &str, keyword: &str) -> i64 {
    let Some(byte_idx) = title.to_lowercase().find(&keyword.to_lowercase()) else {
        return -20;
    };
    let char_idx = title[.. byte_idx].chars().count() as f64;
    let relative = char_idx / title.chars().count().max(1) as f64;
    if relative < 0.20 {
        30
    } else if relative < 0.40 {
        20
    } else if relative < 0.60 {
        10
    } else if relative < 0.80 {
        5
    } else {
        0
    }
}

fn repeated_char_penalty(title: &str) -> i64 {
    let mut last = None;
    let mut penalty = 0;
    for ch in title.chars() {
        if Some(ch) == last && !ch.is_ascii_digit() {
            penalty += 2;
        }
        last = Some(ch);
    }
    penalty
}

fn seo_grade(total: i64) -> &'static str {
    if total >= 85 {
        "S (优秀)"
    } else if total >= 70 {
        "A (良好)"
    } else if total >= 55 {
        "B (及格)"
    } else if total >= 40 {
        "C (需改进)"
    } else {
        "D (较差)"
    }
}

fn build_seo_suggestions(
    primary: &str,
    pos_bonus: i64,
    missing: &[String],
    len: i64,
    lo: i64,
    hi: i64,
) -> Vec<String> {
    let mut out = Vec::new();
    if !primary.is_empty() && pos_bonus < 20 {
        out.push(format!("将主关键词「{primary}」移至标题最前30%字符"));
    }
    if !missing.is_empty() {
        out.push(format!(
            "补充关键词: {}",
            missing
                .iter()
                .take(2)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if len < lo || len > hi {
        out.push("控制字符数在推荐范围内".to_owned());
    }
    if out.is_empty() {
        out.push("标题质量良好，维持现有结构".to_owned());
    }
    out
}
