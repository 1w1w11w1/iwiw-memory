<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-top">
        <header class="brand">
          <div>
            <strong>SelfEcho</strong>
            <span>personal memory agent</span>
          </div>
          <div class="health-dot" :class="{ ok: health?.ok }" :title="health?.ok ? '服务正常' : '等待连接'"></div>
        </header>

        <nav class="primary-nav" aria-label="主导航">
          <span class="nav-indicator" :style="navIndicatorStyle"></span>
          <button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" @click="switchTab(item.id)">
            {{ item.label }}
          </button>
        </nav>

        <div class="sidebar-hint">
          <span>{{ navHint }}</span>
        </div>
      </div>

      <section class="context-panel">
        <Transition name="panel-fade" mode="out-in">
          <div :key="`${tab}-${memoryMode}`" class="context-panel-inner">
        <template v-if="tab === 'chat'">
          <div class="panel-title">
            <span>会话</span>
            <button class="ghost-btn" @click="newSession">新建</button>
          </div>
          <div class="session-list">
            <button
              v-for="session in sessions"
              :key="session.id"
              class="list-item"
              :class="{ selected: activeSession === session.id }"
              @click="openSession(session.id)"
            >
              <span class="item-title">{{ session.title || '未命名会话' }}</span>
              <span class="item-meta">{{ session.turn_count || 0 }} 条 · {{ formatDate(session.updated_at) }}</span>
            </button>
          </div>
        </template>

        <template v-else-if="tab === 'memory'">
          <div class="panel-title">
            <span>记忆</span>
            <button class="ghost-btn" @click="rebuildIndex">重建</button>
          </div>
          <div class="segmented">
            <button :class="{ active: memoryMode === 'long' }" @click="memoryMode = 'long'">长期</button>
            <button :class="{ active: memoryMode === 'session' }" @click="memoryMode = 'session'">会话</button>
          </div>

          <template v-if="memoryMode === 'long'">
            <form class="search-row" @submit.prevent="searchMemories">
              <input v-model="memoryQuery" placeholder="搜索长期记忆" />
            </form>
            <div class="memory-tier-list">
              <section v-for="tier in tiers" :key="tier.id" class="tier-group" :class="tier.id">
                <header>
                  <span class="tier-code">{{ tier.level }}</span>
                  <span>{{ tier.name }}</span>
                  <em>{{ groupedMemories[tier.id]?.length || 0 }}</em>
                </header>
                <button
                  v-for="memory in groupedMemories[tier.id]"
                  :key="memory.slug"
                  class="list-item memory-item"
                  :class="{ selected: memoryDetail?.slug === memory.slug }"
                  @click="openMemory(memory.slug)"
                >
                  <span class="item-title">{{ memory.slug }}</span>
                  <span class="item-meta">{{ memory.description || '无描述' }}</span>
                </button>
              </section>
            </div>
          </template>

          <template v-else>
            <form class="search-row" @submit.prevent="searchHistory">
              <input v-model="historyQuery" placeholder="搜索会话记忆" />
            </form>
            <button class="quiet-wide" @click="migrateLegacy">导入旧历史</button>
            <div class="session-list">
              <button
                v-for="result in historyResults"
                :key="`${result.session_id}-${result.turn_idx}`"
                class="list-item"
                :class="{ selected: historySessionId === result.session_id }"
                @click="openHistory(result.session_id)"
              >
                <span class="item-title">{{ result.title || '历史会话' }}</span>
                <span class="item-meta">{{ cleanSnippet(result.snippet) }}</span>
              </button>
            </div>
          </template>
        </template>

        <template v-else>
          <div class="panel-title">
            <span>模型</span>
            <button class="ghost-btn" @click="saveProviders">保存</button>
          </div>
          <div class="context-label">常用模板</div>
          <div class="session-list tight">
            <button v-for="template in modelTemplates" :key="template.id" class="list-item" @click="applyTemplate(template)">
              <span class="item-title">{{ template.label }}</span>
              <span class="item-meta">{{ template.api_style }} · {{ template.models?.[0] || '自定义' }}</span>
            </button>
          </div>
          <div class="context-label">已配置</div>
          <div class="session-list">
            <button
              v-for="provider in providers.providers"
              :key="provider.id"
              class="list-item"
              :class="{ selected: activeProviderId === provider.id }"
              @click="editProvider(provider)"
            >
              <span class="item-title">{{ provider.label || provider.id }}</span>
              <span class="item-meta">{{ provider.enabled ? '启用' : '停用' }} · {{ provider.base_url || '未设置地址' }}</span>
            </button>
          </div>
        </template>
          </div>
        </Transition>
      </section>
    </aside>

    <main class="workspace">
      <Transition name="workspace-fade" mode="out-in">
      <section v-if="tab === 'chat'" key="chat" class="chat-page">
        <header class="page-head compact">
          <div>
            <p class="eyebrow">{{ activeSessionTitle }}</p>
            <h1>对话</h1>
          </div>
          <div class="head-actions">
            <label class="inline-check"><input type="checkbox" v-model="privateMode" />私密</label>
            <button :disabled="!activeSession" @click="consolidate">整理当前会话</button>
          </div>
        </header>

        <section class="chat-surface">
          <div ref="messageBox" class="messages">
            <div v-if="messages.length === 0" class="empty-chat">
              <strong>可以开始了。</strong>
              <span>这里优先承接表达；完整原始会话会保存在本地，会话结束后再整理成记忆。</span>
            </div>
            <article v-for="message in messages" :key="message.turn_idx" class="message" :class="message.role">
              <div class="message-role">{{ message.role === 'user' ? '你' : 'SelfEcho' }}</div>
              <div class="message-body">{{ message.content }}</div>
            </article>
          </div>

          <form class="composer" @submit.prevent="sendMessage">
            <textarea v-model="chatInput" placeholder="把想说的话放在这里..." rows="3"></textarea>
            <button :disabled="sending || !chatInput.trim()">{{ sending ? '回应中' : '发送' }}</button>
          </form>
          <p v-if="lastConsolidation" class="soft-note">{{ consolidationText }}</p>
        </section>
      </section>

      <section v-else-if="tab === 'memory'" :key="`memory-${memoryMode}`" class="tool-page">
        <header class="page-head">
          <div>
            <p class="eyebrow">{{ memoryMode === 'long' ? 'L0 / L1 / L2 / L3' : 'Search / Replay' }}</p>
            <h1>{{ memoryMode === 'long' ? '长期记忆' : '会话记忆' }}</h1>
          </div>
          <div v-if="memoryMode === 'long' && memoryDetail" class="head-actions">
            <button @click="saveMemory">保存</button>
            <button @click="archiveMemory">归档</button>
            <button class="danger" @click="deleteMemory">删除</button>
          </div>
        </header>

        <section v-if="memoryMode === 'long'" class="memory-stage">
          <div v-if="memoryDetail" class="editor-panel">
            <div class="memory-heading">
              <span class="priority-chip" :class="memoryEdit.priority">{{ tierLabel(memoryEdit.priority) }}</span>
              <div>
                <h2>{{ memoryDetail.slug }}</h2>
                <p>{{ memoryDetail.path }}</p>
              </div>
            </div>

            <label>
              描述
              <input v-model="memoryEdit.description" />
            </label>
            <div class="field-grid">
              <label>
                权重
                <select v-model="memoryEdit.priority">
                  <option value="core">L0 core</option>
                  <option value="important">L1 important</option>
                  <option value="normal">L2 normal</option>
                  <option value="archive">L3 archive</option>
                </select>
              </label>
              <label>
                类型
                <input v-model="memoryEdit.mem_type" />
              </label>
            </div>
            <label>
              内容
              <textarea v-model="memoryEdit.body" rows="18"></textarea>
            </label>

            <div class="merge-row">
              <label>
                合并来源 slug
                <input v-model="mergeSourceSlug" placeholder="来源记忆会被归档" />
              </label>
              <button :disabled="!mergeSourceSlug.trim()" @click="mergeMemory">合并</button>
            </div>

            <details class="history-box">
              <summary>历史备份 {{ memoryDetail.history?.length || 0 }}</summary>
              <div v-for="item in memoryDetail.history" :key="item.version" class="history-item">
                <span>{{ item.version }}</span>
                <em>{{ formatDate(item.mtime) }}</em>
              </div>
            </details>
          </div>

          <div v-else class="empty-state">
            <strong>从左侧选择一条长期记忆。</strong>
            <span>四级权重已经放到左下列表里，编辑区只保留当前任务。</span>
          </div>
        </section>

        <section v-else class="memory-stage">
          <div v-if="historyReplay" class="replay-panel">
            <header>
              <h2>{{ historyReplayTitle }}</h2>
              <span>{{ historyReplayMessages.length }} 条消息</span>
            </header>
            <article v-for="message in historyReplayMessages" :key="message.turn_idx" class="replay-message" :class="message.role">
              <strong>{{ message.role === 'user' ? '你' : 'SelfEcho' }}</strong>
              <p>{{ message.content }}</p>
            </article>
          </div>
          <div v-else class="empty-state">
            <strong>搜索并回放一段会话。</strong>
            <span>旧历史会进入导入历史筛选，不再改变新会话的生命周期。</span>
          </div>
        </section>
      </section>

      <section v-else key="models" class="tool-page">
        <header class="page-head">
          <div>
            <p class="eyebrow">Providers</p>
            <h1>模型</h1>
          </div>
          <div class="head-actions">
            <button :disabled="!providerForm.id" @click="testProvider(providerForm.id)">测试连接</button>
            <button @click="saveProviders">保存配置</button>
          </div>
        </header>

        <section class="model-panel">
          <div class="model-intro">
            <h2>{{ providerForm.id ? '模型配置' : '选择左侧模板开始' }}</h2>
            <p>优先使用预设模板；需要非标准服务时，再从自定义模型入口补充。</p>
          </div>

          <div class="field-grid">
            <label>
              ID
              <input v-model="providerForm.id" placeholder="deepseek-main" />
            </label>
            <label>
              名称
              <input v-model="providerForm.label" placeholder="DeepSeek" />
            </label>
          </div>
          <div class="field-grid">
            <label>
              接口风格
              <select v-model="providerForm.api_style">
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI Compatible</option>
              </select>
            </label>
            <label class="check-field">
              <input type="checkbox" v-model="providerForm.enabled" />
              启用这个 provider
            </label>
          </div>
          <label>
            Base URL
            <input v-model="providerForm.base_url" placeholder="https://api.example.com/v1" />
          </label>
          <label>
            API Key 环境变量
            <input v-model="providerForm.api_key_env" placeholder="MEMORY_AGENT_LLM_API_KEY" />
          </label>
          <label>
            可用模型，使用逗号分隔
            <input v-model="providerModelsText" placeholder="model-a, model-b" />
          </label>
          <div class="field-grid three">
            <label>
              聊天默认
              <input v-model="providerForm.defaults.chat" />
            </label>
            <label>
              摘要默认
              <input v-model="providerForm.defaults.summary" />
            </label>
            <label>
              记忆默认
              <input v-model="providerForm.defaults.memory" />
            </label>
          </div>
          <button class="primary-action" @click="upsertProvider">加入或更新配置</button>
        </section>
      </section>
      </Transition>

      <button v-if="error" class="toast" @click="error = ''">{{ error }}</button>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { request } from './api'

