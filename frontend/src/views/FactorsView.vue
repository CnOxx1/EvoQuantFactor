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
              :row-props="() => ({ style: 'cursor: pointer' })"
              @update:page="onPageChange"
              @update:page-size="onPageSizeChange"
            />
          </n-card>
        </n-space>
      </n-tab-pane>
      <n-tab-pane name="jobs" tab="任务产出">
        <n-space vertical :size="12">
          <n-space>
            <n-button
              type="warning"
              :disabled="!jobChecked.length"
              :loading="optimizing"
              @click="optimizeJobs"
            >
              优化因子{{ jobChecked.length ? ` (${jobChecked.length})` : '' }}
            </n-button>
          </n-space>
          <n-card size="small">
            <n-data-table
              :columns="jobColumns"
              :data="jobRows"
              :loading="jobLoading"
              size="small"
              :row-key="jobRowKey"
              v-model:checked-row-keys="jobChecked"
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
import { NButton, NCard, NDataTable, NInput, NSpace, NTabPane, NTabs, useMessage } from 'naive-ui'
import type { DataTableColumns, DataTableRowKey, PaginationProps } from 'naive-ui'
import {
  createJobEvaluate,
  getFactors,
  getLibraryFactors,
  listJobs,
  type FactorFormula,
  type LibraryFactor,
  type SeedFactorIn,
} from '@/api/client'

type JobFactorRow = FactorFormula & { job_id: string; _key: string }

const message = useMessage()
const router = useRouter()
const tab = ref<'alpha101' | 'jobs'>('alpha101')
const loading = ref(false)
const jobLoading = ref(false)
const optimizing = ref(false)
const query = ref('')
const libraryRows = ref<LibraryFactor[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const jobRows = ref<JobFactorRow[]>([])
const libraryChecked = ref<DataTableRowKey[]>([])
const jobChecked = ref<DataTableRowKey[]>([])
let jobsLoaded = false

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
  {
    title: '操作',
    key: 'actions',
    width: 100,
    fixed: 'right',
    render: (row) =>
      h(
        NButton,
        {
          text: true,
          type: 'warning',
          size: 'small',
          loading: optimizing.value,
          onClick: (e: MouseEvent) => {
            e.stopPropagation()
            optimizeOneLibrary(row)
          },
        },
        () => '优化因子',
      ),
  },
]

const jobColumns: DataTableColumns<JobFactorRow> = [
  { type: 'selection' },
  { title: '任务', key: 'job_id', width: 160, ellipsis: { tooltip: true } },
  { title: 'ID', key: 'factor_id', width: 70 },
  { title: '名称', key: 'name_zh' },
  { title: '类别', key: 'category', width: 90 },
  { title: '因子公式', key: 'formula_or_rule', ellipsis: { tooltip: true } },
  { title: '评分', key: 'final_score', width: 70 },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    fixed: 'right',
    render: (row) =>
      h(
        NButton,
        {
          text: true,
          type: 'warning',
          size: 'small',
          loading: optimizing.value,
          onClick: (e: MouseEvent) => {
            e.stopPropagation()
            optimizeOneJob(row)
          },
        },
        () => '优化因子',
      ),
  },
]

const pagination = computed<PaginationProps>(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 条`,
}))

function jobRowKey(row: JobFactorRow) {
  return row._key
}

function toSeed(f: {
  factor_id: string
  name_zh?: string
  name_en?: string
  category?: string
  formula_or_rule?: string
  inputs?: string[]
  economic_logic?: string
  signal_direction?: string
  source?: string
  frequency?: string
}): SeedFactorIn | null {
  const formula = (f.formula_or_rule || '').trim()
  if (!formula) return null
  return {
    factor_id: String(f.factor_id),
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
    jobChecked.value = []
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

async function optimizeJobs() {
  const selected = jobRows.value.filter((r) => jobChecked.value.includes(r._key))
  const seeds = selected.map(toSeed).filter((x): x is SeedFactorIn => !!x)
  await submitOptimize(seeds, `优化因子 · 任务产出 · ${seeds.length} 个`)
}

async function optimizeOneLibrary(row: LibraryFactor) {
  const seed = toSeed(row)
  if (!seed) {
    message.warning('该因子缺少可用公式')
    return
  }
  await submitOptimize([seed], `优化因子 · ${row.factor_id}`)
}

async function optimizeOneJob(row: JobFactorRow) {
  const seed = toSeed(row)
  if (!seed) {
    message.warning('该因子缺少可用公式')
    return
  }
  await submitOptimize([seed], `优化因子 · ${row.factor_id}`)
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

async function loadJobs() {
  if (jobsLoaded) return
  jobLoading.value = true
  try {
    const { data: jobs } = await listJobs(30)
    const succeeded = jobs.filter((j) => j.status === 'succeeded').slice(0, 10)
    const all: JobFactorRow[] = []
    for (const j of succeeded) {
      try {
        const { data } = await getFactors(j.job_id)
        data.forEach((f) =>
          all.push({
            ...f,
            job_id: j.job_id,
            _key: `${j.job_id}:${f.factor_id}`,
          }),
        )
      } catch {
        /* ignore */
      }
    }
    jobRows.value = all
    jobsLoaded = true
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败')
  } finally {
    jobLoading.value = false
  }
}

watch(tab, (v) => {
  if (v === 'jobs') loadJobs()
})

onMounted(loadAlpha)
</script>

<style scoped>
h2 { margin: 0; font-size: 20px; }
</style>
