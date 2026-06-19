<template>
  <div class="app-shell" :style="appLayoutStyle">
    <aside class="sidebar" :class="{ 'settings-mode': settingsOpen }">
      <Transition name="settings-shell">
      <div v-if="!settingsOpen" key="normal" class="sidebar-normal">
      <div class="sidebar-top">
        <header class="brand">
          <div>
            <strong>IwIw</strong>
            <span>个人智能体</span>
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
            <div>
              <span>会话</span>
              <em>项目与普通对话</em>
            </div>
            <div class="panel-create">
              <button class="ghost-btn" @click="handlePanelCreate">新建</button>
            </div>
          </div>
          <div class="chat-list-tabs" aria-label="会话分类">
            <span class="chat-list-indicator" :style="chatListIndicatorStyle"></span>
            <button :class="{ active: chatListMode === 'projects' }" @click="setChatListMode('projects')">项目</button>
            <button :class="{ active: chatListMode === 'threads' }" @click="setChatListMode('threads')">对话</button>
          </div>
          <Transition :name="chatListTransition" mode="out-in">
          <TransitionGroup v-if="chatListMode === 'projects'" key="projects" name="list-card" tag="div" class="project-list chat-list-page">
            <section v-for="project in projects" :key="project.id" class="project-group">
              <button class="project-row" :class="{ active: activeProject === project.id }" @click="toggleProject(project.id)">
                <span class="project-caret">{{ expandedProjectIds.has(project.id) ? '▾' : '▸' }}</span>
                <span class="project-text">
                  <span class="project-name">{{ project.name || '未命名项目' }}</span>
                  <span class="project-path">{{ project.path || '未绑定工作目录' }}</span>
                </span>
                <span class="project-count">{{ projectSessions(project.id).length }}</span>
              </button>
              <Transition name="folder">
                <TransitionGroup v-if="expandedProjectIds.has(project.id)" name="list-card" tag="div" class="session-list project-sessions">
                  <div
                    v-for="session in projectSessions(project.id)"
                    :key="session.id"
                    class="list-item session-row"
                    :class="{ selected: activeSession === session.id, pinned: session.pinned }"
                  >
                    <button class="session-main" @click="openSession(session.id)">
                      <span class="item-title">{{ session.pinned ? '置顶 · ' : '' }}{{ displaySessionTitle(session) }}</span>
                    </button>
                    <span class="session-time">{{ relativeTime(session.updated_at) }}</span>
                    <div class="item-actions" aria-label="更多操作">
                      <span>更多操作</span>
                      <button @click.stop="togglePinSession(session)">{{ session.pinned ? '取消置顶' : '置顶' }}</button>
                      <button @click.stop="archiveSession(session)">归档</button>
                      <button class="danger-link" @click.stop="deleteSession(session)">删除</button>
                    </div>
                  </div>
                  <button :key="`add-${project.id}`" class="quiet-wide add-session" @click="newSession(project.id)">在此项目新建会话</button>
                </TransitionGroup>
              </Transition>
            </section>
          </TransitionGroup>
          <TransitionGroup v-else key="threads" name="list-card" tag="div" class="project-list chat-list-page">
            <div
              v-for="session in chatSessions"
              :key="session.id"
              class="list-item session-row"
              :class="{ selected: activeSession === session.id, pinned: session.pinned }"
            >
              <button class="session-main" @click="openSession(session.id)">
                <span class="item-title">{{ session.pinned ? '置顶 · ' : '' }}{{ displaySessionTitle(session) }}</span>
              </button>
              <span class="session-time">{{ relativeTime(session.updated_at) }}</span>
              <div class="item-actions" aria-label="更多操作">
                <span>更多操作</span>
                <button @click.stop="togglePinSession(session)">{{ session.pinned ? '取消置顶' : '置顶' }}</button>
                <button @click.stop="archiveSession(session)">归档</button>
                <button class="danger-link" @click.stop="deleteSession(session)">删除</button>
              </div>
            </div>
            <button key="add-chat-session" class="quiet-wide add-session" @click="newSession()">新建对话</button>
          </TransitionGroup>
          </Transition>
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
              <span class="item-meta">{{ template.api_style }} · 事件流 · {{ template.models?.[0] || '自定义' }}</span>
            </button>
          </div>
          <div class="context-label">已配置</div>
          <div class="session-list">
            <button
              v-for="provider in providers.providers"
              :key="provider.id"
              class="list-item"
              :class="{ selected: activeProviderId === provider.id, current: providers.active_provider_id === provider.id }"
              @click="editProvider(provider)"
            >
              <span class="item-title">{{ provider.label || provider.id }}</span>
              <span class="item-meta">{{ provider.enabled ? '启用' : '停用' }} · {{ provider.streaming === false ? '非流式' : '事件流' }} · {{ provider.base_url || '未设置地址' }}</span>
              <span v-if="providers.active_provider_id === provider.id" class="item-badge">当前</span>
            </button>
          </div>
        </template>
          </div>
        </Transition>
      </section>
      <footer class="sidebar-footer">
        <button type="button" class="settings-entry" @click="openSettings">
          <span>设置</span>
        </button>
      </footer>
      </div>

      <section v-else key="settings" class="settings-sidebar">
        <header class="settings-sidebar-head">
          <div>
            <strong>设置</strong>
            <span>IwIw preferences</span>
          </div>
        </header>
        <nav class="settings-nav" aria-label="设置分组">
          <section v-for="group in settingsGroups" :key="group.id" class="settings-nav-group">
            <h2>{{ group.label }}</h2>
            <button
              v-for="item in group.items"
              :key="item.id"
              type="button"
              :class="{ active: activeSettingsSection === item.id }"
              @click="activeSettingsSection = item.id"
            >
              <span>{{ item.label }}</span>
              <em>{{ item.description }}</em>
            </button>
          </section>
        </nav>
        <footer class="settings-sidebar-footer">
          <button type="button" class="settings-exit" @click="closeSettings">返回</button>
        </footer>
      </section>
      </Transition>
    </aside>
    <div class="sidebar-resizer" title="调整导航栏宽度" @pointerdown="startSidebarResize"></div>

    <main class="workspace">
      <Transition name="workspace-fade">
      <section v-if="settingsOpen" key="settings" class="settings-page">
        <header class="page-head">
          <div>
            <p class="eyebrow">Settings</p>
            <h1>{{ activeSettingsMeta.label }}</h1>
          </div>
          <div class="head-actions">
            <button type="button" @click="closeSettings">返回当前窗口</button>
          </div>
        </header>

        <Transition name="settings-content" mode="out-in">
        <section v-if="activeSettingsSection === 'general'" key="settings-general" class="settings-panel">
          <div class="settings-card">
            <h2>启动与默认行为</h2>
            <label>
              启动页面
              <select v-model="tab">
                <option value="chat">对话</option>
                <option value="memory">记忆</option>
                <option value="models">模型</option>
              </select>
            </label>
            <label class="check-field">
              <input type="checkbox" v-model="privateMode" />
              新消息默认标记为私密
            </label>
          </div>
          <div class="settings-card">
            <h2>当前状态</h2>
            <div class="setting-stats">
              <div><strong>{{ projects.length }}</strong><span>项目</span></div>
              <div><strong>{{ sessions.length }}</strong><span>会话</span></div>
              <div><strong>{{ memories.length }}</strong><span>长期记忆</span></div>
            </div>
          </div>
        </section>

        <section v-else-if="activeSettingsSection === 'agent'" key="settings-agent" class="settings-panel">
          <div class="settings-card">
            <h2>默认对话策略</h2>
            <div class="settings-choice-grid">
              <button type="button" :class="{ active: chatMode === 'companion' }" @click="chatMode = 'companion'">
                <strong>陪我想想</strong>
                <span>更重视承接、澄清与共同整理想法。</span>
              </button>
              <button type="button" :class="{ active: chatMode === 'work' }" @click="chatMode = 'work'">
                <strong>工作模式</strong>
                <span>更明确地拆解问题、推进任务和交付结果。</span>
              </button>
              <button type="button" :class="{ active: chatMode === 'plan' }" @click="chatMode = 'plan'">
                <strong>计划模式</strong>
                <span>先形成方案与边界，再进入实现。</span>
              </button>
            </div>
          </div>
          <div class="settings-card">
            <h2>操作权限</h2>
            <div class="settings-choice-grid permissions">
              <button
                v-for="option in approvalOptions"
                :key="option.id"
                type="button"
                :class="{ active: approvalMode === option.id }"
                @click="approvalMode = option.id"
              >
                <strong>{{ option.label }}</strong>
                <span>{{ option.description }}</span>
              </button>
            </div>
          </div>
        </section>

        <section v-else-if="activeSettingsSection === 'interface'" key="settings-interface" class="settings-panel">
          <div class="settings-card">
            <h2>布局</h2>
            <label>
              左侧栏宽度
              <input v-model.number="sidebarWidth" type="range" :min="MIN_SIDEBAR_WIDTH" :max="MAX_SIDEBAR_WIDTH" />
            </label>
            <label>
              对话栏宽度
              <input v-model.number="chatWidth" type="range" :min="MIN_CHAT_WIDTH" :max="Math.max(MIN_CHAT_WIDTH, availableMainWidth() - MIN_REVIEW_WIDTH - 4)" />
            </label>
          </div>
          <div class="settings-card">
            <h2>编辑器</h2>
            <label>
              默认主题
              <select v-model="editorTheme">
                <option value="light">浅色</option>
                <option value="dark">深色</option>
                <option value="paper">纸张</option>
              </select>
            </label>
          </div>
          <div class="settings-card wide">
            <h2>欢迎词</h2>
            <div class="welcome-editor">
              <article v-for="prompt in welcomePrompts" :key="prompt.id" class="welcome-row">
                <label>
                  标题
                  <input v-model="prompt.title" @input="saveWelcomePrompts" />
                </label>
                <label>
                  内容
                  <textarea v-model="prompt.body" rows="2" @input="saveWelcomePrompts"></textarea>
                </label>
                <button type="button" class="danger" :disabled="welcomePrompts.length <= 1" @click="deleteWelcomePrompt(prompt.id)">删除</button>
              </article>
            </div>
            <button type="button" class="quiet-wide" @click="addWelcomePrompt">新增欢迎词</button>
          </div>
        </section>

        <section v-else-if="activeSettingsSection === 'shortcuts'" key="settings-shortcuts" class="settings-panel">
          <div class="settings-card">
            <h2>发送</h2>
            <div class="settings-choice-grid">
              <button type="button" :class="{ active: sendShortcut === 'enter' }" @click="sendShortcut = 'enter'">
                <strong>Enter 发送</strong>
                <span>按 Enter 直接发送，Shift + Enter 换行。</span>
              </button>
              <button type="button" :class="{ active: sendShortcut === 'mod-enter' }" @click="sendShortcut = 'mod-enter'">
                <strong>Ctrl / Cmd + Enter</strong>
                <span>Enter 保持换行，用组合键发送。</span>
              </button>
            </div>
          </div>
        </section>

        <section v-else-if="activeSettingsSection === 'memory'" key="settings-memory" class="settings-panel">
          <div class="settings-card">
            <h2>记忆入口</h2>
            <div class="settings-choice-grid">
              <button type="button" :class="{ active: memoryMode === 'long' }" @click="memoryMode = 'long'">
                <strong>长期记忆</strong>
                <span>默认显示 L0 / L1 / L2 / L3 的事实记忆。</span>
              </button>
              <button type="button" :class="{ active: memoryMode === 'session' }" @click="memoryMode = 'session'">
                <strong>会话记忆</strong>
                <span>默认进入搜索与历史回放。</span>
              </button>
            </div>
          </div>
          <div class="settings-card">
            <h2>维护</h2>
            <button type="button" class="primary-action" @click="rebuildIndex">重建长期记忆索引</button>
          </div>
        </section>

        <section v-else key="settings-data" class="settings-panel">
          <div class="settings-card">
            <h2>本地数据</h2>
            <div class="settings-path-list">
              <div><span>会话库</span><code>{{ health?.session_memory?.db_path || '等待连接' }}</code></div>
              <div><span>旧库迁移</span><code>{{ health?.legacy_import?.available ? '可用' : '未检测到旧库' }}</code></div>
            </div>
          </div>
          <div class="settings-card">
            <h2>安全边界</h2>
            <p>工作区浏览默认排除记忆、密钥、数据库、日志和构建产物。危险操作后续应进入审查与权限队列。</p>
          </div>
        </section>
        </Transition>
      </section>

      <section v-else-if="tab === 'chat'" key="chat" class="chat-page" :class="{ reviewing: workbenchOpen }">
        <section class="chat-layout" :style="chatLayoutStyle">
          <Transition name="review-pop">
            <section
              v-if="workbenchOpen"
              class="workbench-column"
              :class="{ 'review-visible': reviewOpen, 'terminal-visible': terminalOpen, 'split-visible': reviewOpen && terminalOpen }"
              :style="workbenchStyle"
              aria-label="审查工作区"
            >
            <aside v-if="reviewOpen" class="review-panel" aria-label="会话审查">
              <header class="review-head">
                <div class="review-tabs" role="tablist" aria-label="审查标签页">
                  <button
                    v-for="pane in reviewPanes"
                    :key="pane.id"
                    type="button"
                    class="review-tab"
                    :class="{ active: activeReviewPaneId === pane.id, file: pane.kind === 'files' && Boolean(openWorkspaceFile) }"
                    @click="activeReviewPaneId = pane.id"
                  >
                    <template v-if="pane.kind === 'files' && openWorkspaceFile">
                      <span class="review-tab-title">{{ openWorkspaceFile.name }}</span>
                    </template>
                    <template v-else>
                      <span class="review-tab-title">{{ pane.title }}</span>
                    </template>
                  </button>
                  <div class="review-add">
                    <button type="button" class="icon-btn" title="打开工具" @click="reviewAddOpen = !reviewAddOpen">+</button>
                    <div v-if="reviewAddOpen" class="review-add-menu">
                      <button type="button" @click="openReviewPane('files')">文件</button>
                    </div>
                  </div>
                </div>
              </header>

              <section v-if="activeReviewPane?.kind === 'overview'" class="review-pane overview-pane">
                <div class="review-summary">
                  <div>
                    <strong>{{ reviewStats.messages }}</strong>
                    <span>消息</span>
                  </div>
                  <div>
                    <strong>{{ reviewStats.user }}</strong>
                    <span>用户</span>
                  </div>
                  <div>
                    <strong>{{ reviewStats.assistant }}</strong>
                    <span>回应</span>
                  </div>
                  <div>
                    <strong>{{ reviewStats.private }}</strong>
                    <span>私密</span>
                  </div>
                </div>

                <div class="review-section">
                  <div class="review-section-title">
                    <span>审查项</span>
                    <em>{{ reviewItems.length }}</em>
                  </div>
                  <article v-for="item in reviewItems" :key="item.id" class="review-item" :class="item.tone">
                    <div class="review-item-head">
                      <span class="review-chip">{{ item.label }}</span>
                      <strong>{{ item.title }}</strong>
                    </div>
                    <p>{{ item.body }}</p>
                  </article>
                </div>

                <div class="review-section">
                  <div class="review-section-title">
                    <span>运行轨迹</span>
                    <em>{{ latestAgentRun ? latestAgentRun.status : '等待' }}</em>
                  </div>
                  <article v-if="latestAgentRun" class="run-trace-card">
                    <div class="run-trace-head">
                      <span>{{ latestAgentRun.intent }}</span>
                      <strong>{{ latestAgentRun.path }}</strong>
                      <em>{{ latestAgentRun.model_role }}</em>
                    </div>
                    <p>{{ latestAgentRun.reason || '本轮暂无可展示的运行理由。' }}</p>
                    <div v-if="latestAgentRun.metadata?.policy" class="policy-strip">
                      <span :class="`risk-${latestAgentRun.metadata.policy.risk_level}`">
                        {{ riskLabel(latestAgentRun.metadata.policy.risk_level) }}
                      </span>
                      <em>{{ latestAgentRun.metadata.policy.requires_confirmation ? '需要确认' : '可直接回应' }}</em>
                      <small>{{ latestAgentRun.metadata.policy.guidance }}</small>
                    </div>
                    <div v-if="latestAgentRun.context_sections?.length" class="context-chip-list">
                      <span v-for="section in latestAgentRun.context_sections" :key="section">{{ section }}</span>
                    </div>
                    <div v-if="agentRunTimeline.length" class="timeline-list">
                      <div v-for="event in agentRunTimeline.slice(0, 3)" :key="event.id" class="timeline-row">
                        <span>{{ event.kind }}</span>
                        <strong>{{ event.title }}</strong>
                        <em>{{ event.summary }}</em>
                      </div>
                    </div>
                    <div v-if="agentRunEvents.length" class="run-event-list">
                      <div v-for="event in agentRunEvents" :key="event.id" class="run-event">
                        <span>{{ event.type }}</span>
                        <strong>{{ event.title || '运行事件' }}</strong>
                      </div>
                    </div>
                  </article>
                  <p v-else class="soft-note">发送一条消息后，这里会显示 IwIw 本轮如何判断模式、组装上下文和调用模型。</p>
                </div>

                <div class="review-section">
                  <div class="review-section-title">
                    <span>建议动作</span>
                  </div>
                  <div class="review-actions">
                    <button type="button" :disabled="!activeSession" @click="consolidate">整理上下文</button>
                    <button type="button" :disabled="messages.length === 0" @click="scrollMessages">回到上下文</button>
                  </div>
                </div>
              </section>

              <section
                v-else-if="activeReviewPane?.kind === 'files'"
                class="review-pane files-pane"
                :class="{ selecting: fileExplorerOpen, previewing: !fileExplorerOpen && openWorkspaceFile }"
              >
                <aside v-if="fileExplorerOpen" class="file-sidebar">
                  <div class="file-toolbar">
                    <button type="button" :disabled="!workspacePath" @click="openWorkspaceParent">上一级</button>
                    <button type="button" @click="loadWorkspaceFiles(workspacePath)">刷新</button>
                  </div>
                  <div class="file-path">{{ workspacePath || '工作区' }}</div>
                  <div class="file-list">
                    <div v-if="workspaceDirs.length === 0 && workspaceFiles.length === 0" class="file-empty small">
                      当前目录没有可预览文件。
                    </div>
                    <button
                      v-for="dir in workspaceDirs"
                      :key="dir.path"
                      type="button"
                      class="file-row dir"
                      @click="loadWorkspaceFiles(dir.path)"
                    >
                      <span>▸</span>
                      <strong>{{ dir.name }}</strong>
                    </button>
                    <button
                      v-for="file in workspaceFiles"
                      :key="file.path"
                      type="button"
                      class="file-row"
                      :class="{ selected: openWorkspaceFile?.path === file.path }"
                      @click="readWorkspaceFile(file.path)"
                    >
                      <span>·</span>
                      <strong>{{ file.name }}</strong>
                    </button>
                  </div>
                </aside>
                <article class="file-preview">
                  <header>
                    <div>
                      <p class="eyebrow">{{ editorLanguage }}</p>
                      <h2>{{ openWorkspaceFile?.name || '选择一个文件' }}</h2>
                      <span v-if="openWorkspaceFile" class="file-full-path">{{ openWorkspaceFileFullPath }}</span>
                    </div>
                    <div v-if="openWorkspaceFile" class="file-preview-actions">
                      <span>{{ editorDirty ? '未保存' : `${openWorkspaceFile.size} bytes` }}</span>
                      <button type="button" :disabled="!editorDirty" @click="saveWorkspaceFile">保存</button>
                      <button type="button" @click="openFileExplorer">重新选择</button>
                    </div>
                  </header>
                  <div v-if="openWorkspaceFile" class="file-editor-shell" :class="editorTheme">
                    <div class="file-editor-toolbar">
                      <div v-if="isMarkdownFile" class="editor-segmented">
                        <button type="button" :class="{ active: editorView === 'edit' }" @click="editorView = 'edit'">编辑</button>
                        <button type="button" :class="{ active: editorView === 'preview' }" @click="editorView = 'preview'">预览</button>
                      </div>
                      <select v-model="editorTheme" title="编辑器主题">
                        <option value="light">浅色</option>
                        <option value="dark">深色</option>
                        <option value="paper">纸张</option>
                      </select>
                      <select v-model="editorPrompt" title="常用提示">
                        <option value="">常用提示</option>
                        <option v-for="prompt in editorPrompts" :key="prompt.id" :value="prompt.text">{{ prompt.label }}</option>
                      </select>
                    </div>
                    <p v-if="editorPrompt" class="editor-prompt">{{ editorPrompt }}</p>
                    <textarea
                      v-if="editorView === 'edit'"
                      v-model="editorContent"
                      class="file-editor-textarea"
                      spellcheck="false"
                    ></textarea>
                    <article v-else class="markdown-preview" v-html="markdownPreviewHtml"></article>
                  </div>
                  <div v-else class="file-empty">从左侧选择文件进行审查。</div>
                </article>
              </section>

            </aside>
            <div
              v-if="reviewOpen && terminalOpen"
              class="terminal-resizer"
              title="调整编辑器和终端高度"
              @pointerdown="startTerminalResize"
            ></div>
            <section v-if="terminalOpen" class="terminal-dock" aria-label="终端">
              <header>
                <span>Terminal</span>
              </header>
              <pre>终端面板已放置在中间栏下侧。
