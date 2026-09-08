# 换装验收清单（cutover 后逐项检查）

## 启动
- [ ] DSH 启动日志出现 `[dsh-iwiw-memory] applied: 4 tools + 2 prompt sections + pre-step hook`
- [ ] 无 `memory backend warmup failed` / `hit injection failed` 告警
- [ ] meow-memory 的启动日志**不再出现**（确认已卸载）

## 记忆注入
- [ ] 会话 system prompt 有「IwIw 长期记忆（必读）」段（core 记忆全文）——迁移后的 rules/user 层记忆应在
- [ ] 提及已迁移事实（如哮喘/花生/工作偏好）时，助手自然引用（core 段生效）
- [ ] 某话题相关提问出现「## 相关记忆（命中）」快照（每消息命中注入）
- [ ] 同会话重复同主题提问**不再**重复注入（去重）
- [ ] 长对话压缩后（如触发 compaction），下一轮出现「压缩后补回」快照

## 工具
- [ ] 工具列表有 memory_remember / memory_search / memory_read / memory_list
- [ ] 说一句个人事实 → 模型自主调 memory_remember → /stats 或下轮可检索
- [ ] memory_remember 返回的 related 含近似条目（查重提示）

## GUI（client 层）
- [ ] 注入的快照消息在会话流中折叠显示（横条 + 点击展开）
- [ ] 输入区 dock 出现（MemoryFoldDock）

## 记忆库
- [ ] `data/memory.db` 有 meow 迁移条目（slug 前缀 meow-*，共 42 条）
- [ ] mutation 留痕：`memory_history('meow-...')` 可查

## 回滚
出现问题时：`powershell -File scripts\cutover-iwiw.ps1 -Rollback` → 重启 DSH → 恢复 meow
