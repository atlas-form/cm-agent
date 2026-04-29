# 单机强中心化多 Agent 智能体架构设计（冻结版本）

## 1. 抽象层级确定

### 1.1 智能电脑的定义

- 一台智能电脑 = 一个 **World**
- World 被抽象为一个“社会群体”
- 该社会是 **强中心化结构**
- 只有一个最终决策者

---

## 2. 结构总览

```
World（社会环境）
 ├── CommanderAgent   ← 唯一决策者
 ├── WorkerAgent A
 ├── WorkerAgent B
 ├── WorkerAgent C
```

---

## 3. World 的职责

World 不是生命体，不参与认知或决策。

World 仅负责：

- Agent 注册与生命周期管理
- 消息路由（内部）
- 提供运行环境
- 启动 Agent.run()

World 不负责：

- 决策
- 计划生成
- 任务执行
- 因果裁决

---

## 4. Agent 抽象

### 4.1 Agent 是 struct

- Agent 是“生命体”
- 具有内部运行循环
- 被消息驱动
- 自己维护内部状态

对外只暴露：

- `new()`
- `run()`

其余逻辑封闭在内部。

---

## 5. 强中心化模型

### 5.1 CommanderAgent

- 唯一决策者
- 负责宏观计划生成
- 负责对外通信
- 接收所有外部输入
- 接收所有 Worker 结果

它拥有完整认知能力。

---

### 5.2 WorkerAgent

- 只能与 Commander 通信
- 不彼此通信
- 不直接接触 World 或网络
- 执行具体任务
- 内部可多步推进 Action

Worker 不具备最终决策权。

---

## 6. 决策模式

采用：

> 强中心化 + 宏观控制模型

即：

- Commander 生成高层计划
- Worker 自主执行细节
- Worker 完成后汇报
- Commander 再做下一步决策

不是协商型社会。

---

## 7. 通信模型

### 7.1 内部通信

- Worker → Commander
- Commander → Worker

### 7.2 外部通信

- 仅 Commander 对外发送与接收
- Worker 不感知外部存在

---

## 8. Runtime 结构

### 8.1 没有全局 Runtime

- 不存在统一调度所有 Agent 的 Runtime
- World 不是 Runtime

### 8.2 每个 Agent 内部有 Runtime

Agent.run() 负责：

- 处理消息
- 推进当前 Action
- 在 Cognition 与 Action 之间循环
- 在无任务时进入等待状态

---

## 9. Action 模型

- Action 不是一次函数调用
- Action 是多步可推进的状态机
- Action 完成后可能再次触发 Cognition
- Agent 内部形成认知-行动闭环

---

## 10. 生命模型总结

- Agent 被消息驱动
- 无任务时静止等待
- 有任务必须执行
- 不主动生成目标（懒人模型）
- 只有 Commander 做最终决策

---

## 11. 层级关系

```
OS
 └── Tokio（执行环境）
       └── World（社会环境）
             ├── CommanderAgent
             └── WorkerAgent
```

- Tokio 提供执行能力
- World 提供存在环境
- Agent 内部 runtime 提供生命循环

---

## 12. 当前冻结结论

- 必须有 World
- 必须强中心化
- Commander 必须是 Agent
- Worker 只能通过 Commander 行动
- 每个 Agent 内部拥有自己的 runtime
- 不需要多机协作
- 不需要分布式一致性
- 不需要协商机制

架构已稳定。
