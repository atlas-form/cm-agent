"""
功能预算选择器 — 从12个特性中选出 top-N 注入系统提示词，0 LLM 调用。

每个特性有关键词和角色亲和度，根据消息内容和当前角色评分选出最相关的特性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from src.config import FEATURE_BUDGET_MAX

# ═══════════════════════════════════════════════════════════════════════════
# Feature 定义
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FeatureDef:
    name: str
    description: str
    keywords: List[str]
    role_affinity: Dict[str, float]  # role -> 0.0~1.0


FEATURES: List[FeatureDef] = [
    FeatureDef(
        name="deep_knowledge",
        description="深度领域知识：注入该角色的专业经验、行业标准和最佳实践",
        keywords=["专业", "深度", "经验", "标准", "最佳实践", "行业", "方法论", "框架"],
        role_affinity={"ops": 0.8, "data": 0.9, "service": 0.7, "design": 0.7,
                       "accounting": 0.9, "engineering": 0.9, "web": 0.7, "creative": 0.6},
    ),
    FeatureDef(
        name="platform_knowledge",
        description="平台规则知识：注入各电商平台的规则、算法、政策和限制",
        keywords=["平台", "规则", "政策", "算法", "淘宝", "天猫", "京东", "拼多多", "抖音", "小红书"],
        role_affinity={"ops": 1.0, "data": 0.5, "service": 0.8, "design": 0.6,
                       "accounting": 0.4, "engineering": 0.3, "web": 0.9, "creative": 0.7},
    ),
    FeatureDef(
        name="quality_frameworks",
        description="质量框架：注入FABE、RICE、SWOT等分析框架指导输出结构化",
        keywords=["框架", "FABE", "RICE", "SWOT", "结构", "模板", "SOP", "流程"],
        role_affinity={"ops": 0.8, "data": 0.7, "service": 0.6, "design": 0.5,
                       "accounting": 0.7, "engineering": 0.8, "web": 0.5, "creative": 0.4},
    ),
    FeatureDef(
        name="communication_style",
        description="沟通风格：根据角色调整语气、节奏和表达方式",
        keywords=["沟通", "表达", "语气", "风格", "话术", "措辞", "语言"],
        role_affinity={"ops": 0.6, "data": 0.5, "service": 1.0, "design": 0.6,
                       "accounting": 0.5, "engineering": 0.4, "web": 0.6, "creative": 0.8},
    ),
    FeatureDef(
        name="workflow",
        description="工作流程：注入角色标准工作流程（分析→规划→执行→验证→交接）",
        keywords=["流程", "步骤", "工作流", "阶段", "计划", "排期", "执行", "验证"],
        role_affinity={"ops": 0.9, "data": 0.7, "service": 0.8, "design": 0.7,
                       "accounting": 0.8, "engineering": 0.9, "web": 0.7, "creative": 0.6},
    ),
    FeatureDef(
        name="learning_memory",
        description="学习记忆：注入该角色过去的成功模式、盲点和改进方向",
        keywords=["学习", "记忆", "历史", "经验", "教训", "模式", "改进", "复盘"],
        role_affinity={"ops": 0.7, "data": 0.7, "service": 0.7, "design": 0.6,
                       "accounting": 0.6, "engineering": 0.7, "web": 0.6, "creative": 0.6},
    ),
    FeatureDef(
        name="cognitive_style",
        description="认知风格：注入角色特有的思维链路和推理模式",
        keywords=["思维", "推理", "认知", "逻辑", "判断", "决策", "思路"],
        role_affinity={"ops": 0.7, "data": 0.9, "service": 0.6, "design": 0.7,
                       "accounting": 0.8, "engineering": 0.9, "web": 0.6, "creative": 0.8},
    ),
    FeatureDef(
        name="distilled_knowledge",
        description="蒸馏知识：注入从历史对话中提炼的高质量知识点",
        keywords=["知识", "提炼", "总结", "精华", "要点", "关键", "核心"],
        role_affinity={"ops": 0.7, "data": 0.8, "service": 0.6, "design": 0.5,
                       "accounting": 0.7, "engineering": 0.7, "web": 0.5, "creative": 0.5},
    ),
    FeatureDef(
        name="wave_activation",
        description="波浪激活：根据对话热度动态调整回复的详细程度和深度",
        keywords=["详细", "深入", "展开", "详解", "具体", "深度", "全面"],
        role_affinity={"ops": 0.6, "data": 0.7, "service": 0.5, "design": 0.5,
                       "accounting": 0.6, "engineering": 0.6, "web": 0.5, "creative": 0.6},
    ),
    FeatureDef(
        name="pipeline_context",
        description="管道上下文：注入当前对话管道的状态和阶段信息",
        keywords=["管道", "流水线", "阶段", "状态", "进度", "上下文", "pipeline"],
        role_affinity={"ops": 0.8, "data": 0.5, "service": 0.5, "design": 0.4,
                       "accounting": 0.4, "engineering": 0.7, "web": 0.4, "creative": 0.4},
    ),
    FeatureDef(
        name="handoff_context",
        description="交接上下文：注入跨角色协作时的交接信息和未完成事项",
        keywords=["交接", "协作", "协同", "多角色", "跨部门", "配合", "转交"],
        role_affinity={"ops": 0.9, "data": 0.6, "service": 0.7, "design": 0.6,
                       "accounting": 0.5, "engineering": 0.6, "web": 0.5, "creative": 0.5},
    ),
    FeatureDef(
        name="trust_context",
        description="信任上下文：注入当前角色的信任等级，影响回复的自主程度",
        keywords=["信任", "权限", "自主", "审核", "确认", "验证"],
        role_affinity={"ops": 0.6, "data": 0.6, "service": 0.7, "design": 0.5,
                       "accounting": 0.8, "engineering": 0.7, "web": 0.5, "creative": 0.5},
    ),
]

_FEATURE_MAP: Dict[str, FeatureDef] = {f.name: f for f in FEATURES}


# ═══════════════════════════════════════════════════════════════════════════
# 选择函数
# ═══════════════════════════════════════════════════════════════════════════

def select_features(message: str, role: str, n: int = FEATURE_BUDGET_MAX, platform: str = "general") -> List[str]:
    """
    根据消息内容和角色，选出 top-N 最相关的特性名称。

    Parameters
    ----------
    message : str
        用户消息。
    role : str
        当前角色（ops, data, service 等）。
    n : int
        返回的特性数量，默认取配置值。
    platform : str
        检测到的平台。general 时降低 platform_knowledge 优先级。

    Returns
    -------
    list[str]
        排序后的特性名称列表（最多 n 个）。
    """
    msg_lower = message.lower()
    scored: List[tuple[float, str]] = []

    for feat in FEATURES:
        # 关键词匹配分
        kw_score = sum(1.0 for kw in feat.keywords if kw.lower() in msg_lower)
        # 角色亲和度分
        affinity = feat.role_affinity.get(role, 0.5)
        # platform_knowledge 在无具体平台时降权（避免白占预算）
        if feat.name == "platform_knowledge" and platform == "general":
            affinity *= 0.3
        # 综合分 = 关键词分 * 1.0 + 亲和度 * 2.0（保证角色相关性权重）
        total = kw_score * 1.0 + affinity * 2.0
        scored.append((total, feat.name))

    # 按得分降序排列，取前 n 个
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:n]]


def get_feature_description(name: str) -> str:
    """获取特性的描述文本，用于注入系统提示词。"""
    feat = _FEATURE_MAP.get(name)
    return feat.description if feat else ""
