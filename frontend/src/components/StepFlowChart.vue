<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { StepDetail, StepSummary } from '@/api/client'

const props = defineProps<{ steps: StepSummary[] }>()

type FlowNode = {
  id: string
  label: string
  badge?: string
  hint?: string
  status: string
  stepType: string
  seq: number
  round: number
  x: number
  y: number
  w: number
  h: number
  kind: 'main' | 'role'
  factorIds: string[]
}

type FlowEdge = { from: string; to: string }

const MAIN_W = 132
const MAIN_H = 58
const ROLE_W = 72
const ROLE_H = 52
const GAP_X = 20
const ROUND_GAP = 26
const PAD_X = 20
const PAD_Y = 16
const LABEL_H = 22

const ROLE_NAMES: Record<string, string> = {
  R1: '量化',
  R2: '基金',
  R3: '风控',
  R4: '卖方',
  R5: '数据',
  R6: '总监',
}

function stepId(s: StepSummary) {
  return s.step_id || `s-${s.seq}`
}

function asDetail(s: StepSummary): StepDetail {
  return {
    ...s,
    payload: (s.payload || {}) as Record<string, unknown>,
  }
}

function typeLabel(s: StepSummary): string {
  switch (s.step_type) {
    case 'ingest':
      return '研报入库'
    case 'step1_extract':
      return '因子提取'
    case 'step2_merge':
      return '评分合并'
    case 'step3_gate':
      return '门槛裁决'
    case 'revise_loop':
      return '回灌修订'
    case 'persist':
      return '结果落盘'
    case 'error':
      return s.status === 'retry' ? '自动重试' : '失败'
    default:
      return (s.title || s.step_type).slice(0, 8)
  }
}

function roleShort(s: StepSummary): string {
  const code = s.role_code || 'R?'
  if (s.role_name) {
    const m = String(s.role_name).replace(/^R\d\s*/, '')
    return m.slice(0, 4) || ROLE_NAMES[code] || code
  }
  const m = s.title?.match(/Step2\s+(.+?)\s+评审/)
  if (m?.[1]) return m[1].slice(0, 4)
  return ROLE_NAMES[code] || code
}

function factorIdsOf(s: StepSummary): string[] {
  if (s.factor_ids?.length) return s.factor_ids.map(String)
  const payload = s.payload || {}
  if (s.step_type === 'step2_review') {
    const reviews = (payload as any).reviews || {}
    return Object.keys(reviews)
  }
  if (s.step_type === 'step1_extract') {
    const changed = (payload as any).changed_ids
    if (Array.isArray(changed) && changed.length) return changed.map(String)
    const factors = (payload as any).factors || []
    return factors.map((f: any) => String(f.factor_id)).filter(Boolean)
  }
  return []
}

function nodeBadge(s: StepSummary): string {
  const ids = factorIdsOf(s)
  if (s.step_type === 'step2_review') {
    return ids.length ? `${ids.length}因子` : `#${s.seq}`
  }
  if (s.step_type === 'step1_extract' || s.step_type === 'step3_gate' || s.step_type === 'revise_loop') {
    return ids.length ? `${ids.length}因子` : `#${s.seq}`
  }
  return `#${s.seq}`
}

function nodeHint(s: StepSummary): string {
  const ids = factorIdsOf(s)
  const head = (s.summary || '').replace(/\s+/g, ' ').trim()
  const idText = ids.length ? `因子: ${ids.join(', ')}` : ''
  return [head, idText].filter(Boolean).join('\n')
}

/** 兼容 schema：definition 可能是对象，也可能是说明字符串 */
function factorMeta(f: any): { name: string; category?: string; formula?: string } {
  const def = f?.definition
  const fromObj =
    def && typeof def === 'object'
      ? def.formula_or_rule || def.formula || def.rule || undefined
      : undefined
  const fromStr = typeof def === 'string' && def.trim() ? def.trim() : undefined
  let formula =
    fromObj ||
    f?.formula_or_rule ||
    f?.formula ||
    f?.factor_formula ||
    f?.calculation ||
    fromStr ||
    f?.origin_text ||
    undefined
  if (typeof formula === 'string') formula = formula.trim() || undefined

  const name = (f?.name_zh || f?.name || f?.name_en || f?.factor_id || '').toString().trim()
  const category = (f?.category || f?.type || '').toString().trim() || undefined
  return { name: name || String(f?.factor_id || ''), category, formula }
}

