use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, object_params, optional_i64_param, optional_string_param, optional_string_vec_param,
    string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("design".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        design_main_image_spec(),
        design_detail_page_spec(),
        design_color_scheme_spec(),
        design_material_spec_spec(),
        design_campaign_poster_spec(),
    ]
}

pub fn design_main_image_spec() -> SkillSpec {
    deferred_spec(
        "design_main_image",
        "主图设计方案",
        "AI生成完整主图设计执行方案（色值/构图/文案/素材清单），传入product_id自动加载商品信息",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可从product_id自动加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "style",
                "视觉风格：简约现代/高端奢华/年轻活力/国潮复古/科技感",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息和卖点",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "extra_requirements",
                "额外要求（如指定背景色/必须有价格/A/B测试）",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn design_detail_page_spec() -> SkillSpec {
    deferred_spec(
        "design_detail_page",
        "详情页设计方案",
        "AI生成完整详情页设计执行方案（模块文案/构图/色彩/素材清单），\
         传入product_id自动加载商品信息",
        vec![
            SkillInputField::optional(
                "product_name",
                "商品名称（可从product_id自动加载）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "platform",
                "平台：淘宝/京东/拼多多/抖音",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "style",
                "风格：简约现代/高端奢华/年轻活力/国潮",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional("extra_requirements", "额外要求", json!({"type": "string"})),
        ],
    )
}

pub fn design_color_scheme_spec() -> SkillSpec {
    deferred_spec(
        "design_color_scheme",
        "配色方案",
        "AI生成完整品牌配色方案，含具体HEX色值/使用比例/心理分析/Figma变量命名规范",
        vec![
            SkillInputField::required(
                "brand_tone",
                "品牌调性：高端奢华/年轻活力/清新自然/科技感/国潮复古/可爱萌系",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "category",
                "商品类目（如美妆/食品/数码）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载类目信息",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional("platform", "目标平台", json!({"type": "string"})),
            SkillInputField::optional(
                "extra_requirements",
                "特殊要求（如指定主色/品牌色）",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn design_material_spec_spec() -> SkillSpec {
    SkillSpec::new(
        "design_material_spec",
        "素材规范",
        "生成各平台素材技术规格清单骨架，含尺寸/格式/文件大小/命名规范/验收标准",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::required(
        "usage",
        "用途：主图套组/详情页/直通车/信息流广告/社交媒体/直播封面",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "platforms",
        "目标平台列表（如 [淘宝, 京东, 抖音]）",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_input(SkillInputField::optional(
        "product_id",
        "商品ID（提供更精准的素材建议）",
        json!({"type": "integer"}),
    ))
    .with_tag("deferred")
}

pub fn design_campaign_poster_spec() -> SkillSpec {
    deferred_spec(
        "design_campaign_poster",
        "活动海报设计",
        "AI生成完整活动海报设计方案，含色值/文案/构图/各尺寸版本规格，\
         传入product_id自动加载商品信息",
        vec![
            SkillInputField::required(
                "campaign_name",
                "活动名称（如 618大促/新品首发/年货节）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "discount_info",
                "优惠信息（如 全场5折/满300减50）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("campaign_date", "活动日期", json!({"type": "string"})),
            SkillInputField::optional(
                "style",
                "风格：热闹促销/简约高端/国潮节庆/科技感",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("platform", "主要展示平台", json!({"type": "string"})),
            SkillInputField::optional(
                "product_id",
                "商品ID，关联主推商品信息",
                json!({"type": "integer"}),
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
            .with_tag("design_engine"),
        SkillSpec::with_input,
    )
}

macro_rules! deferred_design_skill {
    ($type_name:ident, $spec_fn:ident, required [$($required:literal),*], defaults {$($key:literal => $value:expr),* $(,)?}) => {
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
                    "deferred_reason": "设计方案需要设计内容引擎、商品数据或平台素材规格库接入",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "deferred_dependencies": ["design_engine", "product_profile", "platform_specs"],
                    "expected_outputs": ["设计策略", "构图/模块", "色彩与字体", "素材清单", "验收标准"]
                })).with_summary("设计技能骨架已返回"))
            }
        }
    };
}

deferred_design_skill!(
    DesignMainImage,
    design_main_image_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "style" => |p: &Value| Ok(optional_string_param(p, "style")?.unwrap_or_else(|| "简约现代".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "extra_requirements" => |p: &Value| optional_string_param(p, "extra_requirements"),
    }
);
deferred_design_skill!(
    DesignDetailPage,
    design_detail_page_spec,
    required [],
    defaults {
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "style" => |p: &Value| Ok(optional_string_param(p, "style")?.unwrap_or_else(|| "简约现代".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "extra_requirements" => |p: &Value| optional_string_param(p, "extra_requirements"),
    }
);
deferred_design_skill!(
    DesignColorScheme,
    design_color_scheme_spec,
    required ["brand_tone"],
    defaults {
        "brand_tone" => |p: &Value| string_param(p, "brand_tone").map(Some),
        "category" => |p: &Value| optional_string_param(p, "category"),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "extra_requirements" => |p: &Value| optional_string_param(p, "extra_requirements"),
    }
);
deferred_design_skill!(
    DesignCampaignPoster,
    design_campaign_poster_spec,
    required ["campaign_name"],
    defaults {
        "campaign_name" => |p: &Value| string_param(p, "campaign_name").map(Some),
        "discount_info" => |p: &Value| Ok(optional_string_param(p, "discount_info")?.unwrap_or_else(|| "全场5折起".to_owned())),
        "campaign_date" => |p: &Value| optional_string_param(p, "campaign_date"),
        "style" => |p: &Value| Ok(optional_string_param(p, "style")?.unwrap_or_else(|| "热闹促销".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
    }
);

pub struct DesignMaterialSpec {
    spec: SkillSpec,
}

impl DesignMaterialSpec {
    pub fn new() -> Self {
        Self {
            spec: design_material_spec_spec(),
        }
    }
}

impl Default for DesignMaterialSpec {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for DesignMaterialSpec {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        let usage = string_param(&params, "usage")?;
        let platforms = optional_string_vec_param(&params, "platforms")?
            .unwrap_or_else(|| vec!["淘宝".to_owned(), "京东".to_owned(), "抖音".to_owned()]);
        let product_id = optional_i64_param(&params, "product_id")?;
        Ok(SkillOutcome::new(json!({
            "status": "deferred",
            "deferred_reason": "完整多平台规格清单需要设计内容引擎或平台规格库接入",
            "用途": usage,
            "目标平台": platforms,
            "商品ID": product_id,
            "规格骨架": {
                "尺寸": "待平台规格库补全",
                "格式": ["JPG", "PNG", "WebP"],
                "文件大小": "待平台限制补全",
                "命名规范": "{platform}_{usage}_{product_or_campaign}_{size}_{version}",
                "验收标准": ["画面主体清晰", "关键信息在安全区内", "文案无违规词", "导出文件符合平台体积限制"]
            }
        })).with_summary("素材规范骨架已返回"))
    }
}
