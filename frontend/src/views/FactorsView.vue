<template>
  <div class="page">
    <h2>因子库</h2>
    <n-tabs v-model:value="tab" type="line" style="margin-top: 8px">
      <n-tab-pane name="alpha101" tab="Alpha101">
        <n-space vertical :size="12">
          <n-space align="center">
            <n-input
              v-model:value="query"
              clearable
              placeholder="搜索 ID / 名称 / 公式"
              style="width: 280px"
              @keyup.enter="onSearch"
            />
            <n-button type="primary" :loading="loading" @click="onSearch">搜索</n-button>
            <n-button
              type="warning"
              :disabled="!libraryChecked.length"
              :loading="optimizing"
              @click="optimizeLibrary"
            >
              优化因子{{ libraryChecked.length ? ` (${libraryChecked.length})` : '' }}
            </n-button>
          </n-space>
          <n-card size="small">
            <n-data-table
              :columns="libraryColumns"
              :data="libraryRows"
              :loading="loading"
              size="small"
              :scroll-x="1080"
              :pagination="pagination"
              :paginate-single-page="false"
              remote
              :row-key="(row: LibraryFactor) => row.factor_id"
              v-model:checked-row-keys="libraryChecked"
              @update:page="onPageChange"
              @update:page-size="onPageSizeChange"
            />
          </n-card>
        </n-space>
      </n-tab-pane>

      <n-tab-pane name="workspace" tab="任务入库">
        <n-space vertical :size="12">
          <n-alert type="info" :bordered="false">
            过线保存（SAVE）的任务因子。淘汰因子请到「淘汰库」查看。
          </n-alert>
          <n-space align="center">
            <n-input
              v-model:value="wsQuery"
              clearable
              placeholder="搜索任务 ID / 名称 / 公式"
              style="width: 300px"
              @keyup.enter="() => reloadPack('workspace')"
            />
            <n-button type="primary" :loading="wsLoading" @click="() => reloadPack('workspace')">搜索</n-button>
            <n-button quaternary :loading="wsLoading" @click="() => loadPack('workspace', true)">刷新</n-button>
            <n-button
              type="warning"
              :disabled="!wsChecked.length"
              :loading="optimizing"
              @click="optimizePack('workspace', '任务入库')"
            >
              优化因子{{ wsChecked.length ? ` (${wsChecked.length})` : '' }}
            </n-button>
          </n-space>
          <n-card size="small">
            <n-data-table
              :columns="wsColumns"
              :data="wsRows"
              :loading="wsLoading"
              size="small"
              :scroll-x="1200"
              :pagination="wsPagination"
              :paginate-single-page="false"
              remote
              :row-key="(row: LibraryFactor) => row.factor_id"
              v-model:checked-row-keys="wsChecked"
              @update:page="(p: number) => onPackPage('workspace', p)"
              @update:page-size="(s: number) => onPackPageSize('workspace', s)"
            />
          </n-card>
        </n-space>
      </n-tab-pane>

      <n-tab-pane name="dropped" tab="淘汰库">
        <n-space vertical :size="12">
          <n-alert type="warning" :bordered="false">
            门槛淘汰（DROP）的因子，保留公式与淘汰原因，可勾选后再次优化。
          </n-alert>
          <n-space align="center">
            <n-input
              v-model:value="dropQuery"
              clearable
              placeholder="搜索任务 ID / 名称 / 淘汰原因"
              style="width: 300px"
              @keyup.enter="() => reloadPack('dropped')"
            />
            <n-button type="primary" :loading="dropLoading" @click="() => reloadPack('dropped')">搜索</n-button>
            <n-button quaternary :loading="dropLoading" @click="() => loadPack('dropped', true)">刷新</n-button>
            <n-button
              type="warning"
              :disabled="!dropChecked.length"
              :loading="optimizing"
              @click="optimizePack('dropped', '淘汰库')"
            >
              优化因子{{ dropChecked.length ? ` (${dropChecked.length})` : '' }}
            </n-button>
          </n-space>
          <n-card size="small">
            <n-data-table
              :columns="dropColumns"
              :data="dropRows"
              :loading="dropLoading"
              size="small"
              :scroll-x="1400"
              :pagination="dropPagination"
              :paginate-single-page="false"
              remote
              :row-key="(row: LibraryFactor) => row.factor_id"
              v-model:checked-row-keys="dropChecked"
              @update:page="(p: number) => onPackPage('dropped', p)"
              @update:page-size="(s: number) => onPackPageSize('dropped', s)"
            />
          </n-card>
        </n-space>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NInput,
  NSpace,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, DataTableRowKey, PaginationProps } from 'naive-ui'
import {
  createJobEvaluate,
  getLibraryFactors,
  type LibraryFactor,
  type SeedFactorIn,
} from '@/api/client'

type PackId = 'workspace' | 'dropped'

