use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, optional_i64_param, optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("design".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![design_material_spec_spec()]
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
