# 评测与重构方向：详细探索报告

> 目标：回答三个问题——①LoCoMo 基准怎么用？②有哪些现成的评测框架和架构可以复用？③我们的系统该怎么评测和重构？
>
> 所有结论基于联网搜索的一手资料（GitHub 仓库、论文、文档），非训练集记忆。

## 1. LoCoMo 基准详解

### 1.1 数据结构

- 来源：snap-research/locomo（ACL 2024，Maharana et al.）
- 内容：10 段超长对话（当前发布版为初版 50 段中筛选的最长子集），每段跨多个"session"（间隔数天的对话）
- 结构：locomo10.json，每段含 conversation（session_N + turns）+ observation（LLM 生成的观察）+ session_summary + QA 标注
- 每轮：speaker / dia_id / text；部分含图片（img_url + blip_caption）
- QA 标注：question + answer + category（5 类）+ evidence（答案所在 dialog ids）

### 1.2 五类 QA

| 类别 | 说明 | 对记忆系统的考验 |
|---|---|---|
| single-hop | 单轮直接可答 | 基础检索能力 |
| multi-hop | 需要组合多轮信息 | 跨轮关联能力 |
| temporal | 需要时间推理（"上次说的那个"） | 时间索引 + 会话状态 |
| open-domain | 需要结合常识回答 | 检索 + LLM 推理 |
| adversarial | 看似可答但答案不在对话中（应回答"不知道"） | 防止幻觉/过度检索 |

### 1.3 评测协议

1. 将整段对话逐轮喂给被测记忆系统（模拟真实使用）
2. 系统自由决定何时提取、如何存储、如何检索
3. 对话结束后，对每个 QA 问系统 → 系统基于记忆库 + 检索回答
4. LLM-as-Judge 评估回答质量（或 F1）
5. 同时统计：总 token 消耗、API 调用次数

## 2. 现成评测框架与实现

| 框架 | 语言 | 评测能力 | 可复用性 |
|---|---|---|---|
| LightMem (zjunlp) | Python | LoCoMo 全流程：add_locomo.py（建库）+ search_locomo.py（检索+QA 评测），含 LLM-as-Judge | **高**：脚本结构清晰，我们只需替换"建库"和"检索"两个模块 |
| HeLa-Mem | Python | encode_locomo.py + eval_locomo.py；同时支持 LongMemEval | **高**：Hebbian 图实现可直接研究 |
| snap-research/locomo | Python | 原始数据 + 生成脚本 + 评测脚本 | **高**：数据源 + 评测协议 |
| surreal-memory | Python | 扩散激活完整实现（hop 衰减 + 侧抑制 + 提前终止） | **中**：激活算法可单独借鉴 |
| neuralmind | Python | SQLite-backed Hebbian 图（加固+衰减+长时增强） | **高**：与我们同为 SQLite，架构最近 |

## 3. 关键论文与架构详解

### 3.1 HeLa-Mem (ACL 2026 Long)

- **核心洞察**：现有系统用非结构化嵌入向量 + 语义相似度检索，"无法捕捉人类记忆的联想结构"
- **机制**：记忆建模为动态图 + Hebbian 学习（"一起激活的连在一起"）
- **双层组织**：情景记忆图（共激活演化）+ 语义记忆库（Hebbian 蒸馏：反思 agent 识别密集连接的记忆枢纽，蒸馏为结构化语义知识）
- **结果**：LoCoMo 四类 QA 全面优于现有方法，token 更少
- **代码**：github.com/ReinerBRO/HeLa-Mem（encode_locomo.py + eval_locomo.py + hebbian_memory.py + hebbian_retriever.py）

### 3.2 Synapse (ACL 2026 Findings)

- **核心洞察**：标准 RAG 检索无法解决长期 agent 记忆的"割裂本质"（Contextual Tunneling）
- **机制**：动态图 + 扩散激活（relevance 从图遍历中涌现）+ 侧抑制 + 时间衰减
- **检索**：Triple Hybrid（geometric embeddings + activation-based graph traversal）
- **结果**：在 temporal 和 multi-hop QA 上显著优于 SOTA