后续会接入可审计的命令运行、输出记录和权限确认。</pre>
            </section>
            </section>
          </Transition>
          <div v-if="workbenchOpen" class="column-resizer" title="调整审查和对话宽度" @pointerdown="startChatResize"></div>

          <section class="chat-column">
            <header class="page-head compact">
              <div>
                <p class="eyebrow">{{ activeSessionTitle }}</p>
                <h1>对话</h1>
              </div>
              <div class="head-actions">
                <button type="button" :class="{ active: reviewOpen }" :disabled="!activeSession" :aria-pressed="reviewOpen" @click="toggleReviewWorkbench">审查</button>
              </div>
            </header>

          <section class="chat-surface">
            <Transition name="chat-content" mode="out-in">
              <div :key="chatViewKey" ref="messageBox" class="messages">
                <div v-if="messages.length === 0" class="empty-chat">
                  <strong>{{ currentWelcomePrompt.title || '我们该做什么' }}</strong>
                  <span>{{ currentWelcomePrompt.body || '开始今天的工作' }}</span>
                </div>
                <article
                  v-for="message in messages"
                  :key="message.id || message.turn_idx"
                  class="message"
                  :class="[message.role, { pending: message.pending, streaming: message.streaming }]"
                >
                  <div class="message-role">{{ message.role === 'user' ? '你' : 'IwIw' }}</div>
                  <div v-if="message.role === 'assistant' && messageMetaText(message)" class="message-meta">
                    {{ messageMetaText(message) }}
                  </div>
                  <div
                    class="message-body"
                    :class="{ 'markdown-body': message.role === 'assistant' }"
                    v-html="renderMessageContent(message)"
                  ></div>
                </article>
              </div>
            </Transition>

            <form class="composer" @submit.prevent="sendMessage">
              <div v-if="composerAttachments.length" class="composer-attachments">
                <button
                  v-for="item in composerAttachments"
                  :key="item.id"
                  type="button"
                  class="attachment-chip"
                  :title="item.name"
                  @click="removeAttachment(item.id)"
                >
                  <span>{{ item.kind === 'folder' ? '文件夹' : item.kind === 'draft' ? '草稿' : '文件' }}</span>
                  <strong>{{ item.name }}</strong>
                  <em>×</em>
                </button>
              </div>
              <textarea
                ref="composerTextarea"
                v-model="chatInput"
                placeholder="把想说的话放在这里..."
                rows="2"
                @input="adjustComposerHeight"
                @keydown="handleComposerKeydown"
              ></textarea>
              <div class="composer-bar">
                <div class="composer-tools">
                  <div class="composer-add">
                    <button type="button" class="tool-pill" @click="toggleAttachmentMenu">＋ 添加</button>
                    <div v-if="attachmentMenuOpen" class="composer-menu">
                      <button type="button" @click="triggerFilePicker">上传文件</button>
                      <button type="button" @click="triggerFolderPicker">上传文件夹</button>
                      <button type="button" @click="createDraftFile">创建文件</button>
                    </div>
                  </div>
                  <div class="mode-menu-wrap">
                    <button
                      type="button"
                      class="tool-pill mode-trigger"
                      aria-label="模式选择"
                      @click="toggleModeMenu"
                    >
                      <span>{{ chatModeCurrent.label }}</span>
                      <span class="pill-chevron" aria-hidden="true">⌄</span>
                    </button>
                    <div v-if="modeMenuOpen" class="mode-menu" role="menu">
                      <button
                        v-for="option in chatModeOptions"
                        :key="option.id"
                        type="button"
                        class="mode-option"
                        :class="{ selected: chatMode === option.id }"
                        @click="selectChatMode(option.id)"
                      >
                        <span>
                          <strong>{{ option.label }}</strong>
                          <em>{{ option.description }}</em>
                        </span>
                        <span v-if="chatMode === option.id" class="mode-check">✓</span>
                      </button>
                    </div>
                  </div>
                  <div class="approval-menu-wrap">
                    <button
                      type="button"
                      class="tool-pill approval-trigger"
                      :class="approvalMode"
                      aria-label="应如何批准 IwIw 的操作"
                      @click="toggleApprovalMenu"
                    >
                      <span>{{ approvalCurrent.label }}</span>
                      <span class="pill-chevron" aria-hidden="true">⌄</span>
                    </button>
                    <div v-if="approvalMenuOpen" class="approval-menu" role="menu">
                      <button
                        v-for="option in approvalOptions"
                        :key="option.id"
                        type="button"
                        class="approval-option"
                        :class="{ selected: approvalMode === option.id }"
                        @click="selectApprovalMode(option.id)"
                      >
                        <span class="approval-icon">{{ option.icon }}</span>
                        <span>
                          <strong>{{ option.label }}</strong>
                          <em>{{ option.description }}</em>
                        </span>
                        <span v-if="approvalMode === option.id" class="approval-check">✓</span>
                      </button>
                    </div>
                  </div>
                </div>
                <div class="composer-actions">
                  <button
                    type="button"
                    class="tool-pill private-toggle"
                    :class="{ active: privateMode }"
                    :aria-pressed="privateMode"
                    title="开启后，本次消息不会进入自动记忆整理"
                    @click="privateMode = !privateMode"
                  >
                    私密
                  </button>
                  <button class="send-button" :disabled="sending || !chatInput.trim()" aria-label="发送">
                    {{ sending ? '…' : '↑' }}
                  </button>
                </div>
              </div>
              <input ref="fileInput" class="hidden-input" type="file" multiple @change="handleAttachmentChange($event, 'file')" />
              <input ref="folderInput" class="hidden-input" type="file" multiple webkitdirectory directory @change="handleAttachmentChange($event, 'folder')" />
            </form>
            <p v-if="lastConsolidation" class="soft-note">{{ consolidationText }}</p>
          </section>
          </section>
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
              <strong>{{ message.role === 'user' ? '你' : 'IwIw' }}</strong>
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
            <span v-if="modelTestResult" class="model-test-status" :class="modelTestResult.status">
              {{ modelTestResult.text }}
            </span>
            <button :disabled="modelTesting || !providerForm.id" @click="testProvider">
              {{ modelTesting ? '测试中' : '测试连接' }}
            </button>
            <button @click="saveProviders">保存配置</button>
          </div>
        </header>

        <Transition name="model-content" mode="out-in">
          <section :key="modelViewKey" class="model-panel">
            <div class="model-intro">
              <h2>{{ providerForm.id ? '模型配置' : '选择左侧模板开始' }}</h2>
              <p>优先使用预设模板；需要非标准服务时，再从自定义模型入口补充。当前使用：{{ currentProviderLabel }}</p>
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
              <label class="check-field">
                <input type="checkbox" v-model="providerForm.streaming" />
                使用流式事件
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
            <div class="model-actions">
              <button class="primary-action" @click="upsertProvider">加入或更新配置</button>
              <button
                type="button"
                class="secondary-action"
                :class="{ active: providers.active_provider_id === providerForm.id }"
                :disabled="!providerForm.id"
                @click="setActiveProvider(providerForm.id)"
              >
                {{ providers.active_provider_id === providerForm.id ? '当前使用' : '设为当前模型' }}
              </button>
            </div>
          </section>
        </Transition>
      </section>
      </Transition>

      <button v-if="error" class="toast" @click="error = ''">{{ error }}</button>
    </main>

    <div v-if="projectDialogOpen" class="dialog-backdrop" @click.self="closeProjectDialog">
      <section class="project-dialog">
        <header>
          <div>
            <p class="eyebrow">Project</p>
            <h2>选择工作目录</h2>
          </div>
          <button type="button" class="icon-btn" title="关闭" @click="closeProjectDialog">×</button>
        </header>

        <div class="project-dialog-body">
          <div class="file-toolbar">
            <button type="button" :disabled="!projectPickerPath" @click="openProjectPickerParent">上一级</button>
            <button type="button" @click="loadProjectPicker(projectPickerPath)">刷新</button>
          </div>
          <div class="file-path-bar">
            <div class="file-path">{{ projectPickerPath || '此电脑' }}</div>
            <div class="path-actions">
              <button type="button" :disabled="!projectPickerPath" @click="revealProjectDirectory">定位</button>
              <button type="button" @click="pickProjectDirectory">选择目录</button>
              <button type="button" :disabled="!projectPickerPath || isProjectFavorite(projectPickerPath)" @click="addProjectFavorite(projectPickerPath)">
                {{ isProjectFavorite(projectPickerPath) ? '已收藏' : '收藏当前' }}
              </button>
            </div>
          </div>
          <div class="project-picker-list">
            <div
              v-for="dir in projectPickerDirs"
              :key="dir.path"
              role="button"
              tabindex="0"
              class="file-row dir"
              :class="{ selected: selectedProjectPath === dir.path }"
              @click="selectProjectDirectory(dir.path)"
              @dblclick="loadProjectPicker(dir.path)"
              @keydown.enter.prevent="selectProjectDirectory(dir.path)"
            >
              <span class="dir-glyph">▸</span>
              <input
                v-if="projectRenamePath === dir.path"
                v-model="projectRenameName"
                class="project-rename-input"
                placeholder="Project"
                :ref="setProjectRenameInput"
                @click.stop
                @keydown.enter.prevent="commitProjectRename"
                @keydown.esc.prevent="cancelProjectRename"
                @blur="commitProjectRename"
              />
              <strong v-else>{{ dir.name }}</strong>
              <span class="dir-path">{{ dir.path }}</span>
              <span class="project-dir-actions">
                <button type="button" class="open-dir" @click.stop="loadProjectPicker(dir.path)">打开</button>
                <button
                  type="button"
                  class="favorite-dir"
                  :disabled="isProjectFavorite(dir.path)"
                  @click.stop="addProjectFavorite(dir.path)"
                >
                  {{ isProjectFavorite(dir.path) ? '已收藏' : '收藏' }}
                </button>
                <button
                  v-if="projectPickerPath"
                  type="button"
                  class="delete-dir"
                  title="删除文件夹"
                  @pointerdown.stop.prevent="confirmProjectDirectoryDelete(dir)"
                  @click.stop="confirmProjectDirectoryDelete(dir)"
                >
                  删除
                </button>
              </span>
            </div>
            <div v-if="projectPickerDirs.length === 0" class="file-empty small">当前目录没有可选子目录。</div>
          </div>
          <div v-if="projectDeleteTarget" class="directory-delete-confirm">
            <div>
              <strong>删除这个文件夹？</strong>
              <span>{{ projectDeleteTarget.path }}</span>
              <em>只会删除空文件夹；如果里面已有内容，IwIw 会拒绝删除。</em>
            </div>
            <div class="directory-delete-actions">
              <button type="button" @click="cancelProjectDirectoryDelete">取消</button>
              <button type="button" class="danger-action" :disabled="projectDeletePending" @click="deleteProjectDirectory">
                {{ projectDeletePending ? '删除中' : '确认删除' }}
              </button>
            </div>
          </div>
        </div>

        <footer>
          <button type="button" @click="closeProjectDialog">取消</button>
          <button type="button" :disabled="!projectPickerPath" @click="createDirectoryForProject">创建目录</button>
          <button type="button" class="primary-action" :disabled="!projectPickerPath && !selectedProjectPath" @click="createProjectFromSelection">选择目录</button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { request, streamRequest } from './api'

