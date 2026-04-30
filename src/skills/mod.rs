pub mod accounting;
pub mod coordination;
pub mod creative;
pub mod data;
pub mod design;
pub mod engineering;
pub mod ops;
pub mod search;
pub mod service;
pub mod spec;
pub mod web;

pub use spec::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec, bool_param, f64_param, i64_param, object_params, optional_bool_param,
    optional_f64_param, optional_i64_param, optional_string_param, optional_string_vec_param,
    optional_u64_param, param, string_param, string_vec_param, u64_param,
};

/// Specs that have been ported into Rust but are intentionally not registered
/// into the runtime tool graph yet.
pub fn staged_specs() -> Vec<SkillSpec> {
    let mut specs = Vec::new();
    specs.extend(accounting::specs());
    specs.extend(coordination::specs());
    specs.extend(creative::specs());
    specs.extend(data::specs());
    specs.extend(design::specs());
    specs.extend(engineering::specs());
    specs.extend(ops::specs());
    specs.extend(search::specs());
    specs.extend(service::specs());
    specs.extend(web::specs());
    specs
}

#[cfg(test)]
mod tests {
    use super::staged_specs;

    #[test]
    fn staged_specs_include_first_port_batch() {
        let specs = staged_specs();
        let ids = specs
            .iter()
            .map(|spec| spec.id.as_str())
            .collect::<Vec<_>>();

        assert!(ids.contains(&"ops_pricing_strategy"));
        assert!(ids.contains(&"ops_abc_xyz_classifier"));
        assert!(ids.contains(&"service_return_handler"));
        assert!(ids.contains(&"service_sentiment_analyzer"));
        assert!(ids.contains(&"accounting_cost_calc"));
        assert!(ids.contains(&"accounting_scenario_analysis"));
        assert!(ids.contains(&"search_trends"));
        assert!(ids.contains(&"data_funnel_analysis"));
        assert!(ids.contains(&"data_ab_test_analyzer"));
        assert!(ids.contains(&"creative_title_ctr_scorer"));
        assert!(ids.contains(&"web_title_seo_scorer"));
        assert!(ids.contains(&"engineering_sla_monitor"));
        assert!(ids.contains(&"coordination_agent_handoff"));
        assert_eq!(specs.len(), 93);
    }
}