### 3.3 LightMem (zjunlp, arXiv 2510.18866)

- **核心洞察**：现有系统的时间和计算开销过大
- **三阶段**：①感官记忆（轻量压缩 + 主题分组，无 LLM）②主题感知短期记忆（整合主题组）③长期记忆（闲时更新，与在线推理解耦）
- **结果**：QA 准确率 +7.7%/29.3%，token −38×/−20.9×，API 调用 −30×/−55.5×
- **代码**：github.com/zjunlp/LightMem（含 LoCoMo 评测脚本）

### 3.4 其他

- **surreal-memory**：扩散激活的完整工程实现（hop 衰减检测 + 侧抑制 + 安全上限），代码质量高
- **neuralmind**：SQLite-backed Hebbian 图（加固/衰减/长时增强），与我们的技术栈最近
- **Mem0**：每轮提取的标准实现（成本已知：1 call/turn）
- **RecMem** (arXiv 2605.16045)：递归记忆固化
- **"Memory in the LLM Era"** (arXiv 2604.01707)：10 种记忆机制的系统实验研究
- **Generative Agents** (Stanford)：记忆流 + 反思 + 规划的基础工作

## 4. 我们的系统 vs 这些方案：差距分析

| 能力 | 我们 | HeLa-Mem | Synapse | LightMem |
|---|---|---|---|---|
| 提取 | 规则触发 + LLM（覆盖 20%→100% 已修） | 会话事件全捕获 | 同左 | 主题分组 + 轻量压缩 |
| 组织 | 扁平表 + 三值分级 | 双层（情景图 + 语义库） | 动态图 + 侧抑制 | 三阶段（感官→STM→LTM） |
| 联想 | FTS + 同义词 + SessionState 回指 | Hebbian 扩散激活 | 扩散激活 + 图遍历 | 主题感知检索 |
| 压缩 | DSC 检查点（确定性） | Hebbian 蒸馏 | 图裁剪 | 闲时更新 |
| 审计 | mutation 全留痕 | 无 | 无 | 无 |
| 评测 | 自建（非标准） | LoCoMo + LongMemEval | LoCoMo | LoCoMo + LongMemEval |

**关键差距**：我们缺的不是"检索精度"（联想 10/10），而是**记忆间的结构化关联**（联想图）和**标准评测**（LoCoMo）。

## 5. 评测接入方案

### 方案：改造 LightMem 的评测脚本

LightMem 的评测脚本结构最清晰（add_locomo.py + search_locomo.py 分离），我们只需替换两个模块：

```
LoCoMo 对话轮次
  │
  ▼
我们的 chat 管线（替代 add_locomo.py）
  │  每轮：自动提取（带上下文）→ mutation 入库
  │  每轮：SessionState.update + 联想注入
  ▼
data/memory.db（或临时库）
  │
  ▼
我们的 search_memories（替代 search_locomo.py 的检索部分）
  │  FTS + 同义词 + SessionState → top-k 记忆
  ▼
LLM 回答 QA → LLM-as-Judge 评分
```

### 需要适配的部分

1. **数据加载**：解析 locomo10.json 的 session/turn 结构
2. **对话管线适配**：把 session 间隔模拟为"时间跳跃"（memory store 持久，SessionState 重置）
3. **检索接口**：search_memories(query) 返回相关记忆 → 拼入 QA prompt
4. **LLM Judge**：按 LoCoMo 协议评估回答 vs 标准答案

### 可直接复用的

- LoCoMo 数据（locomo10.json，10 段对话 + QA 标注）
- LLM-as-Judge 评分逻辑（LightMem / snap-research 均有实现）
- 评测指标（per-category accuracy + overall F1）

## 6. 重构方向建议

基于探索结果，如果评测显示我们的系统在 LoCoMo 上表现不佳，重构方向按优先级：

