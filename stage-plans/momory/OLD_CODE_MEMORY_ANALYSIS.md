# Old Code Agent Memory Analysis

## 旧代码入口

旧实现主要分布在：

- `old_code/server/src/core/chat_pipeline.py`
- `old_code/server/src/core/agent_memory.py`
- `old_code/server/src/services/context_memory.py`
- `old_code/server/src/services/episode_memory.py`
- `old_code/server/src/database.py`

它不是一个独立 memory runtime，而是在 chat pipeline 里以“上下文加载 + 后台学习任务 + 数据表 helper”的方式拼起来。

## 记忆类型

### 1. 当前对话历史

`chat_pipeline.py` 通过 `_load_history(conversation_id, user_id, limit=...)` 读取最近消息，用于：

- 当前轮 intent 分析；
- `context_enricher.enrich_message` 做指代补全；
- 构造最终 LLM messages。

这类记忆本质是 conversation-local history，不是长期记忆。

### 2. 长期跨会话记忆

核心函数：

- `_extract_longterm_facts_from_turn`
- `_merge_longterm_facts`
- `_build_longterm_memory_prompt`
- `_load_longterm_memory_bundle`
- `_bg_longterm_memory`

写入路径：

1. 当前轮完成后进入后台任务 `_bg_longterm_memory`。
2. 从用户消息提取 `focus_platforms`、`goals`、`constraints`。
3. 从回复截取 `last_reply_summary`。
4. 与 `conversation_contexts.facts` 旧值合并。
5. `save_context(conv_id, summary, facts)` upsert 到 `conversation_contexts`。
6. 同时更新用户 profile 里的 `platforms` 和 `interests` 计数。
7. 写 `background_events` 记录 `longterm_memory_updated`。

读取路径：

1. 新请求进入上下文加载阶段。
2. `_load_longterm_memory_bundle(user_id, conversation_id)` 查询同用户最近其他 conversation 的 `conversation_contexts`，排除当前 conversation。
3. 聚合 goals、constraints、summary、focus_platforms，再叠加 profile persona/platforms。
4. 生成一段 prompt 文本 `【跨会话长期记忆】...`。
5. 注入到 `cross_role_context`，再进入 `build_system_prompt`。

旧实现的长期记忆是 prompt-first 的：存储结构只是为了拼接 prompt。

### 3. Agent 学习记忆

核心文件：`old_code/server/src/core/agent_memory.py`。

写入路径：

1. `_bg_learning` 在回答结束后触发。
2. `extract_learnings` 根据 quality score 分类：
   - `success`：质量 >= 0.75 且回复有结构；
   - `blindspot`：质量 < 0.5；
   - `improvement`：中等质量且有质量问题。
3. `_extract_key_phrases` 从回复里抓一句“看起来最有价值”的内容。
4. `save_learnings` 查同 role + category 最近 10 条，用字符级 Jaccard 相似度 > 0.6 去重。
5. 插入 `learnings(role, category, content, confidence, source_action)`。

这套机制是 deterministic extraction，没有 LLM 总结调用。

### 4. 路由关键词学习

核心函数：

- `extract_routing_keywords`
- `save_routing_keywords`

逻辑：

1. 只在 `quality_score >= 0.8` 时触发。
2. 从用户消息中用正则抓 2-4 字中文词组。
3. 排除 intent 里已有 ROLE_KEYWORDS 和停用词。
4. 存成 `learnings.category = routing_keyword`。
5. 后续 intent analyzer 可以读取这些词增强路由。

它实际上是 role router 的自适应词典，不应和长期用户事实混在同一个 Rust trait 里。

### 5. Episode 记忆

`episode_memory.py` 管理 `agent_episodes`：

- `save_episode(user_id, role, topic, summary, messages)`
- `get_episodes(user_id, role, limit)`

它把多条消息压成更高层的事件片段，但旧 pipeline 中不是主路径，更多像预留服务。

### 6. 跨角色共享上下文

`role_shared_context` 表按 `to_role` 拉取最近 7 天其他角色产出的内容，最多 5 条，拼进 `cross_role_context`。

这不是传统用户记忆，更接近 session/workspace 级 blackboard 的持久化版本。

## 旧实现优点

- 写入是异步后台任务，不阻塞主回复。
- 长期记忆有范围控制：最近 6 条 conversation context、prompt 最长约 1100 字。
- extraction 多数是 deterministic，成本低、行为可预测。
- 区分了 profile、conversation summary、role learning、routing keyword、episode。

## 旧实现问题

- 记忆逻辑散在 pipeline、service、database helper 里，没有统一 contract。
- 大量副作用藏在回答完成后的 `asyncio.gather`，失败只能日志告警，调用方无法观测。
- 读出来的是字符串 prompt，不是 typed memory bundle；后续 runtime 很难判断来源、scope、可信度和预算。
- 长期事实提取依赖中文关键词和字符串截断，适合旧电商场景，但不适合作为通用 Rust core。
- `learnings` 同时承载 agent 行为学习和 routing keyword，领域边界混乱。
- 去重、排序、过期策略都在局部函数里，无法被其他 runtime 复用。

## Rust 迁移结论

旧代码值得保留的是“分层语义”：

- turn/session snapshot；
- conversation summary + facts；
- user/workspace profile signals；
- role learning；
- routing hints；
- episode；
- cross-role context。

不应保留的是“Python pipeline 思维”：

- 在巨型 chat pipeline 中临时加载和拼 prompt；
- 每个 helper 自己操作数据库；
- 用裸 `dict` 和字符串作为跨模块协议；
- 把 memory 写入完全隐藏成后台副作用。

