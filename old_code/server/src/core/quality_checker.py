"""
质量检查器 — 11维度规则质量评分，0 LLM 调用。

维度：completeness, accuracy, fabrication, actionability, relevance, goal_satisfaction,
     value_density, length, clarity, risk_awareness, confidence_marking, refusal_detection

总分 = 均值，通过阈值 >= 0.7。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class QualityResult:
    score: float = 0.0
    passed: bool = False
    dimensions: Dict[str, float] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


PASS_THRESHOLD = 0.7

_SMALLTALK_HINTS = (
    "你好",
    "您好",
    "在吗",
    "hi",
    "hello",
    "hey",
    "嗨",
    "早上好",
    "晚上好",
)

_SMALLTALK_BUSINESS_CUES = (
    "运营",
    "店铺",
    "预算",
    "转化",
    "策略",
    "方案",
    "数据",
    "分析",
    "计划",
    "优化",
    "执行",
    "抖音",
    "小红书",
    "淘宝",
    "京东",
    "拼多多",
)


def _looks_like_smalltalk_message(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    if any(cue in text for cue in _SMALLTALK_BUSINESS_CUES):
        return False
    if any(hint in text for hint in _SMALLTALK_HINTS):
        return len(text) <= 36
    return False


def _is_evidence_mapping_request(message: str) -> bool:
    """是否属于“列来源/日期/口径映射”类追问。"""
    text = str(message or "").strip().lower()
    if not text:
        return False

    has_source = bool(re.search(r"来源|出处|引用|source|数据源|口径", text))
    has_date = bool(re.search(r"日期|时间|发布时间|更新|date", text))
    has_mapping = bool(re.search(r"对应|逐条|每条|映射|列出|对照", text))
    has_continue = bool(re.search(r"继续|承接|刚才|上面|上一轮", text))

    # 来源+日期 的组合优先判定；带“继续/对应”时放宽
    return bool((has_source and has_date) or (has_source and has_mapping and has_continue))


def _has_next_step_signal(reply: str) -> bool:
    """识别“下一步动作”信号，兼容标题化与时间节点表达。"""
    text = str(reply or "")
    if not text.strip():
        return False

    explicit = bool(
        re.search(
            r"下一步|先做|先执行|今日动作|今日先做|今天动作|本周计划|24小时内|明日动作|执行顺序|行动项|明日复盘|复盘节点|今日必须完成|30秒可执行动作|可执行动作|立即执行|首轮动作|next step",
            text,
            re.IGNORECASE,
        )
    )
    if explicit:
        return True

    heading_like = bool(
        re.search(
            r"^\s*(?:#{1,6}\s*)?(?:\d+[.、\)]\s*)?(今日先做|下一步|30秒可执行动作|可执行动作|今天动作|明日复盘|行动项|本周计划)\s*[:：]?",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
    )
    if heading_like:
        return True

    has_list = bool(re.search(r"^\s*\d+[.、\)]", text, re.MULTILINE))
    has_time_anchor = bool(re.search(r"今天|明天|今晚|当日|24小时|\d+点|T\+\d", text))
    return bool(has_list and has_time_anchor)

# ═══════════════════════════════════════════════════════════════════════════
# 各维度评分函数
# ═══════════════════════════════════════════════════════════════════════════

def _score_completeness(message: str, reply: str, role: str) -> tuple[float, List[str]]:
    """
    完整性：回复是否覆盖了用户问题的核心要素。

    改进：不仅看长度，还检查是否真正响应了问题类型（分析类/执行类/查询类）。
    """
    issues: List[str] = []
    score = 0.5  # 基础分

    # 1. 回复长度相对于问题长度
    ratio = len(reply) / max(len(message), 1)
    if ratio >= 3.0:
        score += 0.3
    elif ratio >= 1.5:
        score += 0.2
    elif ratio >= 0.8:
        score += 0.1
    elif ratio < 0.4:
        issues.append("回复过短，可能未充分回答问题")

    # 2. 检查是否包含结构化内容（列表、步骤、表格）
    if re.search(r"[1-9][.、]|[-•]\s|步骤|第[一二三四五]|\|.*\|", reply):
        score += 0.1

    # 3. 检查问题类型响应
    msg_lower = message.lower()
    # 如果用户在问"为什么/原因/怎么"，回复应包含原因分析
    if re.search(r"为什么|原因|怎么回事|什么导致|为何", msg_lower):
        has_reason = any(kw in reply for kw in ["原因", "因为", "由于", "导致", "是因为", "主要是"])
        if not has_reason:
            issues.append("问题涉及原因分析，但回复未明确给出原因")
        else:
            score += 0.1

    # 如果用户在问"怎么做/如何"，回复应包含步骤
    if re.search(r"怎么做|如何|怎样|怎么办|步骤", msg_lower):
        has_steps = re.search(r"[1-9][.、]|第[一二三四五]|首先|其次|然后|最后|步骤", reply)
        if not has_steps:
            issues.append("问题涉及操作方法，但回复未给出具体步骤")
        else:
            score += 0.1

    return min(score, 1.0), issues


def _score_accuracy(message: str, reply: str, role: str) -> tuple[float, List[str]]:
    """准确性：检查回复中是否有明显的不确定标记但仍给出结论。"""
    issues: List[str] = []
    role_token = str(role or "").strip().lower()

    # 非数据岗位默认不应被固定压低到0.8，避免无差别惩罚。
    score = 0.9
    if role_token == "data":
        score = 0.85
        msg = str(message or "")
        has_quant_data = bool(re.search(r"\d+[%元万亿]|[0-9]+\.[0-9]", reply))
        has_source_guardrail = bool(
            re.search(r"无法提供|无法调取|无法联网|未获取到|未检索到|暂缺来源|来源不可用|需联网|待验证|请提供数据|补充来源", reply)
        )
        source_or_date_request = bool(re.search(r"来源|出处|引用|日期|时间|发布", msg))
        source_or_date_answered = bool(re.search(r"来源|出处|引用|日期|时间|发布", reply))
        if not has_quant_data and not has_source_guardrail and not (source_or_date_request and source_or_date_answered):
            score -= 0.2
            issues.append("数据角色回复未包含量化数据")

    # 如果有明确的不确定表述
    uncertain = ["不确定", "可能不准确", "我不知道", "无法确认"]
    uncertain_count = sum(1 for u in uncertain if u in reply)
    if uncertain_count >= 2:
        score -= 0.15
        issues.append("回复含多处不确定表述")

    return max(score, 0.0), issues


def _score_fabrication(message: str, reply: str) -> tuple[float, List[str]]:
    """
    虚构数据检测：检测回复中对用户当前状态的捏造性描述。

    只捕捉明确声称"用户当前数据是X"的模式，不惩罚合理的建议数字/预测值。
    捕捉模式：您/你的[指标]目前/约为/是 [数字]
    不惩罚：建议/预计/目标/参考/提升 [数字]
    """
    issues: List[str] = []

    # 检查是否有明确的免责标注（只有专用标注词才免责，不包括通用词"建议"/"参考"）
    safe_marks = ["示例", "假设", "参考值", "行业参考值", "行业参考", "仅供参考", "估算", "典型案例", "以下数据仅供参考", "行业均值"]
    has_disclaimer = any(m in reply for m in safe_marks)
    if has_disclaimer:
        return 0.9, issues

    # 只检测"对用户当前状态的具体声称"模式
    # 如：您的转化率目前约为2.3%、你的店铺UV是1200、当前ROI为1.8
    fabrication_patterns = [
        r"[您你]的.{0,10}(?:目前|现在|当前|约为|约是|是|为)\s*[\d]+\.?\d*\s*[%％元万亿]",
        r"(?:目前|当前).{0,8}(?:约为|约是|是|为|达到|达)\s*[\d]+\.?\d*\s*[%％元万亿]",
        r"[您你]的.{0,10}(?:目前|当前)\s*[\d]+\.?\d*",
    ]

    # 检查用户消息中是否提供了数字
    msg_has_numbers = bool(re.search(r"\d+\.?\d*\s*[%％元万亿]|\b\d{2,}\b", message))

    if not msg_has_numbers:
        for pattern in fabrication_patterns:
            if re.search(pattern, reply):
                issues.append("回复包含用户未提供的具体数字，可能存在数据虚构")
                return 0.4, issues

    return 0.9, issues


def _score_actionability(reply: str, role: str, action: str, message: str = "", tool_used: bool = False) -> tuple[float, List[str]]:
    """可操作性：回复是否给出了可执行的建议或步骤。"""
    issues: List[str] = []
    score = 0.5
    action_token = str(action or "").strip().lower()

    # 需要操作性的动作
    # 分析/诊断也需要“可执行的验证步骤 + 指标”，否则容易流于泛泛描述。
    action_oriented = {"create", "optimize", "execute", "plan", "analysis", "analyze", "diagnosis", "diagnose"}
    learning_oriented = {"learning", "teach", "teaching", "explain"}

    if action_token in action_oriented:
        evidence_mapping_request = _is_evidence_mapping_request(message)
        evidence_style_action = action_token in {"analysis", "analyze", "diagnosis", "diagnose"}

        # 对“来源/日期/口径映射”追问，不强制步骤与下一步动作，重点看证据映射完整度。
        if evidence_style_action and evidence_mapping_request:
            score = 0.72
            has_source = bool(re.search(r"来源|出处|引用|source|数据源|口径", reply, re.IGNORECASE))
            has_date = bool(re.search(r"日期|时间|发布时间|更新|20\d{2}", reply))
            has_mapping = bool(re.search(r"对应|逐条|每条|映射|结论.{0,8}(来源|日期)|来源.{0,8}结论", reply))
            has_structure = bool(re.search(r"^\s*(?:[-•*]|\d+[.、\)])", reply, re.MULTILINE))

            if has_source:
                score += 0.11
            else:
                issues.append("证据映射追问未明确来源")

            if has_date:
                score += 0.09
            else:
                issues.append("证据映射追问未明确日期/时间")

            if has_mapping or has_structure:
                score += 0.08
            else:
                issues.append("证据映射追问缺少“结论-来源-日期”对应关系")

            if tool_used:
                if has_source:
                    score += 0.04
                else:
                    score -= 0.08
                    issues.append("已调用工具但回复缺少“证据->结论”映射")

            return min(max(score, 0.0), 1.0), issues

        # 检查是否有步骤/建议
        if re.search(r"[1-9][.、]|步骤|建议|方案|操作|执行", reply):
            score += 0.28
        else:
            issues.append("执行类问题未给出具体步骤")

        # 检查是否有量化目标
        if re.search(r"\d+[%元天]|目标|KPI|指标", reply):
            score += 0.17
        else:
            if role in ("ops", "data", "accounting"):
                issues.append("缺少量化目标或指标")

        # 执行/分析类至少要有下一步动作，避免“看起来很专业但不可落地”。
        if _has_next_step_signal(reply):
            score += 0.1
        else:
            issues.append("缺少明确下一步动作")

        # 用了工具但没有证据映射，容易出现“结论跳步”。
        if tool_used:
            has_evidence_trace = bool(
                re.search(
                    r"根据工具|工具结果|检索结果|搜索结果|数据来源|来源：|证据|返回结果|接口返回|表格显示",
                    reply,
                )
            )
            if has_evidence_trace:
                score += 0.05
            else:
                score -= 0.08
                issues.append("已调用工具但回复缺少“证据->结论”映射")

    elif action_token in learning_oriented:
        score = 0.65
        if re.search(r"为什么|原因|成因|因为|由于|原理|机制|本质", reply):
            score += 0.15
        else:
            issues.append("教学类回复缺少“为什么/原理”解释")

        if re.search(r"怎么做|步骤|做法|练习|纠偏|改进|复盘", reply):
            score += 0.1
        else:
            issues.append("教学类回复缺少“怎么做/步骤”")

        if re.search(r"例如|比如|示例|案例", reply):
            score += 0.1
        else:
            issues.append("教学类回复缺少示例")

        if re.search(r"思考题|自检问题|请思考|请回答", reply):
            score += 0.05
    else:
        # 查询类问题，有回答即给分
        score = 0.7 if len(reply) > 50 else 0.5

    return min(max(score, 0.0), 1.0), issues


def _score_goal_satisfaction(message: str, reply: str, role: str, action: str) -> tuple[float, List[str]]:
    """目标满足度：回复是否真正形成“可落地结果”而非泛泛描述。"""
    issues: List[str] = []
    msg = str(message or "")
    ans = str(reply or "")
    act = str(action or "").strip().lower()

    if not ans.strip():
        return 0.0, ["回复为空，无法满足目标"]

    has_list = bool(re.search(r"^\s*(?:[-•*]|\d+[.、\)])", ans, re.MULTILINE))
    has_next_step = _has_next_step_signal(ans)
    has_metric = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|％|元|万|天|周|月)|KPI|ROI|ROAS|GMV|转化率|指标", ans))
    has_conclusion = bool(re.search(r"结论|建议|优先|先给|总结|可执行", ans))
    has_evidence = bool(re.search(r"因为|依据|数据|来源|对比|观察|验证", ans))
    has_example = bool(re.search(r"例如|比如|示例|案例", ans))
    has_mistake_fix = bool(re.search(r"误区|常见错误|纠偏|避免", ans))
    has_self_check = bool(re.search(r"思考题|自检问题|请思考|请回答", ans))

    # 寒暄输入不应用执行类高风险门槛，重点评估是否把对话顺畅引导到“可执行问题”。
    if _looks_like_smalltalk_message(msg):
        score = 0.72
        if len(ans.strip()) >= 24:
            score += 0.08
        else:
            issues.append("寒暄回复过短，缺少有效引导")

        if has_next_step or re.search(r"你可以|可直接|继续告诉我|告诉我目标", ans):
            score += 0.14
        else:
            issues.append("寒暄回复缺少明确引导")

        if has_list:
            score += 0.06

        return max(0.0, min(score, 1.0)), issues

    # 执行落地类：必须有步骤与下一步
    action_oriented = {"create", "optimize", "execute", "plan"}
    if act in action_oriented:
        score = 0.45
        if has_list:
            score += 0.2
        else:
            issues.append("缺少结构化步骤（列表/编号）")

        if has_next_step:
            score += 0.2
        else:
            issues.append("缺少明确下一步行动")

        if has_metric:
            score += 0.15
        else:
            issues.append("缺少量化目标/KPI，落地性不足")

        if role in {"ops", "accounting", "engineering", "service"} and not re.search(r"风险|注意|回滚|止损|合规", ans):
            issues.append("高风险场景缺少风险或回滚提示")
            score -= 0.08

        return max(0.0, min(score, 1.0)), issues

    # 教学/解释类：需要解释+示例+误区纠偏
    learning_actions = {"learning", "teach", "teaching", "explain"}
    learning_like = (act in learning_actions) or bool(re.search(r"解释|什么是|是什么|原理|教学|学|为什么|怎么做", msg))
    if learning_like:
        score = 0.5
        if re.search(r"为什么|原因|成因|怎么做|原理|步骤|纠偏|改进", ans):
            score += 0.2
        else:
            issues.append("教学类回复缺少“为什么/怎么做”解释")

        if has_example:
            score += 0.15
        else:
            issues.append("教学类回复缺少示例")

        if has_mistake_fix:
            score += 0.1
        else:
            issues.append("教学类回复缺少误区纠偏")

        # 教学回答可通过互动自检拿到满分（此前上限为0.95）
        if has_self_check:
            score += 0.05

        return max(0.0, min(score, 1.0)), issues

    # 分析/咨询类：至少要有结论与依据
    score = 0.55
    if has_conclusion:
        score += 0.2
    else:
        issues.append("缺少明确结论")

    if has_evidence:
        score += 0.15
    else:
        issues.append("缺少依据说明")

    if has_next_step:
        score += 0.1

    return max(0.0, min(score, 1.0)), issues





def _score_value_density(message: str, reply: str, action: str) -> tuple[float, List[str]]:
    """
    回答价值密度：衡量“信息增量 + 决策价值”，抑制机械化套话。

    目标：避免“看起来很完整但没有新增价值”的回复通过质量门禁。
    """
    msg = str(message or "")
    ans = str(reply or "")
    issues: List[str] = []

    if _looks_like_smalltalk_message(msg):
        return 0.82, issues

    action_token = str(action or "").strip().lower()
    score = 0.45
    evidence_mapping_request = _is_evidence_mapping_request(msg)

    has_structure = bool(re.search(r"[1-9][.、]|第[一二三四五六七八九十]|首先|其次|然后|最后|P[1-3]", ans))
    has_metric = bool(re.search(r"\d+\s*[%％元万亿天周月]|KPI|ROI|ROAS|CTR|CVR|转化率|客单价|预算|样本|周期|阈值", ans))
    has_evidence = bool(re.search(r"证据|来源|根据|数据|对比|验证|假设|口径", ans))
    has_example = bool(re.search(r"例如|比如|案例|场景", ans))
    has_decision = bool(re.search(r"优先|取舍|止损|回滚|触发条件|下一步|行动项|先做", ans))

    copy_ready_like = bool(re.search(r"可直接复制|直接发|模板|话术|标题|正文|开场|结尾", msg + "\n" + ans))

    if has_structure:
        score += 0.16
    if has_metric:
        score += 0.13
    if has_decision:
        score += 0.12
    if has_evidence:
        score += 0.10
    if has_example:
        score += 0.08
    if copy_ready_like:
        score += 0.12

    if evidence_mapping_request and action_token in {"analysis", "analyze", "diagnosis", "diagnose"}:
        has_source_date_mapping = bool(re.search(r"来源|出处|引用", ans)) and bool(re.search(r"日期|时间|发布时间|20\d{2}", ans))
        if has_source_date_mapping and bool(re.search(r"对应|逐条|每条|映射|结论", ans)):
            score = max(score, 0.78)
        elif has_source_date_mapping:
            score = max(score, 0.72)

    actionable_hits = sum(1 for x in [has_structure, has_metric, has_decision, has_evidence, has_example] if x)

    generic_patterns = [
        r"持续优化", r"关注用户体验", r"因地制宜", r"具体情况具体分析", r"综合来看", r"总的来说",
        r"建议加强", r"多维度考虑", r"不断迭代", r"后续再优化", r"可以考虑",
    ]
    generic_count = sum(1 for p in generic_patterns if re.search(p, ans))

    if generic_count >= 2 and actionable_hits < 2:
        score -= 0.22
        issues.append("回复套话比例高，缺少可执行信息增量")
    elif generic_count >= 1 and actionable_hits < 2:
        score -= 0.12
        issues.append("回复偏泛化，信息增量不足")

    if action_token in {"create", "optimize", "execute", "plan", "analysis", "analyze", "diagnosis", "diagnose"}:
        if evidence_mapping_request and action_token in {"analysis", "analyze", "diagnosis", "diagnose"}:
            # 证据映射追问的核心价值是“来源/日期/对应关系”，不强求动作优先级。
            if not (has_evidence and (has_structure or bool(re.search(r"对应|逐条|每条|映射", ans)))):
                score -= 0.08
                issues.append("证据映射类回复缺少来源/日期/对应关系")
        elif not has_structure and not has_decision:
            score -= 0.12
            issues.append("执行/诊断类回复缺少决策动作与优先级")

    if len(ans) > 220 and actionable_hits <= 1:
        score -= 0.18
        issues.append("回复篇幅较长但新增价值不足")

    return max(0.0, min(score, 1.0)), issues


def _score_relevance(message: str, reply: str) -> tuple[float, List[str]]:
    """相关性：回复是否与问题相关（中英关键词混合重合度 + 需求锚点覆盖）。"""
    issues: List[str] = []

    msg_cn = set(re.findall(r"[\u4e00-\u9fff]{2,4}", message))
    reply_cn = set(re.findall(r"[\u4e00-\u9fff]{2,4}", reply))

    # 英文/缩写指标（如 ROI/GMV/KPI）
    msg_en = {x.upper() for x in re.findall(r"[A-Za-z]{2,12}", message)}
    reply_en = {x.upper() for x in re.findall(r"[A-Za-z]{2,12}", reply)}

    # 去除无意义高频 token
    stop_en = {"THE", "AND", "FOR", "WITH", "THIS", "THAT", "FROM", "YOU", "YOUR"}
    msg_en = {x for x in msg_en if x not in stop_en}
    reply_en = {x for x in reply_en if x not in stop_en}

    msg_tokens = msg_cn | msg_en
    reply_tokens = reply_cn | reply_en

    if not msg_tokens:
        return 0.75, issues

    overlap = msg_tokens & reply_tokens
    ratio = len(overlap) / max(len(msg_tokens), 1)

    if ratio >= 0.28:
        score = 0.92
    elif ratio >= 0.14:
        score = 0.75
    else:
        # 若有结构化执行内容，给保底分，避免误伤“同义词表达”
        has_structure = bool(re.search(r"^\s*(?:[-•*]|\d+[.、\)])", reply, re.MULTILINE))
        has_execution_terms = bool(re.search(r"步骤|建议|方案|执行|KPI|ROI|GMV|转化率", reply, re.IGNORECASE))
        has_source_mapping = bool(re.search(r"来源|出处|引用|日期|时间|对应|逐条|每条|映射", reply))
        carryover_like = bool(re.search(r"继续上面|承接上面|基于上面|上个方案|上一轮|沿用上轮|继续该方案|^继续[：:，,\s]", message))
        evidence_mapping_request = _is_evidence_mapping_request(message)
        followup_keywords = [kw for kw in ("反例", "思考题", "自检问题", "示例", "举例") if kw in message]
        has_followup_hit = bool(followup_keywords and any(kw in reply for kw in followup_keywords))

        if evidence_mapping_request and has_source_mapping:
            score = 0.78
        elif has_followup_hit:
            score = 0.78
        elif carryover_like and has_structure and has_execution_terms:
            # 承接追问通常词面很短，但语义锚点在上轮上下文，不能按关键词重合硬扣。
            score = 0.78
        elif has_structure and has_execution_terms:
            score = 0.65
        else:
            score = 0.45
            issues.append("回复与问题关键词重合度低")

    # 需求锚点覆盖：对“版块优先级/A-B测试/指标阈值”等硬约束做二次判定，减少中文分词误伤
    anchor_candidates = [
        "详情页", "改版", "转化", "优先级", "版块", "模块", "A/B", "AB测试", "实验", "对照组", "测试组",
        "指标", "阈值", "回滚", "风险", "方案", "步骤", "预算", "ROI", "ROAS", "GMV", "CTR", "CVR",
        "SEO", "关键词", "层级", "内链", "集群", "Topic Cluster", "topic cluster",
        "短视频", "开场", "钩子", "家居", "清洁", "分级", "潜力", "S级", "A级", "B级",
        "客服", "差评", "危机", "处置", "SOP", "安抚", "升级", "触发", "时效", "话术", "投诉", "补偿", "回访", "闭环",
    ]
    anchors = [a for a in anchor_candidates if (a in message) or (a.upper() in msg_en)]
    if anchors:
        hit = 0
        for a in anchors:
            if a in {"A/B", "AB测试"}:
                if re.search(r"A/B|AB测试|AB 实验", reply, re.IGNORECASE):
                    hit += 1
                continue
            if a.upper() in reply_en or a in reply:
                hit += 1
        anchor_ratio = hit / max(len(anchors), 1)
        if anchor_ratio >= 0.65:
            score = max(score, 0.9)
        elif anchor_ratio >= 0.45:
            score = max(score, 0.82)
        elif anchor_ratio >= 0.30:
            score = max(score, 0.72)

    return score, issues



def _score_length(reply: str) -> tuple[float, List[str]]:
    """长度适当性：过短或过长都扣分。"""
    issues: List[str] = []
    length = len(reply)

    if length < 20:
        issues.append("回复过短（<20字符）")
        return 0.2, issues
    elif length < 50:
        return 0.5, issues
    elif length <= 2000:
        return 1.0, issues
    elif length <= 4000:
        return 0.8, issues
    else:
        issues.append("回复过长（>4000字符），可能需要精简")
        return 0.5, issues


def _score_clarity(reply: str) -> tuple[float, List[str]]:
    """清晰度：是否有清晰的结构和分段。"""
    issues: List[str] = []
    score = 0.6

    # 有分段（换行）
    if reply.count("\n") >= 2:
        score += 0.2

    # 有标题/加粗标记
    if re.search(r"[#*]{1,3}\s|【|】|##|——", reply):
        score += 0.1

    # 有列表
    if re.search(r"^[-•*]\s|^[1-9][.、]", reply, re.MULTILINE):
        score += 0.1

    return min(score, 1.0), issues


def _score_risk_awareness(reply: str, role: str) -> tuple[float, List[str]]:
    """风险意识：高风险角色是否提及风险/注意事项。"""
    issues: List[str] = []
    high_risk_roles = {"ops", "accounting", "engineering", "service"}

    if role not in high_risk_roles:
        return 0.8, issues  # 非高风险角色默认通过

    # 教学解释型回答不强制风险提示，避免误伤
    teaching_like = bool(re.search(r"为什么|怎么做|原理|教学|示例|例子|误区", reply))
    if teaching_like:
        return 0.85, issues

    risk_keywords = ["风险", "注意", "警告", "提醒", "谨慎", "止损", "回滚", "预案", "合规"]
    has_risk = any(kw in reply for kw in risk_keywords)

    if has_risk:
        return 1.0, issues
    else:
        # 短回复可能不需要风险提示
        if len(reply) > 200:
            issues.append(f"{role}角色长回复未包含风险提示")
            return 0.4, issues
        return 0.7, issues


def _score_confidence_marking(reply: str) -> tuple[float, List[str]]:
    """
    置信度标记：区分真正的不确定性声明（有价值）与普通建议词（无信号）。

    真正有价值的置信度标记：
    - 对数据的免责（仅供参考、行业参考值、估算）
    - 对建议的适用性说明（根据…情况、若…则…、具体情况因平台而异）
    - 对预测的不确定说明（可能、大概、预计）+ 具体条件

    普通的"建议"、"参考"不算置信度标记，因为所有回复都会包含这些词。
    """
    issues: List[str] = []

    # 短回复不需要置信度标记
    if len(reply) < 100:
        return 0.75, issues

    # 教学型长回复通常以示例与边界解释为主，不应按商业预测口径重罚。
    if re.search(r"教学|示例|例子|思考题|自检问题|误区", reply):
        return 0.85, issues

    # 高价值的置信度标记（真正对不确定性的说明）
    high_value_marks = [
        "仅供参考", "行业参考值", "行业参考", "行业均值", "参考数据", "估算", "典型案例",
        "具体情况因", "因平台而异", "根据实际", "视情况而定", "可能不准确",
        "预计", "大概", "约为", "左右", "数据仅供", "参考值", "基线",
    ]
    high_value_count = sum(1 for m in high_value_marks if m in reply)

    # 检测是否有条件性表述（"如果X则Y"结构，表示在说明适用条件）
    conditional_pattern = re.search(r"如果|若|当.{0,15}时|在.{0,15}情况下|根据.{0,15}决定", reply)
    has_conditional = bool(conditional_pattern)

    if high_value_count >= 2 or (high_value_count >= 1 and has_conditional):
        return 0.95, issues
    elif high_value_count >= 1:
        return 0.85, issues
    elif has_conditional:
        return 0.75, issues
    else:
        # 长回复没有任何置信度说明，可能存在过度自信问题
        if len(reply) > 500:
            issues.append("较长回复缺少置信度说明，建议对不确定的数据/预测加注'仅供参考'")
            return 0.5, issues
        return 0.65, issues


def _score_refusal_detection(reply: str) -> tuple[float, List[str]]:
    """拒绝检测：是否有无意义的拒绝或推脱。"""
    issues: List[str] = []

    refusal_patterns = [
        "我无法", "我不能", "作为AI", "作为一个语言模型",
        "我没有能力", "超出我的能力", "请咨询专业人士",
    ]

    refusal_count = sum(1 for p in refusal_patterns if p in reply)

    if refusal_count == 0:
        return 1.0, issues
    elif refusal_count == 1 and len(reply) > 200:
        # 一次提醒但仍给出了回答
        return 0.8, issues
    else:
        issues.append("回复包含过多拒绝/推脱表述")
        return 0.3, issues


# ═══════════════════════════════════════════════════════════════════════════
# 主评分函数
# ═══════════════════════════════════════════════════════════════════════════

def check_quality(message: str, reply: str, role: str, action: str = "", tool_used: bool = False) -> QualityResult:
    """
    对回复进行9维度质量评分。

    Parameters
    ----------
    message : str
        用户消息。
    reply : str
        LLM 回复。
    role : str
        当前角色。
    action : str
        检测到的动作类型。

    Returns
    -------
    QualityResult
        包含总分、是否通过、各维度得分、问题和建议。
    """
    if not reply or not reply.strip():
        return QualityResult(
            score=0.0,
            passed=False,
            dimensions={},
            issues=["回复为空"],
            suggestions=["请确保LLM正常返回内容"],
        )

    all_issues: List[str] = []
    dimensions: Dict[str, float] = {}

    # 10个维度评分（含虚构数据检测）
    s, i = _score_completeness(message, reply, role)
    dimensions["completeness"] = s
    all_issues.extend(i)

    s, i = _score_accuracy(message, reply, role)
    dimensions["accuracy"] = s
    all_issues.extend(i)

    s, i = _score_fabrication(message, reply)
    dimensions["fabrication"] = s
    all_issues.extend(i)

    s, i = _score_actionability(reply, role, action, message=message, tool_used=bool(tool_used))
    dimensions["actionability"] = s
    all_issues.extend(i)

    s, i = _score_relevance(message, reply)
    dimensions["relevance"] = s
    all_issues.extend(i)

    s, i = _score_goal_satisfaction(message, reply, role, action)
    dimensions["goal_satisfaction"] = s
    all_issues.extend(i)

    s, i = _score_value_density(message, reply, action)
    dimensions["value_density"] = s
    all_issues.extend(i)

    s, i = _score_length(reply)
    dimensions["length"] = s
    all_issues.extend(i)

    s, i = _score_clarity(reply)
    dimensions["clarity"] = s
    all_issues.extend(i)

    s, i = _score_risk_awareness(reply, role)
    dimensions["risk_awareness"] = s
    all_issues.extend(i)

    s, i = _score_confidence_marking(reply)
    dimensions["confidence_marking"] = s
    all_issues.extend(i)

    s, i = _score_refusal_detection(reply)
    dimensions["refusal_detection"] = s
    all_issues.extend(i)

    # 总分 = 均值
    overall = sum(dimensions.values()) / len(dimensions) if dimensions else 0.0

    # 虚构数据一票否决：检测到可能虚构时强制不通过（无论其他维度多好）
    fabrication_score = dimensions.get("fabrication", 1.0)
    if fabrication_score < 0.5:
        overall = min(overall, PASS_THRESHOLD - 0.05)  # 压到阈值以下

    # 执行/规划类回答，目标满足度不可过低，否则触发重写
    action_token = str(action or "").strip().lower()
    if action_token in {"create", "optimize", "execute", "plan"}:
        goal_score = float(dimensions.get("goal_satisfaction", 1.0))
        if goal_score < 0.65:
            if "执行类回答目标满足度不足" not in all_issues:
                all_issues.append("执行类回答目标满足度不足")
            overall = min(overall, PASS_THRESHOLD - 0.03)

    value_density_score = float(dimensions.get("value_density", 1.0))
    if (not _looks_like_smalltalk_message(message)) and value_density_score < 0.58:
        if "回复价值密度不足" not in all_issues:
            all_issues.append("回复价值密度不足")
        overall = min(overall, PASS_THRESHOLD - 0.02)

    passed = overall >= PASS_THRESHOLD

    # 生成建议
    suggestions: List[str] = []
    low_dims = [k for k, v in dimensions.items() if v < 0.5]
    if "completeness" in low_dims:
        suggestions.append("增加回复内容，确保覆盖用户问题的核心要素")
    if "actionability" in low_dims:
        suggestions.append("添加具体可执行的步骤或建议")
    if "relevance" in low_dims:
        suggestions.append("提高回复与用户问题的相关性")
    if "goal_satisfaction" in low_dims:
        suggestions.append("围绕用户目标输出“结论 + 步骤 + 量化目标 + 下一步”，减少泛化描述")
    if "value_density" in low_dims:
        suggestions.append("减少套话，补充“新增信息/决策依据/取舍条件”，确保每段都有可执行价值")
    if "risk_awareness" in low_dims:
        suggestions.append("添加风险提示或注意事项")
    if "refusal_detection" in low_dims:
        suggestions.append("减少不必要的拒绝表述，尽量提供有价值的回答")
    if "执行类回答目标满足度不足" in all_issues:
        suggestions.append("执行类回复需补齐“编号步骤 + 量化目标 + 下一步动作”，并与用户目标指标直接对应")
    if "回复价值密度不足" in all_issues:
        suggestions.append("请删除泛化陈述，补充至少2条“可验证依据 + 可执行动作 + 触发条件”的增量内容")
    if fabrication_score < 0.5:
        suggestions.append("回复中包含用户未提供的具体数字，请改为追问用户数据，或将数字标注为'行业参考值'/'典型案例'")
    if "已调用工具但回复缺少“证据->结论”映射" in all_issues:
        suggestions.append("若调用了工具/检索，请至少列出1-3条关键证据，并逐条说明其如何支撑结论")

    return QualityResult(
        score=round(overall, 3),
        passed=passed,
        dimensions={k: round(v, 3) for k, v in dimensions.items()},
        issues=all_issues,
        suggestions=suggestions,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════════════════════════

async def save_quality_check(role: str, message: str, reply: str, result: QualityResult) -> None:
    """将质量检查结果保存到数据库。"""
    try:
        from src.database import get_db
        db = await get_db()
        await db.execute(
            """INSERT INTO quality_checks (role, score, passed, dimensions, issues, suggestions)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                role,
                result.score,
                1 if result.passed else 0,
                json.dumps(result.dimensions, ensure_ascii=False),
                json.dumps(result.issues, ensure_ascii=False),
                json.dumps(result.suggestions, ensure_ascii=False),
            ),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save quality check: %s", e)
