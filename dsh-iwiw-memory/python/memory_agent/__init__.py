"""memory_agent — 独立的本地长期记忆系统。

核心：SQLite 标签记忆（profile/fact/lesson/rules/project × active/archived）+ 确定性检索 + 模型工具写入。

界面：
- CLI 工作台：python -m memory_agent.chat
- MCP 工具面：python -m memory_agent.mcp_server
"""
