# 记忆语义检索设计：向量嵌入模块

> **实现状态**（2026-06-20）：Phase 2 已完成。使用 BAAI/bge-small-zh-v1.5（本地部署，512维），
> 向量存储在 `memory_chunks` 表（`sessions.db`）。`memory_agent/embedding.py` + `memory_agent/db.py` 提供了
> 嵌入生成和余弦相似度检索。设计文档大部分已实现，保留作为架构参考。
>
> 与本设计文档的差异：
> - 嵌入模型从 OpenAI text-embedding-3-small 改为本地 BGE（离线、免费、中文优化）
> - 未使用独立的 vector_store.py（功能集成到 db.py 中）
> - 未实现 QueryBuilder / HybridSearch（Plan 中列为 Phase 3）
>
> ---

## 缘起

当前记忆检索使用 BM25 关键词匹配，存在根本局限：

| 场景 | BM25 | 语义检索 |
|------|------|---------|
| 搜"压力大"能匹配"焦虑失眠"吗 | ❌ 不能，词不同 | ✅ 语义相近即可匹配 |
| 搜"喜欢什么电影"能匹配"最近看了沙丘"吗 | ❌ 词面无交集 | ✅ 话题语义相近 |
| 跨语言匹配 | ❌ | ✅（如 embedding 模型支持中文） |
| 同义词/近义词 | ❌ | ✅ |
| 抽象概念匹配 | ❌ | ✅ |

## 架构概览

```
                写入路径                             检索路径
  ┌─────────────────────────────┐    ┌──────────────────────────────┐
  │                             │    │                              │
  │  consolidate() /             │    │  用户消息 / 当前上下文        │
  │  extract_and_save()          │    │       │                      │
  │       │                     │    │       ▼                      │
  │       ▼                     │    │  查询生成器 (QueryBuilder)    │
  │  memory/*.md 写入            │    │       │                      │
  │       │                     │    │       ▼                      │
  │       ▼                     │    │  查询嵌入 → query vector     │
  │  向量化服务 (异步/批量)      │    │       │                      │
  │       │                     │    │   ┌───┴───┐                  │
  │       ▼                     │    │   │ 向量库 │                 │
  │  文本分块 → embedding        │    │   │(SQLite│                  │
  │       │         → 向量库     │    │   │+chroma│                  │
  │       ▼                     │    │   └───┬───┘                  │
  │  向量库写入成功               │    │       │                      │
  │                             │    │       ▼                      │
  │                             │    │  cosine similarity 排序       │
  │                             │    │       │                      │
  │                             │    │   ┌───┴──────────┐           │
  │                             │    │   │ 混合排序引擎  │           │
  │                             │    │   │ BM25 × 语义   │           │
  │                             │    │   │ × 时间衰减     │           │
  │                             │    │   │ × priority加权│           │
  │                             │    │   └───┬──────────┘           │
  │                             │    │       │                      │
  │                             │    │       ▼                      │
  │                             │    │  注入系统提示词               │
  └─────────────────────────────┘    └──────────────────────────────┘
```

## 模块设计

### 1. 向量化服务（`memory_agent/vectorizer.py`）

**职责**：将 `memory/*.md` 中的记忆文本转化为向量嵌入，存入向量库。

```python
class Vectorizer:
    """
    记忆向量化服务。
    
    策略：
    - 异步执行：写入 memory/*.md 后触发，不阻塞主流程。
    - 增量更新：只处理 mtime 变化或新增的文件。
    - 分段嵌入：长记忆自动分块（每个 chunk ≤ 512 tokens），
      每条记忆可能有多个向量，检索时取最匹配 chunk。
    """
    
    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider
        self.store = VectorStore()
    
    async def vectorize_all(self) -> VectorizeResult:
        """全量重建向量索引（用于初始化/重建）"""
    
    async def vectorize_one(self, slug: str) -> bool:
        """单条记忆向量化（增量更新用）"""
    
    async def vectorize_pending(self) -> list[str]:
        """只处理未向量化或已变更的记忆"""
```

#### 嵌入模型选择

| 方案 | 优点 | 缺点 | 建议场景 |
|------|------|------|---------|
| **BGE (bge-small-zh-v1.5)** | 本地部署、中文优化、离线免费、512维 | 需要本地推理（CPU可跑） | **当前选用** |
| **OpenAI text-embedding-3-small** | 质量高、维度可调(256-1536) | 需要 API key、有网络依赖 | 备选 |
| **OpenAI text-embedding-3-large** | 质量最高 | 成本更高、维度 3072 | 质量优先场景 |
| **oMLX `/v1/embeddings`** | 本地 Apple Silicon 运行 | 仅限 Mac | 已有 oMLX 用户 |

**当前选用**：`BAAI/bge-small-zh-v1.5`，维度 512（本地、中文优化、零API依赖）。