type Tab = 'chat' | 'memory' | 'models'
type MemoryMode = 'long' | 'session'
type Provider = {
  id: string
  label: string
  api_style: 'anthropic' | 'openai'
  base_url: string
  api_key_env?: string
  enabled: boolean
  models: string[]
  defaults: { chat: string; summary: string; memory: string }
}

const tabs = [
  { id: 'chat', label: '对话' },
  { id: 'memory', label: '记忆' },
  { id: 'models', label: '模型' },
] as const

const tiers = [
  { id: 'core', level: 'L0', name: '核心' },
  { id: 'important', level: 'L1', name: '重要' },
  { id: 'normal', level: 'L2', name: '日常' },
  { id: 'archive', level: 'L3', name: '归档' },
] as const

const tab = ref<Tab>('chat')
const memoryMode = ref<MemoryMode>('long')
const error = ref('')
const health = ref<any>(null)
const messageBox = ref<HTMLElement | null>(null)

const sessions = ref<any[]>([])
const activeSession = ref('')
const messages = ref<any[]>([])
const chatInput = ref('')
const sending = ref(false)
const privateMode = ref(false)
const lastConsolidation = ref<any>(null)

const memories = ref<any[]>([])
const memoryQuery = ref('')
const memoryDetail = ref<any>(null)
const memoryEdit = ref({ description: '', body: '', priority: 'normal', mem_type: 'user' })
const mergeSourceSlug = ref('')

