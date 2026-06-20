# Phase 6：WebSocket 统一通道设计

> **状态**：设计阶段
> **最后更新**：2026-06-20

---

## 目标

建立 JSON-RPC 2.0 over WebSocket 作为 IwIw 的主要控制通道，统一当前分散的通信方式。

## 现状问题

| 通道 | 用途 | 问题 |
|------|------|------|
| SSE (`/api/chat/stream`) | Chat 流式回复 | 单向、无请求-应答模型 |
| REST (`/api/memories/*`) | 记忆 CRUD | 无推送能力，前端需轮询 |
| REST (`/api/memory-pending/*`) | 淘汰确认 | 无实时通知，用户不知道有待确认 |
| REST (`/api/agent/*`) | Agent 运行状态 | 无运行进度推送 |

**核心矛盾**：前端需要知道"后台正在做什么"（agent 运行进度、记忆整理状态、待确认动作），但当前只有请求-响应模式，没有服务器推送控制通道。

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 协议 | **JSON-RPC 2.0** | 标准化、轻量、支持请求-应答和通知（双向） |
| 传输 | **WebSocket** | 全双工、低延迟、浏览器原生支持 |
| 路由 | **方法名命名空间** | 如 `memory.list`、`agent.run`、`chat.stream` |
| 认证 | **Origin 校验 + 本地 token** | 本地服务仍可能被浏览器页面访问，至少需要限制来源和会话凭证 |
| 心跳 | **30s 间隔 Ping/Pong** | 检测连接健康，自动重连 |
| 并行 | **单连接多路复用** | 一条 WS 连接处理所有 RPC 调用，request ID 关联应答 |

## 架构

```
  Vue 前端                   FastAPI 后端
 ┌──────────┐              ┌──────────────────┐
 │ WSManager│────WS───────▶│ selfecho_api/ws.py│
 │ (api.ts) │              │  ┌──────────────┐ │
 │          │              │  │ 连接注册表    │ │
 │ JSON-RPC │              │  │ 消息路由      │ │
 │ 2.0      │◀────WS───────│  │ 心跳检测      │ │
 └──────────┘              │  │ 事件广播      │ │
                           │  └──────────────┘ │
                           │       │           │
                           │       ▼           │
                           │ ┌──────────────┐  │
                           │ │  handler 注册 │  │
                           │ │ chat.*        │  │
                           │ │ memory.*      │  │
                           │ │ agent.*       │  │
                           │ │ system.*      │  │
                           │ └──────────────┘  │
                           └──────────────────┘
```

## 模块设计

### 1. `selfecho_api/ws.py`（后端 WS 传输层）

```python
class WSManager:
    """
    WebSocket 连接管理器。该模块属于 API/transport 层，不属于 memory_agent。

    职责：
    - 接受/关闭 WS 连接
    - 校验 Origin 和本地 token
    - 维护在线连接注册表（connection_id → WebSocket）
    - JSON-RPC 2.0 消息解析与路由
    - 心跳检测（30s 间隔）
    - 事件广播（如 pending action 通知）
    """

    async def handle_ws(self, websocket: WebSocket): ...
    async def dispatch(self, method: str, params: dict) -> dict: ...
    async def broadcast(self, event: str, data: dict): ...
    async def send_notification(self, connection_id: str, method: str, params: dict): ...
```

业务 handler 放在 `selfecho_api/ws_handlers.py` 或同级 transport 模块中，调用 `memory_agent.db`、`selfecho_session.service`、`selfecho_agent` 等现有能力。`memory_agent` 只提供记忆领域 API，不依赖 FastAPI/WebSocket。

### 2. JSON-RPC 2.0 协议

**请求格式**：
```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "memory.list",
    "params": {"priority": "important"}
}
```

**成功响应**：
```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"memories": [...]}
}
```

**错误响应**：
```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "error": {"code": -32000, "message": "Memory not found", "data": {}}
}
```

**通知（无需应答）**：
```json
{
    "jsonrpc": "2.0",
    "method": "memory.pending_notification",
    "params": {"count": 3}
}
```

### 3. 方法命名空间