type Tab = 'chat' | 'memory' | 'models'
type MemoryMode = 'long' | 'session'
type SettingsSection = 'general' | 'agent' | 'interface' | 'shortcuts' | 'memory' | 'data'
type ChatListMode = 'projects' | 'threads'
type ChatProject = {
  id: string
  name: string
  path: string
  pinned: number | boolean
  archived: number | boolean
  active_session_count?: number
  session_count?: number
}
type ChatSession = {
  id: string
  project_id: string
  title: string
  source: string
  scope?: 'project' | 'chat'
  updated_at: string
  turn_count: number
  pinned: number | boolean
  archived: number | boolean
  last_consolidated_turn?: number
  cycle_count?: number
  rolling_summary?: string
}
type ReviewItem = {
  id: string
  label: string
  title: string
  body: string
  tone: 'ok' | 'warn' | 'note'
}
type ReviewPaneKind = 'overview' | 'files'
type ReviewPane = {
  id: string
  title: string
  kind: ReviewPaneKind
}
type WorkspaceItem = {
  name: string
  path: string
  kind: 'directory' | 'file'
  size?: number
  updated_at: number | null
  favorite?: boolean
}
type WorkspaceFile = {
  name: string
  path: string
  full_path?: string
  content: string
  size: number
}
type AgentRun = {
  id: string
  session_id: string
  status: string
  intent: string
  path: string
  reason: string
  model_role: string
  started_at: string
  ended_at?: string
  context_sections: string[]
  metadata: Record<string, any>
  error?: string
}
type AgentTimelineEvent = {
  id: number
  session_id: string
  run_id?: string
  turn_idx?: number
  ts: string
  kind: string
  title: string
  summary: string
  risk_level: 'low' | 'medium' | 'high'
  reversible: boolean
  status: string
  details: Record<string, any>
}
type AgentEvent = {
  id: number
  run_id: string
  session_id: string
  ts: string
  type: string
  title: string
  details: Record<string, any>
}
type ChatMode = 'companion' | 'work' | 'plan'
type ApprovalMode = 'ask' | 'auto' | 'full'
type SendShortcut = 'enter' | 'mod-enter'
type EditorView = 'edit' | 'preview'
type EditorTheme = 'light' | 'dark' | 'paper'
type ComposerAttachment = {
  id: string
  name: string
  kind: 'file' | 'folder' | 'draft'
  size?: number
}
type Provider = {
  id: string
  label: string
  api_style: 'anthropic' | 'openai'
  base_url: string
  api_key_env?: string
  enabled: boolean
  streaming?: boolean
  models: string[]
  defaults: { chat: string; summary: string; memory: string }
}
type ProvidersState = {
  active_provider_id: string
  providers: Provider[]
}
type ModelTestResult = {
  status: 'pending' | 'ok' | 'error'
  text: string
}
type WelcomePrompt = {
  id: string
  title: string
  body: string
}
type ChatModeOption = {
  id: ChatMode
  label: string
  description: string
}

const tabs = [
  { id: 'chat', label: '对话' },
  { id: 'memory', label: '记忆' },
  { id: 'models', label: '模型' },
] as const

const settingsGroups: Array<{
  id: string
  label: string
  items: Array<{ id: SettingsSection; label: string; description: string }>
}> = [
  {
    id: 'basic',
    label: '基础',
    items: [
      { id: 'general', label: '常规', description: '启动、私密与当前状态' },
      { id: 'interface', label: '界面', description: '布局、宽度与编辑器主题' },
      { id: 'shortcuts', label: '快捷键', description: '发送、换行与输入行为' },
    ],
  },
  {
    id: 'agent',
    label: '智能体',
    items: [
      { id: 'agent', label: '行为与权限', description: '对话策略和操作批准' },
      { id: 'memory', label: '记忆', description: '默认入口和索引维护' },
    ],
  },
  {
    id: 'system',
    label: '系统',
    items: [
      { id: 'data', label: '数据与安全', description: '本地路径和访问边界' },
    ],
  },
]

const tiers = [
  { id: 'core', level: 'L0', name: '核心' },
  { id: 'important', level: 'L1', name: '重要' },
  { id: 'normal', level: 'L2', name: '日常' },
  { id: 'archive', level: 'L3', name: '归档' },
] as const

const tab = ref<Tab>('chat')
const memoryMode = ref<MemoryMode>('long')
const chatListMode = ref<ChatListMode>('projects')
const chatListTransition = ref('chat-slide-left')
const settingsOpen = ref(false)
const activeSettingsSection = ref<SettingsSection>('general')
const error = ref('')
const health = ref<any>(null)
const messageBox = ref<HTMLElement | null>(null)
const composerTextarea = ref<HTMLTextAreaElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
const projectDialogOpen = ref(false)
const projectPickerPath = ref('')
const projectPickerParentPath = ref('')
const projectPickerDirs = ref<WorkspaceItem[]>([])
const projectFavoritePaths = ref<Set<string>>(new Set())
const selectedProjectPath = ref('')
const projectRenamePath = ref('')
const projectRenameName = ref('')
const projectRenameInput = ref<HTMLInputElement | null>(null)
const projectDeleteTarget = ref<WorkspaceItem | null>(null)
const projectDeletePending = ref(false)

const MIN_SIDEBAR_WIDTH = 240
const MAX_SIDEBAR_WIDTH = 460
const MIN_REVIEW_WIDTH = 360
const MIN_CHAT_WIDTH = 320
const DEFAULT_SIDEBAR_WIDTH = 304
const DEFAULT_CHAT_WIDTH = 430
const MIN_TERMINAL_HEIGHT = 160
const MIN_EDITOR_HEIGHT = 260
const DEFAULT_TERMINAL_HEIGHT = 240
const WELCOME_PROMPTS_KEY = 'iwiw.welcomePrompts'
const WELCOME_PROMPT_INDEX_KEY = 'iwiw.welcomePromptIndex'
const defaultWelcomePrompts: WelcomePrompt[] = [
  { id: 'default-work', title: '我们该做什么', body: '开始今天的工作' },
  { id: 'default-think', title: '先把问题放在这里', body: '我会和你一起拆开它。' },
  { id: 'default-quiet', title: '从一句话开始也可以', body: '不用整理好再说。' },
  { id: 'default-plan', title: '今天想推进哪一件事', body: '我们可以先定边界，再慢慢往前走。' },
]

const projects = ref<ChatProject[]>([])
const sessions = ref<ChatSession[]>([])
const activeSession = ref('')
const activeProject = ref('')
const expandedProjectIds = ref<Set<string>>(new Set())
const messages = ref<any[]>([])
const chatViewKey = ref('empty')
const chatInput = ref('')
const welcomePrompts = ref<WelcomePrompt[]>([])
const currentWelcomePrompt = ref<WelcomePrompt>(defaultWelcomePrompts[0])
const sending = ref(false)
const sendingNow = ref(Date.now())
let sendingTimer: number | null = null
let assistantRevealTimer: number | null = null
let assistantRevealToken = 0
let assistantRevealResolve: (() => void) | null = null
const privateMode = ref(false)
const sidebarWidth = ref(DEFAULT_SIDEBAR_WIDTH)
const chatWidth = ref(DEFAULT_CHAT_WIDTH)
const chatMode = ref<ChatMode>('companion')
const sendShortcut = ref<SendShortcut>('enter')
const modeMenuOpen = ref(false)
const approvalMode = ref<ApprovalMode>('ask')
const approvalMenuOpen = ref(false)
const attachmentMenuOpen = ref(false)
const composerAttachments = ref<ComposerAttachment[]>([])
const lastConsolidation = ref<any>(null)
const latestAgentRun = ref<AgentRun | null>(null)
const agentRunEvents = ref<AgentEvent[]>([])
const agentRunTimeline = ref<AgentTimelineEvent[]>([])
const reviewOpen = ref(false)
const reviewPanes = ref<ReviewPane[]>([{ id: 'overview', title: '审查', kind: 'overview' }])
const activeReviewPaneId = ref('overview')
const reviewAddOpen = ref(false)
const terminalOpen = ref(false)
const terminalHeight = ref(DEFAULT_TERMINAL_HEIGHT)
const workspaceProjectId = ref('')
const workspacePath = ref('')
const workspaceDirs = ref<WorkspaceItem[]>([])
const workspaceFiles = ref<WorkspaceItem[]>([])
const openWorkspaceFile = ref<WorkspaceFile | null>(null)
const fileExplorerOpen = ref(false)
const editorContent = ref('')
const editorView = ref<EditorView>('edit')
const editorTheme = ref<EditorTheme>('light')
const editorPrompt = ref('')
const editorPrompts = [
  { id: 'explain', label: '解释当前文件', text: '请解释当前文件的结构、职责和关键逻辑。' },
  { id: 'review', label: '审查潜在问题', text: '请审查当前文件中可能的错误、边界条件和维护风险。' },
  { id: 'refactor', label: '提出重构方案', text: '请在不改变行为的前提下，提出当前文件的重构方案。' },
  { id: 'python', label: 'Python 检查', text: '请重点检查 Python 类型、异常处理、路径处理和测试缺口。' },
  { id: 'markdown', label: 'Markdown 润色', text: '请检查 Markdown 文档的结构、可读性、标题层级和措辞噪声。' },
]

const chatModeOptions: ChatModeOption[] = [
  { id: 'companion', label: '陪我想想', description: '先承接表达，再一起整理思路。' },
  { id: 'work', label: '工作模式', description: '直接拆解问题，推进到可执行结果。' },
  { id: 'plan', label: '计划模式', description: '先定目标、边界和步骤，再开始行动。' },
]

const approvalOptions: Array<{ id: ApprovalMode; label: string; description: string; icon: string }> = [
  { id: 'ask', label: '请求批准', description: '编辑外部文件和使用互联网时始终询问', icon: '!' },
  { id: 'auto', label: '替我批准', description: '仅对检测到的风险操作请求批准', icon: '~' },
  { id: 'full', label: '完全访问权限', description: '可不受限制地访问互联网和电脑上的任何文件', icon: '*' },
]

const memories = ref<any[]>([])
const memoryQuery = ref('')
const memoryDetail = ref<any>(null)
const memoryEdit = ref({ description: '', body: '', priority: 'normal', mem_type: 'user' })
const mergeSourceSlug = ref('')

const historyQuery = ref('')
const historyResults = ref<any[]>([])
const historySessionId = ref('')
const historyReplay = ref<any>(null)

const providers = ref<ProvidersState>({ active_provider_id: '', providers: [] })
const modelTemplates = ref<any[]>([])
const activeProviderId = ref('')
const providerModelsText = ref('')
const modelViewKey = ref('model-empty')
const modelTesting = ref(false)
const modelTestResult = ref<ModelTestResult | null>(null)
const providerForm = reactive<Provider>({
  id: '',
  label: '',
  api_style: 'openai',
  base_url: '',
  api_key_env: '',
  enabled: true,
  streaming: true,
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

const appLayoutStyle = computed(() => ({
  '--sidebar-width': `${sidebarWidth.value}px`,
}))

const chatLayoutStyle = computed(() => ({
  '--chat-width': `${chatWidth.value}px`,
  '--chat-min-width': `${MIN_CHAT_WIDTH}px`,
  '--review-min-width': `${MIN_REVIEW_WIDTH}px`,
}))

const workbenchStyle = computed(() => ({
  '--terminal-height': `${terminalHeight.value}px`,
  '--editor-min-height': `${MIN_EDITOR_HEIGHT}px`,
}))

const activeSessionTitle = computed(() => {
  const session = sessions.value.find((item) => item.id === activeSession.value)
  return session ? displaySessionTitle(session) : '新对话'
})

const activeSessionMeta = computed(() => sessions.value.find((item) => item.id === activeSession.value))
const activeWorkspaceProjectId = computed(() => {
  if (activeSessionMeta.value?.scope === 'project' && activeSessionMeta.value.project_id) {
    return activeSessionMeta.value.project_id
  }
  if (chatListMode.value === 'projects' && activeProject.value) return activeProject.value
  return ''
})

const orderedSessions = computed(() => {
  return [...sessions.value].sort((a, b) => {
    const pinned = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
    if (pinned) return pinned
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
  })
})
const projectScopedSessions = computed(() => orderedSessions.value.filter((session) => (session.scope || 'project') === 'project'))
const chatSessions = computed(() => orderedSessions.value.filter((session) => session.scope === 'chat'))

const navHint = computed(() => {
  if (tab.value === 'chat') return '默认入口，承接当前表达'
  if (tab.value === 'memory') return '长期事实与会话回放'
  return '模型模板与本地 provider'
})

const activeTabIndex = computed(() => tabs.findIndex((item) => item.id === tab.value))
const navIndicatorStyle = computed(() => ({
  transform: `translateY(${Math.max(activeTabIndex.value, 0) * 63}px)`,
}))
const chatListIndicatorStyle = computed(() => ({
  transform: `translateX(${chatListMode.value === 'threads' ? '100%' : '0'})`,
}))

const workbenchOpen = computed(() => reviewOpen.value || terminalOpen.value)
const chatModeCurrent = computed(() => {
  return chatModeOptions.find((option) => option.id === chatMode.value) || chatModeOptions[0]
})
const approvalCurrent = computed(() => {
  return approvalOptions.find((option) => option.id === approvalMode.value) || approvalOptions[0]
})
const activeSettingsMeta = computed(() => {
  return settingsGroups.flatMap((group) => group.items).find((item) => item.id === activeSettingsSection.value)
    || settingsGroups[0].items[0]
})

const consolidationText = computed(() => {
  const result = lastConsolidation.value
  if (!result) return ''
  const memoryResult = result.memory_result || {}
  const saved = Array.isArray(memoryResult.saved) ? memoryResult.saved.length : 0
  return `已完成第 ${result.cycle_no || 1} 次整理，候选记忆写入 ${saved} 条。`
})

const historyReplayMessages = computed(() => historyReplay.value?.messages || [])
const historyReplayTitle = computed(() => historyReplay.value?.session?.title || '历史回放')
const activeReviewPane = computed(() => {
  return reviewPanes.value.find((pane) => pane.id === activeReviewPaneId.value) || reviewPanes.value[0]
})
const currentProviderLabel = computed(() => {
  const current = providers.value.providers.find((item) => item.id === providers.value.active_provider_id)
  if (current) return current.label || current.id
  return providers.value.active_provider_id || '未选择'
})
const openWorkspaceFileFullPath = computed(() => openWorkspaceFile.value?.full_path || openWorkspaceFile.value?.path || '')
const isMarkdownFile = computed(() => {
  const name = openWorkspaceFile.value?.name.toLowerCase() || ''
  return name.endsWith('.md') || name.endsWith('.markdown')
})
const editorDirty = computed(() => {
  return Boolean(openWorkspaceFile.value) && editorContent.value !== (openWorkspaceFile.value?.content || '')
})
const editorLanguage = computed(() => {
  const name = openWorkspaceFile.value?.name.toLowerCase() || ''
  if (!name) return 'Editor'
  if (name.endsWith('.py')) return 'Python'
  if (name.endsWith('.vue')) return 'Vue'
  if (name.endsWith('.ts')) return 'TypeScript'
  if (name.endsWith('.js')) return 'JavaScript'
  if (name.endsWith('.json')) return 'JSON'
  if (name.endsWith('.css')) return 'CSS'
  if (name.endsWith('.html')) return 'HTML'
  if (isMarkdownFile.value) return 'Markdown'
  return 'Text'
})
const markdownPreviewHtml = computed(() => renderMarkdown(editorContent.value))
const reviewStats = computed(() => {
  const user = messages.value.filter((message) => message.role === 'user').length
  const assistant = messages.value.filter((message) => message.role === 'assistant').length
  const privateCount = messages.value.filter((message) => Boolean(message.private)).length
  return {
    messages: messages.value.length,
    user,
    assistant,
    private: privateCount,
  }
})

const reviewItems = computed<ReviewItem[]>(() => {
  const session = activeSessionMeta.value
  const items: ReviewItem[] = []
  const messageCount = messages.value.length
  const turnCount = Number(session?.turn_count || messageCount)
  const lastConsolidated = Number(session?.last_consolidated_turn ?? -1)
  const pendingTurns = Math.max(0, turnCount - lastConsolidated - 1)

  if (!messageCount) {
    return [{
      id: 'empty',
      label: '等待',
      title: '当前会话还没有可审查内容',
      body: '开始对话后，审查窗口会显示上下文、整理状态和可能的下一步动作。',
      tone: 'note',
    }]
  }

  if (pendingTurns >= 8) {
    items.push({
      id: 'consolidation-due',
      label: '建议',
      title: '这段会话已经适合整理',
      body: `距离上次整理已有 ${pendingTurns} 条消息，可以生成摘要并检查是否有长期记忆候选。`,
      tone: 'warn',
    })
  } else {
    items.push({
      id: 'consolidation-ok',
      label: '状态',
      title: '整理压力较低',
      body: pendingTurns > 0 ? `当前还有 ${pendingTurns} 条未整理消息，可以继续对话。` : '当前没有新的公开消息需要整理。',
      tone: 'ok',
    })
  }

  if (reviewStats.value.private > 0 || privateMode.value) {
    items.push({
      id: 'private',
      label: '隐私',
      title: '私密内容不会进入自动整理',
      body: '私密消息会保留在原始会话里，但不应自动写入长期记忆。',
      tone: 'note',
    })
  }

  if (lastConsolidation.value) {
    const saved = Array.isArray(lastConsolidation.value.memory_result?.saved)
      ? lastConsolidation.value.memory_result.saved.length
      : 0
    items.push({
      id: 'last-consolidation',
      label: '完成',
      title: '最近一次整理已有结果',
      body: `第 ${lastConsolidation.value.cycle_no || 1} 次整理完成，候选记忆写入 ${saved} 条。`,
      tone: 'ok',
    })
  }

  return items
})

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

function setChatListMode(next: ChatListMode) {
  if (next === chatListMode.value) return
  chatListTransition.value = next === 'threads' ? 'chat-slide-left' : 'chat-slide-right'
  chatListMode.value = next
}

function openSettings() {
  settingsOpen.value = true
  attachmentMenuOpen.value = false
  approvalMenuOpen.value = false
  modeMenuOpen.value = false
}

function closeSettings() {
  settingsOpen.value = false
}

function formatDate(value: string) {
  if (!value) return ''
  return value.slice(5, 16).replace('T', ' ')
}

function relativeTime(value: string) {
  if (!value) return ''
  const ts = new Date(value).getTime()
  if (!Number.isFinite(ts)) return ''
  const diff = Math.max(0, Date.now() - ts)
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`
  return value.slice(5, 10)
}

function cleanSnippet(value: string) {
  return String(value || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function inlineMarkdown(value: string) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

function renderMarkdown(value: string) {
  const lines = value.split(/\r?\n/)
  const html: string[] = []
  let paragraph: string[] = []
  let codeBlock: string[] | null = null
  let listType: 'ol' | 'ul' | '' = ''
  let listItems: string[] = []

  const flushParagraph = () => {
    if (!paragraph.length) return
    html.push(`<p>${paragraph.map((line) => line.trim()).filter(Boolean).map(inlineMarkdown).join('<br>')}</p>`)
    paragraph = []
  }
  const flushList = () => {
    if (!listType) return
    html.push(`<${listType}>${listItems.join('')}</${listType}>`)
    listType = ''
    listItems = []
  }

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (codeBlock) {
        html.push(`<pre><code>${escapeHtml(codeBlock.join('\n'))}</code></pre>`)
        codeBlock = null
      } else {
        flushParagraph()
        codeBlock = []
      }
      continue
    }
    if (codeBlock) {
      codeBlock.push(line)
      continue
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`)
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    const ordered = /^\d+\.\s+(.+)$/.exec(line.trim())
    if (ordered) {
      flushParagraph()
      if (listType && listType !== 'ol') flushList()
      listType = 'ol'
      listItems.push(`<li>${inlineMarkdown(ordered[1])}</li>`)
      continue
    }
    const unordered = /^[-*]\s+(.+)$/.exec(line.trim())
    if (unordered) {
      flushParagraph()
      if (listType && listType !== 'ul') flushList()
      listType = 'ul'
      listItems.push(`<li>${inlineMarkdown(unordered[1])}</li>`)
      continue
    }
    flushList()
    paragraph.push(line)
  }
  if (codeBlock) html.push(`<pre><code>${escapeHtml(codeBlock.join('\n'))}</code></pre>`)
  flushParagraph()
  flushList()
  return html.join('\n') || '<p class="markdown-empty">没有可预览内容。</p>'
}