const historyQuery = ref('')
const historyResults = ref<any[]>([])
const historySessionId = ref('')
const historyReplay = ref<any>(null)

const providers = ref<{ providers: Provider[] }>({ providers: [] })
const modelTemplates = ref<any[]>([])
const activeProviderId = ref('')
const providerModelsText = ref('')
const providerForm = reactive<Provider>({
  id: '',
  label: '',
  api_style: 'openai',
  base_url: '',
  api_key_env: '',
  enabled: true,
  models: [],
  defaults: { chat: '', summary: '', memory: '' },
})

const groupedMemories = computed<Record<string, any[]>>(() => {
  const groups: Record<string, any[]> = { core: [], important: [], normal: [], archive: [] }
  for (const memory of memories.value) {
    const key = groups[memory.priority] ? memory.priority : 'normal'
    groups[key].push(memory)
  }
  return groups
})

const activeSessionTitle = computed(() => {
  const session = sessions.value.find((item) => item.id === activeSession.value)
  return session?.title || '新会话'
})

const navHint = computed(() => {
  if (tab.value === 'chat') return '默认入口，承接当前表达'
  if (tab.value === 'memory') return '长期事实与会话回放'
  return '模型模板与本地 provider'
})

const activeTabIndex = computed(() => tabs.findIndex((item) => item.id === tab.value))
const navIndicatorStyle = computed(() => ({
  transform: `translateY(${Math.max(activeTabIndex.value, 0) * 63}px)`,
}))