const message = useMessage()
const router = useRouter()
const tab = ref<'alpha101' | PackId>('alpha101')
const loading = ref(false)
const wsLoading = ref(false)
const dropLoading = ref(false)
const optimizing = ref(false)
const query = ref('')
const wsQuery = ref('')
const dropQuery = ref('')
const libraryRows = ref<LibraryFactor[]>([])
const wsRows = ref<LibraryFactor[]>([])
const dropRows = ref<LibraryFactor[]>([])
const total = ref(0)
const wsTotal = ref(0)
const dropTotal = ref(0)
const page = ref(1)
const pageSize = ref(20)
const wsPage = ref(1)
const wsPageSize = ref(20)
const dropPage = ref(1)
const dropPageSize = ref(20)
const libraryChecked = ref<DataTableRowKey[]>([])
const wsChecked = ref<DataTableRowKey[]>([])
const dropChecked = ref<DataTableRowKey[]>([])
const packLoaded: Record<PackId, boolean> = { workspace: false, dropped: false }

function statusTag(status?: string) {
  const type =
    status === 'SAVE' ? 'success' : status === 'DROP' ? 'error' : status === 'CANDIDATE' ? 'warning' : 'default'
  return h(NTag, { size: 'small', type, bordered: false }, () => status || '-')
}

function jobLink(row: LibraryFactor) {
  return row.job_id
    ? h(
        NButton,
        {
          text: true,
          type: 'primary',
          size: 'small',
          onClick: () => router.push(`/jobs/${row.job_id}`),
        },
        () => row.job_id,
      )
    : '-'
}

function actionBtn(row: LibraryFactor, packLabel: string) {
  return h(
    NButton,
    {
      text: true,
      type: 'warning',
      size: 'small',
      loading: optimizing.value,
      onClick: (e: MouseEvent) => {
        e.stopPropagation()
        optimizeOne(row, packLabel)
      },
    },
    () => '优化因子',
  )
}

const libraryColumns: DataTableColumns<LibraryFactor> = [
  { type: 'selection' },
  { title: 'ID', key: 'factor_id', width: 72 },
  { title: '名称', key: 'name_zh', width: 110 },
  { title: '英文', key: 'name_en', width: 90 },
  {
    title: '输入',
    key: 'inputs',
    width: 160,
    render: (row) => (row.inputs || []).join(', '),
  },
  { title: '因子公式', key: 'formula_or_rule', ellipsis: { tooltip: true }, minWidth: 360 },
  { title: '来源', key: 'source', width: 180, ellipsis: { tooltip: true } },
  { title: '操作', key: 'actions', width: 100, fixed: 'right', render: (row) => actionBtn(row, 'Alpha101') },
]

const wsColumns: DataTableColumns<LibraryFactor> = [
  { type: 'selection' },
  { title: '状态', key: 'status', width: 90, render: (row) => statusTag(row.status) },
  { title: '名称', key: 'name_zh', width: 140, ellipsis: { tooltip: true } },
  { title: '原ID', key: 'origin_factor_id', width: 70 },
  { title: '类别', key: 'category', width: 90 },
  { title: '评分', key: 'final_score', width: 70 },
  { title: '因子公式', key: 'formula_or_rule', ellipsis: { tooltip: true }, minWidth: 320 },
  { title: '任务', key: 'job_id', width: 150, ellipsis: { tooltip: true }, render: jobLink },
  { title: '操作', key: 'actions', width: 100, fixed: 'right', render: (row) => actionBtn(row, '任务入库') },
]

const dropColumns: DataTableColumns<LibraryFactor> = [
  { type: 'selection' },
  { title: '状态', key: 'status', width: 80, render: (row) => statusTag(row.status || 'DROP') },
  { title: '名称', key: 'name_zh', width: 140, ellipsis: { tooltip: true } },
  { title: '原ID', key: 'origin_factor_id', width: 70 },
  { title: '评分', key: 'final_score', width: 70 },
  { title: '淘汰原因', key: 'reason', ellipsis: { tooltip: true }, minWidth: 220 },
  { title: '因子公式', key: 'formula_or_rule', ellipsis: { tooltip: true }, minWidth: 280 },
  { title: '任务', key: 'job_id', width: 150, ellipsis: { tooltip: true }, render: jobLink },
  { title: '操作', key: 'actions', width: 100, fixed: 'right', render: (row) => actionBtn(row, '淘汰库') },
]

const pagination = computed<PaginationProps>(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 条`,
}))

const wsPagination = computed<PaginationProps>(() => ({
  page: wsPage.value,
  pageSize: wsPageSize.value,
  itemCount: wsTotal.value,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 条`,
}))