/** 从各轮 Step1 payload 汇总因子名称/公式（后轮覆盖前轮） */
const factorNameMap = computed(() => {
  const map = new Map<string, { name: string; category?: string; formula?: string }>()
  for (const s of props.steps || []) {
    if (s.step_type !== 'step1_extract') continue
    const factors = ((s.payload || {}) as any).factors || []
    for (const f of factors) {
      if (!f?.factor_id) continue
      map.set(String(f.factor_id), factorMeta(f))
    }
  }
  return map
})

function groupByRound(list: StepSummary[]) {
  const map = new Map<number, StepSummary[]>()
  for (const s of list) {
    let r = Number(s.round || 0)
    if (s.step_type === 'ingest') r = 0
    if (s.step_type === 'persist') r = 99
    if (!map.has(r)) map.set(r, [])
    map.get(r)!.push(s)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0])
}

type RoundLane = {
  round: number
  title: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  width: number
  height: number
  y0: number
}

const layout = computed(() => {
  const sorted = [...(props.steps || [])].sort((a, b) => a.seq - b.seq)
  const lanes: RoundLane[] = []
  let yCursor = PAD_Y
  let contentMaxW = 0

  for (const [round, steps] of groupByRound(sorted)) {
    const title = round === 0 ? '准备' : round === 99 ? '落盘' : `第 ${round} 轮`

    type Stage =
      | { kind: 'one'; step: StepSummary }
      | { kind: 'roles'; steps: StepSummary[] }
    const stages: Stage[] = []
    for (let i = 0; i < steps.length; ) {
      if (steps[i].step_type === 'step2_review') {
        const g: StepSummary[] = []
        while (i < steps.length && steps[i].step_type === 'step2_review') {
          g.push(steps[i])
          i += 1
        }
        stages.push({ kind: 'roles', steps: g })
      } else {
        stages.push({ kind: 'one', step: steps[i] })
        i += 1
      }
    }

    let naturalW = PAD_X
    for (const st of stages) {
      if (st.kind === 'one') naturalW += MAIN_W + GAP_X
      else naturalW += st.steps.length * ROLE_W + Math.max(0, st.steps.length - 1) * 8 + GAP_X
    }
    naturalW = naturalW - GAP_X + PAD_X
    contentMaxW = Math.max(contentMaxW, naturalW)

    const nodes: FlowNode[] = []
    const edges: FlowEdge[] = []
    const stageIds: string[][] = []
    const contentY = yCursor + LABEL_H
    let x = PAD_X
    const roleInnerGap = 8

    for (const st of stages) {
      if (st.kind === 'one') {
        const s = st.step
        const id = stepId(s)
        nodes.push({
          id,
          label: typeLabel(s),
          badge: nodeBadge(s),
          hint: nodeHint(s),
          status: s.status,
          stepType: s.step_type,
          seq: s.seq,
          round,
          x,
          y: contentY,
          w: MAIN_W,
          h: MAIN_H,
          kind: 'main',
          factorIds: factorIdsOf(s),
        })
        stageIds.push([id])
        x += MAIN_W + GAP_X
      } else {
        const ids: string[] = []
        let rx = x
        for (const s of st.steps) {
          const id = stepId(s)
          const code = s.role_code || 'R?'
          nodes.push({
            id,
            label: code,
            badge: roleShort(s),
            hint: nodeHint(s),
            status: s.status,
            stepType: s.step_type,
            seq: s.seq,
            round,
            x: rx,
            y: contentY + (MAIN_H - ROLE_H) / 2,
            w: ROLE_W,
            h: ROLE_H,
            kind: 'role',
            factorIds: factorIdsOf(s),
          })
          ids.push(id)
          rx += ROLE_W + roleInnerGap
        }
        stageIds.push(ids)
        x = rx - roleInnerGap + GAP_X
      }
    }

    for (let i = 1; i < stageIds.length; i++) {
      const prev = stageIds[i - 1]
      const curr = stageIds[i]
      if (prev.length === 1 && curr.length > 1) {
        curr.forEach((to) => edges.push({ from: prev[0], to }))
      } else if (prev.length > 1 && curr.length === 1) {
        prev.forEach((from) => edges.push({ from, to: curr[0] }))
      } else if (prev.length > 1 && curr.length > 1) {
        edges.push({
          from: prev[Math.floor(prev.length / 2)],
          to: curr[Math.floor(curr.length / 2)],
        })
      } else {
        edges.push({ from: prev[0], to: curr[0] })
      }
    }

    const laneH = LABEL_H + MAIN_H + PAD_Y
    lanes.push({
      round,
      title,
      nodes,
      edges,
      width: Math.max(x - GAP_X + PAD_X, naturalW),
      height: laneH,
      y0: yCursor,
    })
    yCursor += laneH + ROUND_GAP
  }

  const canvasW = Math.max(contentMaxW, 720)
  for (const lane of lanes) {
    const minX = Math.min(...lane.nodes.map((n) => n.x), PAD_X)
    const maxX = Math.max(...lane.nodes.map((n) => n.x + n.w), PAD_X)
    const contentW = maxX - minX
    const offset = Math.max(0, (canvasW - contentW) / 2 - minX)
    if (offset > 0) lane.nodes.forEach((n) => (n.x += offset))
    lane.width = canvasW
  }

  return {
    lanes,
    allNodes: lanes.flatMap((l) => l.nodes),
    width: canvasW,
    height: Math.max(yCursor - ROUND_GAP + PAD_Y, 140),
  }
})