| 优先级 | 改造 | 来源参考 | 预期收益 |
|---|---|---|---|
| 1 | 记忆间关联图（memory_links 表：共现→边权重） | HeLa-Mem 的共激活模式 | 联想从"词面匹配"进化为"结构化关联" |
| 2 | 主题分组（提取时按主题聚合，替代扁平表） | LightMem 的主题分组 | 减少碎片记忆，提升检索精度 |
| 3 | 扩散激活检索（从 FTS 命中节点沿图边扩散） | Synapse 的 spreading activation | multi-hop/temporal QA 提升 |
| 4 | Hebbian 蒸馏（密集连接的记忆枢纽 → 语义知识） | HeLa-Mem 的 Hebbian Distillation | 记忆库自动"学到"高层模式 |

> 每一步改造后跑 LoCoMo 评测，数字说话。

## 7. HeLa-Mem 实现级细节（源码分析）

### 7.1 HebbianMemoryGraph 数据结构

```
nodes: {node_id: {content, embedding(384维), timestamp, type, keywords, metadata}}
edges: {source_id: {target_id: weight}}  // 邻接表 = 突触强度
```

### 7.2 Hebbian 参数（环境变量可调）

| 参数 | 默认值 | 作用 |
|---|---|---|
| HEBBIAN_DECAY_RATE | 0.995 | 全局边权重衰减 |
| HEBBIAN_LEARNING_RATE | 0.01 | 共激活时边权重增强速率 |
| HEBBIAN_ACTIVATION_ALPHA | 0.1 | 扩散激活系数 |
| HEBBIAN_SPREADING_THRESHOLD | 0.4 | 扩散触发阈值 |
| HEBBIAN_MAX_FLIPPED | 5 | 扩散翻转的最大条数 |
| HEBBIAN_KEYWORD_WEIGHT | 0.5 | 关键词匹配权重 |

### 7.3 检索算法（四步）

```
Step 1: Base Activation
  query_vec = embed(query)
  base[i] = cosine(query_vec, node[i].embedding)  → 归一化到 [0,1]

Step 2: Enhancement
  combined[i] = base[i] + keyword_weight × keyword_score(i)
  enhanced[i] = combined[i] × time_decay(i)

Step 3: Spreading Activation（Hebbian 核心）
  for node i where enhanced[i] > threshold(0.4):
    for neighbor j of node i:
      final[j] += enhanced[i] × edge_weight(i→j) × alpha(0.1)

Step 4: Dual-Pathway Ranking
  base_top_k = top_k of base_ranking（纯语义排序）
  spreading_top_k = top_k of final_scores（含扩散提升）
  翻转检测：spreading 排名中出现但 base 排名中没有的 → 标记为 Hebbian 翻转
  最终返回：base_top_k ∪ hebbian_flipped（最多 max_flipped 条）
```

### 7.4 关键设计洞察

1. **向量和图共存**：不是'替代向量'而是'向量 + 图遍历'双通道。向量提供语义匹配，图提供联想扩展。
2. **时间边**：每条新记忆自动与前一条建立时间边（权重 0.5）—— 时序关系是图的骨架。
3. **关键词提取用 LLM**：add_memory 时调 LLM 提取关键词存入 metadata（成本：每次 add 一次调用）。
4. **adaptive_forgetting**：低权重 + 旧记忆自动清除（dry_run 先预览），替代手动清理。
5. **reinforce_memory_cluster**：一批 node_ids 一起强化（映射到我们的'共现强化'）。

### 7.5 对我们的启发

我们已有 SessionState（话题轨迹）+ FTS（词面），缺的是**记忆间连接**。
HeLa-Mem 的最小可借鉴实现：

```
-- SQLite 新表：memory_links（记忆间关联）
CREATE TABLE memory_links (
  source_id TEXT REFERENCES memories(id),
  target_id TEXT REFERENCES memories(id),
  weight REAL DEFAULT 0.5,        -- Hebbian 突触强度
  last_reinforced TEXT,
  PRIMARY KEY (source_id, target_id)
);

-- 强化规则：当两条记忆在同一次检索中被同时命中时，
-- 它们之间的边权重 += learning_rate（Hebbian：一起激活 → 连接增强）
```

这使得检索时：FTS/关键词命中 → 沿 memory_links 扩散 → 关联记忆也被召回。
这是从'词面联想'到'结构化联想'的最小演进路径。
