# IwIw 模型管理设计

## 定位

IwIw 的模型管理服务于本地个人智能体本身，而不是服务于 CLI、多工具代理或 API 中转。

参考 `cc-switch` 时，只吸收它在模型档案管理上的方法：

- 常用 provider 模板。
- 自定义 provider 档案。
- 当前启用 provider。
- 按用途区分默认模型：聊天、摘要、记忆整理。
- 标记 provider 是否支持流式事件。
- 连接测试、延迟展示和后续健康状态记录。
- 本地配置持久化，避免用户反复编辑 JSON。

## 明确不做

第一阶段不实现以下能力：

- 本地代理服务。
- 请求转发。
- 多 provider 自动路由。
- 失败后的自动切换上游。
- 面向 Claude Code、Codex、Gemini CLI 等外部工具的配置接管。
- 将 IwIw 伪装成任意 CLI 或 vibing code 工具。

这些能力属于跨工具流量管理，不是 IwIw 当前的产品边界。IwIw 的重点是：让用户选择一个可用模型，并让智能体 harness 用稳定、可追踪、可流式的方式调用它。

## Provider 档案

当前本地配置位于 `selfecho_config/providers.local.json`，由 `selfecho_config/model_profiles.py` 读取和保存。结构应保持简单：

```json
{
  "active_provider_id": "deepseek-main",
  "providers": [
    {
      "id": "deepseek-main",
      "label": "DeepSeek",
      "api_style": "anthropic",
      "base_url": "https://api.deepseek.com/anthropic",
      "api_key_env": "MEMORY_AGENT_LLM_API_KEY",
      "enabled": true,
      "streaming": true,
      "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
      "defaults": {
        "chat": "deepseek-v4-flash",
        "summary": "deepseek-v4-flash",
        "memory": "deepseek-v4-flash"
      }
    }
  ]
}
```

`api_key` 不应默认写入仓库。开源默认使用环境变量名，用户本机可在本地配置中保存自己的 provider 档案。

## Harness 接入

模型管理只向 harness 暴露“当前 provider 档案”和“指定用途的默认模型”：

- 聊天：`defaults.chat`
- 会话摘要：`defaults.summary`
- 记忆整理：`defaults.memory`

如果 provider 标记 `streaming: true`，聊天优先走流式事件；否则降级为普通完成请求。降级只改变交互体验，不改变智能体工作流。

## 后续推进

1. 为 provider 测试记录最近一次结果、延迟和错误摘要。
2. 在 GUI 中继续完善当前模型、流式能力和连接状态展示。
3. 在设置中提供 provider 档案导入、导出和一键恢复默认模板。
4. 在请求追踪中记录实际使用的 provider、model、耗时、估算 token 和是否流式。
