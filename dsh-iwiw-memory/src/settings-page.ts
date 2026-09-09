/**
 * settings-page.ts — 设置页「iwiw 记忆」标签页。
 *
 * 形态：settings.section 顶级分区（与「通用」「模型」「插件」平级）。契约：
 * client 挂 list slot（id+order+label）+ settingsScope.bind({namespace}) 读写
 * user 层；host 半身在 index.ts apply 里 ctx.inject(["settings"]) →
 * settings.register(ns, settingsSchema, { base: patch config 归一化值 })，
 * base=patch config，user 层字段级覆盖，大部分字段热生效（消费点读 overrides）。
 *
 * 字段与 host 侧消费一一对应：hitTopK / coreMaxChars / reflectTurns /
 * consolidateIdleMinutes / standingLayers。python/cwd/env 属部署配置，仅在
 * cordis.patch.yml 修改（不在页面暴露）。
 */

import * as React from 'react'

const SETTINGS_NS = 'dsh-iwiw-memory'
const CSS_ID = 'iwiw-memory-settings-css'

const CSS = `
.iwiw_set_page{color:var(--dsw-alias-label-primary);display:flex;flex-direction:column;gap:10px;max-width:760px;padding:4px 0}
.iwiw_set_title{font-size:16px;font-weight:600;margin:0}
.iwiw_set_subtitle{color:var(--dsw-alias-label-caption);font-size:12px;line-height:1.6;margin:0}
.iwiw_set_card{background:color-mix(in srgb,currentColor 3%,transparent);border:1px solid var(--dsw-alias-border-l3);border-radius:10px;display:flex;flex-direction:column;gap:8px;padding:12px}
.iwiw_set_group{color:var(--dsw-alias-label-secondary);font-size:12px;font-weight:600;margin-top:2px}
.iwiw_set_row{align-items:flex-start;display:flex;gap:10px;justify-content:space-between}
.iwiw_set_rowtext{display:flex;flex-direction:column;gap:2px;min-width:0}
.iwiw_set_label{font-size:13px;font-weight:500}
.iwiw_set_hint{color:var(--dsw-alias-label-caption);font-size:12px;line-height:1.5}
.iwiw_set_ctrl{flex:none;padding-top:2px}
.iwiw_set_input{background:transparent;border:1px solid var(--dsw-alias-border-l3);border-radius:6px;color:inherit;font-size:13px;padding:4px 8px;width:190px}
.iwiw_set_check{cursor:pointer}
.iwiw_set_badge{border-radius:999px;font-size:11px;line-height:16px;padding:0 8px;flex:none}
.iwiw_set_badge_override{background:color-mix(in srgb,#f59e0b 18%,transparent);color:#f59e0b}
.iwiw_set_badge_prefill{background:color-mix(in srgb,#60a5fa 18%,transparent);color:#60a5fa}
.iwiw_set_reset{background:transparent;border:1px solid var(--dsw-alias-border-l3);border-radius:6px;color:var(--dsw-alias-label-secondary);cursor:pointer;font-size:12px;padding:2px 8px}
.iwiw_set_reset:hover{border-color:var(--dsw-alias-border-l2);color:inherit}
.iwiw_set_err{color:#f43f5e;font-size:12px;line-height:1.5;margin:0}
.iwiw_set_saved{color:#34d399;font-size:12px}
.iwiw_set_muted{color:var(--dsw-alias-label-caption);font-size:12px}
`

const el = React.createElement

/** 字段元数据：全部为顶层标量（host 侧 settingsSchema 白名单同步维护）。 */
interface FieldSpec {
  key: string
  label: string
  type: 'bool' | 'num' | 'str'
  hint?: string
  placeholder?: string
}

interface GroupSpec {
  title: string
  fields: FieldSpec[]
}

const FIELDS: GroupSpec[] = [
  {
    title: '注入与命中',
    fields: [
      { key: 'hitTopK', label: '每条消息命中注入条数上限', type: 'num', hint: '每条用户消息最多联想注入的记忆条数，改完下一条消息生效' },
      { key: 'coreMaxChars', label: '常驻记忆段字符预算', type: 'num', hint: '长期记忆（常驻层）注入的总字符上限，超出按条截断' },
    ],
  },
  {
    title: '回顾与巩固',
    fields: [
      { key: 'reflectTurns', label: '回顾提示触发步数', type: 'num', hint: '连续 N 步未写入记忆时注入一次性回顾提示；0=关闭' },
      { key: 'consolidateIdleMinutes', label: '空闲巩固阈值（分钟）', type: 'num', hint: '空闲满该分钟数且非峰时（9-12/14-18）自动触发记忆巩固；0=关闭' },
    ],
  },
  {
    title: '常驻层（模式配置）',
    fields: [
      { key: 'standingLayers', label: '常驻记忆类型', type: 'str', hint: '逗号分隔：profile/fact/lesson/rules/project。chat 模式=profile,rules；dev 模式建议 rules。这些类型全量注入每轮，其余按话题检索召回', placeholder: 'profile,rules' },
    ],
  },
]

