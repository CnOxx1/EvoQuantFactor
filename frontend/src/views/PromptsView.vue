<template>
  <div class="page">
    <div class="head">
      <div>
        <h2>提示词管理</h2>
        <p class="sub">配置 Step1 提取/优化、六角色评审与 MCP 共用提示词；保存后写入覆盖层，可一键恢复文件默认。</p>
      </div>
      <n-space v-if="detail" align="center">
        <n-tag size="small" :type="detail.source === 'db_override' ? 'warning' : 'default'" :bordered="false">
          {{ detail.source === 'db_override' ? '已覆盖' : '文件默认' }}
        </n-tag>
        <n-button quaternary :disabled="!dirty" @click="reloadCurrent">撤销未保存</n-button>
        <n-button :loading="resetting" @click="onReset">恢复默认</n-button>
        <n-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</n-button>
      </n-space>
    </div>

    <div class="layout">
      <aside class="side">
        <n-input
          v-model:value="filter"
          clearable
          size="small"
          placeholder="筛选 key / 名称"
          style="margin-bottom: 10px"
        />
        <n-spin :show="listLoading">
          <div v-for="group in grouped" :key="group.title" class="group">
            <div class="group-title">{{ group.title }}</div>
            <button
              v-for="item in group.items"
              :key="item.key"
              type="button"
              class="item"
              :class="{ active: item.key === current }"
              @click="onSelect(item.key)"
            >
              <div class="item-top">
                <span class="item-key">{{ item.key }}</span>
                <n-tag
                  size="small"
                  :bordered="false"
                  :type="item.source === 'db_override' ? 'warning' : 'default'"
                >
                  {{ item.source === 'db_override' ? '覆盖' : '默认' }}
                </n-tag>
              </div>
              <div class="item-name">{{ item.name }}</div>
              <div v-if="weightSummary(item.weights)" class="item-meta">{{ weightSummary(item.weights) }}</div>
            </button>
          </div>
          <n-empty v-if="!grouped.length" description="无匹配提示词" size="small" />
        </n-spin>
      </aside>

      <section class="editor">
        <n-spin :show="detailLoading">
          <template v-if="detail">
            <div class="editor-head">
              <div>
                <div class="editor-title">{{ detail.name || detail.key }}</div>
                <div class="editor-key">{{ detail.key }} · 更新 {{ detail.updated_at || '—' }}</div>
              </div>
              <n-alert v-if="dirty" type="warning" :bordered="false" class="dirty-alert">
                有未保存修改
              </n-alert>
            </div>

            <n-tabs v-model:value="tab" type="line" animated>
              <n-tab-pane name="basic" tab="基本信息">
                <n-form label-placement="top" class="form">
                  <n-form-item label="显示名称">
                    <n-input v-model:value="draft.name" placeholder="提示词名称" />
                  </n-form-item>
                  <n-form-item v-if="mcpTools.length" label="MCP 倾向工具">
                    <n-space>
                      <n-tag v-for="t in mcpTools" :key="t" size="small" :bordered="false">{{ t }}</n-tag>
                    </n-space>
                  </n-form-item>
                </n-form>
              </n-tab-pane>

              <n-tab-pane name="system" tab="System">
                <n-input
                  v-model:value="draft.system"
                  type="textarea"
                  placeholder="System 提示词"
                  :autosize="{ minRows: 14, maxRows: 28 }"
                  class="mono"
                />
              </n-tab-pane>

              <n-tab-pane name="user" tab="User 模板">
                <n-alert type="info" :bordered="false" style="margin-bottom: 10px">
                  支持变量占位如 <code v-pre>{{report}}</code>、<code v-pre>{{factors}}</code>、<code v-pre>{{revise_packet}}</code>。
                </n-alert>
                <n-input
                  v-model:value="draft.user_template"
                  type="textarea"
                  placeholder="User 模板（可空，如 shared MCP）"
                  :autosize="{ minRows: 12, maxRows: 26 }"
                  class="mono"
                />
              </n-tab-pane>

              <n-tab-pane name="weights" tab="评分权重">
                <div v-if="weightRows.length" class="weights">
                  <div v-for="row in weightRows" :key="row.key" class="weight-row">
                    <div class="weight-label">{{ row.key }}</div>
                    <n-input-number
                      v-model:value="row.value"
                      :min="0"
                      :max="100"
                      :step="1"
                      size="small"
                      style="width: 120px"
                      @update:value="syncWeightsFromRows"
                    />
                    <div class="weight-bar">
                      <div class="weight-fill" :style="{ width: `${Math.min(100, Number(row.value) || 0)}%` }" />
                    </div>
                  </div>
                  <div class="weight-total" :class="{ bad: weightTotal !== 100 && weightRows.length }">
                    合计 {{ weightTotal }}
                    <span v-if="weightTotal !== 100">（建议合计 100）</span>
                  </div>
                </div>
                <n-empty v-else description="该模块无评分权重（如 Step1 / shared）" style="margin: 24px 0" />
                <n-collapse style="margin-top: 12px">
                  <n-collapse-item title="高级：权重 JSON" name="json">
                    <n-input
                      v-model:value="weightsText"
                      type="textarea"
                      :rows="8"
                      class="mono"
                      @update:value="onWeightsJsonEdit"
                    />
                  </n-collapse-item>
                </n-collapse>
              </n-tab-pane>
            </n-tabs>
          </template>
          <n-empty v-else description="请选择左侧提示词" style="padding: 48px 0" />
        </n-spin>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSpace,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { getPrompt, listPrompts, putPrompt, resetPrompt } from '@/api/client'

