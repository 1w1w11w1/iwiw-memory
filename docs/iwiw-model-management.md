# IwIw 模型管理

模型管理服务于 IwIw 本地个人智能体本身。它只负责让 harness 获得稳定、可追踪、可流式的模型调用配置。

模型管理不是模型运行时。真实调用、流式回退、结构化输出、usage、latency、retry 和 cancellation 由 `selfecho_model` 中的 `ModelGateway` 承担，设计见 [智能体 Harness 架构](agent-harness-architecture.md)。

## 职责

- 管理常用 provider 模板。
- 管理本地自定义 provider 档案。
- 管理全局模型设置：按 IwIw 行为选择实际使用的 AI。
- 管理模型配置：新建、修改和停用可调用的 provider/model 档案。
- 标记 provider 是否支持流式事件。
- 提供连接测试、延迟展示和健康状态记录。
- 持久化本地配置，避免用户反复编辑 JSON。

## 不负责

- 本地代理服务。
- 请求转发。
- 多 provider 自动路由。
- 失败后自动切换上游。
- 外部工具配置接管。

这些能力属于跨工具流量管理，不是 IwIw 当前产品边界。

## 配置结构

本地配置位于 `selfecho_config/providers.local.json`，由模型配置模块读取和保存。结构分成两层：

- `role_defaults`：页面上半部分“模型设置”，描述不同 IwIw 行为使用哪个已保存模型。
- `providers`：页面下半部分“模型配置”，只描述服务连接、接口风格、环境变量和可用模型 ID。

```json
{
  "role_defaults": {
    "chat": {
      "provider_id": "deepseek-main",
      "model": "deepseek-v4-flash"
    },
    "summary": {
      "provider_id": "deepseek-main",
      "model": "deepseek-v4-flash"
    },
    "memory": {
      "provider_id": "deepseek-main",
      "model": "deepseek-v4-pro"
    }
  },
  "providers": [
    {
      "id": "deepseek-main",
      "label": "DeepSeek",
      "api_style": "anthropic",
      "base_url": "https://api.deepseek.com/anthropic",
      "api_key_env": "MEMORY_AGENT_LLM_API_KEY",
      "enabled": true,
      "streaming": true,
      "models": ["deepseek-v4-flash", "deepseek-v4-pro"]
    }
  ]
}
```

`api_key` 不应写入仓库。默认使用环境变量名，用户本机可保存自己的 provider 档案。

`providers[*]` 不保存行为默认值。停用的 provider 不进入全局模型设置的可选项，也不应继续被运行时选中。

## Harness 接入

模型管理向 harness 暴露两类信息：

- Provider/model 档案。
- 指定行为用途的全局模型设置。

用途：

- 对话回复模型：`role_defaults.chat`
- 会话整理模型：`role_defaults.summary`
- 记忆整理模型：`role_defaults.memory`

`api_style` 取值：`"anthropic"` | `"openai"`。控制 provider adapter 的请求体格式和 endpoint 拼接规则。

如果 provider 标记 `streaming: true`，聊天优先走流式事件；否则降级为普通完成请求。降级只改变交互体验，不改变 agent 工作流。

### Provider 错误处理

- 环境变量缺失：启动时不阻塞，首次调用时返回明确错误并提示缺失的变量名。
- Provider 不可达（超时/503）：`ModelGateway` 按 harness doc 的重试策略处理，模型管理本身不做重试。
- 健康检查：`ModelProviderAdapter.health_check()` 的定义见 [智能体 Harness 架构](agent-harness-architecture.md) 的 Provider Adapter 章节。

## 运行追踪

模型调用的 trace 字段（provider、model、耗时、token、流式标志、错误摘要）由 [智能体 Harness 架构](agent-harness-architecture.md) 的 `TraceRecorder` 统一管理。本文不重复定义。