function renderMessageContent(message: any) {
  const content = String(message?.content || '')
  if (message?.role === 'assistant') return renderMarkdown(content)
  return escapeHtml(content).replace(/\r?\n/g, '<br>')
}

function estimateTokenCount(text: string) {
  const compact = String(text || '').replace(/\s+/g, '')
  if (!compact) return 0
  let ascii = 0
  for (const char of compact) {
    if (char.charCodeAt(0) <= 127) ascii += 1
  }
  const cjk = compact.length - ascii
  return Math.max(1, Math.round(cjk + ascii / 4))
}

function messageMetaText(message: any) {
  const meta = typeof message?.metadata === 'object' && message.metadata ? message.metadata : {}
  const liveStarted = Number(meta.thinking_started_at || 0)
  const live = message?.role === 'assistant' && (message?.streaming || message?.pending) && liveStarted > 0
  const rawTokens = Number(meta.visible_tokens || meta.total_tokens || 0)
  const rawMs = live ? Math.max(0, sendingNow.value - liveStarted) : Number(meta.thinking_ms || 0)
  const parts: string[] = []
  if (rawTokens > 0 || !live) {
    const prefix = meta.token_source === 'estimate' ? '约 ' : ''
    if (rawTokens > 0) parts.push(`${prefix}${Math.round(rawTokens).toLocaleString()} tokens`)
  } else {
    parts.push('正在思考')
  }
  if (rawMs > 0) parts.push(`思考 ${formatDuration(rawMs)}`)
  return parts.join(' · ')
}

function formatDuration(ms: number) {
  const totalSeconds = Math.max(1, Math.floor(ms / 1000))
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const totalMinutes = Math.floor(totalSeconds / 60)
  const secondsRest = totalSeconds % 60
  if (totalMinutes < 60) {
    return secondsRest ? `${totalMinutes} 分 ${secondsRest} 秒` : `${totalMinutes} 分`
  }
  const hours = Math.floor(totalMinutes / 60)
  const minutesRest = totalMinutes % 60
  return minutesRest ? `${hours} 小时 ${minutesRest} 分` : `${hours} 小时`
}

function tierLabel(priority: string) {
  const tier = tiers.find((item) => item.id === priority)
  return tier ? `${tier.level} ${tier.name}` : 'L2 日常'
}

function riskLabel(level: string) {
  if (level === 'high') return '高风险'
  if (level === 'medium') return '中风险'
  return '低风险'
}

function displaySessionTitle(session: Pick<ChatSession, 'title'>) {
  return String(session.title || '').trim() || '未命名会话'
}

function projectNameFromPath(path: string) {
  const value = String(path || '').trim().replace(/\\/g, '/')
  const parts = value.split('/').filter(Boolean)
  return parts[parts.length - 1] || '新项目'
}

function normalizeModels(models: unknown): string[] {
  if (Array.isArray(models)) return models.map(String).filter(Boolean)
  if (typeof models === 'string') return models.split(',').map((model) => model.trim()).filter(Boolean)
  return []
}

function projectSessions(projectId: string) {
  return projectScopedSessions.value.filter((session) => session.project_id === projectId)
}

function replaceExpanded(ids: Iterable<string>) {
  expandedProjectIds.value = new Set(ids)
}

