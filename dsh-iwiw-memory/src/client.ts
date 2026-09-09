/**
 * client.ts — iwiw-memory 渲染端入口。
 *
 * 职责：注册设置页「iwiw 记忆」标签页（settings.section 顶级分区）。
 *
 * 注入的记忆快照（首轮常驻 / 每消息命中 / reflect 回顾 / consolidate 巩固报告）
 * 由 DSH 原生 context 行渲染，不做客户端 DOM 改写；专属呈现方案（自有设计）
 * 待定型后在此入口扩展。
 */

import { applySettingsPage } from './settings-page'

export const inject = ['slots', 'settingsScope']

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function apply(ctx: any): () => void {
  // 设置页「iwiw 记忆」标签页（settings.section 顶级分区）：settingsScope 服务
  // 缺失或注册失败只警告，不影响插件其余功能。
  try {
    applySettingsPage(ctx)
  } catch (e) {
    console.warn('[iwiw-memory] 设置页注册失败：', e)
  }

  return () => {
    /* 渲染端当前无持有时钟/观察者需要清理；入口保留 dispose 形状以符合契约。 */
  }
}