const consolidationText = computed(() => {
  const result = lastConsolidation.value
  if (!result) return ''
  const memoryResult = result.memory_result || {}
  const saved = Array.isArray(memoryResult.saved) ? memoryResult.saved.length : 0
  return `已完成第 ${result.cycle_no || 1} 次整理，候选记忆写入 ${saved} 条。`
})

const historyReplayMessages = computed(() => historyReplay.value?.messages || [])
const historyReplayTitle = computed(() => historyReplay.value?.session?.title || '历史回放')

async function guard(fn: () => Promise<void>) {
  try {
    error.value = ''
    await fn()
  } catch (err: any) {
    error.value = err?.message || String(err)
  }
}

function switchTab(next: Tab) {
  tab.value = next
}

function formatDate(value: string) {
  if (!value) return ''
  return value.slice(5, 16).replace('T', ' ')
}

function cleanSnippet(value: string) {
  return String(value || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
}

function tierLabel(priority: string) {
  const tier = tiers.find((item) => item.id === priority)
  return tier ? `${tier.level} ${tier.name}` : 'L2 日常'
}

function normalizeModels(models: unknown): string[] {
  if (Array.isArray(models)) return models.map(String).filter(Boolean)
  if (typeof models === 'string') return models.split(',').map((model) => model.trim()).filter(Boolean)
  return []
}

async function scrollMessages() {
  await nextTick()
  if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight
}

async function refreshAll() {
  await guard(async () => {
    health.value = await request('/health')
    await Promise.all([loadSessions(), loadMemories(), loadProviders(), loadModelTemplates()])
    await ensureDefaultChat()
  })
}

async function ensureDefaultChat() {
  if (activeSession.value) return
  const guiSession = sessions.value.find((session) => session.source === 'gui') || sessions.value[0]
  if (guiSession) {
    await openSession(guiSession.id)
  } else {
    await newSession()
  }
}

async function loadSessions() {
  const res: any = await request('/chat/sessions?source=gui')
  sessions.value = res.sessions || []
}

async function newSession() {
  await guard(async () => {
    const session: any = await request('/chat/sessions', { method: 'POST' })
    activeSession.value = session.id
    messages.value = []
    lastConsolidation.value = null
    await loadSessions()
  })
}

async function openSession(id: string) {
  await guard(async () => {
    activeSession.value = id
    const res: any = await request(`/session-memory/${id}`)
    messages.value = res.messages || []
    await scrollMessages()
  })
}

async function sendMessage() {
  if (!chatInput.value.trim()) return
  await guard(async () => {
    if (!activeSession.value) await newSession()
    sending.value = true
    const message = chatInput.value
    chatInput.value = ''
    const res: any = await request(`/chat/${activeSession.value}/message`, {
      method: 'POST',
      body: JSON.stringify({ message, private: privateMode.value }),
    })
    lastConsolidation.value = res.consolidation
    await openSession(activeSession.value)
    await loadSessions()
  })
  sending.value = false
}

async function consolidate() {
  if (!activeSession.value) return
  await guard(async () => {
    lastConsolidation.value = await request(`/chat/${activeSession.value}/consolidate`, { method: 'POST' })
  })
}

async function loadMemories() {
  const res: any = await request('/memories')
  memories.value = res.memories || []
}

async function searchMemories() {
  await guard(async () => {
    if (!memoryQuery.value.trim()) {
      await loadMemories()
      return
    }
    const res: any = await request(`/memories/search?q=${encodeURIComponent(memoryQuery.value)}`)
    memories.value = res.results || []
  })
}

async function openMemory(slug: string) {
  await guard(async () => {
    memoryDetail.value = await request(`/memories/${slug}`)
    memoryEdit.value = {
      description: memoryDetail.value.description || '',
      body: memoryDetail.value.body || '',
      priority: memoryDetail.value.priority || 'normal',
      mem_type: memoryDetail.value.type || 'user',
    }
    mergeSourceSlug.value = ''
  })
}

async function saveMemory() {
  if (!memoryDetail.value || !confirm('保存前会备份旧版本并重建索引。继续？')) return
  await guard(async () => {
    await request(`/memories/${memoryDetail.value.slug}`, {
      method: 'PUT',
      body: JSON.stringify({ ...memoryEdit.value, reason: 'GUI 手动编辑' }),
    })
    await openMemory(memoryDetail.value.slug)
    await loadMemories()
  })
}

async function archiveMemory() {
  if (!memoryDetail.value || !confirm('确认把这条记忆归档到 L3？')) return
  await guard(async () => {
    await request(`/memories/${memoryDetail.value.slug}/archive`, { method: 'POST' })
    await openMemory(memoryDetail.value.slug)
    await loadMemories()
  })
}

async function deleteMemory() {
  if (!memoryDetail.value || !confirm('删除前会备份旧版本，但这仍是危险操作。确认删除？')) return
  await guard(async () => {
    await request(`/memories/${memoryDetail.value.slug}`, { method: 'DELETE' })
    memoryDetail.value = null
    await loadMemories()
  })
}

async function mergeMemory() {
  if (!memoryDetail.value || !mergeSourceSlug.value.trim()) return
  if (!confirm(`确认将 ${mergeSourceSlug.value} 合并到 ${memoryDetail.value.slug}？来源记忆会被归档。`)) return
  await guard(async () => {
    await request('/memories/merge', {
      method: 'POST',
      body: JSON.stringify({
        target_slug: memoryDetail.value.slug,
        source_slug: mergeSourceSlug.value,
        merged_body: memoryEdit.value.body,
        description: memoryEdit.value.description,
        priority: memoryEdit.value.priority,
        mem_type: memoryEdit.value.mem_type,
      }),
    })
    await openMemory(memoryDetail.value.slug)
    await loadMemories()
  })
}

async function rebuildIndex() {
  await guard(async () => {
    await request('/memories/rebuild-index', { method: 'POST' })
    await loadMemories()
  })
}

async function searchHistory() {
  if (!historyQuery.value.trim()) return
  await guard(async () => {
    const res: any = await request(`/session-memory/search?q=${encodeURIComponent(historyQuery.value)}`)
    historyResults.value = res.results || []
  })
}

async function openHistory(id: string) {
  await guard(async () => {
    historySessionId.value = id
    historyReplay.value = await request(`/session-memory/${id}`)
  })
}

async function migrateLegacy() {
  if (!confirm('导入旧历史到 SelfEcho 会话记忆层？已有数据会跳过。')) return
  await guard(async () => {
    await request('/session-memory/migrate-legacy', { method: 'POST' })
    await searchHistory()
    await loadSessions()
  })
}

async function loadProviders() {
  providers.value = await request('/models/providers')
}

async function loadModelTemplates() {
  const res: any = await request('/models/templates')
  modelTemplates.value = res.templates || []
}

function applyTemplate(template: any) {
  const suffix = Date.now().toString().slice(-5)
  const id = template.id === 'custom' ? `custom-${suffix}` : `${template.id}-${suffix}`
  const models = normalizeModels(template.models)
  providerForm.id = id
  providerForm.label = template.label || '自定义模型'
  providerForm.api_style = template.api_style || 'openai'
  providerForm.base_url = template.base_url || ''
  providerForm.api_key_env = 'MEMORY_AGENT_LLM_API_KEY'
  providerForm.enabled = true
  providerForm.models = [...models]
  providerModelsText.value = models.join(', ')
  const first = models[0] || ''
  providerForm.defaults = { chat: first, summary: first, memory: first }
  activeProviderId.value = ''
}

function editProvider(provider: Provider) {
  activeProviderId.value = provider.id
  providerForm.id = provider.id
  providerForm.label = provider.label
  providerForm.api_style = provider.api_style
  providerForm.base_url = provider.base_url
  providerForm.api_key_env = provider.api_key_env || ''
  providerForm.enabled = Boolean(provider.enabled)
  providerForm.models = normalizeModels(provider.models)
  providerModelsText.value = providerForm.models.join(', ')
  providerForm.defaults = {
    chat: provider.defaults?.chat || providerForm.models[0] || '',
    summary: provider.defaults?.summary || providerForm.models[0] || '',
    memory: provider.defaults?.memory || providerForm.models[0] || '',
  }
}

function upsertProvider() {
  const models = providerModelsText.value.split(',').map((model) => model.trim()).filter(Boolean)
  if (!providerForm.id.trim() || !providerForm.label.trim()) {
    error.value = '模型 ID 和名称不能为空'
    return
  }
  const item: Provider = {
    id: providerForm.id.trim(),
    label: providerForm.label.trim(),
    api_style: providerForm.api_style,
    base_url: providerForm.base_url.trim(),
    api_key_env: providerForm.api_key_env?.trim(),
    enabled: providerForm.enabled,
    models,
    defaults: { ...providerForm.defaults },
  }
  const index = providers.value.providers.findIndex((provider) => provider.id === item.id)
  if (index >= 0) providers.value.providers.splice(index, 1, item)
  else providers.value.providers.push(item)
  activeProviderId.value = item.id
}

async function saveProviders() {
  await guard(async () => {
    if (providerForm.id.trim() || providerForm.label.trim()) upsertProvider()
    providers.value = await request('/models/providers', {
      method: 'PUT',
      body: JSON.stringify({ providers: providers.value.providers }),
    })
  })
}

async function testProvider(id: string) {
  if (!id) return
  await guard(async () => {
    const res: any = await request(`/models/providers/${id}/test`, { method: 'POST' })
    error.value = res.ok ? `连接成功：${res.model || id}` : `连接失败：${res.error || '未知错误'}`
  })
}

onMounted(refreshAll)
</script>

<style>
:root {
  color: #24211d;
  background: #f7f7f4;
  font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
}

* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; }
button, input, textarea, select { font: inherit; }
button { cursor: pointer; }

