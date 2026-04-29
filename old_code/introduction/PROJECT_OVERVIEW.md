# 电商多Agent协同智能平台

> **一句话定位**：像雇佣了一支电商专职团队——运营、数据、客服、设计、财务、研发、SEO、创意——每条消息自动分配给最合适的专家，每次仅1次LLM调用，流式秒级响应。

---

## 一、产品定位

### 解决什么问题

电商团队日常面临的决策和执行问题高度专业化、跨职能：

| 用户痛点 | 传统方式 | 本平台 |
|---------|--------|--------|
| "直通车ROI下降怎么办？" | 问同事/查攻略/自己分析 | 运营Agent + 数据Agent协同，给出ROI拆解+优化策略 |
| "这款商品值得投吗？" | 人工选品会议 | 数据Agent RFM分层+市场情报+会计Agent成本核算 |
| "客诉量突增怎么处理？" | 客服经理介入 | 客服Agent给SOP，运营Agent跟进，告警自动触发 |
| "主图要改，要什么规格？" | 查平台规范 | 设计Agent直接给800×800px规范+5张策略+文案框架 |

**核心价值**：让1个人用自然语言驱动一支8人专业团队的执行力。

---

## 二、用户使用流程

```
1. 打开聊天页（默认 #chat）
2. 用自然语言描述问题
   ↓ 无需选择Agent，系统自动路由
3. 实时看到：
   - 侧边栏高亮哪个Agent被激活（脉搏动画）
   - IntelligencePanel显示：选中理由 + 4步工作流
   - 消息流式输出
   - 若调用了技能：ToolCallCard展示执行过程
   - 质量评分卡（9维）
4. 如需更深入：产品绑定（product_ids）→ Agent自动加载该商品的知识库
5. 后台自动：沉淀学习、更新信任度、触发告警、推送主动通知
```

### 典型场景案例

**案例1：大促备货决策**
```
用户: "双11备货方案，SKU-A历史销量3000件，利润率18%，仓储成本高"

→ Intent识别: primary=ops, support=[data, accounting], tier=MULTI
→ 运营Agent: 制定备货计划 + 渠道分配
→ 数据Agent: 趋势预测 + 退货率分析（tool: trend_forecast）
→ 会计Agent: 利润预测 + 资金占用测算（tool: profit_analysis）
→ 整合输出: 建议备货2200件 + 分阶段补货策略 + 资金流预测
```

**案例2：客诉危机处理**
```
用户: "今天收到50条差评，说快递破损"

→ Intent识别: primary=service, tier=SINGLE
→ 客服Agent: 激活SOP（共情→分类→方案→执行→跟踪）
→ 技能调用: customer_service + escalation_flow
→ 告警触发: alert_engine推送P1告警给运营
→ 输出: 标准话术模板 + 赔付方案 + 后续风控建议
```

**案例3：新品冷启动**
```
用户: "新品上架第3天，点击率0.8%，转化0.3%，该怎么调整？"

→ Intent识别: primary=ops, support=[data, design], tier=MULTI
→ 数据Agent: 漏斗分析（tool: funnel_analysis），定位断点在主图
→ 设计Agent: 主图优化建议（800×800px + 5张策略 + 色彩方案）
→ 运营Agent: 调价策略 + 直通车ROI优化
→ 整合: 3天优化路线图
```

---

## 三、多Agent协同架构

### 3.1 8个Agent角色

| Agent | 职责 | 核心技能 | 协作对象 |
|-------|-----|--------|--------|
| **ops** 运营执行 | 全局操盘、活动策划、选品、预算 | 促销策划/定价/库存/渠道/执行计划 | data, design, accounting |
| **data** 数据处理 | 趋势预测、异常诊断、用户分层 | 趋势预测/漏斗/RFM/AB测试/市场情报 | ops, accounting, service |
| **service** 客服 | 售前咨询、售后处理、舆情危机 | FAQ/话术/工单分级/投诉分析 | ops, data |
| **design** 媒体设计 | 视觉系统、转化型设计、规范制定 | 视觉规范/主图/详情页/色彩/品牌 | ops, creative, web |
| **accounting** 会计 | 成本核算、利润分析、合规审计 | 成本分析/利润预测/预算/合规 | data, ops |
| **engineering** 研发 | 架构设计、性能优化、安全加固 | 技术方案/架构/性能/安全/接口文档 | data, ops |
| **web** 网页维护 | SEO优化、搜索排名、内容策略 | SEO诊断/关键词/页面优化/内容日历 | data, design, creative |
| **creative** 创意 | 选题策划、视频脚本、品牌叙事 | 创意Brief/视频脚本/文案框架/选题 | ops, design, data |

每个Agent配置11个维度：身份原型、沟通风格、协作网络、成功指标、专业规则(50+条)、输出模板、子专业、工作流标准、学习记忆。