### 2. 向量库（`memory_agent/vector_store.py`）

向量库需要存储：向量 + slug + chunk_id + chunk_text + metadata + embedding_model 版本号。

```python
class VectorStore:
    """
    向量存储抽象层。
    
    支持多种后端，统一接口：
    - SQLite + numpy（零依赖，轻量，适合 < 1k 条记忆）
    - ChromaDB（持久化、过滤、元数据查询，适合 >= 1k 条记忆）
    - 自定义（通过 VectorBackend 接口扩展）
    """
    
    async def upsert(self, entries: list[VectorEntry]) -> None:
        """写入/更新向量"""
    
    async def search(
        self, 
        query_vector: list[float], 
        top_k: int = 10,
        filter: dict | None = None,  # 如 {"priority": "important"}
    ) -> list[SearchResult]:
        """语义相似度搜索"""
    
    async def delete(self, slug: str) -> None:
        """删除某条记忆的所有向量 chunk"""
    
    async def stats(self) -> dict:
        """向量库统计信息"""
```

**存储后端决策**：

```
记忆量 < 500 条 → SQLite + numpy（零依赖，够用）
记忆量 >= 500 条 → ChromaDB（专业向量库，支持元数据过滤）
```

### 3. 查询生成器（`memory_agent/query_builder.py`）

这是语义检索的关键组件——**把当前对话上下文转化为多个检索 query**。

```python
class QueryBuilder:
    """
    从对话上下文生成检索查询。
    
    策略：多条 query 从不同角度检索，提高召回率。
    """
    
    def build_queries(
        self, 
        user_message: str, 
        context_messages: list[str],  # 最近几轮对话
        intent: str = "",             # 当前意图分类
    ) -> list[str]:
        """
        生成 2-5 条检索 query。
        
        例如用户说"最近压力好大，睡不好"：
        → ["压力大", "睡眠问题", "焦虑", "心理健康"]
        
        规则：
        - query 1-2：直接从用户消息提取关键短语
        - query 3：如果意图是个人相关，加入通用前缀
        - query 4：从 AI 上轮回复中提取可能相关的主题
        """
```

**两种实现方案**：

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **启发式 QueryBuilder** | 关键词提取 + 命名实体 + 规则展开 | 零额外 LLM 调用、可预测 | 覆盖不全 |
| **LLM QueryBuilder** | 用小模型对对话做 query 生成 | 覆盖更全、可理解上下文 | 增加一次 LLM 调用 |

**建议**：先做启发式（零额外成本），复杂不够时加 LLM 生成作为补充。

### 4. 混合排序引擎（`memory_agent/hybrid_search.py`）

```python
class HybridSearch:
    """
    混合排序：BM25 × 语义向量 × 时间衰减 × Priority 加权。
    
    RRF (Reciprocal Rank Fusion) 或加权合并：
    final_score = w1 * semantic_rank + w2 * bm25_rank + w3 * time_decay + w4 * priority_boost
    """
    
    def search(
        self, 
        queries: list[str], 
        top_k: int = 10,
    ) -> list[HybridResult]:
        # 1. 对每条 query 做语义检索
        # 2. 对每条 query 做 BM25 检索
        # 3. 合并、去重、RRF 重排
        # 4. 应用时间衰减和 priority 加权
        # 5. 返回 top_k
```

**权重建议（可通过实验调节）**：

| 因子 | 默认权重 | 说明 |
|------|---------|------|
| 语义相似度 | 0.5 | 主要信号 |
| BM25 关键词 | 0.2 | 补充精确匹配 |
| 时间衰减 | 0.15 | 90 天半衰期 |
| Priority 加权 | 0.15 | core × 1.5, important × 1.2 |

### 5. 触发时机

**写入侧**（两种方案选一）：

| 方案 | 做法 | 复杂度 | 实时性 |
|------|------|--------|--------|
| **A: consolidate 后同步** | consolidate() 最后追加向量化 | 低 | 有延迟 |
| **B: 后台 watcher** | 独立进程监听 `memory/*.md` 文件变更 | 中 | 准实时 |

**建议**：先做方案 A，简单可靠。后续需要实时性时加方案 B。

**检索侧**：

```
每次用户消息 → AI 生成回复前 → ContextBuilder 构建提示词时：
  1. 已有 L0/L1 → 始终加载
  2. QueryBuilder 生成检索 query
  3. HybridSearch 检索 top_k
  4. 过滤相关性 < 阈值的条目
  5. 注入上下文：## 相关历史记忆 段
```

## 与现有系统的集成

### 数据流

```
现有流程：
  consolidate() → extract_and_save() → memory/*.md
                                                    ↓（新增）
                                              vectorizer.vectorize_one(slug)

现有流程：
  ContextBuilder.build() → 加载 L0/L1 + 拼接上下文
                              ↓（新增）
                         query_builder.build_queries()
                              ↓
                         hybrid_search.search()
                              ↓
                         注入 ## 相关历史记忆 段
```