function toggleProject(projectId: string) {
  activeProject.value = projectId
  const next = new Set(expandedProjectIds.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  replaceExpanded(next)
}

function handlePanelCreate() {
  if (chatListMode.value === 'threads') {
    void newSession()
    return
  }
  void openProjectDialog()
}

async function openProjectDialog() {
  projectDialogOpen.value = true
  projectPickerPath.value = ''
  projectPickerParentPath.value = ''
  projectPickerDirs.value = []
  selectedProjectPath.value = ''
  projectRenamePath.value = ''
  projectRenameName.value = ''
  projectDeleteTarget.value = null
  projectDeletePending.value = false
  const roots: any = await request('/filesystem/directories')
  await loadProjectPicker(roots.default_path || '')
}

function closeProjectDialog() {
  projectDialogOpen.value = false
  selectedProjectPath.value = ''
  projectRenamePath.value = ''
  projectRenameName.value = ''
  projectDeleteTarget.value = null
  projectDeletePending.value = false
}

async function loadProjectPicker(path = '') {
  await guard(async () => {
    const res: any = await request(`/filesystem/directories?path=${encodeURIComponent(path)}`)
    projectPickerPath.value = res.path || ''
    projectPickerParentPath.value = res.parent_path || ''
    projectPickerDirs.value = res.directories || []
    projectFavoritePaths.value = new Set(res.favorites || [])
    selectedProjectPath.value = ''
    projectDeleteTarget.value = null
    projectDeletePending.value = false
  })
}

function openProjectPickerParent() {
  void loadProjectPicker(projectPickerParentPath.value || '')
}

function selectProjectDirectory(path: string) {
  selectedProjectPath.value = path
  projectDeleteTarget.value = null
}

function setProjectRenameInput(el: any) {
  projectRenameInput.value = el as HTMLInputElement | null
}

function isProjectFavorite(path: string) {
  return Boolean(path && projectFavoritePaths.value.has(path))
}

async function pickProjectDirectory() {
  await guard(async () => {
    const picked: any = await request('/filesystem/pick-directory', {
      method: 'POST',
      body: JSON.stringify({ initial: projectPickerPath.value || selectedProjectPath.value }),
    })
    if (!picked.cancelled && picked.path) {
      await loadProjectPicker(picked.path)
      selectedProjectPath.value = picked.path
    }
  })
}

async function revealProjectDirectory() {
  if (!projectPickerPath.value) return
  await guard(async () => {
    await request('/filesystem/reveal-directory', {
      method: 'POST',
      body: JSON.stringify({ path: projectPickerPath.value }),
    })
  })
}

async function addProjectFavorite(path: string) {
  if (!path || isProjectFavorite(path)) return
  await guard(async () => {
    const res: any = await request('/filesystem/favorites', {
      method: 'POST',
      body: JSON.stringify({ path }),
    })
    projectFavoritePaths.value = new Set((res.favorites || []).map((item: WorkspaceItem) => item.path))
  })
}

async function createProjectRecord(path: string, fallbackName: string) {
  const name = projectNameFromPath(fallbackName || path)
  const project: any = await request('/chat/projects', {
    method: 'POST',
    body: JSON.stringify({ name, path }),
  })
  await loadProjects()
  activeProject.value = project.id
  replaceExpanded([...expandedProjectIds.value, project.id])
  chatListMode.value = 'projects'
  closeProjectDialog()
}

async function createProjectFromSelection() {
  await guard(async () => {
    const path = selectedProjectPath.value || projectPickerPath.value
    if (!path) throw new Error('请选择工作目录')
    if (projectRenamePath.value) await commitProjectRename()
    await createProjectRecord(selectedProjectPath.value || path, selectedProjectPath.value || path)
  })
}

async function createDirectoryForProject() {
  await guard(async () => {
    const dir: any = await request('/filesystem/directory', {
      method: 'POST',
      body: JSON.stringify({ parent: projectPickerPath.value }),
    })
    await loadProjectPicker(projectPickerPath.value)
    selectedProjectPath.value = dir.path
    projectRenamePath.value = dir.path
    projectRenameName.value = dir.name || 'Project'
    await nextTick()
    projectRenameInput.value?.focus()
    projectRenameInput.value?.select()
  })
}

async function commitProjectRename() {
  if (!projectRenamePath.value) return
  const oldPath = projectRenamePath.value
  const name = projectRenameName.value.trim()
  projectRenamePath.value = ''
  if (!name) {
    projectRenameName.value = ''
    return
  }
  await guard(async () => {
    const renamed: any = await request('/filesystem/directory', {
      method: 'PATCH',
      body: JSON.stringify({ path: oldPath, name }),
    })
    projectRenameName.value = ''
    await loadProjectPicker(projectPickerPath.value)
    selectedProjectPath.value = renamed.path
  })
}

function cancelProjectRename() {
  projectRenamePath.value = ''
  projectRenameName.value = ''
}

function confirmProjectDirectoryDelete(dir: WorkspaceItem) {
  projectDeleteTarget.value = dir
  projectDeletePending.value = false
}

function cancelProjectDirectoryDelete() {
  projectDeleteTarget.value = null
  projectDeletePending.value = false
}

async function deleteProjectDirectory() {
  if (!projectDeleteTarget.value || projectDeletePending.value) return
  const target = projectDeleteTarget.value
  projectDeletePending.value = true
  await guard(async () => {
    await request('/filesystem/directory', {
      method: 'DELETE',
      body: JSON.stringify({ path: target.path }),
    })
    if (selectedProjectPath.value === target.path) selectedProjectPath.value = ''
    projectDeleteTarget.value = null
    await loadProjectPicker(projectPickerPath.value)
  })
  projectDeletePending.value = false
}

function toggleReviewWorkbench() {
  reviewOpen.value = !reviewOpen.value
  if (reviewOpen.value && !activeReviewPane.value) activeReviewPaneId.value = 'overview'
}

function toggleTerminalPane() {
  terminalOpen.value = !terminalOpen.value
}

function selectApprovalMode(mode: ApprovalMode) {
  approvalMode.value = mode
  approvalMenuOpen.value = false
}

function selectChatMode(mode: ChatMode) {
  chatMode.value = mode
  modeMenuOpen.value = false
}

function toggleModeMenu() {
  modeMenuOpen.value = !modeMenuOpen.value
  if (modeMenuOpen.value) {
    attachmentMenuOpen.value = false
    approvalMenuOpen.value = false
  }
}

function toggleApprovalMenu() {
  approvalMenuOpen.value = !approvalMenuOpen.value
  if (approvalMenuOpen.value) {
    attachmentMenuOpen.value = false
    modeMenuOpen.value = false
  }
}

function toggleAttachmentMenu() {
  attachmentMenuOpen.value = !attachmentMenuOpen.value
  if (attachmentMenuOpen.value) {
    approvalMenuOpen.value = false
    modeMenuOpen.value = false
  }
}

function closeWorkbench() {
  reviewOpen.value = false
  terminalOpen.value = false
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function availableMainWidth(nextSidebarWidth = sidebarWidth.value) {
  if (typeof window === 'undefined') return 1200
  return Math.max(0, window.innerWidth - nextSidebarWidth - 4)
}

function clampChatWidth(value: number, nextSidebarWidth = sidebarWidth.value) {
  const max = Math.max(MIN_CHAT_WIDTH, availableMainWidth(nextSidebarWidth) - MIN_REVIEW_WIDTH - 4)
  return clamp(value, MIN_CHAT_WIDTH, max)
}

function startSidebarResize(event: PointerEvent) {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = sidebarWidth.value
  document.body.classList.add('resizing-layout')

  const move = (moveEvent: PointerEvent) => {
    const next = clamp(startWidth + moveEvent.clientX - startX, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH)
    sidebarWidth.value = next
    chatWidth.value = clampChatWidth(chatWidth.value, next)
  }
  const stop = () => {
    document.body.classList.remove('resizing-layout')
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop, { once: true })
}

function startChatResize(event: PointerEvent) {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = chatWidth.value
  document.body.classList.add('resizing-layout')

  const move = (moveEvent: PointerEvent) => {
    chatWidth.value = clampChatWidth(startWidth - (moveEvent.clientX - startX))
  }
  const stop = () => {
    document.body.classList.remove('resizing-layout')
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop, { once: true })
}

function startTerminalResize(event: PointerEvent) {
  event.preventDefault()
  const startY = event.clientY
  const startHeight = terminalHeight.value
  const containerHeight = (event.currentTarget as HTMLElement).parentElement?.clientHeight || window.innerHeight
  const maxHeight = Math.max(MIN_TERMINAL_HEIGHT, containerHeight - MIN_EDITOR_HEIGHT - 5)
  document.body.classList.add('resizing-vertical')

  const move = (moveEvent: PointerEvent) => {
    terminalHeight.value = clamp(startHeight - (moveEvent.clientY - startY), MIN_TERMINAL_HEIGHT, maxHeight)
  }
  const stop = () => {
    document.body.classList.remove('resizing-vertical')
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop, { once: true })
}

async function openReviewPane(kind: ReviewPaneKind) {
  reviewOpen.value = true
  reviewAddOpen.value = false
  const existing = reviewPanes.value.find((pane) => pane.kind === kind)
  if (existing) {
    activeReviewPaneId.value = existing.id
  } else {
    const pane: ReviewPane = {
      id: kind,
      title: kind === 'files' ? '文件' : '审查',
      kind,
    }
    reviewPanes.value = [...reviewPanes.value, pane]
    activeReviewPaneId.value = pane.id
  }
  if (kind === 'files' && workspaceDirs.value.length === 0 && workspaceFiles.value.length === 0) {
    fileExplorerOpen.value = true
    await loadWorkspaceFiles('', { clearSelection: true })
  } else if (kind === 'files' && workspaceProjectId.value !== activeWorkspaceProjectId.value) {
    fileExplorerOpen.value = true
    await loadWorkspaceFiles('', { clearSelection: true })
  } else if (kind === 'files' && !openWorkspaceFile.value) {
    fileExplorerOpen.value = true
  }
}

async function loadWorkspaceFiles(path = '', options: { clearSelection?: boolean } = {}) {
  await guard(async () => {
    const query = workspaceQuery(path)
    const res: any = await request(`/workspace/files?${query}`)
    workspacePath.value = res.path || ''
    workspaceProjectId.value = activeWorkspaceProjectId.value
    workspaceDirs.value = res.directories || []
    workspaceFiles.value = res.files || []
    if (options.clearSelection) openWorkspaceFile.value = null
    fileExplorerOpen.value = true
  })
}

async function readWorkspaceFile(path: string) {
  await guard(async () => {
    openWorkspaceFile.value = await request(`/workspace/file?${workspaceQuery(path)}`)
    editorContent.value = openWorkspaceFile.value?.content || ''
    editorView.value = isMarkdownFile.value ? 'edit' : 'edit'
    editorPrompt.value = ''
    fileExplorerOpen.value = false
  })
}

async function saveWorkspaceFile() {
  if (!openWorkspaceFile.value) return
  await guard(async () => {
    const saved = await request<WorkspaceFile>(`/workspace/file?${workspaceQuery(openWorkspaceFile.value!.path)}`, {
      method: 'PUT',
      body: JSON.stringify({ content: editorContent.value }),
    })
    openWorkspaceFile.value = saved
    editorContent.value = saved.content
  })
}

function workspaceQuery(path = '') {
  const params = new URLSearchParams()
  params.set('path', path)
  if (activeWorkspaceProjectId.value) params.set('project_id', activeWorkspaceProjectId.value)
  return params.toString()
}

function openWorkspaceParent() {
  if (!workspacePath.value) return
  const parts = workspacePath.value.split('/').filter(Boolean)
  parts.pop()
  void loadWorkspaceFiles(parts.join('/'))
}

async function openFileExplorer() {
  fileExplorerOpen.value = true
  if (workspaceDirs.value.length === 0 && workspaceFiles.value.length === 0) {
    await loadWorkspaceFiles('')
  }
}

function triggerFilePicker() {
  attachmentMenuOpen.value = false
  approvalMenuOpen.value = false
  fileInput.value?.click()
}

function triggerFolderPicker() {
  attachmentMenuOpen.value = false
  approvalMenuOpen.value = false
  folderInput.value?.click()
}

function handleAttachmentChange(event: Event, kind: 'file' | 'folder') {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  const next = files.slice(0, 8).map((file) => ({
    id: `${kind}-${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name: kind === 'folder'
      ? ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name).split('/')[0]
      : file.name,
    kind,
    size: file.size,
  }))
  const merged = [...composerAttachments.value, ...next]
  const unique = new Map<string, ComposerAttachment>()
  for (const item of merged) unique.set(`${item.kind}:${item.name}`, item)
  composerAttachments.value = [...unique.values()].slice(0, 12)
  input.value = ''
}

function createDraftFile() {
  attachmentMenuOpen.value = false
  approvalMenuOpen.value = false
  const index = composerAttachments.value.filter((item) => item.kind === 'draft').length + 1
  composerAttachments.value = [
    ...composerAttachments.value,
    {
      id: `draft-${Date.now()}-${index}`,
      name: index === 1 ? '新建文件.md' : `新建文件-${index}.md`,
      kind: 'draft',
    },
  ]
}

function removeAttachment(id: string) {
  composerAttachments.value = composerAttachments.value.filter((item) => item.id !== id)
}

async function scrollMessages() {
  await nextTick()
  if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight
}

function isMessagesNearBottom(threshold = 180) {
  const el = messageBox.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold
}

async function scrollMessagesIfFollowing(follow: boolean) {
  if (!follow) return
  await scrollMessages()
}

function adjustComposerHeight() {
  const el = composerTextarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 220)}px`
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  const shouldSend = sendShortcut.value === 'enter'
    ? event.key === 'Enter' && !event.shiftKey
    : event.key === 'Enter' && (event.ctrlKey || event.metaKey)
  if (!shouldSend) return
  event.preventDefault()
  void sendMessage()
}

function startSendingTimer() {
  sendingNow.value = Date.now()
  if (sendingTimer !== null) window.clearInterval(sendingTimer)
  sendingTimer = window.setInterval(() => {
    sendingNow.value = Date.now()
  }, 250)
}

function stopSendingTimer() {
  if (sendingTimer !== null) {
    window.clearInterval(sendingTimer)
    sendingTimer = null
  }
}

function stopAssistantReveal() {
  assistantRevealToken += 1
  if (assistantRevealTimer !== null) {
    window.clearInterval(assistantRevealTimer)
    assistantRevealTimer = null
  }
  if (assistantRevealResolve) {
    assistantRevealResolve()
    assistantRevealResolve = null
  }
}

function pushOptimisticUserMessage(content: string, isPrivate: boolean) {
  const id = `pending-${Date.now()}-${Math.random().toString(36).slice(2)}`
  messages.value = [
    ...messages.value,
    {
      id,
      turn_idx: id,
      role: 'user',
      content,
      private: isPrivate,
      pending: true,
    },
  ]
  return id
}

function markOptimisticUserSettled(id: string) {
  if (!id) return
  messages.value = messages.value.map((item) => (
    item.id === id ? { ...item, pending: false } : item
  ))
}

function pushAssistantPlaceholder(isPrivate: boolean) {
  const id = `assistant-pending-${Date.now()}-${Math.random().toString(36).slice(2)}`
  messages.value = [
    ...messages.value,
    {
      id,
      turn_idx: id,
      role: 'assistant',
      content: '',
      private: isPrivate,
      metadata: {
        thinking_started_at: Date.now(),
        token_source: 'estimate',
      },
      pending: true,
      streaming: true,
    },
  ]
  return id
}

function removeMessageById(id: string) {
  if (!id) return
  messages.value = messages.value.filter((item) => item.id !== id)
}

function updateAssistantStreamMessage(id: string, patch: Record<string, any>) {
  if (!id) return
  messages.value = messages.value.map((item) => (
    item.id === id ? { ...item, ...patch } : item
  ))
}

function appendAssistantDelta(id: string, delta: string) {
  if (!id || !delta) return
  messages.value = messages.value.map((item) => {
    if (item.id !== id) return item
    const content = `${item.content || ''}${delta}`
    return {
      ...item,
      content,
      metadata: {
        ...item.metadata,
        visible_tokens: estimateTokenCount(content),
      },
    }
  })
}

async function revealAssistantMessage(message: any, existingId = '') {
  const fullText = String(message?.content || message?.answer || '')
  if (!fullText) return
  stopAssistantReveal()
  const token = assistantRevealToken
  const follow = isMessagesNearBottom()
  const id = existingId || `assistant-reveal-${Date.now()}-${Math.random().toString(36).slice(2)}`
  const baseMetadata = {
    ...(message?.metadata || {}),
    thinking_started_at: messages.value.find((item) => item.id === id)?.metadata?.thinking_started_at || Date.now(),
  }
  if (existingId) {
    messages.value = messages.value.map((item) => (
      item.id === id
        ? { ...item, content: '', private: Boolean(message?.private), metadata: baseMetadata, pending: true, streaming: true }
        : item
    ))
  } else {
    messages.value = [
      ...messages.value,
      {
        id,
        turn_idx: id,
        role: 'assistant',
        content: '',
        private: Boolean(message?.private),
        metadata: baseMetadata,
        pending: true,
        streaming: true,
      },
    ]
  }
  await scrollMessagesIfFollowing(follow)

  const chunkSize = fullText.length > 2400 ? 24 : fullText.length > 1200 ? 16 : fullText.length > 500 ? 10 : 6
  await new Promise<void>((resolve) => {
    assistantRevealResolve = resolve
    let index = 0
    assistantRevealTimer = window.setInterval(() => {
      if (token !== assistantRevealToken) {
        assistantRevealResolve = null
        resolve()
        return
      }
      index = Math.min(fullText.length, index + chunkSize)
      const nextContent = fullText.slice(0, index)
      messages.value = messages.value.map((item) => (
        item.id === id
          ? {
              ...item,
              content: nextContent,
              metadata: {
                ...item.metadata,
                visible_tokens: estimateTokenCount(nextContent),
              },
            }
          : item
      ))
      void scrollMessagesIfFollowing(follow)
      if (index >= fullText.length) {
        if (assistantRevealTimer !== null) {
          window.clearInterval(assistantRevealTimer)
          assistantRevealTimer = null
        }
        messages.value = messages.value.map((item) => (
          item.id === id
            ? {
                ...item,
                content: fullText,
                metadata: {
                  ...item.metadata,
                  ...(message?.metadata || {}),
                  visible_tokens: estimateTokenCount(fullText),
                },
                pending: false,
                streaming: false,
              }
            : item
        ))
        assistantRevealResolve = null
        resolve()
      }
    }, 18)
  })
}

function cleanWelcomePrompt(prompt: Partial<WelcomePrompt>, index: number): WelcomePrompt {
  return {
    id: String(prompt.id || `welcome-${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`),
    title: String(prompt.title || '').trim() || '我们该做什么',
    body: String(prompt.body || '').trim() || '开始今天的工作',
  }
}

function normalizeWelcomePrompts(value: unknown): WelcomePrompt[] {
  if (!Array.isArray(value)) return [...defaultWelcomePrompts]
  const prompts = value
    .map((item, index) => cleanWelcomePrompt(item as Partial<WelcomePrompt>, index))
    .filter((item) => item.title || item.body)
  return prompts.length ? prompts : [...defaultWelcomePrompts]
}

function saveWelcomePrompts() {
  if (welcomePrompts.value.length === 0) welcomePrompts.value = [...defaultWelcomePrompts]
  welcomePrompts.value = welcomePrompts.value.map((prompt, index) => ({
    id: String(prompt.id || `welcome-${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`),
    title: String(prompt.title ?? ''),
    body: String(prompt.body ?? ''),
  }))
  localStorage.setItem(WELCOME_PROMPTS_KEY, JSON.stringify(welcomePrompts.value))
  currentWelcomePrompt.value = welcomePrompts.value.find((item) => item.id === currentWelcomePrompt.value.id) || welcomePrompts.value[0]
}

function loadWelcomePrompts() {
  let prompts = [...defaultWelcomePrompts]
  try {
    const raw = localStorage.getItem(WELCOME_PROMPTS_KEY)
    prompts = raw ? normalizeWelcomePrompts(JSON.parse(raw)) : [...defaultWelcomePrompts]
  } catch {
    prompts = [...defaultWelcomePrompts]
  }
  welcomePrompts.value = prompts
  const previous = Number(localStorage.getItem(WELCOME_PROMPT_INDEX_KEY) || '-1')
  const next = Number.isFinite(previous) ? (previous + 1) % prompts.length : 0
  localStorage.setItem(WELCOME_PROMPT_INDEX_KEY, String(next))
  currentWelcomePrompt.value = prompts[next] || prompts[0]
}

function addWelcomePrompt() {
  welcomePrompts.value = [
    ...welcomePrompts.value,
    {
      id: `welcome-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      title: '我们该做什么',
      body: '开始今天的工作',
    },
  ]
  saveWelcomePrompts()
}

function deleteWelcomePrompt(id: string) {
  if (welcomePrompts.value.length <= 1) return
  welcomePrompts.value = welcomePrompts.value.filter((prompt) => prompt.id !== id)
  saveWelcomePrompts()
}

async function refreshAll() {
  await guard(async () => {
    health.value = await request('/health')
    await Promise.all([loadProjects(), loadSessions(), loadMemories(), loadProviders(), loadModelTemplates()])
    await ensureDefaultChat()
  })
}

async function ensureDefaultChat() {
  if (activeSession.value) return
  if (!activeProject.value && projects.value[0]) activeProject.value = projects.value[0].id
  if (activeProject.value) replaceExpanded([...expandedProjectIds.value, activeProject.value])
  if (projectScopedSessions.value.length === 0 && chatSessions.value.length > 0) {
    chatListMode.value = 'threads'
  }
  const guiSession = chatSessions.value[0] || projectScopedSessions.value[0] || sessions.value[0]
  if (guiSession) {
    await openSession(guiSession.id)
  } else {
    await newSession()
  }
}

async function loadProjects() {
  const res: any = await request('/chat/projects')
  projects.value = res.projects || []
  if (!activeProject.value && projects.value[0]) activeProject.value = projects.value[0].id
  if (activeProject.value && !expandedProjectIds.value.has(activeProject.value)) {
    replaceExpanded([...expandedProjectIds.value, activeProject.value])
  }
}

async function loadSessions() {
  const res: any = await request('/chat/sessions?source=gui')
  sessions.value = res.sessions || []
}

async function newSession(projectId?: string) {
  stopAssistantReveal()
  await guard(async () => {
    const pid = projectId || activeProject.value || projects.value[0]?.id
    const scope = projectId || chatListMode.value === 'projects' ? 'project' : 'chat'
    const session: any = await request('/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ project_id: pid, scope }),
    })
    activeSession.value = session.id
    activeProject.value = session.project_id || pid || ''
    if (scope === 'project' && activeProject.value) replaceExpanded([...expandedProjectIds.value, activeProject.value])
    if (scope === 'chat') chatListMode.value = 'threads'
    messages.value = []
    chatViewKey.value = session.id
    lastConsolidation.value = null
    latestAgentRun.value = null
    agentRunEvents.value = []
    agentRunTimeline.value = []
    closeWorkbench()
    await loadProjects()
    await loadSessions()
    await nextTick()
    await scrollMessages()
  })
}

async function openSession(id: string) {
  stopAssistantReveal()
  await guard(async () => {
    activeSession.value = id
    const session = sessions.value.find((item) => item.id === id)
    if (session?.project_id) {
      activeProject.value = session.project_id
      replaceExpanded([...expandedProjectIds.value, session.project_id])
    }
    const res: any = await request(`/session-memory/${id}`)
    messages.value = res.messages || []
    chatViewKey.value = id
    await loadLatestAgentRun(id)
    await nextTick()
    await scrollMessages()
  })
}

async function loadLatestAgentRun(sessionId = activeSession.value) {
  if (!sessionId) {
    latestAgentRun.value = null
    agentRunEvents.value = []
    agentRunTimeline.value = []
    return
  }
  const runsRes: any = await request(`/agent/runs?session_id=${encodeURIComponent(sessionId)}&limit=1`)
  const run = runsRes.runs?.[0]
  if (!run) {
    latestAgentRun.value = null
    agentRunEvents.value = []
    agentRunTimeline.value = []
    return
  }
  const detail: any = await request(`/agent/runs/${encodeURIComponent(run.id)}`)
  latestAgentRun.value = detail.run || run
  agentRunEvents.value = detail.events || []
  agentRunTimeline.value = detail.timeline || []
}

async function togglePinSession(session: ChatSession) {
  await guard(async () => {
    await request(`/chat/sessions/${session.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ pinned: !Boolean(session.pinned) }),
    })
    await loadSessions()
  })
}

async function archiveSession(session: ChatSession) {
  await guard(async () => {
    await request(`/chat/sessions/${session.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ archived: true }),
    })
    if (activeSession.value === session.id) {
      activeSession.value = ''
      messages.value = []
      chatViewKey.value = 'empty'
      latestAgentRun.value = null
      agentRunEvents.value = []
      agentRunTimeline.value = []
      closeWorkbench()
    }
    await loadProjects()
    await loadSessions()
    await ensureDefaultChat()
  })
}

async function deleteSession(session: ChatSession) {
  if (!confirm(`确认删除「${displaySessionTitle(session)}」？原始消息也会删除。`)) return
  await guard(async () => {
    await request(`/chat/sessions/${session.id}`, { method: 'DELETE' })
    if (activeSession.value === session.id) {
      activeSession.value = ''
      messages.value = []
      chatViewKey.value = 'empty'
      latestAgentRun.value = null
      agentRunEvents.value = []
      agentRunTimeline.value = []
      closeWorkbench()
    }
    await loadProjects()
    await loadSessions()
    await ensureDefaultChat()
  })
}

async function sendMessage() {
  if (!chatInput.value.trim() || sending.value) return
  const message = chatInput.value
  const isPrivate = privateMode.value
  chatInput.value = ''
  await nextTick()
  adjustComposerHeight()
  sending.value = true
  startSendingTimer()
  let optimisticId = ''
  let assistantPlaceholderId = ''
  if (activeSession.value) {
    optimisticId = pushOptimisticUserMessage(message, isPrivate)
    assistantPlaceholderId = pushAssistantPlaceholder(isPrivate)
    await scrollMessages()
  }
  await guard(async () => {
    if (!activeSession.value) await newSession()
    if (!messages.value.some((item) => item.pending && item.content === message)) {
      optimisticId = pushOptimisticUserMessage(message, isPrivate)
      assistantPlaceholderId = pushAssistantPlaceholder(isPrivate)
      await scrollMessages()
    }
    const sessionId = activeSession.value
    let streamedAnswer = ''
    let streamTrace: any = null
    let streamDone: any = null
    await streamRequest(`/chat/${sessionId}/message/stream`, {
      method: 'POST',
      body: JSON.stringify({ message, private: isPrivate }),
    }, async (event: any) => {
      if (event.type === 'meta') {
        markOptimisticUserSettled(optimisticId)
        updateAssistantStreamMessage(assistantPlaceholderId, {
          metadata: {
            ...(messages.value.find((item) => item.id === assistantPlaceholderId)?.metadata || {}),
            run_id: event.run_id,
            status: event.status,
          },
        })
        return
      }
      if (event.type === 'delta') {
        const delta = String(event.text || '')
        streamedAnswer += delta
        appendAssistantDelta(assistantPlaceholderId, delta)
        await scrollMessagesIfFollowing(true)
        return
      }
      if (event.type === 'model_status') {
        updateAssistantStreamMessage(assistantPlaceholderId, {
          metadata: {
            ...(messages.value.find((item) => item.id === assistantPlaceholderId)?.metadata || {}),
            streaming_enabled: event.streaming !== false,
            streaming_reason: event.reason || '',
          },
        })
        return
      }
      if (event.type === 'done') {
        streamDone = event
        streamTrace = event.trace
        const answer = String(event.answer || streamedAnswer)
        streamedAnswer = answer
        updateAssistantStreamMessage(assistantPlaceholderId, {
          content: answer,
          metadata: {
            ...(event.message?.metadata || {}),
            visible_tokens: estimateTokenCount(answer),
          },
          pending: false,
          streaming: false,
        })
        return
      }
      if (event.type === 'error') {
        throw new Error(event.error || 'stream error')
      }
    })
    lastConsolidation.value = streamDone?.consolidation
    composerAttachments.value = []
    attachmentMenuOpen.value = false
    approvalMenuOpen.value = false
    modeMenuOpen.value = false
    markOptimisticUserSettled(optimisticId)
    await openSession(sessionId)
    if (streamTrace?.run_id) await loadLatestAgentRun(sessionId)
    await loadProjects()
    await loadSessions()
  })
  if (error.value && optimisticId) {
    messages.value = messages.value.filter((item) => item.id !== optimisticId)
    removeMessageById(assistantPlaceholderId)
    chatInput.value = message
    await nextTick()
    adjustComposerHeight()
  }
  sending.value = false
  stopSendingTimer()
}

async function consolidate() {
  if (!activeSession.value) return
  await guard(async () => {
    lastConsolidation.value = await request(`/chat/${activeSession.value}/consolidate`, { method: 'POST' })
    await loadSessions()
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
  if (!confirm('导入旧历史到 IwIw 会话记忆？已有数据会跳过。')) return
  await guard(async () => {
    await request('/session-memory/migrate-legacy', { method: 'POST' })
    await searchHistory()
    await loadProjects()
    await loadSessions()
  })
}

async function loadProviders() {
  const res: any = await request('/models/providers')
  providers.value = Array.isArray(res)
    ? { active_provider_id: res[0]?.id || '', providers: res }
    : { active_provider_id: res.active_provider_id || '', providers: res.providers || [] }
}

async function loadModelTemplates() {
  const res: any = await request('/models/templates')
  modelTemplates.value = res.templates || []
}

function applyTemplate(template: any) {
  const suffix = Date.now().toString().slice(-5)
  const id = template.id === 'custom' ? `custom-${suffix}` : `${template.id}-${suffix}`
  const models = normalizeModels(template.models)
  modelViewKey.value = `template:${template.id}:${suffix}`
  modelTestResult.value = null
  providerForm.id = id
  providerForm.label = template.label || '自定义模型'
  providerForm.api_style = template.api_style || 'openai'
  providerForm.base_url = template.base_url || ''
  providerForm.api_key_env = 'MEMORY_AGENT_LLM_API_KEY'
  providerForm.enabled = true
  providerForm.streaming = template.streaming !== false
  providerForm.models = [...models]
  providerModelsText.value = models.join(', ')
  const first = models[0] || ''
  providerForm.defaults = { chat: first, summary: first, memory: first }
  activeProviderId.value = ''
}

function editProvider(provider: Provider) {
  activeProviderId.value = provider.id
  modelViewKey.value = `provider:${provider.id}`
  modelTestResult.value = null
  providerForm.id = provider.id
  providerForm.label = provider.label
  providerForm.api_style = provider.api_style
  providerForm.base_url = provider.base_url
  providerForm.api_key_env = provider.api_key_env || ''
  providerForm.enabled = Boolean(provider.enabled)
  providerForm.streaming = provider.streaming !== false
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
    return false
  }
  const item: Provider = {
    id: providerForm.id.trim(),
    label: providerForm.label.trim(),
    api_style: providerForm.api_style,
    base_url: providerForm.base_url.trim(),
    api_key_env: providerForm.api_key_env?.trim(),
    enabled: providerForm.enabled,
    streaming: providerForm.streaming !== false,
    models,
    defaults: { ...providerForm.defaults },
  }
  const index = providers.value.providers.findIndex((provider) => provider.id === item.id)
  if (index >= 0) providers.value.providers.splice(index, 1, item)
  else providers.value.providers.push(item)
  activeProviderId.value = item.id
  if (!providers.value.active_provider_id) providers.value.active_provider_id = item.id
  return true
}

function setActiveProvider(id: string) {
  const providerId = id.trim()
  if (!providerId) return
  const exists = providers.value.providers.some((provider) => provider.id === providerId)
  if (!exists && !upsertProvider()) return
  providers.value.active_provider_id = providerId
  activeProviderId.value = providerId
}

function currentProviderDraft(): Provider {
  const models = providerModelsText.value.split(',').map((model) => model.trim()).filter(Boolean)
  return {
    id: providerForm.id.trim(),
    label: providerForm.label.trim(),
    api_style: providerForm.api_style,
    base_url: providerForm.base_url.trim(),
    api_key_env: providerForm.api_key_env?.trim(),
    enabled: providerForm.enabled,
    streaming: providerForm.streaming !== false,
    models,
    defaults: { ...providerForm.defaults },
  }
}

async function saveProviders() {
  await guard(async () => {
    if (providerForm.id.trim() || providerForm.label.trim()) upsertProvider()
    providers.value = await request('/models/providers', {
      method: 'PUT',
      body: JSON.stringify({
        active_provider_id: providers.value.active_provider_id,
        providers: providers.value.providers,
      }),
    })
  })
}

async function testProvider() {
  const provider = currentProviderDraft()
  if (!provider.id) return
  modelTesting.value = true
  modelTestResult.value = { status: 'pending', text: '正在测试连接...' }
  const started = performance.now()
  try {
    const res: any = await request('/models/providers/test', {
      method: 'POST',
      body: JSON.stringify({ provider }),
    })
    const latency = Math.round(Number(res.latency_ms) || performance.now() - started)
    if (res.ok) {
      modelTestResult.value = {
        status: 'ok',
        text: `${res.model || provider.models[0] || provider.id} 模型连接正常 · 延迟 ${latency} ms`,
      }
    } else {
      modelTestResult.value = {
        status: 'error',
        text: `连接失败：${res.error || '未知错误'} · 延迟 ${latency} ms`,
      }
    }
  } catch (err: any) {
    const latency = Math.round(performance.now() - started)
    modelTestResult.value = {
      status: 'error',
      text: `连接失败：${err?.message || String(err)} · 延迟 ${latency} ms`,
    }
  } finally {
    modelTesting.value = false
  }
}

onMounted(() => {
  loadWelcomePrompts()
  void refreshAll()
  void nextTick(adjustComposerHeight)
})

onBeforeUnmount(() => {
  stopSendingTimer()
  stopAssistantReveal()
})
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

.resizing-layout,
.resizing-layout * {
  cursor: col-resize !important;
  user-select: none;
}

.resizing-vertical,
.resizing-vertical * {
  cursor: row-resize !important;
  user-select: none;
}

.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width, 304px) 4px minmax(0, 1fr);
  height: 100vh;
  min-height: 660px;
  background: #fbfbf8;
}

.sidebar {
  position: relative;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  gap: 16px;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  padding: 18px 16px 14px;
  border-right: 1px solid #dfded8;
  background: #f0f1ee;
}

.sidebar.settings-mode {
  grid-template-rows: minmax(0, 1fr);
}

.sidebar-normal,
.settings-sidebar {
  grid-row: 1;
  grid-column: 1;
  min-width: 0;
}

.sidebar-normal {
  min-height: 0;
  height: 100%;
  display: grid;
  grid-template-rows: clamp(280px, 38vh, 360px) minmax(0, 1fr) auto;
  gap: 16px;
}

.settings-shell-enter-active,
.settings-shell-leave-active {
  transition: opacity 180ms ease;
  will-change: opacity;
}

.settings-shell-leave-active {
  pointer-events: none;
}

.settings-shell-enter-from {
  opacity: 0;
}

.settings-shell-leave-to {
  opacity: 0;
}

.sidebar-resizer,
.column-resizer {
  position: relative;
  z-index: 8;
  min-width: 4px;
  cursor: col-resize;
  background: #e6e3dc;
  transition: background 120ms ease;
}

.sidebar-resizer:hover,
.column-resizer:hover,
.resizing-layout .sidebar-resizer,
.resizing-layout .column-resizer {
  background: #b96f4a;
}

.sidebar-resizer::after,
.column-resizer::after {
  content: "";
  position: absolute;
  inset: 0 -4px;
}

.sidebar-top {
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid #dfded8;
  padding-bottom: 16px;
}

.sidebar-footer {
  border-top: 1px solid #dfded8;
  padding-top: 10px;
}

.settings-entry,
.settings-exit {
  width: 100%;
  min-height: 39px;
  display: flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 13px;
  background: transparent;
  color: #34302b;
  padding: 10px 11px;
  text-align: left;
}

.settings-entry:hover,
.settings-exit:hover {
  border-color: #d6d3cb;
  background: #fbfbf8;
}

.settings-entry span,
.settings-exit {
  font-size: 13px;
  font-weight: 820;
}

.settings-sidebar {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  height: 100%;
}

.settings-sidebar-head {
  border-bottom: 1px solid #dfded8;
  padding: 20px 18px 16px;
}

.settings-sidebar-head div {
  display: grid;
  gap: 4px;
}

.settings-sidebar-head strong {
  font-size: 22px;
  letter-spacing: 0;
}

.settings-sidebar-head span {
  color: #777169;
  font-size: 12px;
}

.settings-nav {
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  scrollbar-width: none;
  display: grid;
  align-content: start;
  gap: 18px;
  padding: 14px 12px;
}

.settings-nav-group {
  display: grid;
  gap: 5px;
}

.settings-nav-group h2 {
  margin: 0;
  padding: 7px 7px 3px;
  color: #817b72;
  font-size: 11px;
  font-weight: 850;
  text-transform: uppercase;
}

.settings-nav-group button {
  width: 100%;
  display: grid;
  gap: 3px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: #34302b;
  padding: 9px 10px;
  text-align: left;
}

.settings-nav-group button:hover,
.settings-nav-group button.active {
  border-color: #d8d6ce;
  background: #fbfbf8;
}

.settings-nav-group button span {
  font-size: 13px;
  font-weight: 820;
}

.settings-nav-group button em {
  color: #7d786f;
  font-size: 12px;
  font-style: normal;
  line-height: 1.35;
}

.settings-sidebar-footer {
  border-top: 1px solid #dfded8;
  padding: 12px;
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
  border-radius: 14px;
  background: #24211d;
  box-shadow: 0 10px 24px rgba(36, 33, 29, .13);
  transition: transform 220ms cubic-bezier(.2, .8, .2, 1);
  pointer-events: none;
}

.primary-nav button {
  position: relative;
  z-index: 1;
  border: 0;
  border-radius: 14px;
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

.panel-title div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.panel-title em {
  color: #8b867d;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}

.panel-create {
  position: relative;
}

.ghost-btn, .quiet-wide {
  border: 1px solid #d4d2ca;
  border-radius: 10px;
  background: #fbfbf8;
  padding: 6px 9px;
  color: #514d46;
}

.quiet-wide { width: 100%; }

.chat-list-tabs {
  position: relative;
  display: flex;
  align-items: center;
  gap: 4px;
  border-bottom: 1px solid #dfded8;
  padding: 3px 3px 11px;
}

.chat-list-tabs button {
  position: relative;
  z-index: 1;
  flex: 1 1 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #777169;
  padding: 5px 8px;
  font-size: 12px;
  font-weight: 820;
}

.chat-list-tabs button:hover,
.chat-list-tabs button.active {
  color: #24211d;
}

.chat-list-indicator {
  position: absolute;
  z-index: 0;
  left: 3px;
  top: 3px;
  width: calc((100% - 10px) / 2);
  height: 28px;
  border: 1px solid #d8d6ce;
  border-radius: 9px;
  background: #fbfbf8;
  box-shadow: 0 1px 2px rgba(36, 33, 29, .08);
  transition: transform 180ms ease;
}

.chat-list-page {
  min-height: 0;
}

.chat-slide-left-enter-active,
.chat-slide-left-leave-active,
.chat-slide-right-enter-active,
.chat-slide-right-leave-active {
  transition: opacity 180ms ease, transform 180ms ease;
}

.chat-slide-left-enter-from {
  opacity: 0;
  transform: translateX(14px);
}

.chat-slide-left-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}

.chat-slide-right-enter-from {
  opacity: 0;
  transform: translateX(-14px);
}

.chat-slide-right-leave-to {
  opacity: 0;
  transform: translateX(12px);
}

.list-card-enter-active,
.list-card-leave-active {
  transition: opacity 190ms ease, transform 190ms ease, border-color 190ms ease, background-color 190ms ease;
}

.list-card-enter-from {
  opacity: 0;
  transform: translateY(-8px) scale(.985);
}

.list-card-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(.985);
}

.list-card-move {
  transition: transform 190ms ease;
}

.segmented {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px;
  padding: 3px;
  border: 1px solid #d8d6ce;
  border-radius: 12px;
  background: #e6e7e3;
}

.segmented button {
  border: 0;
  border-radius: 9px;
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
  border-radius: 12px;
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

.project-list,
.session-list, .memory-tier-list {
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  scrollbar-width: none;
  display: grid;
  gap: 6px;
  padding-right: 2px;
}

.settings-nav::-webkit-scrollbar,
.project-list::-webkit-scrollbar,
.session-list::-webkit-scrollbar,
.memory-tier-list::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.project-list {
  align-content: start;
}

.project-group {
  display: grid;
  gap: 5px;
}

.project-row {
  width: 100%;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  padding: 8px 9px;
  color: #4f4b45;
  text-align: left;
}

.project-row:hover,
.project-row.active {
  border-color: #d8d6ce;
  background: #fbfbf8;
}

.project-caret {
  color: #8b867d;
  font-size: 12px;
}

.project-text {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.project-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 800;
}

.project-path {
  overflow: hidden;
  color: #817b72;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 1.25;
}

.project-count {
  min-width: 22px;
  border-radius: 999px;
  background: #e5e3dc;
  color: #68635b;
  padding: 2px 6px;
  text-align: center;
  font-size: 11px;
  font-weight: 800;
}

.project-sessions {
  overflow: visible;
  padding-left: 13px;
  border-left: 1px solid #dedbd2;
}

.session-list.tight { max-height: 210px; }

.list-item {
  width: 100%;
  display: grid;
  gap: 4px;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  padding: 9px 10px;
  color: #24211d;
}

.list-item:hover, .list-item.selected {
  border-color: #d8d6ce;
  background: #fbfbf8;
}

.list-item.current {
  border-color: #cfc6b8;
  background: #fffdf8;
}

.session-row {
  position: relative;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  min-height: 40px;
  padding: 0;
}

.session-row.pinned {
  border-color: #d8c8b8;
}

.session-main {
  min-width: 0;
  display: block;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 9px 8px 9px 10px;
  text-align: left;
}

.session-main:hover {
  background: transparent;
}

.item-actions {
  position: absolute;
  right: 6px;
  top: 50%;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: calc(100% - 12px);
  border: 1px solid #d8d6ce;
  border-radius: 10px;
  background: #fffefb;
  padding: 4px;
  opacity: 0;
  transform: translateY(-50%) translateX(4px);
  pointer-events: none;
  box-shadow: 0 8px 20px rgba(36, 33, 29, .12);
  transition: opacity 140ms ease, transform 140ms ease;
}

.session-time {
  justify-self: end;
  padding: 0 10px 0 4px;
  color: #8b867d;
  font-size: 11px;
  font-weight: 760;
  white-space: nowrap;
}

.session-row:hover .session-time,
.session-row:focus-within .session-time {
  opacity: 0;
}

.session-row:hover .item-actions,
.session-row:focus-within .item-actions {
  opacity: 1;
  transform: translateY(-50%);
  pointer-events: auto;
}

.item-actions span {
  color: #8b867d;
  padding: 0 5px;
  font-size: 11px;
  white-space: nowrap;
}

.item-actions button {
  border: 0;
  border-radius: 8px;
  background: transparent;
  padding: 5px 6px;
  color: #4f4b45;
  font-size: 12px;
  white-space: nowrap;
}

.item-actions button:hover {
  background: #f1eee8;
}

.danger-link {
  color: #a23b2a !important;
}

.add-session {
  margin-top: 2px;
  font-size: 12px;
}

.folder-enter-active,
.folder-leave-active {
  transition: opacity 160ms ease, transform 160ms ease;
}

.folder-enter-from,
.folder-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.item-title {
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-meta {
  color: #7d786f;
  font-size: 12px;
  line-height: 1.45;
  max-height: 3.1em;
  overflow: hidden;
}

.item-badge {
  justify-self: flex-start;
  width: max-content;
  border: 1px solid #d3c8b8;
  border-radius: 999px;
  background: #f7f1e8;
  color: #5e4d35;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 800;
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
  display: grid;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  scrollbar-width: none;
}

.workspace::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.workspace > section {
  grid-row: 1;
  grid-column: 1;
  min-width: 0;
  min-height: 0;
}

.workspace-fade-enter-active,
.workspace-fade-leave-active {
  transition: opacity 190ms ease;
  will-change: opacity;
}

.workspace-fade-leave-active {
  pointer-events: none;
}

.workspace-fade-enter-from {
  opacity: 0;
}

.workspace-fade-leave-to {
  opacity: 0;
}

.settings-content-enter-active,
.settings-content-leave-active {
  transition: opacity 180ms ease, transform 180ms ease;
}

.settings-content-enter-from {
  opacity: 0;
  transform: translateX(14px);
}

.settings-content-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}

.chat-page, .tool-page, .settings-page {
  min-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 18px;
  padding: 30px clamp(22px, 4vw, 54px);
}

.settings-page {
  align-content: start;
  max-width: 1040px;
}

.settings-panel {
  display: grid;
  align-content: start;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.settings-card {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 12px;
  border: 1px solid #e3e0d8;
  border-radius: 14px;
  background: #fffefb;
  padding: 18px;
}

.settings-card h2 {
  margin: 0;
  font-size: 15px;
}

.settings-card.wide {
  grid-column: 1 / -1;
}

.setting-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px;
}

.setting-stats div {
  border: 1px solid #ebe8e1;
  border-radius: 12px;
  background: #faf8f2;
  padding: 10px;
}

.setting-stats strong {
  display: block;
  font-size: 22px;
  line-height: 1.1;
}

.setting-stats span {
  color: #817b72;
  font-size: 12px;
}

.settings-choice-grid {
  display: grid;
  gap: 8px;
}

.settings-choice-grid.permissions {
  grid-template-columns: 1fr;
}

.settings-choice-grid button {
  min-width: 0;
  display: grid;
  gap: 4px;
  border: 1px solid #dedbd2;
  border-radius: 12px;
  background: #fbfaf6;
  color: #2c2823;
  padding: 11px 12px;
  text-align: left;
}

.settings-choice-grid button:hover,
.settings-choice-grid button.active {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.settings-choice-grid button span {
  color: #746f67;
  font-size: 12px;
  line-height: 1.45;
}

.settings-choice-grid button.active span {
  color: #e7dfd3;
}

.welcome-editor {
  display: grid;
  gap: 10px;
}

.welcome-row {
  display: grid;
  grid-template-columns: minmax(0, 220px) minmax(0, 1fr) auto;
  align-items: end;
  gap: 10px;
  border: 1px solid #ebe8e1;
  border-radius: 12px;
  background: #fbfaf6;
  padding: 10px;
}

.welcome-row button {
  border: 1px solid #e5b8ad;
  border-radius: 10px;
  background: #fffefb;
  padding: 8px 10px;
}

.settings-path-list {
  display: grid;
  gap: 8px;
}

.settings-path-list div {
  display: grid;
  gap: 5px;
}

.settings-path-list span {
  color: #6f6a62;
  font-size: 12px;
  font-weight: 800;
}

.settings-path-list code {
  overflow-wrap: anywhere;
  border: 1px solid #e7e2d8;
  border-radius: 10px;
  background: #f8f5ee;
  padding: 8px 9px;
  font-size: 12px;
}

.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  background: rgba(36, 33, 29, .26);
  padding: 24px;
}

.project-dialog {
  width: min(560px, calc(100vw - 48px));
  max-height: min(680px, calc(100vh - 48px));
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  border: 1px solid #d8d4cb;
  border-radius: 16px;
  background: #fffefb;
  overflow: hidden;
  box-shadow: 0 24px 70px rgba(36, 33, 29, .25);
}

.project-dialog header,
.project-dialog footer {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #ebe8e1;
  padding: 14px 16px;
}

.project-dialog footer {
  align-items: center;
  justify-content: flex-end;
  border-top: 1px solid #ebe8e1;
  border-bottom: 0;
}

.project-dialog footer button {
  border: 1px solid #cfcbc2;
  border-radius: 11px;
  background: #fffefb;
  color: #2c2823;
  padding: 8px 11px;
}

.project-dialog footer .primary-action {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.project-dialog-body {
  min-height: 0;
  display: grid;
  grid-template-rows: auto auto minmax(140px, 1fr) auto auto;
  gap: 10px;
  overflow: hidden;
  padding: 12px;
}

.project-picker-list {
  min-height: 0;
  overflow: auto;
  display: grid;
  align-content: start;
  gap: 3px;
  border: 1px solid #ebe8e1;
  border-radius: 12px;
  background: #fbfaf6;
  padding: 6px;
}

.chat-page {
  height: 100%;
  grid-template-rows: minmax(0, 1fr);
  max-width: 980px;
  margin: 0 auto;
  width: 100%;
  transition: max-width 220ms ease, padding 220ms ease;
}

.chat-page.reviewing {
  max-width: none;
  margin: 0;
  padding: 0;
}

.chat-layout {
  min-height: 0;
  height: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  align-items: stretch;
}

.chat-page.reviewing .chat-layout {
  grid-template-columns: minmax(var(--review-min-width, 360px), 1fr) 4px minmax(var(--chat-min-width, 320px), var(--chat-width, 430px));
  gap: 0;
  justify-content: stretch;
}

.chat-column {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 14px;
}

.chat-page.reviewing .chat-column {
  border-left: 1px solid #e0ddd5;
  padding: 18px;
}

.workbench-column {
  min-width: 0;
  min-height: 0;
  align-self: stretch;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  overflow: hidden;
  background: #fffefb;
}

.workbench-column.split-visible {
  grid-template-rows: minmax(var(--editor-min-height, 260px), 1fr) 5px var(--terminal-height, 240px);
}

.review-panel {
  min-height: 0;
  align-self: stretch;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 12px;
  border: 1px solid #dcd8cf;
  border-radius: 0;
  background: #fffefb;
  overflow: hidden;
  box-shadow: none;
}

.terminal-resizer {
  position: relative;
  z-index: 8;
  min-height: 5px;
  cursor: row-resize;
  background: #e6e3dc;
  transition: background 120ms ease;
}

.terminal-resizer:hover,
.resizing-vertical .terminal-resizer {
  background: #b96f4a;
}

.terminal-resizer::after {
  content: "";
  position: absolute;
  inset: -5px 0;
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid #ebe8e1;
  padding: 10px 12px;
  background: #fbfaf6;
}

.review-head h2 {
  margin: 0;
}

.review-tabs {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
}

.review-tab {
  max-width: 132px;
  min-height: 34px;
  display: grid;
  align-content: center;
  gap: 2px;
  border: 1px solid transparent;
  border-radius: 11px;
  background: transparent;
  color: #625d55;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 760;
  overflow: hidden;
  text-align: left;
}

.review-tab.file {
  width: auto;
  max-width: 150px;
}

.review-tab-title {
  min-width: 0;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-tab:hover {
  background: #f1eee8;
}

.review-tab.active {
  border-color: #dedbd2;
  background: #fffefb;
  color: #24211d;
  box-shadow: 0 1px 0 rgba(36, 33, 29, .04);
}

.review-add {
  position: relative;
}

.review-add-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 20;
  width: 132px;
  border: 1px solid #ddd9d0;
  border-radius: 12px;
  background: #fffefb;
  padding: 6px;
  box-shadow: 0 14px 34px rgba(36, 33, 29, .16);
}

.review-add-menu button {
  width: 100%;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #3b3731;
  padding: 8px 9px;
  text-align: left;
}

.review-add-menu button:hover {
  background: #f4f1ea;
}

.icon-btn {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid #dedbd2;
  border-radius: 10px;
  background: #fbfaf6;
  color: #5f5a52;
  font-size: 20px;
  line-height: 1;
}

.icon-btn:hover {
  background: #f1eee8;
}

.review-pane {
  min-height: 0;
  overflow: auto;
  padding: 14px;
}

.overview-pane {
  display: grid;
  align-content: start;
  gap: 14px;
}

.review-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.review-summary div {
  min-width: 0;
  border: 1px solid #ebe8e1;
  border-radius: 12px;
  background: #faf8f2;
  padding: 10px;
}

.review-summary strong {
  display: block;
  margin-bottom: 2px;
  color: #24211d;
  font-size: 20px;
  line-height: 1.1;
}

.review-summary span {
  color: #7d786f;
  font-size: 12px;
}

.review-section {
  min-height: 0;
  display: grid;
  gap: 9px;
}

.review-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #5d574f;
  font-size: 12px;
  font-weight: 800;
}

.review-section-title em {
  color: #8b867d;
  font-style: normal;
}

.review-item {
  border: 1px solid #e6e2da;
  border-left-width: 3px;
  border-radius: 12px;
  background: #fffefb;
  padding: 11px 12px;
}

.review-item.ok { border-left-color: #5a8f6b; }
.review-item.warn { border-left-color: #b96f4a; }
.review-item.note { border-left-color: #70757d; }

.review-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.review-item-head strong {
  color: #2d2924;
  font-size: 13px;
}

.review-chip {
  border-radius: 999px;
  background: #ece8df;
  color: #625d55;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 800;
}

.review-item p {
  margin: 0;
  color: #6f6a62;
  font-size: 13px;
  line-height: 1.55;
}

.run-trace-card {
  border: 1px solid #e5e0d8;
  border-radius: 14px;
  background: #fffefa;
  padding: 12px;
}

.run-trace-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #746d64;
  font-size: 11px;
}

.run-trace-head span,
.run-trace-head em {
  border: 1px solid #ddd7cc;
  border-radius: 999px;
  padding: 3px 7px;
  font-style: normal;
  background: #f7f4ee;
}

.run-trace-head strong {
  color: #27231f;
  font-size: 13px;
}

.run-trace-card p {
  margin: 10px 0 0;
  color: #5c554d;
  font-size: 12px;
  line-height: 1.6;
}

.policy-strip {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  color: #827b72;
  font-size: 11px;
}

.policy-strip span,
.policy-strip em {
  border-radius: 999px;
  padding: 3px 7px;
  font-style: normal;
  font-weight: 800;
}

.policy-strip small {
  min-width: 0;
  overflow: hidden;
  color: #8d877d;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.risk-low {
  background: #eaf2eb;
  color: #52735d;
}

.risk-medium {
  background: #f6eddc;
  color: #9a6841;
}

.risk-high {
  background: #f7e4e0;
  color: #a34d3f;
}

.context-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.context-chip-list span {
  max-width: 100%;
  border-radius: 8px;
  background: #efebe3;
  color: #4e4840;
  padding: 4px 7px;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline-list {
  display: grid;
  gap: 6px;
  margin-top: 11px;
  opacity: .82;
}

.timeline-row {
  display: grid;
  grid-template-columns: 74px minmax(80px, .65fr) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  color: #777168;
  font-size: 11px;
}

.timeline-row span,
.timeline-row em {
  min-width: 0;
  overflow: hidden;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline-row strong {
  min-width: 0;
  overflow: hidden;
  color: #4e4942;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-event-list {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.run-event {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  color: #5e574f;
  font-size: 12px;
}

.run-event span {
  color: #8c8479;
  font-size: 11px;
}

.run-event strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.review-actions button {
  border: 1px solid #cfcbc2;
  border-radius: 11px;
  background: #fffefb;
  color: #2c2823;
  padding: 8px 10px;
}

.files-pane {
  display: grid;
  grid-template-columns: minmax(190px, 240px) minmax(0, 1fr);
  gap: 0;
  padding: 0;
  overflow: hidden;
}

.files-pane.previewing {
  grid-template-columns: minmax(0, 1fr);
}

.files-pane.previewing .file-preview {
  border-left: 0;
}

.file-sidebar {
  min-height: 0;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  border-right: 1px solid #ebe8e1;
  background: #fbfaf6;
}

.file-toolbar {
  display: flex;
  gap: 6px;
  padding: 10px;
}

.file-toolbar button {
  border: 1px solid #d8d4cb;
  border-radius: 10px;
  background: #fffefb;
  color: #4f4941;
  padding: 7px 8px;
  font-size: 12px;
}

.project-dialog .file-path-bar {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #eeeae3;
  border-bottom: 1px solid #eeeae3;
  padding: 7px 0 7px 10px;
}

.project-dialog .file-path-bar .file-path {
  border: 0;
  padding: 0;
}

.path-actions {
  display: flex;
  gap: 6px;
  padding-right: 2px;
}

.path-actions button {
  border: 1px solid #d8d4cb;
  border-radius: 10px;
  background: #fffefb;
  color: #4f4941;
  padding: 6px 8px;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}

.path-actions button:hover {
  background: #f1eee8;
}

.file-path {
  min-width: 0;
  border-top: 1px solid #eeeae3;
  border-bottom: 1px solid #eeeae3;
  color: #7c766d;
  padding: 8px 11px;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-list {
  min-height: 0;
  overflow: auto;
  padding: 6px;
}

.file-row {
  width: 100%;
  min-height: 32px;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  align-items: center;
  gap: 5px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #403b35;
  padding: 6px 7px;
  text-align: left;
  cursor: pointer;
}

.file-row:hover,
.file-row.selected {
  background: #efebe3;
}

.project-picker-list .file-row {
  min-height: 64px;
  grid-template-columns: 18px minmax(0, 1fr) max-content;
  grid-template-rows: 28px 16px;
  align-items: center;
  column-gap: 7px;
  row-gap: 2px;
  border: 1px solid transparent;
  padding: 8px 9px;
}

.project-picker-list .file-row.selected {
  border-color: #d5c7b6;
  background: #f4eee6;
}

.file-row span {
  color: #8a847b;
  text-align: center;
}

.file-row .dir-glyph {
  grid-row: 1 / 3;
}

.project-picker-list .file-row strong,
.project-picker-list .project-rename-input {
  grid-column: 2;
  grid-row: 1;
  align-self: end;
}

.file-row strong {
  min-width: 0;
  font-size: 12px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-row.dir strong {
  color: #2f5f51;
}

.project-rename-input {
  width: 100%;
  min-width: 0;
  height: 28px;
  padding: 4px 8px;
  font-size: 12px;
}

.file-row .dir-path {
  grid-column: 2 / 3;
  grid-row: 2;
  align-self: start;
  min-width: 0;
  color: #918a80;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 1.25;
}

.file-row .open-dir {
  border: 1px solid #d9d4ca;
  border-radius: 8px;
  background: #fffefb;
  color: #5b554d;
  padding: 4px 7px;
  font-size: 11px;
  font-weight: 750;
}

.file-row .open-dir:hover {
  background: #ebe6dc;
}

.project-dir-actions {
  grid-column: 3;
  grid-row: 1 / 3;
  align-self: center;
  display: flex;
  flex-wrap: nowrap;
  gap: 5px;
}

.file-row .favorite-dir {
  border: 1px solid #d6d4ca;
  border-radius: 8px;
  background: #fffefb;
  color: #5f594f;
  padding: 4px 7px;
  font-size: 11px;
  font-weight: 800;
}

.file-row .favorite-dir:hover {
  background: #f0ece3;
}

.file-row .favorite-dir:disabled {
  border-color: transparent;
  background: #ebe8e0;
  color: #8a847b;
  opacity: 1;
}

.file-row .delete-dir {
  border: 1px solid #e4beb7;
  border-radius: 8px;
  background: #fff8f6;
  color: #9c382b;
  padding: 4px 7px;
  font-size: 11px;
  font-weight: 800;
}

.file-row .delete-dir:hover {
  border-color: #d8998d;
  background: #ffece7;
}

.directory-delete-confirm {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  border: 1px solid #e5beb6;
  border-radius: 13px;
  background: #fff6f3;
  color: #3f2b26;
  padding: 11px 12px;
}

.directory-delete-confirm div:first-child {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.directory-delete-confirm strong {
  font-size: 13px;
}

.directory-delete-confirm span {
  min-width: 0;
  overflow: hidden;
  color: #9a3d30;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.directory-delete-confirm em {
  color: #74564f;
  font-size: 12px;
  font-style: normal;
}

.directory-delete-actions {
  display: flex;
  gap: 7px;
}

.directory-delete-actions button {
  border: 1px solid #d6d0c8;
  border-radius: 10px;
  background: #fffefb;
  color: #3f3933;
  padding: 7px 9px;
  font-size: 12px;
  font-weight: 800;
}

.directory-delete-actions .danger-action {
  border-color: #b84434;
  background: #b84434;
  color: #fffdf9;
}

.directory-delete-actions .danger-action:hover {
  background: #9f382b;
}

.file-preview {
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  background: #fffefb;
}

.file-preview header,
.terminal-dock header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #ebe8e1;
  padding: 13px 15px;
}

.file-preview h2,
.terminal-dock h2 {
  margin-bottom: 0;
}

.file-preview header span {
  color: #928b82;
  font-size: 12px;
  white-space: nowrap;
}

.file-preview header > div {
  min-width: 0;
}

.file-full-path {
  display: block;
  max-width: min(560px, 48vw);
  overflow: hidden;
  color: #8c867d;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.file-preview-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.file-preview-actions button {
  border: 1px solid #d8d4cb;
  border-radius: 10px;
  background: #fffefb;
  color: #4f4941;
  padding: 7px 9px;
  font-size: 12px;
}

.file-preview-actions button:hover {
  background: #f1eee8;
}

.file-editor-shell {
  min-height: 0;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  overflow: hidden;
  background: #fffefb;
}

.file-editor-shell.dark {
  background: #1f211f;
  color: #f4f0e8;
}

.file-editor-shell.paper {
  background: #fbf7ee;
}

.file-editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #eeeae3;
  padding: 9px 12px;
  background: #fbfaf6;
}

.file-editor-shell.dark .file-editor-toolbar {
  border-bottom-color: #343833;
  background: #252823;
}

.file-editor-toolbar select {
  width: auto;
  min-height: 30px;
  border: 1px solid #d8d4cb;
  border-radius: 11px;
  background: #fffefb;
  color: #4f4941;
  padding: 5px 28px 5px 9px;
  font-size: 12px;
}

.file-editor-shell.dark .file-editor-toolbar select {
  border-color: #4a5048;
  background: #20231f;
  color: #f2eee6;
}

.editor-segmented {
  display: flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #d8d4cb;
  border-radius: 12px;
  background: #f3f0e9;
  padding: 3px;
}

.editor-segmented button {
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #5d574f;
  padding: 5px 9px;
  font-size: 12px;
  font-weight: 760;
}

.editor-segmented button.active {
  background: #fffefb;
  color: #24211d;
  box-shadow: 0 1px 0 rgba(36, 33, 29, .06);
}

.editor-prompt {
  margin: 0;
  border-bottom: 1px solid #eeeae3;
  background: #f7f4ed;
  color: #655f56;
  padding: 9px 13px;
  font-size: 12px;
  line-height: 1.5;
}

.file-editor-textarea {
  min-height: 0;
  width: 100%;
  height: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: inherit;
  padding: 16px;
  resize: none;
  box-shadow: none;
  font: 13px/1.7 "Cascadia Code", Consolas, monospace;
  tab-size: 2;
}

.file-editor-textarea:focus {
  border-color: transparent;
  box-shadow: none;
}

.markdown-preview {
  min-height: 0;
  overflow: auto;
  padding: 18px 22px 32px;
  color: #2c2823;
  line-height: 1.75;
}

.file-editor-shell.dark .markdown-preview {
  color: #f1ece3;
}

.markdown-preview h1,
.markdown-preview h2,
.markdown-preview h3,
.markdown-preview h4 {
  margin: 1em 0 .4em;
  color: inherit;
  letter-spacing: 0;
}

.markdown-preview p {
  margin: 0 0 .9em;
  color: inherit;
}

.markdown-preview code {
  border-radius: 5px;
  background: rgba(36, 33, 29, .08);
  padding: 1px 5px;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: .92em;
}

.markdown-preview pre {
  overflow: auto;
  border: 1px solid #e5e0d6;
  border-radius: 12px;
  background: #f8f5ee;
  padding: 12px;
}

.markdown-preview pre code {
  background: transparent;
  padding: 0;
}

.markdown-empty {
  color: #817b72;
}

.terminal-dock {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid #2c2c29;
  border-top: 0;
  background: #20211f;
  color: #f5f0e7;
}

.terminal-dock header {
  min-height: 30px;
  align-items: center;
  border-bottom-color: #383a35;
  background: #252722;
  padding: 5px 10px;
}

.terminal-dock header span {
  color: #d7d0c4;
  font-size: 12px;
  font-weight: 800;
}

.terminal-dock pre {
  min-height: 0;
  margin: 0;
  overflow: auto;
  color: #e8e2d8;
  background: #20211f;
  padding: 16px;
  font: 12px/1.7 "Cascadia Code", Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}

.file-empty {
  display: grid;
  place-items: center;
  color: #817b72;
  padding: 22px;
  text-align: center;
}

.file-empty.small {
  min-height: 68px;
  padding: 12px 8px;
  font-size: 12px;
}

.review-pop-enter-active,
.review-pop-leave-active {
  transition: opacity 190ms ease, transform 190ms ease;
}

.review-pop-enter-from,
.review-pop-leave-to {
  opacity: 0;
  transform: translateX(-12px) scale(.985);
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

.head-actions button, .primary-action, .secondary-action, .merge-row button {
  border: 1px solid #cfcbc2;
  border-radius: 11px;
  background: #fffefb;
  color: #2c2823;
  padding: 9px 12px;
}

.head-actions button:hover, .primary-action:hover, .secondary-action:hover, .merge-row button:hover,
.ghost-btn:hover, .quiet-wide:hover {
  background: #f5f2ec;
}

.head-actions button.active, .secondary-action.active {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.model-test-status {
  max-width: min(520px, 44vw);
  overflow: hidden;
  border: 1px solid #dedbd2;
  border-radius: 999px;
  background: #fbfaf6;
  color: #5d574f;
  padding: 8px 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 760;
}

.model-test-status.pending {
  color: #6f665b;
}

.model-test-status.ok {
  border-color: #c9d8ce;
  background: #f2f7f3;
  color: #38674a;
}

.model-test-status.error {
  border-color: #e5c2bb;
  background: #fff6f3;
  color: #9a3d30;
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

.chat-content-enter-active,
.chat-content-leave-active {
  transition: opacity 180ms ease, transform 180ms ease;
}

.chat-content-enter-from {
  opacity: 0;
  transform: translateX(14px);
}

.chat-content-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}

.chat-page.reviewing .chat-surface {
  border-left: 0;
  padding-left: 0;
}

.chat-page.reviewing .messages {
  min-height: 0;
}

.chat-page.reviewing .message {
  max-width: 100%;
}

.chat-page.reviewing .composer {
  grid-template-columns: 1fr;
}

.messages {
  min-height: 420px;
  overflow: auto;
  padding: 8px 2px 20px;
}

.empty-chat {
  min-height: 340px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: #777169;
  text-align: center;
  padding: 22px;
}

.empty-chat strong {
  color: #24211d;
  font-size: clamp(22px, 3vw, 34px);
  font-weight: 760;
  letter-spacing: 0;
}

.empty-chat span {
  max-width: 520px;
  color: #746f67;
  font-size: 15px;
  line-height: 1.65;
}

.empty-state {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 340px;
  gap: 8px;
  border: 1px dashed #d8d6ce;
  border-radius: 14px;
  color: #777169;
  text-align: center;
  padding: 22px;
}

.empty-state strong { color: #2c2823; }

.message {
  max-width: 760px;
  margin-bottom: 20px;
  animation: message-rise 180ms ease-out both;
}

.message.user {
  margin-left: auto;
  max-width: min(78%, 760px);
  display: grid;
  justify-items: end;
}

.message.assistant {
  max-width: min(92%, 820px);
}

.message.pending {
  opacity: .92;
}

.message.streaming .message-body::after {
  content: "";
  display: inline-block;
  width: 6px;
  height: 1em;
  margin-left: 3px;
  border-radius: 999px;
  background: #9a948a;
  vertical-align: -2px;
  animation: stream-caret 900ms ease-in-out infinite;
}

@keyframes message-rise {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes stream-caret {
  0%, 100% { opacity: .18; }
  50% { opacity: .7; }
}

.message-role {
  margin-bottom: 6px;
  color: #8b867d;
  font-size: 12px;
  font-weight: 700;
}

.message.user .message-role { text-align: right; }

.message-meta {
  margin: -2px 0 8px;
  color: #9a948a;
  font-size: 11px;
  line-height: 1.35;
  letter-spacing: 0;
}

.message-body {
  width: fit-content;
  max-width: 100%;
  white-space: pre-wrap;
  line-height: 1.72;
  border: 1px solid #e4e1d9;
  border-radius: 14px;
  background: #fffefb;
  padding: 13px 15px;
}

.message-body.markdown-body {
  width: auto;
  white-space: normal;
}

.message.assistant .message-body {
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 0;
}

.message-body.markdown-body p {
  margin: 0 0 .85em;
  color: inherit;
  line-height: 1.72;
}

.message-body.markdown-body p:last-child,
.message-body.markdown-body ul:last-child,
.message-body.markdown-body ol:last-child,
.message-body.markdown-body pre:last-child {
  margin-bottom: 0;
}

.message-body.markdown-body ul,
.message-body.markdown-body ol {
  margin: 0 0 .85em 1.2em;
  padding: 0;
}

.message-body.markdown-body li {
  margin: .25em 0;
  padding-left: .1em;
}

.message-body.markdown-body h1,
.message-body.markdown-body h2,
.message-body.markdown-body h3,
.message-body.markdown-body h4 {
  margin: .6em 0 .35em;
  color: inherit;
  font-size: 1em;
  letter-spacing: 0;
}

.message-body.markdown-body code {
  border-radius: 5px;
  background: rgba(36, 33, 29, .08);
  padding: 1px 5px;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: .92em;
}

.message-body.markdown-body pre {
  overflow: auto;
  margin: 0 0 .85em;
  border: 1px solid #e5e0d6;
  border-radius: 10px;
  background: #f8f5ee;
  padding: 10px;
}

.message-body.markdown-body pre code {
  background: transparent;
  padding: 0;
}

.message.user .message-body {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.composer {
  position: relative;
  display: grid;
  gap: 8px;
  border: 1px solid #d8d6ce;
  border-radius: 18px;
  background: #fffefb;
  padding: 11px;
  box-shadow: 0 12px 30px rgba(36, 33, 29, .08);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.composer:focus-within {
  border-color: #b96f4a;
  box-shadow: 0 0 0 3px rgba(185, 111, 74, .12), 0 16px 36px rgba(36, 33, 29, .1);
}

.composer textarea {
  width: 100%;
  height: auto;
  min-height: 70px;
  max-height: 220px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  padding: 3px 3px 0;
  resize: none;
  box-shadow: none;
  overflow-y: auto;
}

.composer textarea:focus {
  border-color: transparent;
  box-shadow: none;
}

.composer-bar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}

.composer-tools {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}

.composer-add {
  position: relative;
}

.tool-pill {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid #dedbd2;
  border-radius: 999px;
  background: #f8f6f0;
  color: #4f4941;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 760;
}

.tool-pill:hover {
  background: #f1eee8;
}

.pill-chevron {
  color: #8a847b;
  font-size: 13px;
  line-height: 1;
  transform: translateY(-1px);
}

.tool-pill.active .pill-chevron,
.approval-trigger.full .pill-chevron {
  color: currentColor;
}

.private-toggle.active {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
  box-shadow: 0 6px 16px rgba(36, 33, 29, .14);
}

.private-toggle.active:hover {
  background: #332f2a;
}

.composer-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 20;
  width: 154px;
  border: 1px solid #ddd9d0;
  border-radius: 13px;
  background: #fffefb;
  padding: 6px;
  box-shadow: 0 14px 34px rgba(36, 33, 29, .16);
}

.composer-menu button {
  width: 100%;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #3b3731;
  padding: 8px 9px;
  text-align: left;
}

.composer-menu button:hover {
  background: #f4f1ea;
}

.mode-menu-wrap {
  position: relative;
}

.mode-trigger {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mode-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 21;
  width: min(340px, calc(100vw - 48px));
  max-width: calc(100vw - 48px);
  border: 1px solid #ddd9d0;
  border-radius: 16px;
  background: #fffefb;
  padding: 6px;
  box-shadow: 0 18px 46px rgba(36, 33, 29, .18);
}

.mode-option {
  width: 100%;
  min-height: 58px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 22px;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: #2c2823;
  padding: 10px 11px;
  text-align: left;
}

.mode-option:hover,
.mode-option.selected {
  background: #f3f0e9;
}

.mode-option strong {
  display: block;
  color: #24211d;
  font-size: 13px;
  line-height: 1.2;
}

.mode-option em {
  display: block;
  margin-top: 4px;
  color: #756f66;
  font-size: 12px;
  font-style: normal;
  line-height: 1.35;
}

.mode-check {
  color: #24211d;
  font-size: 17px;
  font-weight: 900;
  text-align: center;
}

.approval-menu-wrap {
  position: relative;
}

.approval-trigger {
  max-width: 178px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-trigger.full {
  border-color: #24211d;
  background: #24211d;
  color: #fffdf9;
}

.approval-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 22;
  width: min(420px, calc(100vw - 48px));
  max-width: calc(100vw - 48px);
  border: 1px solid #ddd9d0;
  border-radius: 16px;
  background: #3a3936;
  padding: 5px;
  box-shadow: 0 18px 46px rgba(36, 33, 29, .24);
}

.approval-option {
  width: 100%;
  min-height: 56px;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 20px;
  align-items: center;
  gap: 11px;
  border: 0;
  border-radius: 11px;
  background: transparent;
  color: #f3eee6;
  padding: 9px 10px;
  text-align: left;
}

.approval-option:hover,
.approval-option.selected {
  background: rgba(255, 255, 255, .1);
}

.approval-icon {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, .42);
  border-radius: 999px;
  color: #f5efe6;
  font-size: 12px;
  font-weight: 900;
}

.approval-option strong {
  display: block;
  color: #fffaf1;
  font-size: 13px;
  line-height: 1.2;
}

.approval-option em {
  display: block;
  margin-top: 3px;
  color: #cfc7bb;
  font-size: 12px;
  font-style: normal;
  line-height: 1.35;
}

.approval-check {
  color: #fffaf1;
  font-size: 18px;
  font-weight: 900;
  text-align: center;
}

.send-button {
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 999px;
  background: #24211d;
  color: #fffdf9;
  padding: 0;
  font-size: 20px;
  font-weight: 800;
  line-height: 1;
}

.send-button:hover {
  background: #3a342e;
}

.send-button:disabled {
  background: #d6d2c9;
  color: #817b72;
  opacity: 1;
}

.composer-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.attachment-chip {
  min-width: 0;
  max-width: 210px;
  display: inline-grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px;
  border: 1px solid #dfdbd2;
  border-radius: 999px;
  background: #f8f6f0;
  color: #4f4941;
  padding: 5px 8px;
  font-size: 12px;
}

.attachment-chip span {
  color: #817b72;
  font-weight: 760;
}

.attachment-chip strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-chip em {
  color: #9a5f3f;
  font-style: normal;
  font-weight: 900;
}

.hidden-input {
  display: none;
}

.soft-note {
  margin: 0;
  border-left: 3px solid #b96f4a;
  padding: 8px 10px;
  color: #5f5a52;
  background: #f6f1ec;
  border-radius: 10px;
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

.model-content-enter-active,
.model-content-leave-active {
  transition: opacity 180ms ease, transform 180ms ease;
}

.model-content-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.model-content-leave-to {
  opacity: 0;
  transform: translateX(-8px);
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

.model-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.model-actions .primary-action {
  margin-top: 0;
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
  .chat-list-indicator,
  .primary-nav button,
  .panel-fade-enter-active,
  .panel-fade-leave-active,
  .chat-slide-left-enter-active,
  .chat-slide-left-leave-active,
  .chat-slide-right-enter-active,
  .chat-slide-right-leave-active,
  .list-card-enter-active,
  .list-card-leave-active,
  .list-card-move,
  .settings-shell-enter-active,
  .settings-shell-leave-active,
  .settings-content-enter-active,
  .settings-content-leave-active,
  .review-pop-enter-active,
  .review-pop-leave-active,
  .chat-content-enter-active,
  .chat-content-leave-active,
  .model-content-enter-active,
  .model-content-leave-active,
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

  .sidebar-resizer,
  .column-resizer {
    display: none;
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

  .chat-page.reviewing .chat-layout {
    grid-template-columns: 1fr !important;
  }

  .chat-page.reviewing .chat-surface {
    border-left: 0;
    padding-left: 0;
  }

  .chat-page.reviewing .chat-column {
    border-left: 0;
    padding-left: 0;
  }

  .files-pane {
    grid-template-columns: 1fr;
  }

  .file-sidebar {
    max-height: 280px;
    border-right: 0;
    border-bottom: 1px solid #ebe8e1;
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
