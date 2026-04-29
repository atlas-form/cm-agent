# World 设计文档（阶段 1 冻结版）

---

## 1. 角色定位

World 是：

> 全局通信注册中心
> Agent 存在的结构环境
> 非生命体

World 不具备：

- Cognition
- Action
- Runtime
- 决策能力
- 状态认知能力

World 只负责：

> 保存 Agent 的通信端点
> 提供查询接口

---

## 2. 设计原则

- 强中心化
- 单机
- 多线程
- 无分布式
- 无复杂调度
- 无状态缓存
- 无结构膨胀

World 越简单越好。

---

## 3. 单例模型

World 是全局单例。

```rust
static WORLD: Lazy<World>;
```

整个系统只存在一个 World。

---

## 4. World 保存的信息

World 只保存：

> 每个 Agent 的消息发送端点（Sender）

### 结构定义

```rust
pub struct World {
    commander_tx: Sender<Message>,
    workers: HashMap<WorkerId, Sender<Message>>,
}
```

仅此而已。

World 不保存：

- Commander 实例
- Worker 实例
- Worker 状态
- Worker 能力
- Cognition
- Memory
- 运行状态

---

## 5. 职责划分

### World 负责

- 注册 Commander 通信端点
- 注册 Worker 通信端点
- 提供查询接口：
  - get_commander_tx()
  - get_worker_tx(worker_id)

### World 不负责

- 决策
- 状态同步
- 调度
- 生命周期管理
- 运行循环

---

## 6. 通信模型

### Commander → Worker

1. Commander 从 World 查询 Worker 的 Sender
2. 直接发送 Message

### Worker → Commander

1. Worker 从 World 查询 Commander 的 Sender
2. 直接发送 Message

World 不转发消息。
World 不解释消息。
World 不参与消息处理。

---

## 7. 并发模型

- 每个 Agent 在独立线程运行
- World 必须实现 Send + Sync
- 内部使用线程安全容器（如 Mutex / RwLock）
- Sender 本身是线程安全的

World 是跨线程共享的通信目录。

---

## 8. 状态模型

World 不保存 Worker 状态。

Worker 状态：

- 存在于 Worker 内部
- 通过消息主动上报给 Commander

Commander：

- 保存自己的认知视图
- 不从 World 查询状态

World 仅保存通信能力。

---

## 9. 架构层级

```text
OS
 └── Tokio
       └── World (static)
             ├── Commander (独立线程)
             └── Worker (独立线程)
```

World 是结构。
Commander 是大脑。
Worker 是执行体。

---

## 10. 阶段 1 冻结结论

World：

- 极简
- 单例
- 线程安全
- 只保存 Sender
- 不持有实例
- 不保存状态
- 不参与决策
- 不参与执行

World 是：

> 全局通信地址簿

设计完成。
