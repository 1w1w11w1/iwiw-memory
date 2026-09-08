/**
 * iwiw 记忆 — 注入折叠：纯计算逻辑（与 DOM 无关，可在宿主冒烟中单测）。
 *
 * 迁移自 meow-memory（MIT License，Copyright Phant0Meow）client-fold.ts，
 * 按 dsh-iwiw-memory 的快照消息 shape 适配（PLUGIN_NAME 与 source.plugin 对齐）。
 *
 * 识别：会话快照 chat 节点里 kind='context' 且 source 为
 * { kind: 'plugin', plugin: 'dsh-iwiw-memory' } 的节点 = 插件注入的快照消息。
 * fail-closed：快照形状变化时降级为不折叠，绝不抛错——computeInjectionGroups
 * 在每次渲染都跑，一炸就是一整个会话视图。
 */

/** 会话快照的最小结构（与 DSH client 运行时的 ConversationSnapshot 结构兼容）。 */
export interface SnapshotNode {
  kind: string
  data: {
    source?: { kind?: string; plugin?: string; form?: string; memory?: { kind?: string } }
    content?: readonly { type?: string; text?: string }[]
    time?: unknown
    root?: { name?: string; call?: { name?: string } | null }
    status?: string
  }
  location?: { kind?: string; turn?: { turn?: number } }
}

export interface ConversationSnapshotLike {
  chat?: {
    order: readonly string[]
    nodes: Map<string, SnapshotNode>
    locations: { getTurn: (turn: number) => readonly string[] }
  }
}

/** 插件 source 识别（与 host 端 index.ts 快照消息保持一致）。 */
export const PLUGIN_NAME = 'dsh-iwiw-memory'

/** 反思/dream 轮识别标记（与 host 端 reflect/dream 保持一致；当前未启用，预留）。 */
export const REFLECT_MARKER = '[iwiw-memory-reflect]'
export const DREAM_MARKER = '[iwiw-memory-dream]'

export type InjectionKind = 'first' | 'hit'

/** 一个可折叠的记忆注入（独立快照 context 节点）。 */
export interface InjectionGroup {
  /** 要隐藏并在其原位放置横条的节点 key。 */
  readonly id: string
  readonly kind: InjectionKind
  /** 注入的完整文本。 */
  readonly injectedText: string
}

interface ContextLike {
  readonly source?: unknown
  readonly content?: readonly { type?: string; text?: string }[]
}

/** 从 content blocks 提取纯文本。 */
export function blocksToText(blocks: readonly { type?: string; text?: string }[]): string {
  return blocks
    .map((block) => block.text ?? '')
    .join('\n')
    .trim()
}

function contextText(node: SnapshotNode): string {
  return blocksToText((node.data as ContextLike).content ?? [])
}

/**
 * 从会话快照计算全部可折叠的注入组。
 * 新格式识别 source.memory.kind (hit/reinjection) 机器元数据，解耦于自然语言文本。
 */
export function computeInjectionGroups(snapshot: ConversationSnapshotLike): InjectionGroup[] {
  if (snapshot?.chat === undefined) return [] // fail-closed：快照无 chat 时不折叠，绝不抛错
  const groups: InjectionGroup[] = []
  for (const key of snapshot.chat.order) {
    const node = snapshot.chat.nodes.get(key)
    if (node === undefined) continue
    if (node.kind !== 'context') continue
    const source = (node.data as ContextLike).source as {
      kind?: string
      plugin?: string
      form?: string
      memory?: { kind?: string }
    } | undefined
    if (source?.kind !== 'plugin' || source.plugin !== PLUGIN_NAME) continue
    const memKind = source.memory?.kind
    if (memKind === 'reinjection') {
      groups.push({ id: key, kind: 'first', injectedText: contextText(node) })
      continue
    }
    if (memKind === 'hit') {
      groups.push({ id: key, kind: 'hit', injectedText: contextText(node) })
      continue
    }
    if (source.form === 'snapshot') {
      // 未带 memory.kind 的旧快照：按文本内容判定
      const injectedText = contextText(node)
      const isHit = injectedText.includes('相关记忆（命中）')
      groups.push({ id: key, kind: isHit ? 'hit' : 'first', injectedText })
    }
  }
  return groups
}