/** 读某字段的当前合成值。 */
function fieldValue(value: Record<string, unknown> | undefined, spec: FieldSpec): unknown {
  return value?.[spec.key]
}

/** 是否在 user 层（=已覆盖，可恢复预填）。 */
function inUserLayer(user: Record<string, unknown> | undefined, spec: FieldSpec): boolean {
  return user !== undefined && spec.key in user
}

/** JSON 数据结构相等：镜像值是冻结快照的深拷贝，引用必不同，只能按结构比。 */
function jsonEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((entry, i) => jsonEqual(entry, b[i]))
  }
  const ka = Object.keys(a as Record<string, unknown>)
  const kb = Object.keys(b as Record<string, unknown>)
  return ka.length === kb.length
    && ka.every((k) => k in (b as Record<string, unknown>) && jsonEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
}

// ── 页面 ────────────────────────────────────────────────────────────────────

function MemorySettingsSection(props: { scope: any }): any {
  const scope = props.scope
  const subscribe = React.useCallback((cb: () => void) => scope.subscribe(cb), [scope])
  const getSnapshot = React.useCallback(() => scope.getSnapshot(), [scope])
  const snap: {
    status: string
    value: Record<string, unknown> | undefined
    base: Record<string, unknown> | undefined
    user: Record<string, unknown> | undefined
    writable: boolean
    mode: string
  } = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  const [savedAt, setSavedAt] = React.useState(0)
  const [error, setError] = React.useState<string | null>(null)
  // 本地草稿（受控输入先写本地态再异步落库，避免往返延迟里被 React 回滚）。
  const [drafts, setDrafts] = React.useState<Record<string, string | boolean>>({})

  const flashSaved = (): void => {
    setSavedAt(Date.now())
    window.setTimeout(() => setSavedAt((t) => (t === 0 ? 0 : t)), 4000)
  }

  const clearDraft = (spec: FieldSpec): void => {
    const key = spec.key
    setDrafts((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  /** 写一个字段（scope.set 单层键）。成功与否回读 user 层判定——
   *  写入被校验拒绝时 client 静默 recover 重载镜像，不能看返回值。 */
  const apply = async (spec: FieldSpec, newValue: unknown): Promise<boolean> => {
    setError(null)
    try {
      await scope.set(spec.key, newValue)
    } catch (e) {
      setError(`保存失败：${e instanceof Error ? e.message : String(e)}`)
      return false
    }
    const landed = (): boolean => {
      const v = scope.getSnapshot().user as Record<string, unknown> | undefined
      return jsonEqual(v?.[spec.key], newValue)
    }
    if (landed()) {
      flashSaved()
      return true
    }
    // 写入被排队 supersede 时镜像尚未 publish，给一点追赶时间再复查。
    await new Promise((resolve) => window.setTimeout(resolve, 300))
    if (landed()) {
      flashSaved()
      return true
    }
    clearDraft(spec)
    setError('保存未生效：写入被服务器拒绝（可能未通过校验），已恢复显示服务器当前值。')
    return false
  }

  const reset = async (spec: FieldSpec): Promise<void> => {
    setError(null)
    try {
      await scope.unset(spec.key)
      const gone = (): boolean => {
        const v = scope.getSnapshot().user as Record<string, unknown> | undefined
        return v?.[spec.key] === undefined
      }
      if (!gone()) await new Promise((resolve) => window.setTimeout(resolve, 300))
      if (gone()) {
        flashSaved()
      } else {
        setError('恢复默认未生效，请重试。')
      }
    } catch (e) {
      setError(`恢复默认失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (snap.status === 'loading') {
    return el('div', { className: 'iwiw_set_page' }, el('span', { className: 'iwiw_set_muted' }, 'iwiw 记忆配置加载中…'))
  }
  if (snap.status === 'unavailable') {
    return el('div', { className: 'iwiw_set_page' }, el('span', { className: 'iwiw_set_muted' }, '当前连接不支持设置写入（仅本机回环连接可编辑）。'))
  }

  const renderField = (spec: FieldSpec): any => {
    const raw = fieldValue(snap.value, spec)
    const overridden = inUserLayer(snap.user, spec)
    const mirrorText = typeof raw === 'string' ? raw : (spec.type === 'num' && typeof raw === 'number' ? String(raw) : '')
    const draft = drafts[spec.key]
    let control: any = null
    if (spec.type === 'bool') {
      const checked = typeof draft === 'boolean' ? draft : raw === true
      control = el('input', {
        className: 'iwiw_set_check',
        type: 'checkbox',
        checked,
        disabled: !snap.writable,
        onChange: (e: any) => {
          const next = e.target.checked
          setDrafts((prev) => ({ ...prev, [spec.key]: next }))
          void apply(spec, next).then((ok) => {
            if (ok) clearDraft(spec)
          })
        },
      })
    } else if (spec.type === 'num') {
      const text = typeof draft === 'string' ? draft : mirrorText
      control = el('input', {
        className: 'iwiw_set_input',
        type: 'number',
        value: text,
        disabled: !snap.writable,
        onChange: (e: any) => setDrafts((prev) => ({ ...prev, [spec.key]: e.target.value })),
        onBlur: (e: any) => {
          const v = e.target.value
          const num = Number(v)
          if (v.trim() === '' || !Number.isFinite(num) || num === raw) {
            clearDraft(spec)
            return
          }
          void apply(spec, num).then((ok) => {
            if (ok) clearDraft(spec)
          })
        },
      })
    } else {
      const text = typeof draft === 'string' ? draft : mirrorText
      control = el('input', {
        className: 'iwiw_set_input',
        type: 'text',
        value: text,
        placeholder: spec.placeholder,
        disabled: !snap.writable,
        onChange: (e: any) => setDrafts((prev) => ({ ...prev, [spec.key]: e.target.value })),
        onBlur: (e: any) => {
          const next = e.target.value
          if (next === mirrorText) {
            clearDraft(spec)
            return
          }
          void apply(spec, next).then((ok) => {
            if (ok) clearDraft(spec)
          })
        },
      })
    }
    return el(
      'div',
      { key: spec.key, className: 'iwiw_set_row' },
      el(
        'div',
        { className: 'iwiw_set_rowtext' },
        el('span', { className: 'iwiw_set_label' }, spec.label),
        spec.hint !== undefined ? el('span', { className: 'iwiw_set_hint' }, spec.hint) : null,
      ),
      el(
        'div',
        { className: 'iwiw_set_ctrl', style: { display: 'flex', gap: '8px', alignItems: 'center' } },
        control,
        el('span', { className: `iwiw_set_badge ${overridden ? 'iwiw_set_badge_override' : 'iwiw_set_badge_prefill'}` }, overridden ? '已覆盖' : '默认'),
        overridden && snap.writable ? el('button', { className: 'iwiw_set_reset', onClick: () => { clearDraft(spec); void reset(spec) } }, '恢复默认') : null,
      ),
    )
  }

  return el(
    'div',
    { className: 'iwiw_set_page' },
    el('h2', { className: 'iwiw_set_title' }, 'iwiw 记忆'),
    el(
      'p',
      { className: 'iwiw_set_subtitle' },
      '本地长期记忆插件的运行参数。改动保存在 DSH 设置里（字段级，可单项恢复默认）；hitTopK / reflectTurns / consolidateIdleMinutes / standingLayers 热生效，coreMaxChars 在下次 core 段刷新时生效。',
    ),
    !snap.writable ? el('span', { className: 'iwiw_set_muted' }, '当前连接为只读（设置写入仅限本机回环连接）。') : null,
    savedAt > 0 ? el('span', { className: 'iwiw_set_saved' }, '已保存 ✓') : null,
    error !== null ? el('div', { className: 'iwiw_set_err' }, error) : null,
    ...FIELDS.map((group) =>
      el(
        'div',
        { key: group.title, className: 'iwiw_set_card' },
        el('div', { className: 'iwiw_set_group' }, group.title),
        ...group.fields.map(renderField),
      ),
    ),
  )
}

// ── 挂载 ────────────────────────────────────────────────────────────────────

export function applySettingsPage(ctx: any): void {
  if (typeof document !== 'undefined' && document.querySelector(`style[data-plugin-css="${CSS_ID}"]`) === null) {
    const tag = document.createElement('style')
    tag.dataset.plugin = 'iwiw-memory-settings'
    tag.dataset.pluginCss = CSS_ID
    tag.textContent = CSS
    document.head.appendChild(tag)
  }

  const scope = ctx.settingsScope.bind({ namespace: SETTINGS_NS })

  // 顶级分区（与「通用」「模型」「插件」平级）：list slot 契约 = id + order + label。
  ctx.slots.inject('settings.section', () =>
    ctx.slots.register(
      {
        name: 'settings.section',
        id: SETTINGS_NS,
        order: 35,
        label: () => 'iwiw 记忆',
        inject: (): unknown => ({ scope }),
      },
      MemorySettingsSection,
    ),
  )
}