| 命名空间 | 方法 | 说明 |
|---------|------|------|
| `chat.stream` | `chat.stream.start` | 启动 SSE 流式回复（替代当前 REST 端点） |
| `chat.stream` | `chat.stream.cancel` | 取消正在进行的流式回复 |
| `memory.list` | — | 列出记忆 |
| `memory.get` | — | 获取单条记忆 |
| `memory.search` | — | 混合搜索 |
| `memory.edit` | — | 编辑记忆 |
| `memory.archive` | — | 归档记忆 |
| `memory.delete` | — | 删除记忆 |
| `agent.run` | `agent.run.start` | 启动 agent 运行 |
| `agent.run` | `agent.run.cancel` | 取消 agent 运行 |
| `agent.run` | `agent.run.status` | 查询运行状态 |
| `agent.run` | `agent.run.progress` | **服务器推送**：运行进度 |
| `pending.list` | — | 列出待确认动作 |
| `pending.approve` | — | 批准动作 |
| `pending.reject` | — | 拒绝动作 |
| `pending.notify` | — | **服务器推送**：新待确认项 |
| `system.heartbeat` | — | 心跳 Ping/Pong |
| `system.stats` | — | 系统统计信息 |

### 4. 事件推送（服务器→客户端）

服务器主动推送的事件（无需客户端请求）：

| 事件 | 触发条件 | 频率 |
|------|---------|------|
| `agent.run.progress` | Agent 执行步骤变化 | 每步 |
| `memory.pending_notification` | 新的待确认淘汰动作 | 即时 |
| `memory.updated` | 记忆被修改（其他客户端） | 即时 |
| `system.consolidate.done` | 记忆整理完成 | 每次 consolidation |

### 5. 前端 WSManager（`web/src/api.ts` 或独立文件）

```typescript
interface WSMessage {
    jsonrpc: '2.0';
    id?: number;
    method?: string;
    params?: Record<string, any>;
    result?: any;
    error?: { code: number; message: string; data?: any };
}

class WSManager {
    private ws: WebSocket | null = null;
    private pending = new Map<number, { resolve, reject }>();
    private messageId = 0;
    private reconnectTimer: number | null = null;

    async connect(url: string): Promise<void>;
    async call(method: string, params: object): Promise<any>;
    on(method: string, handler: (params: any) => void): void;
    private reconnect(): void;
    private handleMessage(data: WSMessage): void;
}
```

### 6. 迁移路径

**过渡期（两条通道并行）**：
- REST 端点和 SSE 流继续工作
- WS 通道逐步接管控制类操作
- 前端先接入 wsManager，与现有 REST 调用共存

**最终状态**：
- WS 通道处理所有控制通信（chat stream、agent run、memory CRUD、pending actions）
- REST 仅保留纯数据 CRUD（session CRUD、project CRUD）和文件操作
- SSE 流关闭，由 WS 通道承载

## 实施步骤

### Step 1：后端 WSManager 骨架（~2h）

- 创建 `selfecho_api/ws.py`
- 增加 Origin allowlist 和本地 token 校验
- JSON-RPC 2.0 消息解析与路由
- 连接管理 + 心跳检测
- 在 FastAPI 中注册 `/ws` 端点

### Step 2：基础方法实现（~1.5h）

- `system.heartbeat`、`system.stats`
- `memory.list`、`memory.get`、`memory.search`
- 这些方法直接委托现有 `db.py` 函数

### Step 3：事件推送机制（~1h）

- `broadcast()` 方法实现
- `memory.pending_notification` 推送
- 在 `consolidate()` 完成后触发推送

### Step 4：前端 WSManager（~1.5h）

- 实现 `web/src/api.ts` 中的 WSManager 类
- 连接管理、重连、事件分发
- 与现有 REST 调用共存

### Step 5：Agent 运行进度推送（~1h）

- agent.run.progress 推送
- 前端进度显示

### Step 6：chat stream 迁移（~2h）

- 将 SSE `/api/chat/stream` 逻辑在 WS 通道上实现
- 前端 StreamManager 适配 WS 流

---

## 未纳入本次设计的

- **多房间/多会话**：WS 连接与会话绑定，不是全局广播。
- **远程认证体系**：本阶段只做本地 token + Origin 校验。如果未来支持远程访问，需要单独设计账号、权限和审计边界。
- **二进制帧**：纯 JSON 文本帧，不传二进制数据。

## 参考

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- FastAPI WebSocket 文档
- Vue 3 WebSocket 最佳实践
