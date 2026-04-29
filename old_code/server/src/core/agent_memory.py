"""
Agent 学习记忆 — 从交互中提取经验教训并存储，0 LLM 调用。

规则：
- quality >= 0.75 且回复有结构 → 记录成功模式 (success)
- quality < 0.5 → 记录盲点 (blindspot)
- 介于中间 → 记录改进方向 (improvement)
- 去重：与现有记忆内容相似度高则跳过
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Learning:
    role: str
    category: str  # success | blindspot | improvement
    content: str
    confidence: float = 0.5
    source_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "category": self.category,
            "content": self.content,
            "confidence": round(self.confidence, 3),
            "source_action": self.source_action,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _has_structure(text: str) -> bool:
    """检测回复是否有结构化内容（列表、步骤、标题等）。"""
    patterns = [
        r"[1-9][.、]",           # 数字列表
        r"^[-•*]\s",             # 无序列表
        r"^#{1,3}\s",            # Markdown 标题
        r"步骤|阶段|第[一二三四五]",  # 中文步骤
        r"\|.*\|.*\|",          # 表格
    ]
    for p in patterns:
        if re.search(p, text, re.MULTILINE):
            return True
    return False


def _extract_key_phrases(text: str, max_len: int = 200) -> str:
    """
    从回复中提取最有业务价值的短语作为记忆内容。

    优先级：
    1. 包含量化数据的结论句（最有参考价值）
    2. 包含业务策略关键词的句子
    3. 标题级内容（Markdown 标题）
    4. 第一段非空行
    """
    lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) >= 15]
    if not lines:
        return text[:max_len]

    # 1. 含量化数据 + 结论词的句子（最优先）
    quant_patterns = re.compile(r"\d+[%元万亿天]|[0-9]+\.[0-9]")
    conclusion_kw = ["建议", "结论", "应该", "需要", "方案", "策略", "关键", "核心", "重点", "首先", "优先"]
    for line in lines:
        if quant_patterns.search(line) and any(kw in line for kw in conclusion_kw):
            return line[:max_len]

    # 2. 业务策略关键词行
    business_kw = ["策略", "方案", "建议", "发现", "问题", "原因", "关键", "核心", "重点", "结论",
                   "优化", "提升", "降低", "改进", "解决", "效果", "转化", "ROI", "利润"]
    for line in lines:
        matched = sum(1 for kw in business_kw if kw in line)
        if matched >= 2:
            return line[:max_len]

    # 3. 标题级内容（## 开头的标题往往是最核心结论）
    for line in lines:
        if line.startswith("#") or line.startswith("**") or line.startswith("【"):
            clean = re.sub(r"^[#*【】\s]+", "", line).strip()
            if clean:
                return clean[:max_len]

    # 4. 第一行
    return lines[0][:max_len]


def _extract_domain_from_message(message: str) -> str:
    """从用户消息中提取问题领域标签，用于丰富记忆上下文。"""
    domain_map = {
        "定价": "定价策略", "价格": "定价策略", "利润": "利润分析",
        "转化": "转化优化", "流量": "流量运营", "广告": "广告投放",
        "活动": "活动运营", "大促": "活动运营", "618": "活动运营", "双11": "活动运营",
        "客服": "客服管理", "售后": "售后处理", "差评": "评价管理",
        "设计": "视觉设计", "主图": "视觉设计", "详情页": "视觉设计",
        "数据": "数据分析", "分析": "数据分析", "指标": "数据分析",
        "财务": "财务管理", "成本": "成本分析",
        "SEO": "搜索优化", "文案": "内容创作", "视频": "内容创作",
    }
    for keyword, domain in domain_map.items():
        if keyword in message:
            return domain
    return ""


def _simple_similarity(a: str, b: str) -> float:
    """简单的字符级 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / max(len(union), 1)


# ═══════════════════════════════════════════════════════════════════════════
# 提取函数
# ═══════════════════════════════════════════════════════════════════════════

