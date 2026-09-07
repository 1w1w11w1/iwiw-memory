# Codex 上下文压缩机制梳理

> 来源：openai/codex 仓库 RFC/Issues（一手）+ 社区 strategic-compact skill + 工程分析文章。
>
> 一句话：**Codex 的压缩不是"叙事摘要"，而是"确定性状态检查点"（Deterministic Session Checkpoint, DSC）—— 不追求让模型"记得发生了什么"，而是让模型"拥有继续工作所需的全部状态"。** 这正是它体感好的核心。

## 0. 核心哲学

传统压缩（Claude/多数 harness）= LLM 生成一段摘要，丢失细节（lossy）。
Codex 的取向 = **压缩是"换上下文"，不是"讲故事"**：

- 把会话事件日志（rollout JSONL）确定性投影成结构化检查点；
- 压缩 = 重置上下文 + 注入检查点的渲染视图；
- **检查点生成不需要 LLM**（确定性、可测试、省 token）。

## 1. 压缩的两条触发路径

| 路径 | 触发 | 特点 |
|---|---|---|
| 手动 /compact | 用户在逻辑边界主动触发 | 社区强烈推荐（strategic-compact skill） |
| 自动压缩 | token 接近上限（body_after_prefix token limit） | 任意点触发，可能打断任务 |

## 2. 核心：Deterministic Session Checkpoint（DSC）

### 数据模型（checkpoint_v1.json）

| 类型 | 作用 | 为什么重要 |
|---|---|---|
| Artifact | 已读文件 / 工具输出，带 hash + lastObservedSeq | 模型知道"哪些文件读过、哪些输出看过"，压缩后**不重读** |
| FactRecord | 已建立事实 + evidence 引用 + status(VALID/SUSPECT) | 事实可追溯；**文件 hash 变化 → 依赖它的事实自动标 SUSPECT**（防静默出错） |
| DecisionRecord | 已做决策 + rationale + supersedes | 压缩后**不重复决策**、知道"某个方案已被否" |

### 压缩流程

```
rollout.jsonl（结构化事件日志，已存在）
   │  reduce()（确定性投影，无 LLM）
   ▼
checkpoint_v1.json（单一事实源）
   │  view_v1(checkpoint, caps)（稳定文本渲染）
   ▼
重置上下文 + 注入 "SESSION_CHECKPOINT v1" 消息
```

## 3. 配套机制

### 3.1 尾部保留（compaction tail）
- 压缩时保留最近 N 条**完整**执行项（工具调用 + 输出 + 测试结果），不只用户消息；
- 解决经典死循环：压缩 → 忘记测试跑过 → 重跑 → 再压缩。

### 3.2 状态外部化（context offload）—— 压缩敢放心的前提
- **CODEX.md**（指令）：压缩后仍在；
- **TodoWrite 任务列表**：压缩的"锚"，计划落盘后可以放心压缩；
- **memory 文件**（~/.codex/memory/）：长期记忆落盘；
- git 状态 / 磁盘文件：天然不占上下文。

### 3.3 Token 预算纪律
- 工具输出有预算（避免 oversized tool output 吞掉上下文/配额）；
- reasoning summary（推理摘要）控制 verbose；
- 前缀缓存（cache positioning）降低重复成本。

## 4. 社区最佳实践（strategic-compact）

| 时机 | 压缩？ | 原因 |
|---|---|---|
| 探索完 → 规划前 | 是 | 探索上下文笨重，规划已提炼进 TodoWrite |
| 里程碑完成 | 是 | 新阶段干净起点 |
| 失败方法后 | 是 | 清掉死胡同推理 |
| 实现中途 | **否** | 变量名/路径/部分状态丢失代价高 |
| 调试 → 新功能 | 是 | 调试痕迹污染无关任务 |

> /compact 可带摘要指令（"focus on X next"），给压缩后的模型一个方向锚。

## 5. 对我们的启示（记忆系统）

| Codex 机制 | 我们的现状 | 可借鉴 |
|---|---|---|
| DSC 状态检查点 | 12 轮截断丢弃（无压缩） | 滚动摘要不该只是 LLM 摘要，应保留**结构化状态**（已决策/关键事实/进行中任务） |
| 尾部保留 | 无 tail 概念 | 压缩时保留最近 N 轮完整原文 |
| 状态外部化 | 我们有长期记忆库（SQLite） | 相当于 Codex 的 memory 文件，已占优；但缺"任务清单锚" |
| Artifact hash 去重 | 无 | 对话场景映射为"已注入记忆去重"（已有窗口去重，可增强为内容 hash） |
| token 预算 | 字符级分层独立 | 应向总量共享 + 近似 token 演进 |

## 6. 结论

Codex 体感好的根因：**压缩后模型"没有失忆"—— 状态以结构化方式保留，而不是以模糊摘要存活**。对记忆系统而言，最值得抄的不是"摘要"而是"检查点 + 尾部保留 + 外部状态锚"三件套。