const dropPagination = computed<PaginationProps>(() => ({
  page: dropPage.value,
  pageSize: dropPageSize.value,
  itemCount: dropTotal.value,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 条`,
}))

function toSeed(f: LibraryFactor): SeedFactorIn | null {
  const formula = (f.formula_or_rule || '').trim()
  if (!formula) return null
  return {
    factor_id: String(f.origin_factor_id || f.factor_id),
    name_zh: f.name_zh || String(f.factor_id),
    name_en: f.name_en,
    category: f.category,
    formula_or_rule: formula,
    inputs: f.inputs || [],
    economic_logic: f.economic_logic,
    signal_direction: f.signal_direction,
    source: f.source,
    frequency: f.frequency,
  }
}

async function submitOptimize(factors: SeedFactorIn[], title: string) {
  if (!factors.length) {
    message.warning('所选因子缺少可用公式')
    return
  }
  optimizing.value = true
  try {
    const { data } = await createJobEvaluate({ factors, title })
    message.success(`已创建优化任务 ${data.job_id}`)
    libraryChecked.value = []
    wsChecked.value = []
    dropChecked.value = []
    router.push(`/jobs/${data.job_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '创建优化任务失败')
  } finally {
    optimizing.value = false
  }
}

async function optimizeLibrary() {
  const selected = libraryRows.value.filter((r) => libraryChecked.value.includes(r.factor_id))
  const seeds = selected.map(toSeed).filter((x): x is SeedFactorIn => !!x)
  await submitOptimize(seeds, `优化因子 · Alpha101 · ${seeds.length} 个`)
}

async function optimizePack(pack: PackId, label: string) {
  const rows = pack === 'workspace' ? wsRows.value : dropRows.value
  const checked = pack === 'workspace' ? wsChecked.value : dropChecked.value
  const selected = rows.filter((r) => checked.includes(r.factor_id))
  const seeds = selected.map(toSeed).filter((x): x is SeedFactorIn => !!x)
  await submitOptimize(seeds, `优化因子 · ${label} · ${seeds.length} 个`)
}

async function optimizeOne(row: LibraryFactor, packLabel: string) {
  const seed = toSeed(row)
  if (!seed) {
    message.warning('该因子缺少可用公式')
    return
  }
  await submitOptimize([seed], `优化因子 · ${packLabel} · ${row.name_zh || row.factor_id}`)
}

async function loadAlpha() {
  loading.value = true
  try {
    const { data } = await getLibraryFactors('alpha101', {
      q: query.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    libraryRows.value = data.factors
    total.value = data.total
    const ids = new Set(data.factors.map((f) => f.factor_id))
    libraryChecked.value = libraryChecked.value.filter((k) => ids.has(String(k)))
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载 Alpha101 失败')
  } finally {
    loading.value = false
  }
}

async function loadPack(pack: PackId, force = false) {
  if (packLoaded[pack] && !force) return
  const loadingRef = pack === 'workspace' ? wsLoading : dropLoading
  const queryRef = pack === 'workspace' ? wsQuery : dropQuery
  const pageRef = pack === 'workspace' ? wsPage : dropPage
  const pageSizeRef = pack === 'workspace' ? wsPageSize : dropPageSize
  const rowsRef = pack === 'workspace' ? wsRows : dropRows
  const totalRef = pack === 'workspace' ? wsTotal : dropTotal
  const checkedRef = pack === 'workspace' ? wsChecked : dropChecked
  loadingRef.value = true
  try {
    const { data } = await getLibraryFactors(pack, {
      q: queryRef.value.trim() || undefined,
      limit: pageSizeRef.value,
      offset: (pageRef.value - 1) * pageSizeRef.value,
    })
    rowsRef.value = data.factors
    totalRef.value = data.total
    const ids = new Set(data.factors.map((f) => f.factor_id))
    checkedRef.value = checkedRef.value.filter((k) => ids.has(String(k)))
    packLoaded[pack] = true
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || `加载${pack === 'dropped' ? '淘汰库' : '任务入库'}失败`)
  } finally {
    loadingRef.value = false
  }
}

function reloadPack(pack: PackId) {
  if (pack === 'workspace') wsPage.value = 1
  else dropPage.value = 1
  loadPack(pack, true)
}

function onPackPage(pack: PackId, p: number) {
  if (pack === 'workspace') wsPage.value = p
  else dropPage.value = p
  loadPack(pack, true)
}

function onPackPageSize(pack: PackId, size: number) {
  if (pack === 'workspace') {
    wsPageSize.value = size
    wsPage.value = 1
  } else {
    dropPageSize.value = size
    dropPage.value = 1
  }
  loadPack(pack, true)
}

function onSearch() {
  page.value = 1
  loadAlpha()
}

function onPageChange(p: number) {
  page.value = p
  loadAlpha()
}

function onPageSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadAlpha()
}

watch(tab, (v) => {
  if (v === 'workspace' || v === 'dropped') loadPack(v)
})

onMounted(loadAlpha)
</script>

<style scoped>
h2 { margin: 0; font-size: 20px; }
</style>