type PromptSummary = {
  key: string
  name: string
  source: string
  weights: Record<string, number>
  has_system?: boolean
  mcp_prefer_tools?: string[]
}

type PromptDetail = {
  key: string
  name: string
  system: string
  user_template: string
  weights: Record<string, number>
  scoring?: Record<string, unknown>
  mcp?: Record<string, unknown>
  source: string
  updated_at?: string | null
}

const message = useMessage()
const dialog = useDialog()
const list = ref<PromptSummary[]>([])
const listLoading = ref(false)
const detailLoading = ref(false)
const current = ref('')
const detail = ref<PromptDetail | null>(null)
const saving = ref(false)
const resetting = ref(false)
const filter = ref('')
const tab = ref<'basic' | 'system' | 'user' | 'weights'>('system')
const weightsText = ref('{}')
const weightRows = ref<{ key: string; value: number | null }[]>([])

const draft = reactive({
  name: '',
  system: '',
  user_template: '',
  weights: {} as Record<string, number>,
})

const snapshot = ref('')

function serializeDraft() {
  return JSON.stringify({
    name: draft.name,
    system: draft.system,
    user_template: draft.user_template,
    weights: draft.weights,
  })
}

const dirty = computed(() => !!detail.value && serializeDraft() !== snapshot.value)

const mcpTools = computed(() => {
  const fromDetail = (detail.value?.mcp as any)?.prefer_tools
  if (Array.isArray(fromDetail) && fromDetail.length) return fromDetail as string[]
  const fromList = list.value.find((x) => x.key === current.value)?.mcp_prefer_tools
  return fromList || []
})

const weightTotal = computed(() =>
  weightRows.value.reduce((s, r) => s + (Number(r.value) || 0), 0),
)

function groupOf(key: string): string {
  if (key.startsWith('step1')) return 'Step1 提取 / 优化'
  if (/^R\d$/.test(key) || key.startsWith('step2')) return '六角色评审'
  if (key.includes('shared') || key.includes('mcp')) return '共用'
  if (key.startsWith('step3')) return '门禁'
  return '其他'
}

const GROUP_ORDER = ['Step1 提取 / 优化', '六角色评审', '门禁', '共用', '其他']

const grouped = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const items = list.value.filter((x) => {
    if (!q) return true
    return x.key.toLowerCase().includes(q) || (x.name || '').toLowerCase().includes(q)
  })
  const map = new Map<string, PromptSummary[]>()
  for (const item of items) {
    const g = groupOf(item.key)
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(item)
  }
  return GROUP_ORDER.filter((t) => map.has(t)).map((title) => ({
    title,
    items: map.get(title)!,
  }))
})

function weightSummary(weights?: Record<string, number>) {
  const entries = Object.entries(weights || {})
  if (!entries.length) return ''
  return entries.map(([k, v]) => `${k}:${v}`).join(' · ')
}

function applyDetail(data: PromptDetail) {
  detail.value = data
  draft.name = data.name || ''
  draft.system = data.system || ''
  draft.user_template = data.user_template || ''
  draft.weights = { ...(data.weights || {}) }
  weightsText.value = JSON.stringify(data.weights || {}, null, 2)
  weightRows.value = Object.entries(data.weights || {}).map(([key, value]) => ({
    key,
    value: Number(value),
  }))
  snapshot.value = serializeDraft()
  if (!Object.keys(data.weights || {}).length && tab.value === 'weights') {
    tab.value = data.user_template ? 'user' : 'system'
  }
}

function syncWeightsFromRows() {
  const next: Record<string, number> = {}
  for (const row of weightRows.value) {
    next[row.key] = Number(row.value) || 0
  }
  draft.weights = next
  weightsText.value = JSON.stringify(next, null, 2)
}

