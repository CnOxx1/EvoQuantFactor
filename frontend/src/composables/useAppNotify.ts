import { computed, ref } from 'vue'

export type NotifyLevel = 'info' | 'success' | 'warning' | 'error'

export type AppNotification = {
  id: string
  module: string
  title: string
  content: string
  level: NotifyLevel
  createdAt: number
  read: boolean
  dedupeKey?: string
}

type PopupApi = {
  info: (opts: Record<string, unknown>) => void
  success: (opts: Record<string, unknown>) => void
  warning: (opts: Record<string, unknown>) => void
  error: (opts: Record<string, unknown>) => void
}

const MAX_ITEMS = 80
const DEDUPE_MS = 60_000

const items = ref<AppNotification[]>([])
let popupApi: PopupApi | null = null
const recentDedupe = new Map<string, number>()

export function bindNotificationApi(api: PopupApi | null) {
  popupApi = api
}

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

function shouldDedupe(key?: string) {
  if (!key) return false
  const now = Date.now()
  const prev = recentDedupe.get(key)
  if (prev && now - prev < DEDUPE_MS) return true
  recentDedupe.set(key, now)
  if (recentDedupe.size > 200) {
    for (const [k, t] of recentDedupe) {
      if (now - t > DEDUPE_MS) recentDedupe.delete(k)
    }
  }
  return false
}

export type NotifyOptions = {
  module: string
  title?: string
  content: string
  level?: NotifyLevel
  /** 默认 true：右上角弹窗 */
  popup?: boolean
  dedupeKey?: string
  duration?: number
}

export function notify(opts: NotifyOptions) {
  const level = opts.level || 'info'
  const title = (opts.title || opts.module).trim()
  const content = String(opts.content || '').trim()
  if (!content) return null
  if (shouldDedupe(opts.dedupeKey)) return null

  const item: AppNotification = {
    id: uid(),
    module: opts.module,
    title,
    content,
    level,
    createdAt: Date.now(),
    read: false,
    dedupeKey: opts.dedupeKey,
  }
  items.value = [item, ...items.value].slice(0, MAX_ITEMS)

  if (opts.popup !== false && popupApi) {
    const duration = opts.duration ?? (level === 'error' ? 8000 : 4500)
    const popupTitle = title === opts.module ? opts.module : `${opts.module} · ${title}`
    popupApi[level]({
      title: popupTitle,
      content,
      duration,
      keepAliveOnHover: true,
    })
  }
  return item
}

export function useAppNotify() {
  const unreadCount = computed(() => items.value.filter((x) => !x.read).length)

  function markRead(id: string) {
    const row = items.value.find((x) => x.id === id)
    if (row) row.read = true
  }

  function markAllRead() {
    for (const row of items.value) row.read = true
  }

  function remove(id: string) {
    items.value = items.value.filter((x) => x.id !== id)
  }

  function clear() {
    items.value = []
  }

  return {
    items,
    unreadCount,
    notify,
    markRead,
    markAllRead,
    remove,
    clear,
  }
}

/** 按模块封装：各页面统一用这一套 */
export function useModuleNotify(module: string) {
  return {
    info: (content: string, opts?: Partial<NotifyOptions>) =>
      notify({ module, content, level: 'info', ...opts }),
    success: (content: string, opts?: Partial<NotifyOptions>) =>
      notify({ module, content, level: 'success', ...opts }),
    warning: (content: string, opts?: Partial<NotifyOptions>) =>
      notify({ module, content, level: 'warning', ...opts }),
    error: (content: string, opts?: Partial<NotifyOptions>) =>
      notify({ module, content, level: 'error', ...opts }),
  }
}

export type CollectorStatusLike = {
  enabled?: boolean
  running?: boolean
  last_finished_at?: string | null
  last_added?: number | null
  last_skipped?: number | null
  last_sources?: string[]
  last_error?: string | null
  luobo_configured?: boolean
  luobo_auth_required?: boolean
  fingerprint_skipped?: number
  pdf_refetched?: number
  titles_fixed?: number
  source_stats?: Record<
    string,
    { added?: number; skipped?: number; failed?: number; elapsed_ms?: number; auth_error?: boolean }
  >
  summarize?: {
    queue_depth?: number
    queue_max?: number
    running?: number
    done_total?: number
    failed_total?: number
    dropped_full?: number
  }
}

/** 采集器状态 → 统一右上角通知 */
export function notifyCollectorStatus(
  s: CollectorStatusLike,
  opts?: { forcePopup?: boolean; dedupeKey?: string },
) {
  const sources = (s.last_sources || []).join(',') || '—'
  const parts = [
    s.enabled ? '已开启' : '未开启',
    s.running ? '同步中' : null,
    s.last_finished_at ? `上次同步 ${s.last_finished_at}` : null,
    s.last_added != null ? `新增 ${s.last_added} / 跳过 ${s.last_skipped ?? 0}` : null,
    s.fingerprint_skipped ? `指纹去重 ${s.fingerprint_skipped}` : null,
    s.pdf_refetched ? `PDF重抓 ${s.pdf_refetched}` : null,
    s.titles_fixed ? `标题修复 ${s.titles_fixed}` : null,
    `源 ${sources}`,
    `萝卜投研 ${s.luobo_configured ? '已登录配置' : '未配置 token'}`,
  ]
  const stats = s.source_stats || {}
  const statLines = Object.entries(stats).map(([name, st]) => {
    const bits = [
      `${name}: +${st.added ?? 0}`,
      `跳过 ${st.skipped ?? 0}`,
      st.failed ? `失败 ${st.failed}` : '',
      st.auth_error ? '鉴权失败' : '',
    ].filter(Boolean)
    return bits.join(' · ')
  })
  if (statLines.length) parts.push(`分源 ${statLines.join('；')}`)
  if (s.summarize) {
    parts.push(
      `摘要队列 ${s.summarize.queue_depth ?? 0}/${s.summarize.queue_max ?? '-'} · 运行 ${s.summarize.running ?? 0} · 完成 ${s.summarize.done_total ?? 0} · 失败 ${s.summarize.failed_total ?? 0}`,
    )
  }
  const base = parts.filter(Boolean).join(' · ')

  const hasError = Boolean(s.last_error)
  const luoboWarn = Boolean(s.luobo_auth_required) || (!s.luobo_configured && (s.last_sources || []).includes('luobo'))
  let content = hasError ? `${base} · 最近错误：${s.last_error}` : base
  if (luoboWarn) {
    content +=
      '。萝卜投研登录失效或未配置：请在浏览器登录 https://robo.datayes.com ，复制 Cookie 中的 cloud-sso-token 到后端 LUOBO_CLOUD_SSO_TOKEN 后重启。其它源不受影响。'
  }
  const level: NotifyLevel = hasError || luoboWarn ? 'warning' : opts?.forcePopup ? 'success' : 'info'
  const key =
    opts?.dedupeKey ||
    [
      s.last_finished_at || '',
      s.last_error || '',
      String(s.last_added ?? ''),
      String(s.last_skipped ?? ''),
      s.luobo_configured ? '1' : '0',
      s.luobo_auth_required ? '1' : '0',
      String(s.running ? 1 : 0),
      sources,
    ].join('|')

  return notify({
    module: '资讯采集',
    title: luoboWarn ? '萝卜投研登录失效或未配置' : '采集状态',
    content,
    level,
    dedupeKey: `collector:${key}`,
    duration: hasError || luoboWarn ? 10000 : 6000,
    popup: opts?.forcePopup ?? (hasError || luoboWarn),
  })
}
