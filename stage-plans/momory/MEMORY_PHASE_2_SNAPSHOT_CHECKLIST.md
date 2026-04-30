# Memory Phase 2 Checklist

阶段入口：

- `MEMORY_PHASE_2_SNAPSHOT_PLAN.md`

## 当前阶段

```text
Memory Phase 2: File Store + Snapshot Enrichment
```

## 状态总览

```text
status: in_progress
started: yes
completed: no
```

## Checklist

### 1. Plan Hygiene

- [x] 删除已完成的 memory runtime / compaction 旧计划入口
- [x] 新建 Phase 2 plan
- [x] 新建 Phase 2 checklist
- [x] 将当前重点调整为 file-backed memory 和未来 object storage 边界

### 2. File Memory Store

- [x] 新增 `FileMemoryStore`
- [x] 文件不存在时加载为空 store
- [x] 文件损坏时 open 返回错误
- [x] JSON envelope 包含 version 和 records
- [x] 写入使用临时文件 + rename
- [x] `load_bundle` 复用现有查询语义
- [x] `persist_session` 复用现有 snapshot 持久化语义
- [x] `upsert_record` 复用现有 compaction 语义
- [x] 更新 public API export

### 3. File Store Tests

- [x] 文件 store 能写入并重载 records
- [x] 文件 store 保存前仍会压缩长 record
- [x] 文件 store 能持久化 session snapshot
- [x] 文件损坏时返回 open error

### 4. Object Storage Readiness

- [x] durable envelope 不绑定本地文件语义
- [x] plan 记录未来 S3/object key 方向
- [x] 当前代码不提前引入 S3 SDK

### 5. Session Result Model

- [ ] 盘点现有 task graph / worker report 可复用字段
- [ ] 扩展 `SessionResult` 结构化字段
- [ ] 保持普通 final output 路径兼容
- [ ] 添加 `SessionResult` 结构化字段测试

### 6. Session Snapshot Model

- [ ] 扩展 `SessionSnapshot`
- [ ] 增加 role summary / risks / open questions / evaluation summary 字段
- [ ] 确认 snapshot 不包含 prompt / permission / runtime config
- [ ] 添加 snapshot model 测试

### 7. Runtime Capture

- [ ] 从 task graph runtime 捕获 role contribution summary
- [ ] 捕获 risks / open questions
- [ ] 捕获 evaluation pass/fail summary
- [ ] 普通非 task graph session 保持可保存

### 8. Persist Mapping

- [ ] final output 映射为 `SessionSummary`
- [ ] blackboard 映射为 `ConversationFact`
- [ ] role summaries 映射为 `CrossRoleContext` 或 `WorkspaceFact`
- [ ] risks / open questions 映射为带 tag 的 `ConversationFact`
- [ ] evaluation summary 映射为 keyed `ConversationFact`
- [ ] 所有 records 继续走 `upsert_record` 和 compaction

### 9. Safety Rules

- [ ] 不保存 system prompt
- [ ] 不保存 developer instructions
- [ ] 不保存 role prompt
- [ ] 不保存 permission / sandbox policy
- [ ] 不保存 runtime config
- [ ] 增加防泄漏测试

### 10. Verification

- [x] `rtk cargo test --test memory`
- [x] `rtk cargo test`
- [x] `rtk cargo check --bins`

## Notes

- 当前 durable backend 先用文件。
- 未来 S3 应复用 durable JSON envelope，而不是重写 memory model。
- 本阶段不做 LLM summarization。
- 本阶段不做 role learning / routing hint extraction。