### 3.2 协同调度机制

```
TIER_SINGLE: 单Agent独立回答（简单问题）
TIER_MULTI:  主Agent + 支持Agent联合输出（复杂跨职能问题）
TIER_CHAIN:  技能链顺序执行（结构化流程问题）
```

**协作触发**：
- accounting发现成本异常 → 自动通知 ops/data
- data发现流量异常 → 自动通知 ops
- service高频投诉 → 自动升级 ops

**交接规则（handoff_triggers）**：每个Agent内置预定义的移交条件，触发时自动拉入对应角色。

---

## 四、内核智能：需求解析与调度

### 4.1 意图分析（0 LLM调用）

```python
# core/intent.py — 纯规则关键词匹配，延迟 <10ms
intent = IntentAnalyzer.analyze(message)
# 输出:
{
  "primary_role": "ops",
  "support_roles": ["data"],
  "action": "optimize",
  "platform": "taobao",
  "tier": "TIER_MULTI",
  "confidence": 0.95
}
```

25条知识规则（knowledge_rules.json）按角色+触发词动态注入专业知识（ROI公式、SOP流程、设计规范等），无需每次LLM重新学习。

### 4.2 功能预算（Feature Budget）

每次消息只注入 **top-3相关技能**，避免prompt膨胀和幻觉，兼顾用户偏好和历史成功率。

### 4.3 Prompt注入层次

```
系统提示 = Agent身份 + 子专业角色 + 认知风格 + 平台知识 + 知识规则
         ≤ 2500字符（精简但信息密度极高）
```

### 4.4 完整调度流水线 v4.1

```
用户消息
  ① Intent分析        — 0 LLM，规则路由
  ② Context富化       — 历史10条 + 产品知识L1/L2/L3
  ③ Feature Budget    — top-3技能选择
  ④ Prompt构建        — 身份+规则+个性注入
  ⑤ LLM流式调用       — 1次LLM，streaming
  ⑥ Tool使用循环      — 最多3轮 skill执行
  ⑦ 多Agent协作       — TIER_MULTI时自动编排
  ⑧ 质量检查          — 9维评分，不及格重试
  ⑨ 后台异步          — 学习/信任/告警/指标
```

### 4.5 质量保证

- **9维评分**：完整性、准确性、可执行性、专业性、合规性、创意性、个性化、及时性、可追踪性
- **4级信任度**：LOW→MODERATE→HIGH→EXPERT，持久化存储，影响future prompt权重
- **失败重试**：质量不达标 → 最多3次重试 + prompt微调

### 4.6 学习闭环

```
用户评分(1-5星) → skill_feedback → agent_memory沉淀
→ 追踪成功案例/盲点/改进方向 → 下次调用权重调整
```

---

## 五、技能系统（41个Skills）

### 5.1 技能分类

| 模块 | 技能数 | 代表技能 |
|-----|------|--------|
| ops.py | 7 | 促销策划、定价策略、渠道规划、库存计划、选品、文案、执行计划 |
| data_analysis.py | 6 | 趋势预测、异常诊断、漏斗分析、RFM分层、市场情报、AB测试 |
| customer_service.py | 5 | FAQ生成、话术模板、工单分级、投诉分析、满意度评估 |
| design.py | 6 | 视觉规范、主图文案、详情页结构、色彩方案、排版系统、品牌审计 |
| accounting.py | 5 | 成本分析、利润预测、预算编制、财务报告、合规检查 |
| engineering.py | 5 | 技术方案、架构设计、性能优化、安全审计、接口文档 |
| web.py | 5 | SEO诊断、关键词选取、页面优化、A/B测试、内容日历 |
| creative.py | 4 | 创意Brief、视频脚本、文案框架、选题策划 |
| coordination.py | 3 | 多Agent协调、项目进度、资源分配 |

### 5.2 技能执行框架

```python
class SkillBase:
    name: str           # "ops_promo_planning"
    display_name: str   # "促销策划"
    category: str       # "ops"
    input_schema: Dict  # JSON Schema
    async def execute(**kwargs) -> Dict  # 结构化结果
```

调用链：LLM决策调用skill → Registry路由 → 执行返回 → 结果注回LLM → 继续生成。

---

## 六、全功能技术文档

### 6.1 API端点（17个路由模块）