.app-shell {
  display: grid;
  grid-template-columns: 304px minmax(0, 1fr);
  height: 100vh;
  min-height: 660px;
  background: #fbfbf8;
}

.sidebar {
  display: grid;
  grid-template-rows: clamp(280px, 38vh, 360px) minmax(0, 1fr);
  gap: 16px;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  padding: 18px 16px 14px;
  border-right: 1px solid #dfded8;
  background: #f0f1ee;
}

.sidebar-top {
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid #dfded8;
  padding-bottom: 16px;
}

.brand {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 2px 2px 20px;
}

.brand div:first-child { display: grid; gap: 3px; }
.brand strong { font-size: 22px; line-height: 1; letter-spacing: 0; }
.brand span { font-size: 12px; color: #7a766f; }

.health-dot {
  width: 9px;
  height: 9px;
  margin-top: 4px;
  border-radius: 50%;
  background: #bbb8af;
}
.health-dot.ok { background: #5a8f6b; }

.primary-nav {
  position: relative;
  display: grid;
  gap: 9px;
  margin-top: auto;
  margin-bottom: auto;
}

.nav-indicator {
  position: absolute;
  z-index: 0;
  top: 0;
  left: 0;
  right: 0;
  height: 54px;
  border-radius: 8px;
  background: #24211d;
  box-shadow: 0 10px 24px rgba(36, 33, 29, .13);
  transition: transform 220ms cubic-bezier(.2, .8, .2, 1);
  pointer-events: none;
}

.primary-nav button {
  position: relative;
  z-index: 1;
  border: 0;
  border-radius: 8px;
  background: transparent;
  height: 54px;
  padding: 15px 14px;
  color: #6e6a63;
  font-size: 18px;
  font-weight: 760;
  text-align: left;
  letter-spacing: 0;
  transition: color 160ms ease, transform 160ms ease;
}

.primary-nav button.active {
  background: transparent;
  color: #fffdf9;
}

.primary-nav button:hover {
  color: #2f2a25;
}

.primary-nav button.active:hover {
  color: #fffdf9;
}

.sidebar-hint {
  min-height: 34px;
  display: flex;
  align-items: flex-end;
  padding: 14px 2px 0;
  color: #817c73;
  font-size: 12px;
  line-height: 1.45;
}

.context-panel {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 0;
}

.context-panel-inner {
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.panel-fade-enter-active,
.panel-fade-leave-active {
  transition: opacity 170ms ease, transform 170ms ease;
}

.panel-fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.panel-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #4f4b45;
  font-weight: 700;
}

.ghost-btn, .quiet-wide {
  border: 1px solid #d4d2ca;
  border-radius: 7px;
  background: #fbfbf8;
  padding: 6px 9px;
  color: #514d46;
}

.quiet-wide { width: 100%; }

.segmented {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px;
  padding: 3px;
  border: 1px solid #d8d6ce;
  border-radius: 8px;
  background: #e6e7e3;
}

.segmented button {
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 6px 8px;
  color: #69655e;
}

.segmented button.active {
  background: #fbfbf8;
  color: #24211d;
  box-shadow: 0 1px 2px rgba(36, 33, 29, .08);
}

.search-row input, input, textarea, select {
  width: 100%;
  border: 1px solid #d8d6ce;
  border-radius: 8px;
  background: #fffefb;
  color: #24211d;
  padding: 9px 10px;
  outline: none;
}

textarea { resize: vertical; line-height: 1.65; }

input:focus, textarea:focus, select:focus {
  border-color: #b96f4a;
  box-shadow: 0 0 0 3px rgba(185, 111, 74, .13);
}

.session-list, .memory-tier-list {
  min-height: 0;
  overflow: auto;
  display: grid;
  gap: 6px;
  padding-right: 2px;
}

.session-list.tight { max-height: 210px; }

.list-item {
  width: 100%;
  display: grid;
  gap: 4px;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  padding: 9px 10px;
  color: #24211d;
}

.list-item:hover, .list-item.selected {
  border-color: #d8d6ce;
  background: #fbfbf8;
}

.item-title {
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.item-meta {
  color: #7d786f;
  font-size: 12px;
  line-height: 1.45;
  max-height: 3.1em;
  overflow: hidden;
}

.context-label {
  margin-top: 4px;
  color: #8b867d;
  font-size: 12px;
  font-weight: 700;
}

.tier-group {
  display: grid;
  gap: 4px;
  padding-top: 3px;
}

.tier-group header {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 7px;
  padding: 7px 4px 3px;
  color: #5c574f;
  font-size: 12px;
  font-weight: 800;
}

.tier-code {
  border-radius: 5px;
  padding: 2px 6px;
  color: white;
  background: #24211d;
}

.tier-group.important .tier-code { background: #9a5f3f; }
.tier-group.normal .tier-code { background: #56766a; }
.tier-group.archive .tier-code { background: #70757d; }
.tier-group em { color: #8b867d; font-style: normal; }

.workspace {
  min-width: 0;
  height: 100vh;
  overflow: auto;
}

.workspace-fade-enter-active,
.workspace-fade-leave-active {
  transition: opacity 190ms ease, transform 190ms ease;
}

.workspace-fade-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.workspace-fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

.chat-page, .tool-page {
  min-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 18px;
  padding: 30px clamp(22px, 4vw, 54px);
}

.chat-page {
  max-width: 980px;
  margin: 0 auto;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e7e5df;
}

.page-head.compact { border-bottom: 0; padding-bottom: 0; }

.eyebrow {
  margin: 0 0 5px;
  color: #8b867d;
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

h1, h2, p { margin-top: 0; }
h1 { margin-bottom: 0; font-size: 28px; font-weight: 760; letter-spacing: 0; }
h2 { margin-bottom: 8px; font-size: 18px; letter-spacing: 0; }
p { color: #746f67; line-height: 1.6; }

.head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.head-actions button, .composer button, .primary-action, .merge-row button {
  border: 1px solid #cfcbc2;
  border-radius: 8px;
  background: #fffefb;
  color: #2c2823;
  padding: 9px 12px;
}

.head-actions button:hover, .composer button:hover, .primary-action:hover, .merge-row button:hover,
.ghost-btn:hover, .quiet-wide:hover {
  background: #f5f2ec;
}

button:disabled { cursor: not-allowed; opacity: .48; }
.danger { color: #a23b2a !important; border-color: #e5b8ad !important; }

.inline-check, .check-field {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #5f5a52;
  font-size: 13px;
}

.inline-check input, .check-field input { width: auto; }

.chat-surface {
  min-height: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto auto;
  gap: 12px;
}

.messages {
  min-height: 420px;
  overflow: auto;
  padding: 8px 2px 20px;
}

.empty-chat, .empty-state {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 340px;
  gap: 8px;
  border: 1px dashed #d8d6ce;
  border-radius: 8px;
  color: #777169;
  text-align: center;
  padding: 22px;
}

.empty-chat strong, .empty-state strong { color: #2c2823; }

.message {
  max-width: 760px;
  margin-bottom: 20px;
}

.message.user {
  margin-left: auto;
}

.message-role {
  margin-bottom: 6px;
  color: #8b867d;
  font-size: 12px;
  font-weight: 700;
}

.message.user .message-role { text-align: right; }

.message-body {
  white-space: pre-wrap;
  line-height: 1.72;
  border: 1px solid #e4e1d9;
  border-radius: 8px;
  background: #fffefb;
  padding: 13px 15px;
}

.message.user .message-body {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 10px;
  padding-top: 6px;
}

.composer textarea {
  min-height: 76px;
  max-height: 220px;
}

.soft-note {
  margin: 0;
  border-left: 3px solid #b96f4a;
  padding: 8px 10px;
  color: #5f5a52;
  background: #f6f1ec;
  border-radius: 6px;
}

.memory-stage, .model-panel {
  max-width: 960px;
}

.editor-panel, .model-panel, .replay-panel {
  border: 1px solid #e3e0d8;
  border-radius: 8px;
  background: #fffefb;
  padding: 20px;
}

.memory-heading {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.memory-heading p {
  margin: 0;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.priority-chip {
  flex: 0 0 auto;
  border-radius: 6px;
  padding: 5px 8px;
  color: #fff;
  background: #56766a;
  font-size: 12px;
  font-weight: 800;
}

.priority-chip.core { background: #24211d; }
.priority-chip.important { background: #9a5f3f; }
.priority-chip.archive { background: #70757d; }

label {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
  color: #5d574f;
  font-size: 13px;
  font-weight: 700;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.field-grid.three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.merge-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 10px;
}

.history-box {
  margin-top: 8px;
  border-top: 1px solid #ebe8e1;
  padding-top: 10px;
  color: #625d55;
}

.history-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f0eee8;
  font-size: 13px;
}

.history-item em { color: #8b867d; font-style: normal; }

.replay-panel header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid #ebe8e1;
  margin-bottom: 12px;
  padding-bottom: 10px;
}

.replay-panel header span { color: #8b867d; font-size: 13px; }

.replay-message {
  display: grid;
  gap: 6px;
  padding: 12px 0;
  border-bottom: 1px solid #f0eee8;
}

.replay-message p {
  margin: 0;
  color: #2d2924;
  white-space: pre-wrap;
}

.model-intro {
  margin-bottom: 16px;
}

.primary-action {
  margin-top: 4px;
  background: #24211d;
  color: #fffdf9;
  border-color: #24211d;
}

.primary-action:hover {
  background: #3a342e;
}

.toast {
  position: fixed;
  right: 22px;
  bottom: 22px;
  max-width: min(520px, calc(100vw - 44px));
  border: 0;
  border-radius: 8px;
  background: #24211d;
  color: #fffdf9;
  padding: 12px 14px;
  text-align: left;
  box-shadow: 0 12px 30px rgba(36, 33, 29, .2);
}

@media (prefers-reduced-motion: reduce) {
  .nav-indicator,
  .primary-nav button,
  .panel-fade-enter-active,
  .panel-fade-leave-active,
  .workspace-fade-enter-active,
  .workspace-fade-leave-active {
    transition: none;
  }
}

@media (max-width: 860px) {
  .app-shell {
    grid-template-columns: 1fr;
    height: auto;
  }

  .sidebar {
    position: static;
    border-right: 0;
    border-bottom: 1px solid #dfded8;
    max-height: none;
  }

  .workspace {
    height: auto;
  }

  .chat-page, .tool-page {
    padding: 22px 16px;
  }

  .page-head, .composer, .field-grid, .field-grid.three, .merge-row {
    grid-template-columns: 1fr;
  }

  .page-head {
    display: grid;
  }

  .head-actions {
    justify-content: flex-start;
  }
}
</style>
