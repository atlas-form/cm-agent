"""
Pydantic 请求/响应模型 — 覆盖全部17个路由模块的请求和响应。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# 认证
# ═══════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    account_role: str = Field(default="general", description="账号类型：general/teacher/student")

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    token: str
    user_id: int
    email: str
    name: str = ""
    account_role: str = Field(default="general", description="账号类型：general/teacher/student")

# ═══════════════════════════════════════════════════════════════════════════
# 聊天
# ═══════════════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    role: Optional[str] = None
    role_lock: Optional[bool] = Field(default=None, description="是否锁定主角色；为空时按协作模式自动推断")
    conversation_id: Optional[str] = None
    product_id: Optional[int] = None
    product_ids: List[int] = Field(default_factory=list)
    workspace_id: Optional[int] = None
    response_mode: str = Field(default="execution", description="回复模式：execution（执行）或 learning（教学）")
    learning_level: str = Field(default="higher_vocational", description="教学层级：vocational（中职）/higher_vocational（高职）/undergraduate（本科）")
    collaboration_mode: str = Field(default="auto", description="协作模式：auto（自动）/single（单角色）/manual（手动指定）")
    hired_roles: List[str] = Field(default_factory=list, description="手动协作时指定的辅助角色列表")
    attachments: List[Dict[str, Any]] = Field(default_factory=list, description="聊天上传附件（解析后的文本片段）")
    page_context: Dict[str, Any] = Field(default_factory=dict, description="页面级上下文快照（用于跨页面悬浮助手）")

class ChatResponse(BaseModel):
    reply: str
    role: str
    skill_used: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class FeedbackRequest(BaseModel):
    message_id: Optional[int] = None
    conversation_id: Optional[str] = None
    rating: int = Field(ge=1, le=5)
    tags: List[str] = Field(default_factory=list)
    comment: str = ""


class TeachingSubmissionRequest(BaseModel):
    teacher_user_id: int = Field(gt=0)
    note: str = ""


class TeachingEvaluationRequest(BaseModel):
    score: float = Field(ge=0, le=100)
    feedback: str = ""
    rubric: Dict[str, Any] = Field(default_factory=dict)
    message_id: Optional[int] = None


class TeachingClassCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class TeachingClassMemberAddRequest(BaseModel):
    student_user_id: int = Field(gt=0)


class TeachingAssignmentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = ""
    due_at: Optional[str] = None


class TeachingAssignmentSubmitRequest(BaseModel):
    note: str = ""


class TeachingTemplateCreateRequest(BaseModel):
    template_type: str = Field(min_length=1, max_length=40, description="模板类型：class/assignment/rubric")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    initial_status: str = Field(default="active", description="模板初始状态：active/draft/review/approved")


class TeachingClassTemplateInstantiateRequest(BaseModel):
    name_override: str = ""
    description_override: str = ""


class TeachingAssignmentTemplateApplyRequest(BaseModel):
    title_override: str = ""
    description_override: str = ""
    due_at_override: str = ""


class TeachingTemplateRollbackRequest(BaseModel):
    version_id: Optional[int] = Field(default=None, gt=0)
    version_no: Optional[int] = Field(default=None, gt=0)
    note: str = ""


class TeachingTemplateStatusUpdateRequest(BaseModel):
    target_status: str = Field(min_length=1, max_length=20, description="目标状态：draft/review/approved")
    note: str = ""


class TeachingInterventionActionCreateRequest(BaseModel):
    intervention_code: str = Field(min_length=1, max_length=80)
    intervention_title: str = ""
    intervention_severity: str = ""
    assignment_id: Optional[int] = Field(default=None, gt=0)
    note: str = ""
    strategy_variant: str = Field(default="", max_length=32, description="策略实验分组，例如 A/B")
    strategy_experiment_id: str = Field(default="", max_length=80, description="策略实验ID")
    strategy_note: str = Field(default="", max_length=200, description="策略说明")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TeachingClassGoalCreateRequest(BaseModel):
    goal_code: str = Field(min_length=1, max_length=80)
    goal_name: str = Field(min_length=1, max_length=120)
    metric_type: str = Field(min_length=1, max_length=64)
    target_value: float = Field(gt=0)
    note: str = ""
    due_at: str = ""


class TeachingClassGoalStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    note: str = ""


class TeachingGoalTermArchiveRequest(BaseModel):
    term_code: str = Field(min_length=1, max_length=80)
    term_name: str = Field(default="", max_length=120)
    term_start: str = ""
    term_end: str = ""
    note: str = ""

class TeachingInterventionExperimentPlanRequest(BaseModel):
    intervention_code: str = Field(min_length=1, max_length=80)
    intervention_title: str = ""
    experiment_id: str = Field(default="", max_length=120)
    strategy_mode: str = Field(default="experiment", max_length=32)
    recommended_variant: str = Field(default="A", max_length=32)
    variants: List[str] = Field(default_factory=list)
    window_days: int = Field(default=14, ge=7, le=90)
    target_metric: str = Field(default="submission_rate", max_length=64)
    note: str = ""
    source_recommendation: Dict[str, Any] = Field(default_factory=dict)


class TeachingInterventionExperimentStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    note: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Agent
# ═══════════════════════════════════════════════════════════════════════════

class AgentInfo(BaseModel):
    id: int
    name: str
    display_name: str
    role: str
    description: str = ""
    avatar: str = ""
    enabled: bool = True

class AgentListResponse(BaseModel):
    agents: List[AgentInfo]

class AgentActivateRequest(BaseModel):
    agent_name: str

# ═══════════════════════════════════════════════════════════════════════════
# 产品
# ═══════════════════════════════════════════════════════════════════════════

class ProductCreate(BaseModel):
    name: str
    category: str = ""
    sku: str = ""
    cost_price: float = 0.0
    selling_price: float = 0.0
    supplier: str = ""
    description: str = ""
    lifecycle_status: str = "draft"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    sku: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    supplier: Optional[str] = None
    description: Optional[str] = None
    lifecycle_status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ProductInfo(BaseModel):
    id: int
    name: str
    category: str = ""
    sku: str = ""
    cost_price: float = 0.0
    selling_price: float = 0.0
    supplier: str = ""
    description: str = ""
    folder_path: str = ""
    lifecycle_status: str = "draft"

class SetPinRequest(BaseModel):
    pin: str = ""  # 空字符串表示移除PIN

class DeleteProductRequest(BaseModel):
    pin: str = ""  # 有PIN保护时需提供

class FileDescUpdate(BaseModel):
    description: str = ""

class KnowledgeCreate(BaseModel):
    content: str
    content_type: str = "text"
    source_type: str = "manual"
    confidence: float = 0.8

class KnowledgeUpdate(BaseModel):
    content: Optional[str] = None
    confidence: Optional[float] = None

class LifecycleTransitionRequest(BaseModel):
    target_status: str  # 选品中 → 上架中 → 运营中 → 下架 → 归档

# ═══════════════════════════════════════════════════════════════════════════
# 产品素材
# ═══════════════════════════════════════════════════════════════════════════

class MaterialCreate(BaseModel):
    material_type: str  # 主图/详情/文案/视频脚本
    title: str = ""
    content: str = ""

class MaterialUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None

class MaterialGenerateRequest(BaseModel):
    material_type: str = "主图"  # 主图/详情页/文案/视频脚本/其他
    title: str = ""
    brief: str = ""
    platform: str = "淘宝"
    style: str = "简约现代"
    output_format: str = "auto"  # auto/svg/png/md/txt
    save_to_files: bool = True
    prefer_doubao_image: bool = True
# ═══════════════════════════════════════════════════════════════════════════
# 工作区
# ═══════════════════════════════════════════════════════════════════════════

class WorkspaceCreate(BaseModel):
    title: str
    workspace_type: str = "general"
    product_id: Optional[int] = None
    campaign_id: Optional[int] = None
    config: Dict[str, Any] = Field(default_factory=dict)

class WorkspaceUpdate(BaseModel):
    title: Optional[str] = None
    config: Optional[Dict[str, Any]] = None

class WorkspaceInfo(BaseModel):
    id: int
    title: str
    workspace_type: str = "general"
    product_id: Optional[int] = None
    campaign_id: Optional[int] = None

class WorkspaceTaskCreate(BaseModel):
    title: str
    description: str = ""
    owner_role: str = ""
    priority: int = 0
    depends_on: str = ""
    acceptance_criteria: str = ""

class WorkspaceTaskUpdate(BaseModel):
    status: Optional[str] = None
    result: Optional[str] = None
    priority: Optional[int] = None

class WorkspaceMemoryCreate(BaseModel):
    role: str = ""
    key: str
    content: str
    memory_type: str = "fact"

# ═══════════════════════════════════════════════════════════════════════════
# 营销活动
# ═══════════════════════════════════════════════════════════════════════════

class CampaignCreate(BaseModel):
    name: str
    product_id: Optional[int] = None
    budget: float = 0.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "draft"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    budget: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

# ═══════════════════════════════════════════════════════════════════════════
# 技能
# ═══════════════════════════════════════════════════════════════════════════

class SkillRunRequest(BaseModel):
    skill_name: str
    args: Dict[str, Any] = Field(default_factory=dict)

class SkillRunResponse(BaseModel):
    skill_name: str
    result: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0

class SkillFeedbackRequest(BaseModel):
    run_id: int
    rating: int = Field(ge=1, le=5)
    tags: List[str] = Field(default_factory=list)


class UserSkillPackUpsertRequest(BaseModel):
    skill_code: str = Field(default="", max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = ""
    category: str = Field(default="custom", max_length=40)
    system_prompt: str = ""
    prompt_template: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_mode: str = Field(default="text", max_length=20, description="输出模式：text/json")
    temperature: float = Field(default=0.3, ge=0, le=1.5)
    model: str = Field(default="", max_length=120)
    note: str = ""


class UserSkillPackPublishRequest(BaseModel):
    note: str = ""


class UserSkillPackStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=1, max_length=20, description="目标状态：draft/published/disabled")
    note: str = ""


class UserSkillPackRollbackRequest(BaseModel):
    version_no: int = Field(gt=0)
    note: str = ""

# ═══════════════════════════════════════════════════════════════════════════
# 项目
# ═══════════════════════════════════════════════════════════════════════════

class ProjectCreate(BaseModel):
    title: str
    description: str = ""
    owner_role: str = "ops"

class ProjectTaskCreate(BaseModel):
    title: str
    description: str = ""
    owner_role: str = ""
    priority: int = 0
    depends_on: str = ""
    acceptance_criteria: str = ""

# ═══════════════════════════════════════════════════════════════════════════
# 智能中心
# ═══════════════════════════════════════════════════════════════════════════

class AutopilotTriggerRequest(BaseModel):
    run_type: str = "manual"

class AutopilotScheduleUpdate(BaseModel):
    schedule_type: str = "daily"
    cron_expr: str = "0 9 * * *"
    enabled: bool = True
    policy: Optional[Dict[str, Any]] = None

# ═══════════════════════════════════════════════════════════════════════════
# 告警
# ═══════════════════════════════════════════════════════════════════════════

class AlertSnoozeRequest(BaseModel):
    minutes: int = 60

# ═══════════════════════════════════════════════════════════════════════════
# 管理面板
# ═══════════════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str  # ok | degraded | error
    db: str
    llm: str
    version: str = "4.0.0"
    instance_id: str = ""
    pid: int = 0
    booted_at: str = ""
    build_fingerprint: str = ""

class AdminSkillCreate(BaseModel):
    name: str
    display_name: str
    category: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)

class AdminAgentCreate(BaseModel):
    name: str
    display_name: str
    role: str
    description: str = ""
    avatar: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)



