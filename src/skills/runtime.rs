use std::collections::{BTreeMap, BTreeSet};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use thiserror::Error;

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillOutcome, SkillSpec, accounting,
    coordination, creative, data, design, engineering, ops, search, service, staged_specs, web,
};

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct SkillId(pub String);

impl SkillId {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<&str> for SkillId {
    fn from(value: &str) -> Self {
        Self::new(value)
    }
}

impl From<String> for SkillId {
    fn from(value: String) -> Self {
        Self::new(value)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SkillRequest {
    pub request_id: String,
    pub skill_id: SkillId,
    #[serde(default)]
    pub input: Value,
    #[serde(default)]
    pub context: SkillContext,
}

impl SkillRequest {
    pub fn new(request_id: impl Into<String>, skill_id: impl Into<SkillId>, input: Value) -> Self {
        Self {
            request_id: request_id.into(),
            skill_id: skill_id.into(),
            input,
            context: SkillContext::default(),
        }
    }

    pub fn with_context(mut self, context: SkillContext) -> Self {
        self.context = context;
        self
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkillExecutionStatus {
    Success,
    Deferred,
    PendingApproval,
    NotFound,
    ValidationError,
    Failed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkillRiskLevel {
    Low,
    Medium,
    High,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkillExecutionMode {
    Native,
    Deferred,
    Adapter,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkillAdapterKind {
    Search,
    Llm,
    Db,
    Platform,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SkillExecutionResult {
    pub request_id: String,
    pub skill_id: SkillId,
    pub status: SkillExecutionStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl SkillExecutionResult {
    fn from_outcome(
        request: &SkillRequest,
        spec: &SkillSpec,
        outcome: SkillOutcome,
        mode: SkillExecutionMode,
    ) -> Self {
        let mut metadata = outcome.metadata;
        add_runtime_metadata(&mut metadata, spec, mode);
        let status = status_from_output(&outcome.output).unwrap_or(SkillExecutionStatus::Success);
        Self {
            request_id: request.request_id.clone(),
            skill_id: request.skill_id.clone(),
            status,
            output: Some(outcome.output),
            summary: outcome.summary,
            metadata,
            error: None,
        }
    }

    fn error(
        request: &SkillRequest,
        status: SkillExecutionStatus,
        message: impl Into<String>,
    ) -> Self {
        Self {
            request_id: request.request_id.clone(),
            skill_id: request.skill_id.clone(),
            status,
            output: None,
            summary: None,
            metadata: Map::new(),
            error: Some(message.into()),
        }
    }
}

#[derive(Debug, Error, PartialEq)]
pub enum SkillRuntimeError {
    #[error("duplicate skill id '{0}'")]
    DuplicateSkillId(String),
}

#[derive(Clone, Debug)]
pub struct SkillRegistry {
    specs: BTreeMap<SkillId, SkillSpec>,
}

impl SkillRegistry {
    pub fn staged() -> Result<Self, SkillRuntimeError> {
        Self::from_specs(staged_specs())
    }

    pub fn from_specs(specs: Vec<SkillSpec>) -> Result<Self, SkillRuntimeError> {
        let mut by_id = BTreeMap::new();
        for spec in specs {
            let id = SkillId::new(spec.id.clone());
            if by_id.insert(id.clone(), spec).is_some() {
                return Err(SkillRuntimeError::DuplicateSkillId(id.0));
            }
        }
        Ok(Self { specs: by_id })
    }

    pub fn get(&self, id: impl Into<SkillId>) -> Option<&SkillSpec> {
        self.specs.get(&id.into())
    }

    pub fn all(&self) -> Vec<&SkillSpec> {
        self.specs.values().collect()
    }

    pub fn len(&self) -> usize {
        self.specs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.specs.is_empty()
    }

    pub fn by_category(&self, category: &SkillCategory) -> Vec<&SkillSpec> {
        self.specs
            .values()
            .filter(|spec| &spec.category == category)
            .collect()
    }

    pub fn by_tag(&self, tag: &str) -> Vec<&SkillSpec> {
        self.specs
            .values()
            .filter(|spec| spec.tags.iter().any(|value| value == tag))
            .collect()
    }

    pub fn by_execution_mode(&self, mode: SkillExecutionMode) -> Vec<&SkillSpec> {
        self.specs
            .values()
            .filter(|spec| execution_mode(spec) == mode)
            .collect()
    }
}

#[async_trait]
pub trait SkillExecutor: Send + Sync {
    async fn execute(&self, request: SkillRequest) -> SkillExecutionResult;
}

#[derive(Clone, Debug)]
pub struct LocalSkillExecutor {
    registry: SkillRegistry,
}

impl LocalSkillExecutor {
    pub fn new(registry: SkillRegistry) -> Self {
        Self { registry }
    }

    pub fn staged() -> Result<Self, SkillRuntimeError> {
        Ok(Self::new(SkillRegistry::staged()?))
    }

    pub fn registry(&self) -> &SkillRegistry {
        &self.registry
    }
}

#[async_trait]
impl SkillExecutor for LocalSkillExecutor {
    async fn execute(&self, request: SkillRequest) -> SkillExecutionResult {
        if let Err(message) = validate_request_envelope(&request) {
            return SkillExecutionResult::error(
                &request,
                SkillExecutionStatus::ValidationError,
                message,
            );
        }

        let Some(spec) = self.registry.get(request.skill_id.clone()) else {
            return SkillExecutionResult::error(
                &request,
                SkillExecutionStatus::NotFound,
                "skill id not found",
            );
        };

        if let Err(error) = validate_request(spec, &request.input) {
            return SkillExecutionResult::error(
                &request,
                SkillExecutionStatus::ValidationError,
                error.to_string(),
            );
        }

        let mode = execution_mode(spec);
        let outcome = run_known_skill(
            spec.id.as_str(),
            request.input.clone(),
            request.context.clone(),
        )
        .await;
        match outcome {
            Ok(outcome) => SkillExecutionResult::from_outcome(&request, spec, outcome, mode),
            Err(error) => SkillExecutionResult::error(
                &request,
                SkillExecutionStatus::Failed,
                error.to_string(),
            ),
        }
    }
}

#[derive(Clone, Debug)]
pub struct SkillExecutorSet {
    local: LocalSkillExecutor,
}

impl SkillExecutorSet {
    pub fn staged() -> Result<Self, SkillRuntimeError> {
        Ok(Self {
            local: LocalSkillExecutor::staged()?,
        })
    }

    pub fn registry(&self) -> &SkillRegistry {
        self.local.registry()
    }
}

#[async_trait]
impl SkillExecutor for SkillExecutorSet {
    async fn execute(&self, request: SkillRequest) -> SkillExecutionResult {
        self.local.execute(request).await
    }
}

fn validate_request_envelope(request: &SkillRequest) -> Result<(), &'static str> {
    if request.request_id.trim().is_empty() {
        return Err("request_id is required");
    }
    if request.skill_id.as_str().trim().is_empty() {
        return Err("skill_id is required");
    }
    Ok(())
}

fn validate_request(spec: &SkillSpec, input: &Value) -> Result<(), SkillError> {
    let object = input
        .as_object()
        .ok_or_else(|| SkillError::invalid_parameter("$", "object"))?;
    for field in &spec.inputs {
        if field.required && !object.contains_key(&field.name) {
            return Err(SkillError::missing_parameter(field.name.clone()));
        }
    }
    Ok(())
}

fn execution_mode(spec: &SkillSpec) -> SkillExecutionMode {
    if spec.tags.iter().any(|tag| tag == "deferred") {
        SkillExecutionMode::Deferred
    } else if adapter_kinds(spec).is_empty() {
        SkillExecutionMode::Native
    } else {
        SkillExecutionMode::Adapter
    }
}

fn adapter_kinds(spec: &SkillSpec) -> BTreeSet<SkillAdapterKind> {
    let mut kinds = BTreeSet::new();
    let category = custom_category(spec);
    if category == Some("search") {
        kinds.insert(SkillAdapterKind::Search);
        kinds.insert(SkillAdapterKind::Llm);
    }
    if spec.tags.iter().any(|tag| tag == "content_engine") {
        kinds.insert(SkillAdapterKind::Llm);
    }
    if spec
        .tags
        .iter()
        .any(|tag| tag == "design_engine" || tag == "analysis_engine")
    {
        kinds.insert(SkillAdapterKind::Llm);
    }
    if category == Some("data") && spec.tags.iter().any(|tag| tag == "deferred") {
        kinds.insert(SkillAdapterKind::Db);
    }
    if spec.tags.iter().any(|tag| tag == "pending_approval") || spec.id.starts_with("platform_") {
        kinds.insert(SkillAdapterKind::Platform);
    }
    kinds
}

fn risk_level(spec: &SkillSpec) -> SkillRiskLevel {
    if spec.tags.iter().any(|tag| tag == "pending_approval") || spec.id.starts_with("platform_") {
        SkillRiskLevel::High
    } else if spec.tags.iter().any(|tag| tag == "deferred")
        || custom_category(spec) == Some("search")
    {
        SkillRiskLevel::Medium
    } else {
        SkillRiskLevel::Low
    }
}

fn add_runtime_metadata(
    metadata: &mut Map<String, Value>,
    spec: &SkillSpec,
    mode: SkillExecutionMode,
) {
    metadata.insert("execution_mode".to_string(), json!(mode));
    metadata.insert("risk_level".to_string(), json!(risk_level(spec)));
    metadata.insert(
        "approval_required".to_string(),
        json!(risk_level(spec) == SkillRiskLevel::High),
    );
    let adapters = adapter_kinds(spec).into_iter().collect::<Vec<_>>();
    if !adapters.is_empty() {
        metadata.insert("adapter_requirements".to_string(), json!(adapters));
    }
}

fn status_from_output(output: &Value) -> Option<SkillExecutionStatus> {
    let status = output.get("status")?.as_str()?;
    match status {
        "deferred" => Some(SkillExecutionStatus::Deferred),
        "pending_approval" => Some(SkillExecutionStatus::PendingApproval),
        _ => None,
    }
}

fn custom_category(spec: &SkillSpec) -> Option<&str> {
    match &spec.category {
        SkillCategory::Custom(value) => Some(value.as_str()),
        _ => None,
    }
}

async fn run_known_skill(
    id: &str,
    input: Value,
    context: SkillContext,
) -> Result<SkillOutcome, SkillError> {
    match custom_category_from_id(id) {
        "ops" => return ops::execute(id, input, &context).await,
        "accounting" => return accounting::execute(id, input, &context).await,
        "data" | "query" => return data::execute(id, input, &context).await,
        _ => {}
    }

    match id {
        "search_trends" => search::SearchTrends::new().run(input, context).await,
        "search_competitor" => search::SearchCompetitor::new().run(input, context).await,
        "search_market_info" => search::SearchMarketInfo::new().run(input, context).await,
        "search_product_reviews" => {
            search::SearchProductReviews::new()
                .run(input, context)
                .await
        }
        "search_platform_policy" => {
            search::SearchPlatformPolicy::new()
                .run(input, context)
                .await
        }
        "search_industry_benchmarks" => {
            search::SearchIndustryBenchmarks::new()
                .run(input, context)
                .await
        }
        "service_ticket_handler" => {
            service::ServiceTicketHandler::new()
                .run(input, context)
                .await
        }
        "service_faq_playbook" => service::ServiceFaqPlaybook::new().run(input, context).await,
        "service_escalation_flow" => {
            service::ServiceEscalationFlow::new()
                .run(input, context)
                .await
        }
        "service_dsr_improvement" => {
            service::ServiceDsrImprovement::new()
                .run(input, context)
                .await
        }
        "service_return_handler" => {
            service::ServiceReturnHandler::new()
                .run(input, context)
                .await
        }
        "service_query_product" => {
            service::ServiceQueryProduct::new()
                .run(input, context)
                .await
        }
        "service_nps_analyzer" => service::ServiceNpsAnalyzer::new().run(input, context).await,
        "service_sentiment_analyzer" => {
            service::ServiceSentimentAnalyzer::new()
                .run(input, context)
                .await
        }
        "service_nps_driver_analysis" => {
            service::ServiceNpsDriverAnalysis::new()
                .run(input, context)
                .await
        }
        "creative_video_script" => {
            creative::CreativeVideoScript::new()
                .run(input, context)
                .await
        }
        "creative_seeding_copy" => {
            creative::CreativeSeedingCopy::new()
                .run(input, context)
                .await
        }
        "creative_ip_branding" => {
            creative::CreativeIpBranding::new()
                .run(input, context)
                .await
        }
        "creative_content_calendar" => {
            creative::CreativeContentCalendar::new()
                .run(input, context)
                .await
        }
        "creative_trend_catch" => {
            creative::CreativeTrendCatch::new()
                .run(input, context)
                .await
        }
        "creative_live_script" => {
            creative::CreativeLiveScript::new()
                .run(input, context)
                .await
        }
        "creative_product_article" => {
            creative::CreativeProductArticle::new()
                .run(input, context)
                .await
        }
        "creative_title_ctr_scorer" => {
            creative::CreativeTitleCtrScorer::new()
                .run(input, context)
                .await
        }
        "web_seo_optimize" => web::WebSeoOptimize::new().run(input, context).await,
        "web_title_generator" => web::WebTitleGenerator::new().run(input, context).await,
        "web_page_conversion" => web::WebPageConversion::new().run(input, context).await,
        "web_store_design" => web::WebStoreDesign::new().run(input, context).await,
        "web_keyword_research" => web::WebKeywordResearch::new().run(input, context).await,
        "web_product_description" => web::WebProductDescription::new().run(input, context).await,
        "web_title_seo_scorer" => web::WebTitleSeoScorer::new().run(input, context).await,
        "design_main_image" => design::DesignMainImage::new().run(input, context).await,
        "design_detail_page" => design::DesignDetailPage::new().run(input, context).await,
        "design_color_scheme" => design::DesignColorScheme::new().run(input, context).await,
        "design_material_spec" => design::DesignMaterialSpec::new().run(input, context).await,
        "design_campaign_poster" => {
            design::DesignCampaignPoster::new()
                .run(input, context)
                .await
        }
        "engineering_arch_review" => {
            engineering::EngineeringArchReview::new()
                .run(input, context)
                .await
        }
        "engineering_bug_analysis" => {
            engineering::EngineeringBugAnalysis::new()
                .run(input, context)
                .await
        }
        "engineering_perf_optimize" => {
            engineering::EngineeringPerfOptimize::new()
                .run(input, context)
                .await
        }
        "engineering_tech_selection" => {
            engineering::EngineeringTechSelection::new()
                .run(input, context)
                .await
        }
        "engineering_system_check" => {
            engineering::EngineeringSystemCheck::new()
                .run(input, context)
                .await
        }
        "engineering_sla_monitor" => {
            engineering::EngineeringSlaMonitor::new()
                .run(input, context)
                .await
        }
        "coordination_agent_handoff" => {
            coordination::CoordinationAgentHandoff::new()
                .run(input, context)
                .await
        }
        "coordination_task_orchestration" => {
            coordination::CoordinationTaskOrchestration::new()
                .run(input, context)
                .await
        }
        "coordination_status_query" => {
            coordination::CoordinationStatusQuery::new()
                .run(input, context)
                .await
        }
        "coordination_create_campaign" => {
            coordination::CoordinationCreateCampaign::new()
                .run(input, context)
                .await
        }
        "coordination_create_task" => {
            coordination::CoordinationCreateTask::new()
                .run(input, context)
                .await
        }
        "coordination_save_memory" => {
            coordination::CoordinationSaveMemory::new()
                .run(input, context)
                .await
        }
        "platform_update_price" => {
            coordination::PlatformUpdatePrice::new()
                .run(input, context)
                .await
        }
        "platform_update_inventory" => {
            coordination::PlatformUpdateInventory::new()
                .run(input, context)
                .await
        }
        "platform_sync_products" => {
            coordination::PlatformSyncProducts::new()
                .run(input, context)
                .await
        }
        "coordination_skill_chain_planner" => {
            coordination::CoordinationSkillChainPlanner::new()
                .run(input, context)
                .await
        }
        "coordination_external_agent_call" => {
            coordination::CoordinationExternalAgentCall::new()
                .run(input, context)
                .await
        }
        _ => Err(SkillError::execution(format!(
            "skill executor missing: {id}"
        ))),
    }
}

fn custom_category_from_id(id: &str) -> &str {
    id.split_once('_').map(|(prefix, _)| prefix).unwrap_or(id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn staged_registry_loads_all_specs() {
        let registry = SkillRegistry::staged().expect("registry should load");
        assert_eq!(registry.len(), 93);
        assert!(registry.get("ops_pricing_strategy").is_some());
        assert!(registry.get("missing_skill").is_none());
    }

    #[test]
    fn registry_rejects_duplicate_ids() {
        let specs = vec![
            SkillSpec::new("dup", "A", "one"),
            SkillSpec::new("dup", "B", "two"),
        ];
        assert!(matches!(
            SkillRegistry::from_specs(specs),
            Err(SkillRuntimeError::DuplicateSkillId(id)) if id == "dup"
        ));
    }

    #[test]
    fn registry_filters_by_category_tag_and_mode() {
        let registry = SkillRegistry::staged().expect("registry should load");
        assert_eq!(
            registry
                .by_category(&SkillCategory::Custom("ops".to_string()))
                .len(),
            11
        );
        assert!(!registry.by_tag("deferred").is_empty());
        assert!(
            !registry
                .by_execution_mode(SkillExecutionMode::Deferred)
                .is_empty()
        );
    }

    #[tokio::test]
    async fn executor_runs_native_ops_skill() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let result = executor
            .execute(SkillRequest::new(
                "req-1",
                "ops_pricing_strategy",
                json!({"cost": 10.0, "competitor_prices": [18.0, 20.0]}),
            ))
            .await;

        assert_eq!(result.status, SkillExecutionStatus::Success);
        assert_eq!(
            result.metadata.get("execution_mode"),
            Some(&json!(SkillExecutionMode::Native))
        );
        assert!(result.output.is_some());
    }

    #[tokio::test]
    async fn executor_runs_representative_native_categories() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let cases = [
            (
                "accounting_cost_calc",
                json!({"purchase_cost": 20.0, "selling_price": 49.0}),
            ),
            ("data_funnel_analysis", json!({})),
            ("service_dsr_improvement", json!({})),
            (
                "creative_title_ctr_scorer",
                json!({"title": "春夏新款轻便通勤保温杯"}),
            ),
            (
                "web_title_seo_scorer",
                json!({"title": "春夏新款轻便通勤保温杯", "target_keywords": ["保温杯"]}),
            ),
            ("engineering_sla_monitor", json!({})),
            (
                "coordination_agent_handoff",
                json!({"task_description": "分析店铺转化率下滑并给出运营动作"}),
            ),
        ];

        for (index, (skill_id, input)) in cases.into_iter().enumerate() {
            let result = executor
                .execute(SkillRequest::new(
                    format!("req-native-{index}"),
                    skill_id,
                    input,
                ))
                .await;
            assert_eq!(
                result.status,
                SkillExecutionStatus::Success,
                "{skill_id} failed: {:?}",
                result.error
            );
            assert_eq!(
                result.metadata.get("execution_mode"),
                Some(&json!(SkillExecutionMode::Native)),
                "{skill_id} used unexpected execution mode"
            );
        }
    }

    #[tokio::test]
    async fn executor_validates_required_input() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let result = executor
            .execute(SkillRequest::new(
                "req-2",
                "ops_pricing_strategy",
                json!({}),
            ))
            .await;

        assert_eq!(result.status, SkillExecutionStatus::ValidationError);
        assert!(result.error.unwrap_or_default().contains("cost"));
    }

    #[tokio::test]
    async fn executor_validates_request_envelope() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let missing_request_id = executor
            .execute(SkillRequest::new(
                "",
                "ops_pricing_strategy",
                json!({"cost": 10.0}),
            ))
            .await;
        assert_eq!(
            missing_request_id.status,
            SkillExecutionStatus::ValidationError
        );
        assert!(
            missing_request_id
                .error
                .unwrap_or_default()
                .contains("request_id")
        );

        let missing_skill_id = executor
            .execute(SkillRequest::new("req-empty-skill", "", json!({})))
            .await;
        assert_eq!(
            missing_skill_id.status,
            SkillExecutionStatus::ValidationError
        );
        assert!(
            missing_skill_id
                .error
                .unwrap_or_default()
                .contains("skill_id")
        );
    }

    #[tokio::test]
    async fn executor_returns_deferred_for_search() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let result = executor
            .execute(SkillRequest::new(
                "req-3",
                "search_trends",
                json!({"query": "咖啡"}),
            ))
            .await;

        assert_eq!(result.status, SkillExecutionStatus::Deferred);
        assert_eq!(
            result.metadata.get("risk_level"),
            Some(&json!(SkillRiskLevel::Medium))
        );
        assert!(result.metadata.contains_key("adapter_requirements"));
    }

    #[tokio::test]
    async fn executor_blocks_platform_write_as_pending_approval() {
        let executor = SkillExecutorSet::staged().expect("executor should load");
        let result = executor
            .execute(SkillRequest::new(
                "req-4",
                "platform_update_price",
                json!({"platform": "taobao", "product_id": "p1", "new_price": 99.0}),
            ))
            .await;

        assert_eq!(result.status, SkillExecutionStatus::PendingApproval);
        assert_eq!(result.metadata.get("approval_required"), Some(&json!(true)));
        assert_eq!(
            result.metadata.get("risk_level"),
            Some(&json!(SkillRiskLevel::High))
        );
    }
}