function onWeightsJsonEdit(val: string) {
  weightsText.value = val
  try {
    const parsed = JSON.parse(val || '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      draft.weights = parsed
      weightRows.value = Object.entries(parsed).map(([key, value]) => ({
        key,
        value: Number(value),
      }))
    }
  } catch {
    /* 编辑中允许暂时非法 JSON */
  }
}

async function loadList() {
  listLoading.value = true
  try {
    const { data } = await listPrompts()
    list.value = data
    if (!data.find((x: PromptSummary) => x.key === current.value)) {
      current.value = data[0]?.key || ''
    }
  } finally {
    listLoading.value = false
  }
}

async function loadDetail(key: string) {
  if (!key) {
    detail.value = null
    return
  }
  detailLoading.value = true
  try {
    const { data } = await getPrompt(key)
    applyDetail(data as PromptDetail)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

function onSelect(key: string) {
  if (key === current.value) return
  if (dirty.value) {
    dialog.warning({
      title: '未保存的修改',
      content: '切换提示词将丢弃当前未保存内容，是否继续？',
      positiveText: '继续切换',
      negativeText: '取消',
      onPositiveClick: () => {
        current.value = key
        loadDetail(key)
      },
    })
    return
  }
  current.value = key
  loadDetail(key)
}

async function reloadCurrent() {
  await loadDetail(current.value)
  message.info('已重新加载')
}

async function save() {
  if (!detail.value) return
  let weights = draft.weights
  try {
    weights = JSON.parse(weightsText.value || '{}')
  } catch {
    message.error('权重 JSON 格式不正确')
    tab.value = 'weights'
    return
  }
  saving.value = true
  try {
    const { data } = await putPrompt(current.value, {
      name: draft.name,
      system: draft.system,
      user_template: draft.user_template,
      weights,
    })
    applyDetail(data as PromptDetail)
    message.success('已保存')
    await loadList()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onReset() {
  dialog.warning({
    title: '恢复默认',
    content: `将清除「${current.value}」的数据库覆盖，恢复 prompts/ 文件默认内容。`,
    positiveText: '恢复',
    negativeText: '取消',
    onPositiveClick: async () => {
      resetting.value = true
      try {
        const { data } = await resetPrompt(current.value)
        applyDetail(data as PromptDetail)
        message.success('已恢复默认')
        await loadList()
      } catch (e: any) {
        message.error(e?.response?.data?.detail || e.message || '恢复失败')
      } finally {
        resetting.value = false
      }
    },
  })
}

onMounted(async () => {
  try {
    await loadList()
    if (current.value) await loadDetail(current.value)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败')
  }
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: calc(100vh - 120px);
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
h2 {
  margin: 0;
  font-size: 20px;
}
.sub {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  max-width: 640px;
  line-height: 1.5;
}
.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
  flex: 1;
  min-height: 0;
}
.side,
.editor {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px;
  min-height: 520px;
}
.side {
  overflow: auto;
  max-height: calc(100vh - 180px);
}
.group {
  margin-bottom: 12px;
}
.group-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #94a3b8;
  text-transform: uppercase;
  margin: 4px 6px 8px;
}
.item {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s, border-color 0.15s;
}
.item:hover {
  background: #f8fafc;
}
.item.active {
  background: #eff6ff;
  border-color: #bfdbfe;
}
.item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.item-key {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #334155;
  font-weight: 600;
}
.item-name {
  margin-top: 2px;
  font-size: 13px;
  color: #0f172a;
}
.item-meta {
  margin-top: 4px;
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.editor {
  display: flex;
  flex-direction: column;
  min-height: 520px;
}
.editor-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 4px;
}
.editor-title {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}
.editor-key {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.dirty-alert {
  padding: 6px 10px;
}
.form {
  max-width: 520px;
}
.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.weights {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 640px;
}
.weight-row {
  display: grid;
  grid-template-columns: 140px 120px 1fr;
  gap: 10px;
  align-items: center;
}
.weight-label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}
.weight-bar {
  height: 8px;
  background: #f1f5f9;
  border-radius: 999px;
  overflow: hidden;
}
.weight-fill {
  height: 100%;
  background: #3b82f6;
  border-radius: 999px;
}
.weight-total {
  font-size: 13px;
  color: #64748b;
  margin-top: 4px;
}
.weight-total.bad {
  color: #b45309;
}
@media (max-width: 960px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .side {
    max-height: 280px;
  }
  .weight-row {
    grid-template-columns: 1fr 120px;
  }
  .weight-bar {
    display: none;
  }
}
</style>