### 配置项（新增到 `config.py`）

```python
# ── 向量嵌入配置 ──
EMBEDDING_PROVIDER = _env("MEMORY_AGENT_EMBEDDING_PROVIDER", "local")  # local | openai
EMBEDDING_MODEL = _env("MEMORY_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DIMENSIONS = _env_int("MEMORY_AGENT_EMBEDDING_DIMENSIONS", 512)
EMBEDDING_BATCH_SIZE = _env_int("MEMORY_AGENT_EMBEDDING_BATCH_SIZE", 16)
EMBEDDING_DEVICE = _env("MEMORY_AGENT_EMBEDDING_DEVICE", "cpu")  # cpu | cuda | mps

# ── 混合搜索配置 ──
HYBRID_SEARCH_TOP_K = _env_int("MEMORY_AGENT_HYBRID_SEARCH_TOP_K", 10)
HYBRID_SEMANTIC_WEIGHT = _env_float("MEMORY_AGENT_HYBRID_SEMANTIC_WEIGHT", 0.5)
HYBRID_BM25_WEIGHT = _env_float("MEMORY_AGENT_HYBRID_BM25_WEIGHT", 0.2)
HYBRID_TIME_WEIGHT = _env_float("MEMORY_AGENT_HYBRID_TIME_WEIGHT", 0.15)
HYBRID_PRIORITY_WEIGHT = _env_float("MEMORY_AGENT_HYBRID_PRIORITY_WEIGHT", 0.15)
HYBRID_RELEVANCE_THRESHOLD = _env_float("MEMORY_AGENT_HYBRID_RELEVANCE_THRESHOLD", 0.4)
```

## 实现计划

### Phase 1：核心向量化 + 存储（预计 1-2 天）

1. 创建 `memory_agent/vectorizer.py` — 调用 embedding API，分段逻辑
2. 创建 `memory_agent/vector_store.py` — SQLite + numpy 后端
3. 创建 `memory_agent/embedding_provider.py` — 抽象 embedding API 调用
4. 在 `consolidate()` 最后追加 `vectorizer.vectorize_pending()`

**产出**：每条记忆写入后自动向量化，可通过 `vector_store.search()` 手动验证。

### Phase 2：混合搜索（预计 1-2 天）

1. 创建 `memory_agent/query_builder.py` — 启发式检索 query 生成
2. 创建 `memory_agent/hybrid_search.py` — 合并 BM25 + 语义 + 时间衰减
3. 在 MCP 中提供 `hybrid_search` 工具

**产出**：`search_memories` 工具支持语义检索，用户可对比 BM25 和混合检索效果。

### Phase 3：检索注入（预计 1-2 天）

1. 在 `ContextBuilder` 中接入 query builder + hybrid search
2. 注入结果到系统提示词
3. 相关性阈值过滤 + 来源标注

**产出**：对话自动检索相关历史记忆。

### Phase 4：评估与调优（持续）

1. 记录每次检索的 query、top_k 结果、最终是否被 LLM 使用
2. 通过日志分析调整权重参数
3. 按需添加 ChromaDB 后端支持

## 关键设计决策

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 嵌入模型 | OpenAI / 本地 BGE / oMLX | **BGE (bge-small-zh-v1.5)** | 本地离线、中文优化、零API依赖 |
| 向量库 | SQLite+numpy / ChromaDB / Pinecone | **SQLite+numpy 优先** | 零依赖、够用，迁移到 ChromaDB 的接口已预留 |
| 查询生成 | 启发式 / LLM 生成 | **启发式优先 + LLM 补充** | 零额外 LLM 调用即可启动 |
| 向量化触发 | 同步 consolidated / 后台 watcher | **consolidate 后同步** | 简单可靠，一期先跑通 |
| 检索注入时机 | 每次对话 / 仅主题触发 | **每次对话 + L0/L1 分离** | L0/L1 始终加载，L2/L3 语义检索补充 |

## 避免的陷阱

1. **不要用 AI 回复的内容来检索记忆** — 检索 query 应基于用户消息，而不是 AI 自己的输出，否则容易自我强化（model collapse）。
2. **检索结果必须标注来源** — 注入上下文时注明"检索自历史记忆"，让 AI 知道这不是直接对话上下文，降低幻觉风险。
3. **相关性阈值不可省略** — 低相关度的向量检索结果比无结果更害人（引入噪声）。默认阈值 0.4，低于此值的不注入。
4. **chunk 不能跨段** — 按段落/NLP句子分割，不要在句子中间截断。每条 chunk 在注入时附带其来源 memory slug，方便追溯。
5. **模型版本号必须存储** — 向量库中记录 embedding_model 版本，换模型后全量重建，避免新旧向量混用导致排序混乱。
