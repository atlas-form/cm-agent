# 电商多Agent协同智能平台 — 完整技术文档

> 适用对象：接手开发的工程师。读完本文档可完全掌握系统每一层的实现方式和设计动机。

---

## 目录

1. [系统定位与核心约束](#1-系统定位与核心约束)
2. [整体架构](#2-整体架构)
3. [后端入口与基础设施](#3-后端入口与基础设施)
4. [意图分析引擎](#4-意图分析引擎-coreintentpy)
5. [聊天流水线 v4.1](#5-聊天流水线-v41-corechat_pipelinepy)
6. [功能预算选择器](#6-功能预算选择器-corefeature_budgetpy)
7. [Prompt 构建层](#7-prompt-构建层-coreprompt_builderpy)
8. [智能注入层](#8-智能注入层-coreprompt_injectorspy)
9. [上下文富化器](#9-上下文富化器-corecontext_enricherpy)
10. [LLM 客户端](#10-llm-客户端-llm_clientpy)
11. [技能系统](#11-技能系统-skills)
12. [多 Agent 协作调度](#12-多-agent-协作调度-coremulti_agentpy)
13. [质量评分器](#13-质量评分器-corequality_checkerpy)
14. [信任度评分器](#14-信任度评分器-coretrust_scorerpy)
15. [告警引擎](#15-告警引擎-corealert_enginepy)
16. [Agent 记忆与学习](#16-agent-记忆与学习-coreagent_memorypy)
17. [路由层](#17-路由层-routes)
18. [产品管理系统](#18-产品管理系统)
19. [数据库设计](#19-数据库设计-databasepy)
20. [数据模型](#20-数据模型-modelspy)
21. [配置中心](#21-配置中心-configpy)
22. [前端架构](#22-前端架构)
23. [测试体系](#23-测试体系)
24. [启动与部署](#24-启动与部署)

---

## 1. 系统定位与核心约束

### 解决的本质问题

电商工作天然是多职能的：一个促销活动涉及运营定价、数据分析、设计出图、财务核算。传统单一 AI 助手无法覆盖全职能，而原版系统（Project A）虽有多 Agent，但**每条消息触发 5–40 次 LLM 调用**，响应时间 30 秒到 2 分钟，无法正常使用。

**本系统的核心约束**：

| 约束 | 实现方式 | 为何这样做 |
|-----|---------|----------|
| 每条消息 1 次 LLM 调用 | 意图路由用纯规则，质量评分用规则 | LLM 调用是最大延迟来源，把所有"判断"尽量改成代码逻辑 |
| 响应流式秒级输出 | SSE + AsyncGenerator，不等 LLM 完成再返回 | 用户感知延迟=首 token 时间，流式极大改善体感 |
| 专业深度不降级 | 25 条知识规则 + 8 套角色 Prompt + 41 个结构化技能 | 用工程手段弥补减少 LLM 调用带来的"深度损失" |
| 质量可量化 | 9 维规则评分 + 信任度持久化 | 不依赖主观感受，系统可自动发现低质量输出并重试 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT (Vite SPA)                     │
│  Hash路由 → 10个页面 → SSE EventSource + 后台事件轮询         │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP / SSE
┌─────────────────────────▼───────────────────────────────────┐
│                  FastAPI (app.py)                            │
│  CORS中间件 + 限流中间件 → 17个路由模块                       │
├─────────────────────────────────────────────────────────────┤
│                  CHAT PIPELINE v4.1                          │
│                                                              │
│  [意图] → [预算] → [上下文] → [Prompt] → [LLM+Tool循环]      │
│         → [质量重试] → [多Agent协作] → [后台任务]             │
├──────────┬──────────┬───────────────┬───────────────────────┤
│ 技能系统  │ 质量评分  │  信任/学习/告警 │  产品/工作区/活动     │
│ 41个skill│ 9维规则   │  EMA/Jaccard  │  生命周期管理          │
└──────────┴──────────┴───────────────┴───────────────────────┘
                          │
              ┌───────────▼──────────┐
              │  aiosqlite (WAL模式)  │
              │      45张表           │
              └──────────────────────┘
```

**技术选型动机**：

- **FastAPI**：原生 async/await，天然支持 SSE StreamingResponse，类型注解自动生成文档。
- **aiosqlite**：SQLite 足够应对单机电商后台，WAL 模式解决读写并发，无需运维数据库服务。
- **Vite + Vanilla JS**：不引入框架，减少依赖，SSE 用原生 `EventSource` 实现，可控性最高。
- **uv**：比 pip 快 10-100 倍的 Python 包管理，CI 友好。

---

## 3. 后端入口与基础设施

### 3.1 应用入口 `app.py`（160 行）

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()          # 初始化45张表
    db = await get_db()
    await seed_agents(db)    # 写入8个Agent的初始数据
    yield
    await close_db()
```

> **为何用 lifespan 而不是 startup 事件**：FastAPI 0.93+ 推荐 lifespan，startup/shutdown 已废弃，lifespan 用 contextmanager 形式更清晰地表达"持有资源、用完释放"的生命周期。

**CORS 配置**：
```python
allow_origins=["http://localhost:3000", "http://localhost:5173"]
allow_credentials=True
```
> 只允许开发端口，不用 `*`，是因为前端需要携带 JWT Cookie（credentials=True），而 `*` + credentials 是被浏览器禁止的组合。

**限流中间件**（进程内字典，无外部依赖）：
```python
_RATE_LIMITS = {
    "/api/auth": (10, 60),   # 防暴力破解
    "/api/chat": (20, 60),   # 防 LLM 滥用
    "/api/admin": (30, 60),  # 管理操作适度宽松
}
```
> 选择进程内字典而非 Redis 限流，原因：单机部署，Redis 是额外运维成本。代价是重启后计数清零，对这类内部工具可以接受。

### 3.2 数据库连接 `database.py`（947 行）

```python
# 全局单连接复用
global _db: Optional[aiosqlite.Connection] = None

async def get_db() -> aiosqlite.Connection:
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.execute("PRAGMA busy_timeout=5000")
    return _db
```

> **为何全局单连接**：aiosqlite 在异步环境下本身是线程安全的单连接封装，SQLite WAL 模式允许并发读，写操作串行化。全局复用避免频繁 open/close 的开销，busy_timeout=5000ms 处理短暂写锁竞争。

**表初始化策略**：
```python
# 对于新增字段用 try-except 兼容旧库
try:
    await db.execute("ALTER TABLE products ADD COLUMN description TEXT")
except:
    pass  # 字段已存在，忽略
```
> 没有用 migration 框架（如 Alembic），原因是 SQLite 单文件数据库的 schema 变更很简单，用 ALTER TABLE + try-except 就够了，引入 Alembic 反而增加复杂度。

---

## 4. 意图分析引擎 `core/intent.py`（290 行）

这是系统最核心的"零成本路由"模块，**完全不调用 LLM**。

### 4.1 数据结构

```python
@dataclass
class Intent:
    primary_role: str       # 主Agent角色
    support_roles: List[str] # 支持角色（最多3个）
    action: str             # 动作类型
    platform: str           # 电商平台
    tier: int               # 复杂度等级
    confidence: float       # 置信度 0.0-1.0
    keywords: List[str]     # 命中的关键词
```

**三个 Tier 等级**：

| Tier | 值 | 触发条件 | 后续处理 |
|------|---|--------|--------|
| TIER_QUICK | 0 | 问候/简单查询 | 极简 Prompt，不走工具和质量检查 |
| TIER_SINGLE | 1 | 单职能问题 | 单 Agent，完整工具循环 |
| TIER_MULTI | 2 | 跨职能问题 | 主 Agent + 支持 Agent 协作 |

> **为何分三级而不是两级**：TIER_QUICK 单独处理是关键优化。"你好"、"怎么用"这类消息如果走完整流水线，会触发特性预算、产品上下文加载等操作，浪费 200–500ms 的 DB 查询时间。

### 4.2 关键词体系

```python
ROLE_KEYWORDS = {
    "ops":  ["直通车", "ROI", "转化率", "大促", "活动", "定价", "选品", ...],  # ~50个
    "data": ["数据", "趋势", "分析", "同比", "环比", "漏斗", "RFM", ...],     # ~45个
    "service": ["客服", "投诉", "退款", "差评", "评价", "售后", ...],         # ~40个
    # ...其余5个角色
}
PLATFORM_KEYWORDS = {
    "taobao": ["淘宝", "天猫", "万相台", "直通车", "超级推荐"],
    "douyin": ["抖音", "千川", "dou+", "达人", "直播间"],
    # ...共11个平台
}
QUICK_KEYWORDS = ["你好", "hi", "hello", "谢谢", "帮我", "怎么用", ...]  # 14个
```

> **为何手工维护关键词而不用 TF-IDF 或 embedding**：电商词汇高度领域化且稳定（"直通车"永远属于 ops），手工维护精确度更高，延迟<1ms vs embedding 需要 10-50ms 推理。同时 `agent_memory.py` 会从高质量交互中自动提取新关键词补充进来。

### 4.3 评分算法

```python
# 当前消息权重2倍，历史权重0.5倍
msg_scores  = count_keywords(message)
hist_scores = count_keywords(recent_history[:3])
total_score = msg_scores * 2.0 + hist_scores * 0.5

primary_role = max(total_score, key=total_score.get)
support_roles = [r for r in total_score if total_score[r] > 0 and r != primary_role][:3]

# 若支持角色不足，从亲和度表补充
ROLE_AFFINITY = {
    "ops":   {"data": 0.7, "design": 0.6, "accounting": 0.5},
    "data":  {"ops": 0.6, "accounting": 0.5},
    # ...
}

confidence = primary_score / sum(all_scores) if sum > 0 else 0.5
tier = TIER_MULTI if support_roles and second_highest_score > 0 else TIER_SINGLE
```

> **历史权重用 0.5 而非更高**：历史的作用是补充当前消息语境，而不是主导路由。如果用户问完"数据分析"后紧接着问"主图怎么做"，应该路由到 design 而不是 data。0.5 的权重可以防止历史"污染"当前意图。

---

## 5. 聊天流水线 v4.1 `core/chat_pipeline.py`（879 行）

这是系统的主编排器，所有智能能力在这里被串联。

### 5.1 主函数签名

```python
async def chat_stream(
    message: str,
    user_id: int,
    role: Optional[str] = None,
    conversation_id: Optional[str] = None,
    product_id: Optional[int] = None,
    product_ids: Optional[List[int]] = None,
    workspace_id: Optional[int] = None,
) -> AsyncGenerator[PipelineEvent, None]
```

> **用 AsyncGenerator 而非 WebSocket**：SSE 是单向推送，比 WebSocket 简单得多，不需要维护连接状态，中间件（Nginx/CDN）对 SSE 兼容性更好。AsyncGenerator 让流水线每一步都可以 `yield` 事件，代码结构清晰。

### 5.2 完整 11 步流程

#### Step 0 — 上下文富化

```python
enriched_message, was_enriched = enrich_message(message, history)
if was_enriched:
    yield status_event("context_enriched")
```

> **为何在流水线最前面做富化**：后续所有步骤（意图分析、Prompt 构建）都依赖消息内容。先把"这个产品怎么定价"扩展为"[上下文: 我们在讨论SKU-A直通车] 这个产品怎么定价"，意图分析命中关键词才准确。

#### Step 1 — 意图分析

```python
intent = analyze_intent(enriched_message, history[-3:])
effective_role = role or intent.primary_role  # 用户指定优先
yield intent_analyzed_event(intent)
```

> **用户指定角色仍然保留**：系统默认 auto-dispatch，但允许用户在 URL 参数或调试时强制指定角色，给高级用户留出控制权。

#### Step 1.5 — 主动通知（异步非阻塞）

```python
if ENABLE_PROACTIVE:
    notifications = await generate_notifications(user_id)
    for n in notifications[:5]:
        yield proactive_event(n)
```

> **在意图分析之后、LLM 调用之前推送**：主动通知（如"直通车预算快到了"）与当前对话内容无关，但应该在用户看到回复之前显示，放在这个位置保证了时序正确且不影响主流程延迟。

#### Step 2 — 快速回复短路

```python
if intent.tier == TIER_QUICK:
    prompt = build_quick_reply_prompt()
    async for event in _llm_stream_simple(prompt, message):
        yield event
    return  # 直接结束，不走后续步骤
```

> **短路的价值**：TIER_QUICK 跳过特性预算（节省 DB 查询）、跳过产品上下文（节省文件 IO）、跳过质量检查（节省评分计算）、跳过多 Agent 协作，整体节省 200–800ms。

#### Step 3 — 功能预算

```python
features = select_features(
    message=enriched_message,
    role=effective_role,
    platform=intent.platform,
)
yield features_selected_event(features)
```

> 详见第 6 节。

#### Step 4 — 并发加载上下文（三路并发）

```python
memories, trust_info, product_ctx = await asyncio.gather(
    _load_memories(effective_role, limit=5),
    _load_trust(effective_role),
    _load_product_ctx_rich(product_ids),
)
```

> **为何三路并发**：这三次 DB 查询彼此独立，顺序执行需要 ~30ms × 3 = 90ms，并发只需要 ~30ms。`asyncio.gather` 是最自然的并发方式，代码可读性高于手动 Task 管理。

**产品上下文四级加载** `_load_product_ctx_rich()`：

```
L1: 基础信息（名称/品类/SKU/定价）              → products 表
L2: 结构化知识（功能卖点/使用场景/竞品对比）     → product_knowledge 表
L3: 文件总结（上传文档的 AI 摘要）               → product_assets 表
L4: 统计数据（销售数量/活动次数）                → campaigns/messages 表聚合
```

> **分级加载的原因**：不是所有对话都需要全部四级数据。L1 永远加载，L2-L4 在 product_ids 非空时按需加载，避免无谓的 DB 查询。这是"按需付出成本"的设计原则。

#### Step 5 — Prompt 构建

```python
system_prompt = build_system_prompt(
    role=effective_role,
    message=enriched_message,
    memories=memories,
    platform=intent.platform,
    product_context=product_ctx,
    trust_level=trust_info.level,
    features=features,
)
# 硬限制：截断到 MAX_PROMPT_CHARS = 2500 字符
```

> **2500 字符上限**：实测超过 2500 字符的系统提示对中文电商场景效果提升有限，但会增加每次 LLM 调用的 token 成本 10–30%。2500 字符是效果与成本的平衡点。

#### Step 6 — 工具和历史加载

```python
tools = reg.get_tools_for_role(effective_role)   # 该角色的技能 + coordination
history_msgs = await _load_history(
    conversation_id, user_id, limit=MAX_HISTORY_MESSAGES  # 默认10条
)
```

> **历史限制 10 条**：历史越多，LLM 注意力越分散，且 token 成本线性增长。10 条涵盖约 5 轮对话，对大多数连续讨论场景足够。

#### Step 7 — LLM 流式调用 + Tool 循环

```python
for round_num in range(MAX_TOOL_ROUNDS + 1):  # 最多4轮（含初始调用）
    async for chunk in call_llm_stream(system, message, history, tools=tools):
        if chunk["type"] == "token":
            reply_buffer += chunk["text"]
            yield token_event(chunk["text"])
        elif chunk["type"] == "tool_call":
            yield tool_call_event(chunk)
            result = await reg.execute(chunk["name"], chunk["args"])
            yield tool_result_event(chunk["name"], result)
            # 将结果追加到 llm_messages，进入下一轮
            llm_messages.append({"role": "tool", "content": json.dumps(result)})
            break  # 跳出 chunk 循环，进入下一轮 LLM 调用
    else:
        break  # LLM 没有调用工具，对话完成
```

> **MAX_TOOL_ROUNDS = 3 的选择**：实测大多数电商问题 1–2 轮工具调用即可完成（如：调用定价策略 → 调用利润预测）。3 轮是防止无限循环的安全上限，超过 3 轮说明 LLM 陷入了工具调用循环，直接截断。

#### Step 7.5 — 质量重试

```python
if ENABLE_QUALITY_CHECK:
    quality_result = run_quality_check(effective_role, message, reply)
    if not quality_result.passed:
        # 注入质量反馈，重试一次
        retry_prompt = f"你的回复存在问题：{quality_result.issues}。请重新回复。"
        llm_messages.append({"role": "user", "content": retry_prompt})
        # 再次流式调用，替换 reply_buffer
        yield status_event("quality_retry")
```

> **只重试一次**：重试一次已经能覆盖大多数低质量情况（通常是回复太短或缺乏结构）。多次重试会让用户等待时间翻倍，且效果递减。

#### Step 8 — 检测是否在追问

```python
def _is_asking_user(reply: str) -> bool:
    if len(reply) > 200:
        return False  # 长回复不是追问
    has_question = "?" in reply or "？" in reply
    has_ask_pattern = any(p in reply for p in ["请问", "能告诉我", "需要提供", "可以分享"])
    return has_question and has_ask_pattern
```

> **追问时跳过多 Agent 协作**：如果主 Agent 在问用户"你的目标 ROI 是多少？"，就不应该触发支持 Agent 分析——信息不足时分析没有意义，只会输出一堆泛泛的假设性内容。

#### Step 9 — 多 Agent 协作

```python
if intent.tier == TIER_MULTI and support_roles and not is_asking:
    # 从主 Agent 回复中扫描额外需要的角色
    detected_roles = _detect_needed_roles_from_reply(reply_buffer)
    all_support = list(set(intent.support_roles + detected_roles))

    async for event in dispatch_support_agents(
        message, effective_role, reply_buffer, all_support, ...
    ):
        yield event
```

> **从回复中动态检测需要的角色**：意图分析在消息到达时只能看到"提问"，但主 Agent 回复后可能说"需要数据团队验证ROI预测"，这时可以精准地拉入 data Agent。两步检测（静态意图+动态回复扫描）比只用意图分析更准确。

#### Step 10–11 — 保存消息 + 后台任务

```python
# 保存消息（同步，必须完成）
await _save_messages(user_message, assistant_message, metadata)

# 后台任务（fire-and-forget，不阻塞 SSE 流关闭）
asyncio.create_task(_dispatch_background(
    role, message, reply, quality_result, intent, user_id
))
```

> **后台任务用 `create_task` 而非 `await`**：质量更新、信任计算、学习沉淀对用户来说是"看不见的"，不需要等它们完成。用 `create_task` 让这些操作在事件循环空闲时执行，SSE 流可以立即关闭，用户体验更好。

---

## 6. 功能预算选择器 `core/feature_budget.py`（163 行）

### 6.1 12 个智能特性

| 特性名 | 注入内容 | 主要受益角色 |
|--------|---------|------------|
| `deep_knowledge` | 专业经验/行业标准/最佳实践 | 全部 |
| `platform_knowledge` | 平台算法/规则/政策 | ops, web |
| `quality_frameworks` | FABE/RICE/SWOT 等框架 | data, ops |
| `communication_style` | 语气/节奏/表达方式 | service, creative |
| `workflow` | 分析→规划→执行→验证→交接 | ops, engineering |
| `learning_memory` | 历史成功模式/盲点/改进方向 | 全部 |
| `cognitive_style` | 角色思维链路和推理模式 | 全部 |
| `distilled_knowledge` | 从历史提炼的高质量知识点 | data, accounting |
| `wave_activation` | 根据话题热度调整详细程度 | creative, ops |
| `pipeline_context` | 当前所处阶段状态 | ops, engineering |
| `handoff_context` | 跨角色协作的交接信息 | 全部 |
| `trust_context` | 信任等级对应的自主程度 | 全部 |

### 6.2 选择算法

```python
def select_features(message, role, platform) -> List[str]:
    scored = []
    for feature in FEATURES:
        kw_score = sum(1 for kw in feature.keywords if kw in message)
        affinity  = feature.role_affinity.get(role, 0.5)

        # 无具体平台时降低平台知识权重
        if feature.name == "platform_knowledge" and platform == "general":
            affinity *= 0.3

        total = kw_score * 1.0 + affinity * 2.0  # 亲和度权重是关键词的2倍
        scored.append((total, feature.name))

    scored.sort(reverse=True)
    return [name for _, name in scored[:FEATURE_BUDGET_MAX]]  # 默认取3个
```

> **亲和度权重是关键词的 2 倍**：关键词匹配是"该特性现在相关"，亲和度是"该特性对这个角色长期有用"。长期有用的特性应该比偶尔相关的特性更稳定地出现，否则 Prompt 内容会随消息关键词大幅波动，导致 Agent 行为不稳定。

> **只选 3 个特性（FEATURE_BUDGET_MAX=3）**：每个特性注入约 100-200 字，3 个特性约 300-600 字。这与角色身份、知识规则、产品上下文加在一起刚好在 2500 字符预算内。增加到 5 个会超预算，减少到 2 个则信息密度不够。

---

## 7. Prompt 构建层 `core/prompt_builder.py`（259 行）

### 7.1 构建顺序（12 段）

```python
def build_system_prompt(...) -> str:
    sections = []

    # 1. 英文 header（防止某些模型中文提示乱码）
    sections.append(
        f"You are {display_name} ({archetype}), "
        f"a specialized AI assistant for Chinese e-commerce. "
        f"CRITICAL: Always respond in Chinese."
    )

    # 2. 角色身份
    sections.append(f"你是{display_name}（{archetype}）。{vibe}")

    # 3. 认知风格（来自 COGNITIVE_STYLES，8种）
    sections.append(f"思维模式：{COGNITIVE_STYLES[role]}")

    # 4. 沟通风格（来自 role_presets.json）
    sections.append(communication_style_text)

    # 5. 关键规则（最多5条，来自 role_presets.json）
    sections.append(critical_rules_text)

    # 6. 激活特性（3个，来自 feature_budget）
    sections.append("激活特性：" + "｜".join(feature_descriptions))

    # 7. 知识规则（匹配当前角色，最多3条）
    sections.append(knowledge_rules_text)

    # 8. 平台上下文
    if platform != "general":
        sections.append(f"当前平台：{PLATFORM_KNOWLEDGE[platform]}")

    # 9. 产品上下文
    if product_context:
        sections.append(f"产品信息：{product_context}")

    # 10. 信任等级
    sections.append(TRUST_PROMPTS[trust_level])

    # 11. 学习记忆（最多3条）
    sections.append(memories_text)

    # 12. 智能注入（来自 prompt_injectors）+ 因果推理（仅 data 角色）
    sections.append(build_intelligence_prompt_section(role, message, platform))
    if role == "data":
        sections.append(build_multi_causal_prompt())

    result = "\n\n".join(s for s in sections if s.strip())
    return result[:MAX_PROMPT_CHARS]  # 硬截断
```

**信任等级对应的不同 Prompt**：

| 等级 | 分数范围 | 注入内容 |
|-----|---------|--------|
| HIGH | ≥0.8 | "可自主决策，减少不必要的确认步骤" |
| MODERATE | ≥0.5 | "正常流程，关键决策前确认用户意图" |
| LOW | ≥0.3 | "谨慎输出，多提供依据和替代方案" |
| NONE | <0.3 | "仅提供信息，所有建议需用户最终确认" |

> **英文 header 是关键设计**：测试发现 `gpt-oss` 系列 thinking 模型在系统提示以中文开头时偶发乱码。英文 header 作为"锚点"，后续内容全中文，模型处理稳定。

### 7.2 快速回复 Prompt

```python
def build_quick_reply_prompt() -> str:
    return (
        "You are a multi-role AI assistant for Chinese e-commerce. "
        "CRITICAL: Always respond in Chinese (中文). Never respond in English.\n"
        "你是一个电商多职能AI助手。用户发来了简短的问候或状态查询。"
        "请用简洁友好的中文回复，不超过3句话。"
        "如果用户问你能做什么，简述你拥有8个专业角色。"
    )
```

> **快速回复不注入角色身份**：问候消息不需要 Agent 以特定专业身份回应，一个通用友好的回复就够了。注入角色身份会让 Agent 回答"你好"时也显得过于专业生硬。

---

## 8. 智能注入层 `core/prompt_injectors.py`（421 行）

这一层让 8 个 Agent 展现出真正不同的思维方式，而不只是名字不同。

### 8.1 行为引导（每角色 5 条规则）

以 ops 为例：
```
1. 库存低于安全线立即预警；ROI 低于止损线立即建议调整
2. 发现竞品异常动作时主动通知团队
3. 每次推广建议必须附带预算和预期 ROI
4. [严格规则] 用户未说明产品/平台/当前数据时，只提问，不分析
5. [铁律] 回复中具体数字必须来自用户，否则标注"行业参考值"或"典型案例"
```

> **规则 4 和 5 是最重要的**：LLM 最大的风险是在信息不足时编造具体数字（如"你的ROI目前是3.2"）。第 4 条强制追问，第 5 条强制标注，两道防线防止虚假数据输出。这与质量评分器中的"fabrication 检测"形成呼应——Prompt 层预防，评分层兜底。

### 8.2 子专业动态激活

```python
SUB_SPECIALIZATIONS = {
    "service": {
        "presale":  {"keywords": ["咨询", "尺码", "能不能", "多久"], "prompt": "售前咨询专家..."},
        "aftersale":{"keywords": ["退款", "退货", "差评", "投诉"], "prompt": "售后处理专家..."},
        "crisis":   {"keywords": ["舆情", "315", "曝光", "大量投诉"], "prompt": "危机处理专家..."},
    },
    "ops": {
        "search":   {"keywords": ["直通车", "关键词", "出价", "质量分"]},
        "content":  {"keywords": ["内容营销", "种草", "笔记", "UGC"]},
        "live":     {"keywords": ["直播", "主播", "连麦", "开播"]},
        "activity": {"keywords": ["大促", "双11", "618", "满减"]},
    },
    # ...
}

def _detect_sub_specialization(role, message) -> Optional[str]:
    for sub_name, config in SUB_SPECIALIZATIONS.get(role, {}).items():
        if any(kw in message for kw in config["keywords"]):
            return config["prompt"]
    return None
```

> **子专业动态激活而非静态注入**：service Agent 面对售前和售后问题时需要完全不同的 SOP（售前关注推荐，售后关注退款流程）。静态注入所有子专业会撑满 Prompt 预算，动态激活让 Agent 在合适时候切换专业模式。

### 8.3 认知风格（8 种）

```python
COGNITIVE_STYLES = {
    "ops":         ("执行型", "拆解→盘点→规划→止损→监控"),
    "data":        ("证据型", "怀疑→验证→排除替代→结论"),
    "service":     ("共情型", "情绪→共情→分类→SOP→执行→满意度"),
    "design":      ("视觉型", "理解→参考→视觉策略→规格→验收"),
    "accounting":  ("审慎型", "采集→核对→标记→确认→结论"),
    "engineering": ("系统型", "需求→评估→风险→方案→回滚→实施"),
    "web":         ("优化型", "诊断→定位→方案→预估→验证"),
    "creative":    ("发散型", "趋势→联想→碰撞→过滤→呈现"),
}
```

> **思维链路是区分 Agent 个性最有效的方式**：名字和描述不同只是表面，真正让 Agent 回复风格不同的是思维顺序。data Agent 会先质疑数据可靠性，engineering Agent 会先列风险，这是通过思维链路 Prompt 实现的，而不是靠角色描述。

### 8.4 平台知识（8 个平台，各 100–150 字）

每个平台注入该平台的核心算法逻辑，例如：
```
douyin: 千川投流核心逻辑：赛马机制（初期小额多计划竞争→跑出计划放量）；
        核心指标优先级：5秒完播率 > 互动率 > 转化率；
        dou+ 适合内容冷启动，千川适合商品转化；
        直播间算法权重：在线人数 × 互动率 × 转化率
```

> **平台知识在 Prompt 层注入而非写入 LLM**：平台算法规则频繁更新（如抖音千川的出价策略每半年就变），写死在 LLM 训练数据中的知识会过时。放在 `knowledge_rules.json` 配置文件中，更新规则只需改 JSON 不需要重新部署。

---

## 9. 上下文富化器 `core/context_enricher.py`（120 行）

### 9.1 触发条件（三种情况）

```python
def needs_context_enrichment(message, history) -> bool:
    # 1. 短消息 + 指代语言（"这个产品"、"上面说的方案"）
    if len(message) < 30 and detect_referential_language(message):
        return True
    # 2. 续接信号（"好的"、"那么"、"所以"）
    if detect_continuation(message):
        return True
    # 3. 显式指代（不管消息长短）
    if detect_referential_language(message):
        return True
    return False
```

**指代语言 Regex（4 个模式）**：
```python
[r"这个|这些|那个|那些|它们?|这里|那里",
 r"上面说的|刚才说的|前面提到|之前说的|你说的那个",
 r"同样的|一样的|还是那个|跟刚才一样",
 r"继续|接着|然后呢|还有呢|再.*一下"]
```

### 9.2 富化结果

```python
enriched = f"[对话上下文]\n{history_snippet}\n\n[当前问题]\n{message}"
```

> **为何不直接把历史放进 llm_messages**：历史消息已经通过 `_load_history()` 放进了 messages 列表。富化是针对**意图分析**的：`analyze_intent()` 只看当前消息，不看 messages 列表。把上下文注入消息本身，让意图分析也能理解指代语言，路由准确率显著提升。

---

## 10. LLM 客户端 `llm_client.py`（559 行）

### 10.1 核心流式函数

```python
async def call_llm_stream(
    system: str = "",
    message: str = "",
    history: Optional[List[Dict]] = None,
    model: Optional[str] = None,
    tools: Optional[List[Dict]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    # 产出三种事件
    yield {"type": "token",     "text": "..."}
    yield {"type": "tool_call", "name": "...", "args": {...}, "id": "..."}
    yield {"type": "done"}
```

### 10.2 熔断器

```python
@dataclass
class CircuitBreaker:
    fail_threshold: int   = 20      # 窗口内失败20次触发
    window_seconds: float = 120.0   # 2分钟观察窗口
    open_seconds: float   = 6.0     # 打开后6秒禁止调用
    is_open: bool         = False
    fail_times: List[float] = field(default_factory=list)
```

```python
def record_failure(self):
    now = time.time()
    self.fail_times = [t for t in self.fail_times if now - t < self.window_seconds]
    self.fail_times.append(now)
    if len(self.fail_times) >= self.fail_threshold:
        self.is_open = True
        self.opened_at = now

def check(self) -> bool:  # 返回 True 表示可以调用
    if not self.is_open:
        return True
    if time.time() - self.opened_at > self.open_seconds:
        self.is_open = False
        return True
    return False
```

> **熔断器是必要的保护**：当 LLM 服务出现故障时，没有熔断器会导致所有请求都等待 30 秒超时，大量用户同时等待会把服务器内存和连接耗尽。熔断器让系统在服务故障时快速失败（fail fast），用户立刻得到错误提示而不是无限等待。

### 10.3 Thinking 标签处理

```python
def _strip_think_tags(text: str) -> str:
    # 移除 <think>...</think>，只保留对用户可见的内容
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
```

> **gpt-oss thinking 模型会在回复中输出 `<think>` 标签**，这是模型的内部推理过程，不应该展示给用户。剥离处理保证前端只收到干净的回复文本。

### 10.4 重试机制

```python
for attempt in range(3):
    try:
        async for chunk in _do_stream(...):
            yield chunk
        return
    except (TimeoutError, httpx.TimeoutException):
        if attempt < 2:
            await asyncio.sleep([1.0, 2.0, 4.0][attempt])
            continue
        raise
```

> **指数退避（1s, 2s, 4s）而非固定延迟**：网络抖动通常是瞬时的，短暂等待后重试成功率高。固定延迟浪费时间，指数退避在反复失败时避免频繁重试加剧服务器压力。

---

## 11. 技能系统 `skills/`

### 11.1 基类 `SkillBase`

```python
class SkillBase:
    name: str           # 唯一标识，如 "ops_promo_planning"
    display_name: str   # 展示名，如 "促销策划"
    category: str       # 所属角色，如 "ops"
    description: str    # 功能描述（注入到 tool_spec 的 description 字段）
    input_schema: Dict  # JSON Schema（LLM 用它生成正确的参数）

    async def execute(**kwargs) -> Dict:  # 执行并返回结构化结果
        raise NotImplementedError
```

### 11.2 注册中心 `registry.py`

```python
class SkillRegistry:
    _skills: Dict[str, SkillBase] = {}

    def register(self, skill: SkillBase):
        self._skills[skill.name] = skill

    def get_tools_for_role(self, role: str) -> List[Dict]:
        # 返回该角色技能 + coordination 技能的 OpenAI tool_spec
        tools = []
        for skill in self._skills.values():
            if skill.category == role or skill.category == "coordination":
                tools.append({
                    "type": "function",
                    "function": {
                        "name": skill.name,
                        "description": skill.description,
                        "parameters": skill.input_schema,
                    }
                })
        return tools

    async def execute(self, name: str, args: Dict) -> Dict:
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Skill {name} not found"}
        return await skill.execute(**args)
```

> **为何用注册中心而不是直接调用**：注册中心实现了技能的动态发现，新增技能只需在模块中注册，不需要修改流水线代码。同时 `get_tools_for_role` 为 LLM 提供标准 OpenAI tool_spec，LLM 只能调用该角色的技能，防止越权调用（如客服 Agent 调用会计技能）。

### 11.3 41 个技能分类

| 模块 | 技能（7个）|
|-----|----------|
| **ops.py** | 促销策划、定价策略、渠道规划、库存计划、选品分析、文案生成、执行计划 |
| **data_analysis.py** | 趋势预测、异常诊断、漏斗分析、RFM 分层、市场情报、AB 测试设计 |
| **customer_service.py** | FAQ 生成、话术模板、工单分级、投诉分析、满意度评估 |
| **design.py** | 视觉规范、主图优化、详情页结构、色彩方案、排版系统、品牌审计 |
| **accounting.py** | 成本分析、利润预测、预算编制、财务报告、合规检查 |
| **engineering.py** | 技术方案、架构设计、性能优化、安全审计、接口文档 |
| **web.py** | SEO 诊断、关键词选取、页面优化、A/B 测试、内容日历 |
| **creative.py** | 创意 Brief、视频脚本、文案框架、选题策划 |
| **coordination.py** | 多 Agent 协调、项目进度、资源分配（每个角色都可调用）|

**技能返回格式示例（ops_promo_planning）**：
```python
return {
    "活动阶段": [
        {"阶段": "预热期", "天数": 3, "预算占比": "20%",
         "目标": "加购+收藏", "执行动作": ["开直通车预热", "发优惠券"]},
        {"阶段": "爆发期", "天数": 2, "预算占比": "60%", ...},
        {"阶段": "续航期", "天数": 5, "预算占比": "15%", ...},
        {"阶段": "复盘期", "天数": 2, "预算占比": "5%", ...},
    ],
    "KPI": {"目标GMV": "...", "目标ROI": 5.0, "预估UV": "...", "预估转化率": "3.5%"},
    "风险提示": "..."
}
```

> **技能返回结构化 Dict 而非纯文本**：结构化数据让 LLM 可以在后续轮次中引用具体字段（"根据你的定价策略中推荐价格XX元..."），也让前端 ToolCallCard 组件可以格式化展示，而不是把 JSON 直接输出给用户。

---

## 12. 多 Agent 协作调度 `core/multi_agent.py`（263 行）

### 12.1 调度函数

```python
async def dispatch_support_agents(
    message: str,
    primary_role: str,
    primary_reply: str,
    support_roles: List[str],
    user_id: int,
    product_id: Optional[int] = None,
    platform: str = "general",
) -> AsyncGenerator[Dict[str, Any], None]
```

### 12.2 SSE 事件序列

```
collab_start       → {"agents": ["data", "accounting"]}
collab_agent_start → {"role": "data", "display_name": "数据处理助理"}
collab_token       → {"role": "data", "text": "根据销售趋势..."}
collab_tool_call   → {"role": "data", "tool_name": "trend_forecast", ...}
collab_tool_result → {"role": "data", "result": {...}}
collab_agent_done  → {"role": "data", "reply": "...", "elapsed_ms": 1234}
collab_agent_start → {"role": "accounting", ...}
... (accounting 的事件)
collab_done        → {"contributions": [{role, reply, skills_used, elapsed_ms}, ...]}
```

### 12.3 支持 Agent 的 Context Message

```python
context_message = (
    f"用户原始问题：{message[:200]}\n\n"
    f"主Agent（{primary_role}）的分析：{primary_reply[:300]}\n\n"
    f"请以{display_name}的专业视角补充分析，重点关注你的专业领域，"
    f"不要重复主Agent已经说过的内容。"
)
```

> **明确告知"不要重复"**：没有这条指令时，支持 Agent 会在回复开头重述主 Agent 的结论（"正如运营助理所说..."），浪费 token 且降低信息密度。明确指令让支持 Agent 专注于增量价值。

> **主 Agent 回复截断到 300 字**：支持 Agent 需要了解主 Agent 的结论，但不需要全文。截断节省 token，同时避免支持 Agent 被主 Agent 的详细内容"带偏"而失去自己的专业视角。

---

## 13. 质量评分器 `core/quality_checker.py`（403 行）

### 13.1 9 个评分维度

| 维度 | 关键评分逻辑 | 通过阈值影响 |
|-----|-----------|-----------|
| **completeness** | 回复长度 / 问题长度 ≥ 2.0 得满分 | 太短的回复直接不过 |
| **accuracy** | 数据角色回复中出现"无数据"扣 0.2；多处"不确定"扣 0.15 | 不准确的数据分析结果被降分 |
| **fabrication** | 检测"你的XX目前为YY"模式；有免责标注则豁免 | **一票否决**：虚构数据直接压低总分到 0.65 以下 |
| **actionability** | 执行类问题：有步骤列表 +0.3；有量化目标 +0.2 | 没有可操作步骤的"方案"不过 |
| **relevance** | 回复与问题的关键词重合度 ≥ 30% | 答非所问的回复被识别 |
| **length** | 20–2000 字满分；<20 字 0.2 分；>4000 字 0.5 分 | 过短或过长都扣分 |
| **clarity** | 有段落换行 +0.2；有标题/加粗 +0.1；有列表 +0.1 | 无结构的大段文字被扣分 |
| **risk_awareness** | 高风险角色（ops/accounting/engineering）需提及风险词 | 财务/技术建议必须有风险提示 |
| **confidence_marking** | 有"可能"/"建议"/"参考"等不确定标记 | 过于武断的表述被扣分 |
| **refusal_detection** | 拒绝关键词（"无法帮助"/"不能做"）出现 ≥2 次 → 0.3 分 | 无谓拒绝被惩罚 |

### 13.2 虚构数据检测 Regex

```python
FABRICATION_PATTERNS = [
    r"[您你]的.{0,10}(?:目前|现在|当前|约为|约是|是|为)\s*[\d]+\.?\d*\s*[%％元万亿]",
    r"(?:目前|当前).{0,8}(?:约为|约是|是|为|达到|达)\s*[\d]+\.?\d*\s*[%％元万亿]",
    r"[您你]的.{0,10}(?:目前|当前)\s*[\d]+\.?\d*",
]
DISCLAIMER_WORDS = ["示例", "假设", "参考值", "仅供参考", "行业参考值", "举例", "假设场景"]
```

> **虚构检测是整个质量系统的核心**：LLM 最危险的错误是把行业数据说成用户自己的数据（"你的ROI目前是3.2"）。这类错误用户不容易发现，但会导致错误决策。Regex 检测虽然不完美，但能覆盖 80%+ 的常见虚构模式，且完全无 LLM 调用延迟。

### 13.3 总分与一票否决

```python
overall = statistics.mean(list(dimensions.values()))

# 虚构数据一票否决
if dimensions["fabrication"] < 0.5:
    overall = min(overall, PASS_THRESHOLD - 0.05)  # 压到 0.65

passed = overall >= PASS_THRESHOLD  # PASS_THRESHOLD = 0.7
```

> **为何 PASS_THRESHOLD = 0.7 而非 0.8**：0.7 是"可用但不完美"的门槛。0.8 过于严格会导致频繁重试，增加延迟；0.6 过于宽松无法有效筛选低质量输出。0.7 实测通过率约 85%，触发重试率约 15%，平衡了质量和效率。

---

## 14. 信任度评分器 `core/trust_scorer.py`（139 行）

### 14.1 EMA 平滑更新

```python
EMA_ALPHA = 0.3

async def update_trust(role: str, quality_score: float) -> TrustUpdate:
    old_score = await _get_current_score(role)  # 从DB读取，默认0.5

    new_score = EMA_ALPHA * quality_score + (1 - EMA_ALPHA) * old_score
    new_score  = max(0.0, min(1.0, new_score))

    new_level  = _score_to_level(new_score)
    await _save_score(role, new_score, new_level)

    return TrustUpdate(
        role=role,
        previous_score=old_score, new_score=new_score,
        previous_level=old_level, new_level=new_level,
        delta=new_score - old_score,
    )
```

> **EMA α=0.3 的选择**：α=0.3 意味着当前质量分占 30% 权重，历史占 70%。这防止了单次异常高分或低分"翻转"信任等级。例如一次偶然的低质量回复不会把 HIGH 信任降到 LOW；需要持续多次低质量才会触发等级下降，这与"信任"的真实含义一致。

### 14.2 信任等级分布

```python
def _score_to_level(score: float) -> str:
    if score >= 0.8: return "HIGH"
    if score >= 0.5: return "MODERATE"
    if score >= 0.3: return "LOW"
    return "NONE"
```

新 Agent 初始分 0.5（MODERATE），表示"尚未建立信任，正常流程"，不过于激进也不过于保守。

---

## 15. 告警引擎 `core/alert_engine.py`（272 行）

### 15.1 6 种告警类型

| 类型 | 检测目标 | 关键词（critical级）| 应用场景 |
|-----|---------|-----------|--------|
| emotion | 用户消息 | 愤怒、崩溃、垃圾 | 用户情绪激动时升级处理 |
| urgency | 消息+回复 | 紧急、deadline、故障、宕机 | 时间敏感问题优先处理 |
| quality | 评分结果 | score<0.3 | 连续低质量输出预警 |
| trust | 信任等级 | trust_level==NONE | Agent 信任度极低预警 |
| anomaly | 消息内容 | ROI下降、流量骤降、封店 | 业务异常主动上报 |
| compliance | 消息+回复 | 刷单、虚假宣传、假货、侵权 | 合规风险强制提示 |

### 15.2 去重与冷却

```python
_dedup_cache: Dict[str, float] = {}  # dedup_key → 上次触发时间
DEDUP_WINDOW  = 300.0   # 5分钟内同类型不重复触发
COOLDOWN_WINDOW = 600.0 # 10分钟内同 key 冷却

def _check_dedup(key: str) -> bool:
    now = time.time()
    last = _dedup_cache.get(key, 0)
    if now - last < COOLDOWN_WINDOW:
        return False  # 被冷却，不触发
    _dedup_cache[key] = now
    return True
```

> **为何用进程内缓存而非 DB 去重**：告警去重需要高频检查（每条消息都扫描），DB 查询延迟 5-20ms，进程内字典延迟 <0.1ms。代价是重启后缓存清空，但告警是提示性功能，偶尔重复触发不是问题。

---

## 16. Agent 记忆与学习 `core/agent_memory.py`（305 行）

### 16.1 学习提取逻辑

```python
def extract_learnings(role, message, reply, quality_score, quality_issues) -> List[Learning]:
    learnings = []

    if quality_score >= 0.75 and _has_structure(reply):
        learnings.append(Learning(
            role=role,
            category="success",        # 成功模式
            content=f"[{action}] {reply[:200]}",
            confidence=min(quality_score, 0.95),
        ))
    elif quality_score < 0.5:
        learnings.append(Learning(
            category="blindspot",      # 盲点
            content=f"在[{action}]时回复质量低：{', '.join(quality_issues[:3])}",
            confidence=0.6,
        ))
    elif 0.5 <= quality_score < 0.75 and quality_issues:
        learnings.append(Learning(
            category="improvement",    # 待改进
            content=f"[{action}]可改进：{quality_issues[0]}",
            confidence=0.55,
        ))

    return learnings
```

### 16.2 去重机制

```python
async def save_learnings(learnings: List[Learning]):
    for learning in learnings:
        # 查询同角色同类别最近10条
        existing = await _fetch_recent_learnings(learning.role, learning.category, limit=10)
        # Jaccard 相似度 > 0.6 则跳过
        for ex in existing:
            if _jaccard_similarity(learning.content, ex["content"]) > 0.6:
                break
        else:
            await _insert_learning(learning)
```

> **Jaccard 相似度去重**：同一个问题问了 100 次，不应该存 100 条相同的学习记录。Jaccard 基于词集合重叠度，计算简单，对中文电商词汇效果好（不需要分词库）。

### 16.3 自适应关键词学习

```python
async def extract_routing_keywords(role, message, quality_score) -> List[str]:
    if quality_score < 0.8:
        return []  # 仅高质量交互才学习新关键词

    # 提取2-4字中文词组
    candidates = re.findall(r"[\u4e00-\u9fff]{2,4}", message)
    # 过滤已知关键词和停用词
    known = set(ROLE_KEYWORDS[role] + STOP_WORDS)
    new_kw = [c for c in candidates if c not in known]
    return new_kw[:5]
```

> **只从高质量（>0.8）交互中学习关键词**：低质量交互可能是 Agent 误判的结果，从误判中学习会污染路由关键词库。只从高质量交互中学习保证了关键词的可靠性。学到的新关键词以 `category="routing_keyword"` 保存，可在后续意图分析中查询增强。

---

## 17. 路由层 `routes/`

### 17.1 SSE 端点详解 `routes/chat.py`

```python
@router.post("/stream")
async def chat_stream_endpoint(req: ChatRequest, user = Depends(get_current_user)):
    async def generate():
        async for event in chat_stream(
            message=req.message,
            user_id=user["id"],
            role=req.role,
            conversation_id=req.conversation_id or str(uuid4()),
            product_ids=req.product_ids,
        ):
            yield event.to_sse()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证实时推送
        }
    )
```

**PipelineEvent.to_sse() 格式**：
```python
def to_sse(self) -> str:
    payload = json.dumps({"event": self.event, **self.data}, ensure_ascii=False)
    return f"data: {payload}\n\n"  # 标准 SSE 格式，空行分隔
```

> **`X-Accel-Buffering: no` header 是关键**：Nginx 默认会缓冲代理响应，导致 SSE 事件被攒批后一次性发出，失去实时性。这个 header 告诉 Nginx 不要缓冲，每个 `\n\n` 立即推送到客户端。

**后台事件轮询端点**：
```python
@router.get("/events")
async def get_background_events(user = Depends(get_current_user), since_id: int = 0):
    events = await db.execute(
        "SELECT * FROM background_events WHERE user_id=? AND id>? AND consumed=0 LIMIT 20",
        (user["id"], since_id)
    )
    await db.execute(
        "UPDATE background_events SET consumed=1 WHERE id IN (?)",
        tuple(e["id"] for e in events)
    )
    return {"events": events}
```

> **后台事件用轮询而非 SSE Push**：后台事件（质量分、信任更新、学习记录）在 SSE 主流关闭后产生，无法推送到已关闭的连接。轮询虽然不如 Push 实时，但实现简单，前端每 5 秒轮询一次即可。

### 17.2 认证中间件 `routes/auth.py`

```python
async def get_current_user(
    authorization: Optional[str] = Header(None),
    db = Depends(get_db)
) -> Dict:
    if not authorization:
        return {"id": 0, "role": "guest"}  # guest 模式
    token = authorization.replace("Bearer ", "")
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    user = await db.execute("SELECT * FROM users WHERE id=?", (payload["sub"],))
    return user
```

> **Guest 模式保留**：允许未登录用户使用基本功能，降低使用门槛。对于 B2B 工具来说，让团队成员不需要注册就能快速体验是重要的。

### 17.3 产品路由 `routes/products.py`

关键端点：
```
GET    /api/products              列表（支持 lifecycle_status 过滤）
POST   /api/products              创建（支持 description 字段）
GET    /api/products/{id}         详情（含 L1-L4 知识）
PUT    /api/products/{id}         更新
DELETE /api/products/{id}?pin=xx  PIN 验证后软删除到回收站

POST   /api/products/{id}/files/upload   上传文件（docs/images/videos）
GET    /api/products/{id}/files          文件列表
DELETE /api/products/{id}/files/{fid}    删除文件

POST   /api/products/{id}/knowledge      新增知识条目
GET    /api/products/{id}/knowledge      知识列表
DELETE /api/products/{id}/knowledge/{kid} 删除知识

POST   /api/products/{id}/pin            设置/更改 PIN
POST   /api/products/{id}/lifecycle/transition  生命周期转换
```

---

## 18. 产品管理系统

### 18.1 生命周期引擎 `core/product_lifecycle.py`

```
draft（选品中）→ listing（上架中）→ active（运营中）→ archived（归档）
```

**转换规则**（通过状态机实现）：
```python
VALID_TRANSITIONS = {
    "draft":   ["listing"],        # 选品完成才能上架
    "listing": ["active", "draft"],# 上架后可激活，也可退回选品
    "active":  ["archived"],       # 运营结束归档
    "archived":[]                  # 终态，不可转换
}
```

> **为何不允许从 archived 恢复**：归档商品通常是历史数据，允许恢复会增加运营复杂度。需要重新运营的商品应该创建新品，保持历史数据干净。

### 18.2 文件存储

```python
PRODUCTS_DIR = DATA_DIR / "products"

def get_product_file_path(product_id: int, file_type: str, filename: str) -> Path:
    subdir = {"doc": "docs", "image": "images", "video": "videos"}[file_type]
    path = PRODUCTS_DIR / str(product_id) / subdir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
```

> **文件按 product_id 分目录**：而不是按日期或上传顺序。按产品分目录让"删除产品时清理所有文件"非常简单（`shutil.rmtree(product_dir)`），按日期分目录则需要遍历所有文件匹配 product_id。

### 18.3 PIN 保护删除

```python
async def verify_and_delete(product_id: int, pin: str) -> Dict:
    lock = await db.fetchone(
        "SELECT * FROM product_pin_lock WHERE product_id=?", (product_id,)
    )

    # 检查是否被锁定
    if lock and lock["locked_until"] and datetime.now() < lock["locked_until"]:
        return {"error": "已被锁定，请30分钟后重试"}

    # 验证 PIN
    if not bcrypt.checkpw(pin.encode(), lock["delete_pin_hash"]):
        attempts = (lock["attempts"] or 0) + 1
        locked_until = datetime.now() + timedelta(minutes=30) if attempts >= 5 else None
        await db.execute(
            "UPDATE product_pin_lock SET attempts=?, locked_until=? WHERE product_id=?",
            (attempts, locked_until, product_id)
        )
        return {"error": f"PIN错误，还剩{5-attempts}次机会"}

    # 验证通过，软删除
    await db.execute("UPDATE products SET deleted_at=? WHERE id=?", (datetime.now(), product_id))
    return {"success": True}
```

> **5 次失败 + 30 分钟锁定**：参考手机解锁的安全标准。bcrypt 存储哈希而非明文 PIN。软删除到回收站而非硬删除，给误操作留 30 天恢复窗口。

---

## 19. 数据库设计 `database.py`（947 行）

### 19.1 45 张表分域说明

**用户域（6 张）**
```sql
users       (id, email, password_hash, active_agent, created_at)
sessions    (id, user_id, token, expires_at)
user_settings (id, user_id, preferences JSON)
```

**对话域（3 张）**
```sql
conversations   (id UUID, user_id, agent_role, created_at)
messages        (id, conversation_id, role, content TEXT, metadata JSON)
conversation_contexts (id, conversation_id, context_type, context_data)
```

**Agent 域（2 张）**
```sql
agents   (name PK, display_name, role, description, config JSON, enabled)
learnings (id, role, category, content, confidence FLOAT, source_action)
```

**技能域（3 张）**
```sql
skills       (name PK, display_name, category, description, input_schema JSON)
skill_runs   (id, skill_name, input JSON, output JSON, duration_ms, rating INT)
skill_feedback (id, run_id FK, rating INT, tags JSON, comment)
```

**质量域（4 张）**
```sql
quality_checks (id, conversation_id, role, score FLOAT, passed INT,
                dimensions JSON, issues JSON, suggestions JSON)
trust_scores   (id, role, score FLOAT, trust_level, created_at)
alerts         (id, alert_type, severity, message, role, dedup_key, created_at)
metrics        (id, metric_type, metric_key, metric_value FLOAT, recorded_at)
```

**产品域（7 张）**
```sql
products          (id, name, category, sku, cost_price, selling_price,
                   supplier, description, lifecycle_status, metadata JSON,
                   deleted_at, created_at)
product_materials (id, product_id FK, material_type, content TEXT, version INT)
product_assets    (id, product_id FK, original_name, stored_name,
                   file_type, file_size, description, created_at)
product_knowledge (id, product_id FK, content_type, content TEXT,
                   confidence FLOAT, source_type, created_at)
product_pin_lock  (id, product_id FK UNIQUE, delete_pin_hash,
                   attempts INT DEFAULT 0, locked_until)
campaigns         (id, name, product_id FK, budget FLOAT,
                   start_date, end_date, status, metadata JSON)
campaign_items    (id, campaign_id FK, item_id, quantity INT)
```

**工作区域（5 张）**
```sql
workspaces       (id, title, workspace_type, product_id FK, owner_id, status)
workspace_tasks  (id, workspace_id FK, title, status, owner_role, priority, due_date)
workspace_memory (id, workspace_id FK, role, key, content TEXT, updated_at)
projects         (id, title, owner_role, status, metadata JSON)
tasks            (id, project_id FK, title, priority, status, depends_on JSON)
```

**其他（3 张）**
```sql
background_events (id, user_id, conversation_id, event_type, data JSON,
                   consumed INT DEFAULT 0, created_at)
settings          (key TEXT PK, value TEXT)
role_shared_context (id, role, context_key, content TEXT, updated_at)
```

### 19.2 关键索引

```sql
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_trust_role ON trust_scores(role, created_at);
CREATE INDEX idx_learnings_role ON learnings(role, category);
CREATE INDEX idx_alerts_type ON alerts(alert_type, role, created_at);
CREATE INDEX idx_quality_role ON quality_checks(role, created_at);
CREATE INDEX idx_bg_events_user ON background_events(user_id, consumed, id);
```

> **背景事件表的复合索引（user_id, consumed, id）**：查询模式是"找 user_id=X 且 consumed=0 且 id>N 的记录"，复合索引覆盖了这三个过滤条件，避免全表扫描。

---

## 20. 数据模型 `models.py`（287 行）

所有请求/响应使用 Pydantic v2 自动校验：

```python
class ChatRequest(BaseModel):
    message: str
    role: Optional[str] = None
    conversation_id: Optional[str] = None
    product_id: Optional[int] = None
    product_ids: List[int] = Field(default_factory=list)
    workspace_id: Optional[int] = None

class ProductCreate(BaseModel):
    name: str
    category: str
    sku: Optional[str] = None
    cost_price: Optional[float] = None
    selling_price: Optional[float] = None
    supplier: Optional[str] = None
    description: Optional[str] = None
    lifecycle_status: str = "draft"
    metadata: Dict = Field(default_factory=dict)

class SetPinRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=8)

class DeleteProductRequest(BaseModel):
    pin: str

class KnowledgeCreate(BaseModel):
    content_type: str   # "feature" | "scenario" | "competitor" | "faq"
    content: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source_type: str = "manual"  # "manual" | "ai_extracted" | "file_summary"
```

> **PIN 长度限制 4–8 位**：4 位最小可用，8 位足够安全，超出这个范围的 PIN 使用体验差且无必要。通过 `Field(..., min_length=4)` 在请求校验层就拦截，不进入业务逻辑。

---

## 21. 配置中心 `config.py`（119 行）

### 21.1 所有配置项

```python
# === 路径 ===
PROJECT_ROOT  = Path(__file__).parent.parent
DATA_DIR      = PROJECT_ROOT / "data"
DB_PATH       = DATA_DIR / "v4.db"
UPLOAD_DIR    = DATA_DIR / "uploads"
PRODUCTS_DIR  = DATA_DIR / "products"
CONFIG_DIR    = PROJECT_ROOT / "config"

# === LLM ===
LLM_PROVIDER          = os.getenv("LLM_PROVIDER",    "openai_compat")
LLM_API_URL           = os.getenv("LLM_API_URL",     "https://api.openai.com/v1")
LLM_API_KEY           = os.getenv("LLM_API_KEY",     "")
LLM_MODEL             = os.getenv("LLM_MODEL",        "gpt-4o-mini")
LLM_TIMEOUT_SECONDS   = int(os.getenv("LLM_TIMEOUT", "30"))
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY","")
ANTHROPIC_MODEL       = os.getenv("ANTHROPIC_MODEL",  "claude-sonnet-4-20250514")
ENABLE_MULTI_MODEL    = int(os.getenv("ENABLE_MULTI_MODEL", "0"))

# === 功能开关（Core，始终开启）===
ENABLE_QUALITY_CHECK  = int(os.getenv("ENABLE_QUALITY_CHECK",  "1"))
ENABLE_TRUST_SCORING  = int(os.getenv("ENABLE_TRUST_SCORING",  "1"))
ENABLE_LEARNING       = int(os.getenv("ENABLE_LEARNING",        "1"))
ENABLE_ALERTS         = int(os.getenv("ENABLE_ALERTS",          "1"))
ENABLE_METRICS        = int(os.getenv("ENABLE_METRICS",         "1"))

# === 功能开关（Enhanced，默认开启）===
ENABLE_TOOL_USE         = int(os.getenv("ENABLE_TOOL_USE",         "1"))
ENABLE_KNOWLEDGE_RULES  = int(os.getenv("ENABLE_KNOWLEDGE_RULES",  "1"))
ENABLE_PERSONALITY      = int(os.getenv("ENABLE_PERSONALITY",      "1"))
ENABLE_HANDOFF          = int(os.getenv("ENABLE_HANDOFF",          "1"))
ENABLE_PROACTIVE        = int(os.getenv("ENABLE_PROACTIVE",        "1"))

# === 功能开关（Advanced，按需开启）===
ENABLE_WORKSPACE_ENGINE   = int(os.getenv("ENABLE_WORKSPACE_ENGINE",   "1"))
ENABLE_PRODUCT_LIFECYCLE  = int(os.getenv("ENABLE_PRODUCT_LIFECYCLE",  "1"))
ENABLE_AUTOPILOT          = int(os.getenv("ENABLE_AUTOPILOT",          "0"))  # 默认关
ENABLE_INTELLIGENCE       = int(os.getenv("ENABLE_INTELLIGENCE",       "1"))
ENABLE_EXTERNAL_DATA      = int(os.getenv("ENABLE_EXTERNAL_DATA",      "0"))  # 默认关

# === 运行时限制 ===
MAX_TOOL_ROUNDS         = int(os.getenv("MAX_TOOL_ROUNDS",        "3"))
MAX_HISTORY_MESSAGES    = int(os.getenv("MAX_HISTORY_MESSAGES",   "10"))
MAX_PROMPT_CHARS        = int(os.getenv("MAX_PROMPT_CHARS",       "2500"))
FEATURE_BUDGET_MAX      = int(os.getenv("FEATURE_BUDGET_MAX",     "3"))
UPLOAD_MAX_SIZE_MB      = int(os.getenv("UPLOAD_MAX_SIZE_MB",     "20"))
TRASH_EXPIRE_DAYS       = int(os.getenv("TRASH_EXPIRE_DAYS",      "30"))
```

> **所有配置用环境变量覆盖**：这是 12-Factor App 的标准做法。生产环境不改代码，只改 `.env` 文件或容器环境变量。整数配置统一用 `int(os.getenv(..., "默认值"))` 解析，避免字符串比较错误。

> **ENABLE_AUTOPILOT 和 ENABLE_EXTERNAL_DATA 默认关**：这两个功能会触发系统自动执行操作（自动驾驶）或访问外部服务，风险较高，需要用户主动开启，避免意外行为。

---

## 22. 前端架构 `client/`

### 22.1 Hash 路由 SPA

```javascript
// main.js — Hash 路由器
const ROUTES = {
    "chat":          () => import("./pages/chat.js"),
    "board":         () => import("./pages/board.js"),
    "product/:id":   () => import("./pages/product-detail.js"),
    "workspace/:id": () => import("./pages/product-workspace.js"),
    "campaigns":     () => import("./pages/campaign-view.js"),
    "knowledge":     () => import("./pages/knowledge-browser.js"),
    "briefing":      () => import("./pages/daily-briefing.js"),
    "history":       () => import("./pages/conversation-history.js"),
    "dashboard":     () => import("./pages/dashboard.js"),
    "agents":        () => import("./pages/agents-showcase.js"),
};

window.addEventListener("hashchange", router);
```

> **用 Hash 路由而非 History API**：Hash 路由不需要服务端配置，FastAPI 的 `static.py` Catch-all 只处理一条路由 `/`，所有 `#xxx` 路由都在前端处理。History API 需要服务端对所有路径返回 `index.html`，增加后端配置复杂度。

### 22.2 SSE 事件路由 `core/sse-event-router.js`

```javascript
// 11+ 种事件的统一处理器
class SSEEventRouter {
    route(event) {
        const handlers = {
            "status":           this.onStatus,
            "intent_analyzed":  this.onIntentAnalyzed,  // 更新侧边栏高亮
            "token":            this.onToken,            // 追加流式文本
            "tool_call":        this.onToolCall,         // 渲染 ToolCallCard
            "tool_result":      this.onToolResult,       // 更新 ToolCallCard
            "quality_check":    this.onQualityCheck,     // 渲染 QualityCard
            "trust_update":     this.onTrustUpdate,      // 更新 TrustBadge
            "learning_captured":this.onLearning,         // 渲染学习记录
            "alert_triggered":  this.onAlert,            // 弹出 AlertCard
            "collab_start":     this.onCollabStart,
            "collab_token":     this.onCollabToken,
            "done":             this.onDone,
            "error":            this.onError,
        };
        handlers[event.event]?.(event);
    }
}
```

> **独立的事件路由器而非 switch 语句散落各处**：SSE 事件类型多且每种事件对应不同 UI 更新，集中在路由器中处理让代码可维护性高。新增事件类型只需在路由器中添加一个 handler，不需要改动聊天核心逻辑。

### 22.3 IntelligencePanel `components/IntelligencePanel.js`（v2.0）

```javascript
// Dispatch Card 展示
renderDispatchCard(intentData) {
    return `
    <div class="dispatch-card">
        <div class="agent-name">${intentData.display_name}</div>
        <div class="business-reason">${intentData.reason}</div>
        <div class="workflow">
            ${intentData.workflow.map((step, i) => `
                <div class="workflow-step ${step.active ? 'active' : ''}">
                    <span class="step-num">${i+1}</span>
                    <span class="step-title">${step.title}</span>
                    <span class="step-rationale">${step.rationale}</span>
                </div>
            `).join('')}
        </div>
    </div>`;
}
```

**4 步电商工作流（32 个角色×动作组合）**：系统内置了 8 个角色 × 主要动作类型的工作流文案，`intent_analyzed` 事件携带 workflow 数组，前端直接渲染，不需要前端计算逻辑。

### 22.4 侧边栏 AI Team（仅展示，不选择）

```javascript
// agents.js
function updateAgentHighlight(activeRole) {
    document.querySelectorAll(".agent-item").forEach(el => {
        el.classList.remove("active", "pulse");
    });
    const activeEl = document.querySelector(`[data-role="${activeRole}"]`);
    if (activeEl) {
        activeEl.classList.add("active", "pulse");
        // 2秒后停止脉搏动画
        setTimeout(() => activeEl.classList.remove("pulse"), 2000);
    }
}
```

> **侧边栏不支持点击切换角色**：系统的核心价值是 auto-dispatch，允许用户手动切换会让用户"绕过"意图分析，变成只是换了个皮肤的普通聊天。强制 auto-dispatch 让用户信任系统的判断，也保证所有交互都经过意图分析形成学习数据。

### 22.5 API 客户端 `api.js`

```javascript
// SSE 请求
export async function streamChat(payload, onEvent, onDone, onError) {
    const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {"Content-Type": "application/json",
                  "Authorization": `Bearer ${getToken()}`},
        body: JSON.stringify(payload),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const {done, value} = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
            if (line.startsWith("data: ")) {
                const event = JSON.parse(line.slice(6));
                if (event.event === "done") onDone(event);
                else onEvent(event);
            }
        }
    }
}
```

> **用 `fetch` + ReadableStream 而非 `EventSource`**：`EventSource` 是标准 SSE 接口，但不支持 POST 请求（只支持 GET），而聊天请求需要在 body 携带消息内容。用 `fetch` 手动解析 SSE 格式是绕过这个限制的标准做法。

---

## 23. 测试体系 `tests/`（537 个测试，5257 行）

### 23.1 测试文件分布

| 文件 | 行数 | 覆盖范围 |
|-----|-----|--------|
| `test_core.py` | 937 | Intent分析、流水线、质量评分、信任度、记忆 |
| `test_skills.py` | 672 | 41个技能的输入/输出校验 |
| `test_services.py` | 1061 | 20个业务服务的逻辑 |
| `test_new_routes.py` | 934 | 17个API端点（含认证、错误处理）|
| `test_routes.py` | 697 | 旧路由向后兼容 |
| `test_multi_agent.py` | 484 | 多Agent协作调度逻辑 |
| `test_intelligence.py` | 472 | 智能分析、主动通知 |

### 23.2 测试策略

- **LLM 调用**：在测试中 mock `call_llm_stream`，返回预设的 token 序列，不依赖真实 LLM API（速度快，结果稳定）。
- **DB 操作**：使用内存 SQLite（`:memory:`），每个测试用例独立初始化，测试后自动销毁。
- **SSE 流**：通过 `httpx.AsyncClient` 的 `stream()` 方法测试 SSE 端点，验证事件序列。

```bash
# 运行全部测试
cd server && python -m uv run pytest tests/ -v

# 运行单个模块
python -m uv run pytest tests/test_core.py -v -k "intent"

# 带覆盖率
python -m uv run pytest tests/ --cov=src --cov-report=html
```

---

## 24. 启动与部署

### 24.1 开发环境

```bash
# 克隆后首次安装
cd server && uv sync           # 安装 Python 依赖
cd client && npm install       # 安装前端依赖

# 配置 LLM
cp .env.example .env
# 编辑 .env，填写 LLM_API_URL、LLM_API_KEY、LLM_MODEL

# 启动服务端（端口 8100）
cd server && python -m uv run python -m src.app

# 启动前端开发服务（端口 5173，带热重载）
cd client && npm run dev
```

### 24.2 生产部署

```bash
# 构建前端
cd client && npm run build
# 产出 client/dist/assets/（由 FastAPI static.py 挂载为 /assets）

# 启动服务端（服务端同时提供前端静态文件）
cd server && python -m uv run python -m src.app
# 访问 http://localhost:8100 即可
```

**app.py 中的静态文件挂载**：
```python
# static.py — Catch-all，必须最后注册
app.mount("/assets", StaticFiles(directory="client/dist/assets"))
app.mount("/product-files", StaticFiles(directory=str(PRODUCTS_DIR)))

@app.get("/{full_path:path}")
async def serve_spa():
    return FileResponse("client/dist/index.html")
```

> **为何单进程同时服务 API 和静态文件**：对于内部工具/小规模部署，单进程最简单，不需要配置 Nginx。如果需要扩展，只需在前面加 Nginx 或 Caddy 做反代和静态文件服务，后端 API 不需要改动。

### 24.3 环境变量完整列表

```bash
# LLM 接入（必填）
LLM_API_URL=https://api.openai.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini

# 可选：Anthropic 多模型
ANTHROPIC_API_KEY=sk-ant-xxx
ENABLE_MULTI_MODEL=0

# 可选：调整运行时参数
MAX_TOOL_ROUNDS=3
MAX_HISTORY_MESSAGES=10
MAX_PROMPT_CHARS=2500

# 可选：关闭某些功能
ENABLE_AUTOPILOT=0
ENABLE_EXTERNAL_DATA=0
```

---

## 附录：设计决策速查

| 决策 | 选择 | 原因 |
|-----|-----|-----|
| 意图分析 | 纯规则关键词 | 0 LLM 调用，<10ms，精度足够 |
| 流式推送 | SSE (fetch+ReadableStream) | POST 支持，比 WebSocket 简单 |
| 数据库 | SQLite WAL | 单机足够，无运维成本 |
| Prompt 上限 | 2500 字符 | 效果/成本平衡点 |
| 特性预算 | top-3 | 避免 Prompt 臃肿，亲和度优先 |
| 质量阈值 | 0.7 | 85% 通过率，15% 触发重试 |
| 信任 EMA | α=0.3 | 防止单次波动翻转等级 |
| 告警去重 | 进程内字典 | 高频检查，<0.1ms，可接受重启清空 |
| 学习去重 | Jaccard >0.6 | 无需分词，中文词汇效果好 |
| 关键词学习 | 仅质量 >0.8 | 防止低质量交互污染路由库 |
| 工具调用上限 | 3 轮 | 防循环，实测 1-2 轮足够 |
| 支持 Agent 上限 | 回复截断 300 字 | 节省 token，防止信息过载 |
| PIN 保护 | bcrypt + 5次锁定 | 手机解锁标准，工程简单 |
| 文件目录 | 按 product_id | 删除清理 O(1) |
| 历史消息 | 10 条 | 注意力/成本平衡，覆盖5轮对话 |