| 路由前缀 | 核心端点 | 功能 |
|---------|--------|------|
| `/api/auth` | POST /register, /login, /logout | JWT认证，支持guest模式 |
| `/api/chat` | POST /stream, GET /events | SSE流式聊天，后台事件轮询 |
| `/api/agents` | GET /, POST /activate | Agent列表与激活 |
| `/api/products` | CRUD + /files/upload + /knowledge + /pin | 产品全生命周期管理 |
| `/api/workspaces` | CRUD + /tasks + /memory | 工作区与任务编排 |
| `/api/skills` | GET /, POST /run, POST /feedback | 技能执行与反馈 |
| `/api/campaigns` | CRUD + /items | 营销活动管理 |
| `/api/intelligence` | GET /insights, POST /autopilot | 智能分析与自动驾驶 |
| `/api/briefing` | GET /daily, POST /generate | 日报生成 |
| `/api/assets` | CRUD（文件上传） | 产品素材管理 |
| `/api/trash` | GET /, DELETE /purge | 回收站（30天软删除） |
| `/api/user` | GET /me, PUT /profile | 用户设置 |
| `/api/health` | GET / | 健康检查（db+llm） |
| `/api/admin` | GET /overview, POST /seed | 管理后台 |
| `/api/kernel` | GET /stats, GET /events | 系统监控 |
| `/api/external` | GET /fetch | 外部数据接入（可选） |
| `/` | GET /* | 静态资源 Catch-all |

### 6.2 SSE事件流（/api/chat/stream）

```
请求: POST /api/chat/stream
{
  "message": "直通车ROI怎么优化？",
  "role": null,           // 不传 → 自动路由
  "conversation_id": "uuid",
  "product_ids": [1, 2]  // 可选，绑定产品知识
}

事件序列:
  status          → 处理阶段（intent_analyzing / tool_calling / ...）
  intent_analyzed → {primary_role, support_roles, action, platform}
  token           → {text: "..."} 流式字符
  tool_call       → {tool_name, tool_input} 技能调用展示
  tool_result     → 技能执行结果
  quality_check   → 9维评分
  trust_update    → 信任度变化
  learning_captured → 学习记录
  alert_triggered → 告警触发
  done            → {reply, role, metadata} 完整回复
  error           → 错误信息
```

### 6.3 数据库（45张表，SQLite WAL模式）

```
用户域(6): users, sessions, user_settings, ...
对话域(3): conversations, messages, conversation_contexts
Agent域(2): agents, learnings
技能域(3): skills, skill_runs, skill_feedback
质量域(4): quality_checks, trust_scores, alerts, metrics
产品域(7): products, product_materials, product_assets,
           product_knowledge, product_pin_lock, campaigns, campaign_items
工作域(5): workspaces, workspace_tasks, workspace_memory, projects, tasks
其他(3):   background_events, settings, role_shared_context
```

关键索引：messages(conversation_id, created_at)、trust_scores(role)、learnings(role)

### 6.4 产品管理系统

**生命周期**：`draft（选品）→ listing（上架）→ active（运营）→ archived（归档）`

**三层知识体系**：
- L1 基础信息（名称/品类/供应商）
- L2 结构化知识（product_knowledge表，置信度标注）
- L3 文件总结（上传的文档/图片/视频自动摘要）

**文件管理**：
```
data/products/{id}/docs|images|videos/
product_assets表记录元数据（original_name, description, file_type）
```

**PIN保护删除**：5次错误尝试 → 30分钟锁定，通过`product_pin_lock`表管理。

### 6.5 前端架构（Vite + Vanilla JS，深色主题SPA）

**页面路由（Hash-based）**：

| 路由 | 页面 | 功能 |
|-----|-----|-----|
| `#chat` | 聊天主页 | SSE对话 + IntelligencePanel |
| `#board` | 全局看板 | KPI卡片 + 图表 |
| `#product/:id` | 产品详情 | Files/Knowledge标签 + PIN删除弹窗 |
| `#workspace/:id` | 产品工作区 | 任务管理 + 内存树 |
| `#campaigns` | 营销活动 | 活动表 + 进度跟踪 |
| `#knowledge` | 知识库 | 知识树 + 搜索 |
| `#briefing` | 日报仪表板 | 指标卡 + 趋势图 |
| `#history` | 对话历史 | 会话列表 + 搜索 |
| `#dashboard` | KPI仪表板 | 实时指标 |
| `#agents` | Agent展示 | 8个Agent卡片 + 技能矩阵 |

**核心组件**：

```
IntelligencePanel.js (v2.0)  — Dispatch卡片：选中Agent + 业务理由 + 4步工作流可视化
sse-event-router.js          — 路由11+事件类型到对应UI更新
MessageBubble.js             — 消息气泡（streaming友好）
ToolCallCard.js              — 技能调用展示
QualityCard.js               — 9维质量评分卡
TrustBadge.js                — 信任度徽章
AlertCard.js                 — 告警弹窗
ChartRenderer.js             — 图表渲染
CommandPalette.js            — 快捷命令面板
```

**侧边栏 AI Team**：仅展示当前激活状态（脉搏动画），不支持手动切换 — 强制auto-dispatch。

### 6.6 智能核心模块（core/，20个）

| 模块 | 功能 |
|-----|-----|
| intent.py | 纯规则意图分析，0 LLM调用 |
| chat_pipeline.py | v4.1调度编排器 |
| feature_budget.py | top-3技能预算选择 |
| prompt_builder.py | 多层Prompt构建（≤2500字符） |
| quality_checker.py | 9维质量评分 + 重试 |
| trust_scorer.py | 4级信任度持久化 |
| multi_agent.py | 多Agent协作自动编排 |
| agent_memory.py | 学习沉淀 + 自适应关键词 |
| alert_engine.py | 6类告警（去重+冷却） |
| proactive_engine.py | 6类主动通知（DB驱动） |
| context_enricher.py | 指称语言检测 + 历史富化 |
| prompt_injectors.py | 行为注入/子专业/认知风格/平台知识 |
| causal_reasoning.py | 6类异常 × 结构化假设验证链 |
| product_lifecycle.py | 4阶段生命周期引擎 |
| workspace_engine.py | 工作区任务文件引擎 |
| briefing_engine.py | 日报自动生成 |

### 6.7 功能开关（15个，config.py）

```python
# Core层（始终开启）
ENABLE_QUALITY_CHECK, ENABLE_TRUST_SCORING, ENABLE_LEARNING
ENABLE_ALERTS, ENABLE_METRICS

# Enhanced层（默认开启）
ENABLE_TOOL_USE, ENABLE_KNOWLEDGE_RULES, ENABLE_PERSONALITY
ENABLE_HANDOFF, ENABLE_PROACTIVE

# Advanced层（可选）
ENABLE_WORKSPACE_ENGINE, ENABLE_PRODUCT_LIFECYCLE
ENABLE_AUTOPILOT, ENABLE_INTELLIGENCE, ENABLE_EXTERNAL_DATA
```

### 6.8 运行时参数

| 参数 | 默认值 |
|-----|------|
| MAX_TOOL_ROUNDS | 3 |
| MAX_HISTORY_MESSAGES | 10 |
| MAX_PROMPT_CHARS | 2500 |
| FEATURE_BUDGET_MAX | 3 |
| LLM_TIMEOUT_SECONDS | 30 |
| Rate Limit /api/chat | 20 req/60s |

---

## 七、项目文件结构

```
雇佣多职能agent适应多系统/
├── server/
│   ├── src/
│   │   ├── app.py              # FastAPI入口，挂载17个路由
│   │   ├── config.py           # 15功能开关 + LLM配置
│   │   ├── database.py         # 45张表初始化，WAL模式
│   │   ├── models.py           # Pydantic模型
│   │   ├── llm_client.py       # OpenAI兼容流式客户端
│   │   ├── core/               # 20个智能核心模块
│   │   ├── routes/             # 17个API路由
│   │   ├── skills/             # 9个技能模块（41+技能）
│   │   ├── services/           # 20个业务服务
│   │   └── config/
│   │       ├── role_presets.json      # 8个Agent完整配置(v2.1)
│   │       ├── knowledge_rules.json   # 25条知识规则
│   │       └── skill_chains.json      # 技能链配置
│   ├── tests/                  # 5257行，537个测试全通过
│   ├── data/
│   │   ├── v4.db               # SQLite数据库
│   │   └── products/{id}/docs|images|videos/
│   └── pyproject.toml          # uv管理，Python 3.11
│
├── client/
│   ├── src/
│   │   ├── main.js             # 入口 + Hash路由
│   │   ├── api.js              # API客户端
│   │   ├── core/               # sse-event-router, event-bus
│   │   ├── components/         # 9个UI组件
│   │   └── pages/              # 10个页面模块
│   ├── index.html
│   ├── vite.config.js
│   └── dist/assets/            # 构建产物（服务端挂载）
│
└── PROJECT.md                  # 本文档
```

---

## 八、启动与测试

```bash
# 服务端（端口8100）
cd server
python -m uv run python -m src.app

# 运行测试（537个）
python -m uv run pytest tests/ -v

# 客户端开发（端口5173）
cd client && npm run dev

# 客户端生产构建
cd client && npm run build
# 服务端自动读取 client/dist/assets/
```

---

## 九、技术栈汇总

| 层 | 技术 |
|---|-----|
| 后端框架 | FastAPI + aiosqlite（异步SQLite） |
| 包管理 | uv（Python 3.11） |
| LLM接入 | OpenAI兼容API，当前模型 `gpt-oss:120b` |
| 流式协议 | Server-Sent Events（SSE） |
| 前端 | Vite + Vanilla JS + 原生CSS（深色主题） |
| 数据库 | SQLite（WAL模式，45张表） |
| 测试 | pytest，537个用例，5257行测试代码 |
| 部署 | 单进程，client/dist作为静态资源由FastAPI直接服务 |