def extract_learnings(
    role: str,
    message: str,
    reply: str,
    quality_score: float,
    quality_issues: List[str],
    action: str = "",
) -> List[Learning]:
    """
    根据质量评分和回复内容提取学习记忆。

    Parameters
    ----------
    role : str
        角色名。
    message : str
        用户消息。
    reply : str
        LLM 回复。
    quality_score : float
        质量评分 (0~1)。
    quality_issues : list[str]
        质量检查发现的问题列表。
    action : str
        检测到的动作类型。

    Returns
    -------
    list[Learning]
        提取到的学习记忆列表。
    """
    learnings: List[Learning] = []

    if not reply or not reply.strip():
        return learnings

    domain = _extract_domain_from_message(message)
    domain_tag = f"[{domain}]" if domain else f"[{action}]"

    # 成功模式：高质量 + 有结构 → 提取核心策略内容
    if quality_score >= 0.75 and _has_structure(reply):
        summary = _extract_key_phrases(reply)
        # 构建更有指导价值的记忆内容
        content = f"{domain_tag} 有效策略：{summary}"
        # 额外提取：如果回复包含量化目标，单独记一条
        quant_match = re.search(r"(?:目标|建议|预计|提升)[^。！\n]{0,30}(\d+[%元万天][^。！\n]{0,20})", reply)
        if quant_match:
            learnings.append(Learning(
                role=role,
                category="success",
                content=f"{domain_tag} 量化目标参考：{quant_match.group(0)[:100]}",
                confidence=min(quality_score, 0.9),
                source_action=action,
            ))
        learnings.append(Learning(
            role=role,
            category="success",
            content=content,
            confidence=min(quality_score, 0.95),
            source_action=action,
        ))

    # 盲点：低质量 → 记录失败原因和上下文
    elif quality_score < 0.5:
        if quality_issues:
            issues_text = "；".join(quality_issues[:3])
            learnings.append(Learning(
                role=role,
                category="blindspot",
                content=f"{domain_tag} 注意避免：{issues_text}",
                confidence=0.65,
                source_action=action,
            ))
        else:
            learnings.append(Learning(
                role=role,
                category="blindspot",
                content=f"{domain_tag} 低质量模式：评分{quality_score:.2f}，此类问题需加强深度和结构",
                confidence=0.5,
                source_action=action,
            ))

    # 改进方向：中等质量 → 记录具体改进点
    elif quality_score < 0.75 and quality_issues:
        issues_text = "；".join(quality_issues[:2])
        learnings.append(Learning(
            role=role,
            category="improvement",
            content=f"{domain_tag} 改进方向：{issues_text}",
            confidence=0.6,
            source_action=action,
        ))

    return learnings


# ═══════════════════════════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════════════════════════

async def save_learnings(learnings: List[Learning]) -> int:
    """
    去重后保存学习记忆到数据库。

    Parameters
    ----------
    learnings : list[Learning]
        待保存的学习记忆列表。

    Returns
    -------
    int
        实际保存的数量。
    """
    if not learnings:
        return 0

    saved = 0
    try:
        from src.database import get_db
        db = await get_db()

        for learning in learnings:
            # 去重检查：查找同角色、同类别的最近记忆
            existing = await db.execute_fetchall(
                """SELECT content FROM learnings
                   WHERE role = ? AND category = ?
                   ORDER BY created_at DESC LIMIT 10""",
                (learning.role, learning.category),
            )

            # 检查相似度
            is_dup = False
            for row in existing:
                if _simple_similarity(learning.content, row["content"]) > 0.6:
                    is_dup = True
                    break

            if is_dup:
                continue

            await db.execute(
                """INSERT INTO learnings (role, category, content, confidence, source_action)
                   VALUES (?, ?, ?, ?, ?)""",
                (learning.role, learning.category, learning.content,
                 learning.confidence, learning.source_action),
            )
            saved += 1

        if saved > 0:
            await db.commit()

    except Exception as e:
        logger.warning("Failed to save learnings: %s", e)

    return saved


# ═══════════════════════════════════════════════════════════════════════════
# 自适应关键词学习
# ═══════════════════════════════════════════════════════════════════════════

def extract_routing_keywords(message: str, reply: str, role: str, quality_score: float) -> List[str]:
    """
    从高质量交互中提取可能的新routing关键词。

    只在 quality_score >= 0.8 时触发。
    从用户消息中提取2-4字的中文词组，排除已知关键词后返回候选词。
    """
    if quality_score < 0.8:
        return []

    from src.core.intent import ROLE_KEYWORDS

    # 已知关键词集
    known = set()
    for kws in ROLE_KEYWORDS.values():
        known.update(kw.lower() for kw in kws)

    # 从消息中提取2-4字的中文词组
    candidates = set(re.findall(r"[\u4e00-\u9fff]{2,4}", message))

    # 过滤：去掉已知词、常用停用词
    stopwords = {"什么", "怎么", "如何", "为什么", "可以", "能不能", "帮我", "请问",
                 "一下", "看看", "告诉", "知道", "需要", "应该", "这个", "那个"}
    new_keywords = [w for w in candidates if w.lower() not in known and w not in stopwords]

    return new_keywords[:5]  # 最多5个候选


async def save_routing_keywords(role: str, keywords: List[str]) -> int:
    """
    将新发现的routing关键词保存为learning记录（category=routing_keyword）。

    后续可以在 intent analyzer 中查询这些记录来增强路由能力。
    """
    if not keywords:
        return 0

    saved = 0
    try:
        from src.database import get_db
        db = await get_db()

        for kw in keywords:
            # 检查是否已存在
            existing = await db.execute_fetchall(
                "SELECT id FROM learnings WHERE role = ? AND category = 'routing_keyword' AND content = ?",
                (role, kw),
            )
            if existing:
                continue

            await db.execute(
                "INSERT INTO learnings (role, category, content, confidence, source_action) VALUES (?, ?, ?, ?, ?)",
                (role, "routing_keyword", kw, 0.6, "auto_learned"),
            )
            saved += 1

        if saved > 0:
            await db.commit()
            logger.info("Learned %d new routing keywords for role %s: %s", saved, role, keywords[:3])
    except Exception as e:
        logger.warning("Failed to save routing keywords: %s", e)

    return saved