const nodeMap = computed(() => {
  const m = new Map<string, FlowNode>()
  layout.value.allNodes.forEach((n) => m.set(n.id, n))
  return m
})

function edgePath(fromId: string, toId: string): string {
  const a = nodeMap.value.get(fromId)
  const b = nodeMap.value.get(toId)
  if (!a || !b) return ''
  const x1 = a.x + a.w
  const y1 = a.y + a.h / 2
  const x2 = b.x
  const y2 = b.y + b.h / 2
  const mid = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`
}

function statusClass(n: FlowNode): string {
  if (n.status === 'error') return 'is-error'
  if (n.status === 'retry') return 'is-retry'
  if (n.stepType === 'persist') return 'is-done'
  if (n.stepType === 'step3_gate') return 'is-gate'
  if (n.stepType === 'revise_loop') return 'is-revise'
  if (n.kind === 'role') return 'is-role'
  if (n.stepType === 'step1_extract') return 'is-extract'
  return 'is-ok'
}

const selectedId = ref<string | null>(null)
const selected = computed(() => {
  if (!selectedId.value) return null
  const s = (props.steps || []).find((x) => stepId(x) === selectedId.value)
  return s ? asDetail(s) : null
})

type FactorRow = {
  factor_id: string
  name: string
  category?: string
  formula?: string
  score?: number | string
  veto?: boolean
  comment?: string
  action?: string
}

const selectedFactors = computed<FactorRow[]>(() => {
  const s = selected.value
  if (!s) return []
  const payload = s.payload || {}
  const names = factorNameMap.value

  if (s.step_type === 'step2_review') {
    const reviews = (payload as any).reviews || {}
    return Object.entries(reviews).map(([fid, raw]) => {
      const r = (raw || {}) as any
      const meta = names.get(String(fid))
      return {
        factor_id: String(fid),
        name: meta?.name || String(fid),
        category: meta?.category,
        formula: meta?.formula,
        score: r.total_score,
        veto: !!r.veto,
        comment: r.comment || r.veto_reason || '',
      }
    })
  }

  if (s.step_type === 'step1_extract') {
    const factors = (payload as any).factors || []
    return factors.map((f: any) => {
      const meta = factorMeta(f)
      return {
        factor_id: String(f.factor_id),
        name: meta.name,
        category: meta.category,
        formula: meta.formula,
      }
    })
  }

  if (s.step_type === 'step3_gate') {
    const rows = (payload as any).rows || []
    return rows.map((r: any) => {
      const meta = names.get(String(r.factor_id))
      return {
        factor_id: String(r.factor_id),
        name: meta?.name || String(r.factor_id),
        category: meta?.category,
        formula: meta?.formula,
        score: r.final_score ?? r.mean,
        action: r.action,
        comment: r.reason || (r.veto_reasons || []).join('；') || (r.main_gaps || []).join('；') || '',
        veto: !!r.veto,
      }
    })
  }

  if (s.step_type === 'persist') {
    const saved = ((payload as any).saved || []) as any[]
    const dropped = ((payload as any).dropped || []) as any[]
    const rows = [
      ...saved.map((r) => ({ ...r, action: r.action || 'SAVE' })),
      ...dropped.map((r) => ({ ...r, action: r.action || 'DROP' })),
    ]
    if (!rows.length) {
      const ids = [
        ...(((payload as any).saved_ids || []) as string[]).map((id) => ({
          factor_id: id,
          action: 'SAVE',
          reason: '已保存',
        })),
        ...(((payload as any).dropped_ids || []) as string[]).map((id) => ({
          factor_id: id,
          action: 'DROP',
          reason: '已淘汰',
        })),
      ]
      return ids.map((r) => {
        const meta = names.get(String(r.factor_id))
        return {
          factor_id: String(r.factor_id),
          name: meta?.name || String(r.factor_id),
          category: meta?.category,
          formula: meta?.formula,
          action: r.action,
          comment: r.reason,
        }
      })
    }
    return rows.map((r: any) => {
      const meta = names.get(String(r.factor_id))
      return {
        factor_id: String(r.factor_id),
        name: meta?.name || r.name_zh || String(r.factor_id),
        category: meta?.category || r.category,
        formula: meta?.formula || r.formula_or_rule,
        score: r.final_score,
        action: r.action,
        comment: r.reason || (r.veto_reasons || []).join('；') || (r.main_gaps || []).join('；') || '',
        veto: !!r.veto,
      }
    })
  }

  if (s.step_type === 'revise_loop') {
    const items = (payload as any).items || []
    return items.map((it: any) => {
      const meta = names.get(String(it.factor_id))
      return {
        factor_id: String(it.factor_id),
        name: meta?.name || String(it.factor_id),
        category: meta?.category,
        formula: meta?.formula,
        score: it.final_score,
        comment: it.reason || (it.main_gaps || []).join('；') || '',
      }
    })
  }

  if (s.step_type === 'step2_merge') {
    const ids = (payload as any).factor_ids || s.factor_ids || []
    return ids.map((fid: string) => {
      const meta = names.get(String(fid))
      return {
        factor_id: String(fid),
        name: meta?.name || String(fid),
        category: meta?.category,
        formula: meta?.formula,
      }
    })
  }

  return []
})

function selectNode(id: string) {
  selectedId.value = id
}

watch(
  () => props.steps?.length,
  async (len) => {
    await nextTick()
    if (!len) return
    const last = [...props.steps].sort((a, b) => a.seq - b.seq).at(-1)
    if (last && !selectedId.value) selectedId.value = stepId(last)
  },
  { immediate: true },
)

const stats = computed(() => {
  const list = props.steps || []
  return {
    total: list.length,
    rounds: new Set(list.map((s) => s.round).filter((r) => r && r > 0)).size,
    reviews: list.filter((s) => s.step_type === 'step2_review').length,
  }
})
</script>

<template>
  <div class="flow-wrap">
    <div class="toolbar">
      <div class="stats">
        <span>共 {{ stats.total }} 步</span>
        <span v-if="stats.rounds">· {{ stats.rounds }} 轮</span>
        <span v-if="stats.reviews">· 评审 {{ stats.reviews }}</span>
      </div>
      <div class="legend">
        <span class="lg is-extract">提取</span>
        <span class="lg is-role">角色</span>
        <span class="lg is-gate">门槛</span>
        <span class="lg is-revise">修订</span>
        <span class="lg is-done">落盘</span>
      </div>
    </div>

    <div v-if="!steps?.length" class="empty">暂无执行步骤</div>
    <div v-else class="flow-scroll">
      <div class="canvas" :style="{ width: '100%', minWidth: layout.width + 'px', height: layout.height + 'px' }">
        <svg
          class="edges"
          :width="layout.width"
          :height="layout.height"
          :viewBox="`0 0 ${layout.width} ${layout.height}`"
          preserveAspectRatio="xMinYMin meet"
        >
          <defs>
            <marker id="flow-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="#94a3b8" />
            </marker>
          </defs>
          <template v-for="lane in layout.lanes" :key="'e-' + lane.round">
            <path
              v-for="(e, i) in lane.edges"
              :key="i"
              class="edge"
              :d="edgePath(e.from, e.to)"
              marker-end="url(#flow-arrow)"
            />
          </template>
        </svg>

        <div
          v-for="lane in layout.lanes"
          :key="'t-' + lane.round"
          class="round-label"
          :style="{ top: lane.y0 + 'px', left: PAD_X + 'px' }"
        >
          {{ lane.title }}
        </div>

        <button
          v-for="n in layout.allNodes"
          :key="n.id"
          type="button"
          class="node"
          :class="[statusClass(n), n.kind, { active: selectedId === n.id }]"
          :style="{ left: n.x + 'px', top: n.y + 'px', width: n.w + 'px', height: n.h + 'px' }"
          :title="n.hint"
          @click="selectNode(n.id)"
        >
          <span class="node-label">{{ n.label }}</span>
          <span v-if="n.badge" class="node-badge">{{ n.badge }}</span>
          <span v-if="n.factorIds.length && n.kind === 'main'" class="node-ids">
            {{ n.factorIds.slice(0, 3).join(' ') }}{{ n.factorIds.length > 3 ? '…' : '' }}
          </span>
        </button>
      </div>
    </div>

    <div v-if="selected" class="detail">
      <div class="detail-head">
        <div class="detail-title">#{{ selected.seq }} {{ selected.title }}</div>
        <div class="detail-tags">
          <span class="pill">{{ selected.step_type }}</span>
          <span v-if="selected.role_code" class="pill">{{ selected.role_code }}</span>
          <span v-if="selected.role_name" class="pill">{{ selected.role_name }}</span>
          <span class="pill">{{ selected.status }}</span>
          <span v-if="selected.round" class="pill">round {{ selected.round }}</span>
          <span v-if="selected.factor_count" class="pill">{{ selected.factor_count }} 个因子</span>
        </div>
      </div>
      <div class="detail-body">{{ selected.summary || '无摘要' }}</div>

      <div v-if="selectedFactors.length" class="factor-block">
        <div class="factor-title">涉及因子</div>
        <div class="factor-table-wrap">
          <table class="factor-table">
            <thead>
              <tr>
                <th style="width: 72px">ID</th>
                <th style="width: 160px">名称</th>
                <th style="width: 90px">类别</th>
                <th>因子公式</th>
                <th
                  v-if="
                    selected.step_type === 'step2_review' ||
                    selected.step_type === 'step3_gate' ||
                    selected.step_type === 'persist'
                  "
                  style="width: 64px"
                >
                  评分
                </th>
                <th
                  v-if="selected.step_type === 'step3_gate' || selected.step_type === 'persist'"
                  style="width: 72px"
                >
                  裁决
                </th>
                <th v-if="selected.step_type === 'step2_review'" style="width: 56px">一票否决</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in selectedFactors" :key="f.factor_id + '-' + (f.action || '')">
                <td class="mono">{{ f.factor_id }}</td>
                <td>{{ f.name }}</td>
                <td>{{ f.category || '-' }}</td>
                <td class="formula">{{ f.formula || '-' }}</td>
                <td
                  v-if="
                    selected.step_type === 'step2_review' ||
                    selected.step_type === 'step3_gate' ||
                    selected.step_type === 'persist'
                  "
                >
                  {{ f.score ?? '-' }}
                </td>
                <td v-if="selected.step_type === 'step3_gate' || selected.step_type === 'persist'">
                  <span class="action" :class="String(f.action || '').toLowerCase()">{{ f.action || '-' }}</span>
                </td>
                <td v-if="selected.step_type === 'step2_review'">
                  <span :class="f.veto ? 'veto-yes' : 'veto-no'">{{ f.veto ? '是' : '否' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div
          v-if="
            (selected.step_type === 'step2_review' ||
              selected.step_type === 'step3_gate' ||
              selected.step_type === 'persist' ||
              selected.step_type === 'revise_loop') &&
            selectedFactors.some((f) => f.comment)
          "
          class="comments"
        >
          <div class="factor-title" style="margin-top: 4px">
            {{ selected.step_type === 'persist' ? '保存 / 淘汰理由' : '说明' }}
          </div>
          <div v-for="f in selectedFactors.filter((x) => x.comment)" :key="'c-' + f.factor_id + (f.action || '')" class="comment-item">
            <strong>{{ f.factor_id }}</strong>
            <span v-if="f.action" class="action-inline" :class="String(f.action).toLowerCase()">{{ f.action }}</span>
            <span>{{ f.comment }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.flow-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.stats {
  font-size: 12px;
  color: #64748b;
}
.legend {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.lg {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid #e2e8f0;
  color: #475569;
  background: #fff;
}
.lg.is-extract { border-color: #99f6e4; background: #f0fdfa; color: #0f766e; }
.lg.is-role { border-color: #bae6fd; background: #f0f9ff; color: #0369a1; }
.lg.is-gate { border-color: #fde68a; background: #fffbeb; color: #b45309; }
.lg.is-revise { border-color: #ddd6fe; background: #f5f3ff; color: #6d28d9; }
.lg.is-done { border-color: #bbf7d0; background: #f0fdf4; color: #15803d; }

.flow-scroll {
  overflow: visible;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background:
    radial-gradient(circle at 1px 1px, #e2e8f0 1px, transparent 0) 0 0 / 16px 16px,
    #f8fafc;
}
.canvas {
  position: relative;
  min-height: 120px;
  width: 100%;
}
.edges {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.edge {
  fill: none;
  stroke: #94a3b8;
  stroke-width: 1.5;
}
.round-label {
  position: absolute;
  font-size: 12px;
  font-weight: 700;
  color: #334155;
}
.node {
  position: absolute;
  border-radius: 10px;
  border: 1.5px solid #cbd5e1;
  background: #fff;
  box-sizing: border-box;
  padding: 4px 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.node:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
}
.node.active {
  border-width: 2px;
  box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.15);
}
.node-label {
  font-size: 12px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.15;
  text-align: center;
}
.node-badge {
  font-size: 10px;
  color: #64748b;
  line-height: 1.1;
}
.node-ids {
  font-size: 9px;
  color: #0d9488;
  line-height: 1.1;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.node.role .node-label { font-size: 12px; }
.node.is-extract { border-color: #14b8a6; background: #f0fdfa; }
.node.is-role { border-color: #0ea5e9; background: #f0f9ff; }
.node.is-gate { border-color: #f59e0b; background: #fffbeb; }
.node.is-revise { border-color: #8b5cf6; background: #f5f3ff; }
.node.is-done { border-color: #22c55e; background: #f0fdf4; }
.node.is-error { border-color: #ef4444; background: #fef2f2; }
.node.is-retry { border-color: #8b5cf6; background: #f5f3ff; }
.node.is-ok { border-color: #94a3b8; }

.empty {
  color: #94a3b8;
  font-size: 13px;
  padding: 28px;
  text-align: center;
}
.detail {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px 14px;
  background: #fff;
}
.detail-title {
  font-size: 14px;
  font-weight: 650;
  color: #0f172a;
}
.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.pill {
  font-size: 11px;
  color: #475569;
  background: #f1f5f9;
  border-radius: 999px;
  padding: 2px 8px;
}
.detail-body {
  margin-top: 10px;
  font-size: 13px;
  color: #334155;
  line-height: 1.55;
  white-space: pre-wrap;
}
.factor-block {
  margin-top: 12px;
  border-top: 1px solid #e2e8f0;
  padding-top: 12px;
}
.factor-title {
  font-size: 13px;
  font-weight: 650;
  color: #0f172a;
  margin-bottom: 8px;
}
.factor-table-wrap {
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}
.factor-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.factor-table th,
.factor-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f1f5f9;
  text-align: left;
  vertical-align: top;
}
.factor-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
  white-space: nowrap;
}
.factor-table tr:last-child td {
  border-bottom: none;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #0f766e;
}
.formula {
  color: #334155;
  word-break: break-word;
}
.action {
  font-weight: 650;
}
.action.save { color: #15803d; }
.action.revise { color: #b45309; }
.action.drop { color: #b91c1c; }
.action-inline {
  display: inline-block;
  margin: 0 6px;
  font-size: 11px;
  font-weight: 650;
}
.action-inline.save { color: #15803d; }
.action-inline.drop { color: #b91c1c; }
.action-inline.revise { color: #b45309; }
.veto-yes { color: #b91c1c; font-weight: 650; }
.veto-no { color: #64748b; }
.comments {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.comment-item {
  font-size: 12px;
  color: #475569;
  background: #f8fafc;
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  gap: 8px;
}
.comment-item strong {
  color: #0f766e;
  flex: 0 0 auto;
}
</style>
