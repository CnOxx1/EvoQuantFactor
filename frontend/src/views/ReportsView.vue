<template>
  <div class="page">
    <div class="title-row">
      <div>
        <h2>资讯分析</h2>
        <div class="sub">
          入库后自动 LLM 摘要（非因子）；可看原文与摘要。宏观/晨报等仍提示「不建议」做因子流水线。提示词见「提示词管理 → 资讯分析」
        </div>
      </div>
      <n-space>
        <n-button :loading="fixingTitles" secondary @click="onFixTitles">修复坏标题</n-button>
        <n-button :loading="backfilling" secondary @click="onBackfill">补跑未摘要</n-button>
        <n-button :loading="syncing" @click="onSync">立即同步</n-button>
        <n-button quaternary :loading="loading" @click="load">刷新</n-button>
      </n-space>
    </div>

    <n-space style="margin-bottom: 12px" align="center">
      <n-input
        v-model:value="query"
        clearable
        placeholder="搜索标题 / 机构 / external_id"
        style="width: min(420px, 40vw)"
        @keyup.enter="onSearch"
      />
      <n-select
        v-model:value="source"
        clearable
        placeholder="来源"
        style="width: 160px"
        :options="sourceOptions"
        @update:value="onSearch"
      />
      <n-select
        v-model:value="suitability"
        clearable
        placeholder="因子适配"
        style="width: 160px"
        :options="suitabilityOptions"
        @update:value="onSearch"
      />
      <n-button type="primary" :loading="loading" @click="onSearch">搜索</n-button>
    </n-space>

    <n-card size="small">
      <n-data-table
        :columns="columns"
        :data="rows"
        :loading="loading"
        size="small"
        :pagination="pagination"
        :paginate-single-page="false"
        remote
        :row-key="(r: ReportItem) => r.report_id"
        @update:page="onPage"
        @update:page-size="onPageSize"
      />
    </n-card>

    <n-drawer v-model:show="drawerShow" :width="drawerWidth" placement="right">
      <n-drawer-content :title="drawerTitle" closable>
        <n-space vertical :size="12">
          <n-space align="center">
            <n-tag size="small" :type="summaryTagType(content?.news_summary_status)" :bordered="false">
              摘要 {{ summaryLabel(content?.news_summary_status) }}
            </n-tag>
            <n-button size="small" secondary :loading="summarizing" @click="onResummarize">重新摘要</n-button>
            <n-button
              v-if="canRefetchPdf"
              size="small"
              secondary
              type="warning"
              :loading="refetchingPdf"
              @click="onRefetchPdf"
            >
              重抓 PDF
            </n-button>
            <n-button
              v-if="content?.pdf_url"
              tag="a"
              :href="content.pdf_url"
              target="_blank"
              rel="noopener noreferrer"
              size="small"
              secondary
            >
              打开源 PDF
            </n-button>
          </n-space>

          <n-alert v-if="content?.news_summary_error" type="error" :bordered="false">
            {{ content.news_summary_error }}
          </n-alert>
          <n-alert v-if="content?.text_incomplete" type="warning" :bordered="false">
            正文可能不完整（PDF 未抽到全文时为摘要占位）
          </n-alert>

          <template v-if="summaryObj">
            <div class="block">
              <div class="block-title">资讯摘要</div>
              <div class="headline">{{ summaryObj.headline || '—' }}</div>
              <p class="summary-text">{{ summaryObj.summary || '—' }}</p>
              <ul v-if="summaryObj.key_points?.length" class="points">
                <li v-for="(p, i) in summaryObj.key_points" :key="i">{{ p }}</li>
              </ul>
              <n-space v-if="summaryObj.topics?.length || summaryObj.entities?.length" size="small" style="margin-top: 8px">
                <n-tag v-for="t in summaryObj.topics || []" :key="'t'+t" size="small" :bordered="false">{{ t }}</n-tag>
                <n-tag v-for="e in summaryObj.entities || []" :key="'e'+e" size="small" type="info" :bordered="false">{{ e }}</n-tag>
              </n-space>
              <div v-if="summaryObj.sentiment || summaryObj.time_sensitivity" class="meta-line">
                情绪：{{ summaryObj.sentiment || '—' }} · 时效：{{ summaryObj.time_sensitivity || '—' }}
              </div>
              <p v-if="summaryObj.implications" class="impl">{{ summaryObj.implications }}</p>
            </div>
          </template>

          <div class="block">
            <div class="block-title">原文</div>
            <n-spin :show="contentLoading">
              <pre class="report-body">{{ content?.content || (contentLoading ? '' : '（无正文）') }}</pre>
            </n-spin>
          </div>
        </n-space>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NInput,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useDialog,
} from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import {
  backfillReportTitles,
  collectReportsRun,
  collectReportsStatus,
  createJobFromReport,
  getReportContent,
  listReports,
  refetchReportPdf,
  summarizeReport,
  summarizeReportsBackfill,
  type ReportContent,
  type ReportItem,
} from '@/api/client'
import { notifyCollectorStatus, useModuleNotify } from '@/composables/useAppNotify'

type NewsSummary = {
  headline?: string
  summary?: string
  key_points?: string[]
  entities?: string[]
  topics?: string[]
  sentiment?: string
  time_sensitivity?: string
  implications?: string
  quality_note?: string
}

const notify = useModuleNotify('资讯分析')
const dialog = useDialog()
const router = useRouter()
const loading = ref(false)
const syncing = ref(false)
const backfilling = ref(false)
const fixingTitles = ref(false)
const analyzingId = ref<string | null>(null)
const summarizing = ref(false)
const refetchingPdf = ref(false)
const rows = ref<ReportItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = ref('')
const source = ref<string | null>(null)
const suitability = ref<string | null>(null)

const drawerShow = ref(false)
const contentLoading = ref(false)
const content = ref<ReportContent | null>(null)
const activeReportId = ref<string | null>(null)
const drawerWidth = ref(840)
const drawerTitle = computed(() => content.value?.title || content.value?.filename || '原文')
const summaryObj = computed(() => (content.value?.news_summary || null) as NewsSummary | null)
const canRefetchPdf = computed(() => {
  const m = content.value?.meta || {}
  const code = String(m.info_code || '')
  return code.startsWith('AP') && (Boolean(m.text_incomplete) || Boolean(m.pdf_error))
})

function summaryStatusOf(row: ReportItem): string {
  return String(row.meta?.news_summary_status || '')
}

function summaryLabel(status?: string | null) {
  const s = status || ''
  if (s === 'done') return '已完成'
  if (s === 'running') return '生成中'
  if (s === 'pending') return '排队中'
  if (s === 'failed') return '失败'
  if (s === 'skipped') return '已跳过'
  return '未摘要'
}

function summaryTagType(status?: string | null): 'success' | 'warning' | 'error' | 'info' | 'default' {
  const s = status || ''
  if (s === 'done') return 'success'
  if (s === 'running' || s === 'pending') return 'info'
  if (s === 'failed') return 'error'
  if (s === 'skipped') return 'default'
  return 'warning'
}

/** 宏观/晨报或正文不完整：保留入库，因子挖掘不建议跑流水线 */
function analyzeAdvice(row: ReportItem): { skip: boolean; reason: string } {
  const qType = Number(row.meta?.q_type)
  const label = String(row.meta?.q_type_label || '')
  if (qType === 3 || label === '宏观') {
    return { skip: true, reason: '宏观研报多为叙事/时政，通常抽不出可回测因子' }
  }
  if (qType === 4 || label === '晨报') {
    return { skip: true, reason: '晨报偏资讯汇总，通常不适合因子挖掘' }
  }
  if (row.meta?.text_incomplete) {
    return { skip: true, reason: '正文不完整（PDF 未抽到），分析结果可靠性差' }
  }
  return { skip: false, reason: '' }
}

const sourceOptions = [
  { label: '东财研报', value: 'eastmoney' },
  { label: '东财资讯', value: 'eastmoney_news' },
  { label: '华尔街见闻', value: 'wallstreetcn' },
  { label: '新浪财经', value: 'sina' },
  { label: '同花顺', value: 'ths' },
  { label: '金十数据', value: 'jin10' },
  { label: '萝卜投研', value: 'luobo' },
  { label: '上传', value: 'upload' },
  { label: '文本', value: 'text' },
]

const suitabilityOptions = [
  { label: '适合因子', value: 'factor' },
  { label: '仅资讯/不建议因子', value: 'news_only' },
]

const pagination = computed<PaginationProps>(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 条`,
}))

const columns: DataTableColumns<ReportItem> = [
  {
    title: '标题',
    key: 'title',
    ellipsis: { tooltip: true },
    minWidth: 280,
    render: (row) => row.title || row.filename,
  },
  {
    title: '机构',
    key: 'org',
    width: 120,
    ellipsis: { tooltip: true },
    render: (row) => String(row.meta?.org || '-'),
  },
  {
    title: '类型',
    key: 'q_type',
    width: 120,
    ellipsis: { tooltip: true },
    render: (row) => String(row.meta?.q_type_label || '-'),
  },
  {
    title: '摘要',
    key: 'news_summary',
    width: 96,
    render: (row) => {
      const st = summaryStatusOf(row)
      return h(
        NTag,
        { size: 'small', type: summaryTagType(st), bordered: false, title: String(row.meta?.news_summary_error || '') },
        () => summaryLabel(st),
      )
    },
  },
  {
    title: '来源',
    key: 'source',
    width: 130,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false }, () => row.source || row.meta?.source || '-'),
  },
  {
    title: '日期',
    key: 'publish_date',
    width: 150,
    render: (row) => String(row.meta?.publish_date || row.created_at?.slice(0, 10) || '-'),
  },
  {
    title: '因子',
    key: 'job_count',
    width: 160,
    render: (row) => {
      const advice = analyzeAdvice(row)
      const tags = [
        h(
          NTag,
          {
            size: 'small',
            type: (row.job_count || 0) > 0 ? 'success' : 'default',
            bordered: false,
          },
          () => ((row.job_count || 0) > 0 ? `已分析 ${row.job_count}` : '未跑因子'),
        ),
      ]
      if (advice.skip) {
        tags.push(
          h(
            NTag,
            {
              size: 'small',
              type: 'warning',
              bordered: false,
              title: advice.reason,
            },
            () => '不建议因子',
          ),
        )
      }
      return h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' } }, tags)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    fixed: 'right',
    render: (row) => {
      const advice = analyzeAdvice(row)
      return h(
        NSpace,
        { size: 8 },
        {
          default: () => [
            h(
              NButton,
              {
                text: true,
                type: 'default',
                size: 'small',
                onClick: () => onViewContent(row),
              },
              () => '原文',
            ),
            h(
              NButton,
              {
                text: true,
                type: advice.skip ? 'warning' : 'primary',
                size: 'small',
                loading: analyzingId.value === row.report_id,
                onClick: () => onAnalyze(row),
              },
              () => '因子',
            ),
          ],
        },
      )
    },
  },
]

async function load() {
  loading.value = true
  try {
    const { data } = await listReports({
      q: query.value.trim() || undefined,
      source: source.value || undefined,
      suitability: suitability.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    rows.value = data.items
    total.value = data.total
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  load()
}

function onPage(p: number) {
  page.value = p
  load()
}

function onPageSize(size: number) {
  pageSize.value = size
  page.value = 1
  load()
}

async function onBackfill() {
  backfilling.value = true
  try {
    const { data } = await summarizeReportsBackfill({ limit: 50, only_missing: true })
    notify.success(`已入队摘要 ${data.queued} 条（跳过已完成 ${data.skipped}）`)
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '补跑失败')
  } finally {
    backfilling.value = false
  }
}

async function onFixTitles() {
  fixingTitles.value = true
  try {
    const { data } = await backfillReportTitles(300)
    notify.success(`标题修复：扫描 ${data.scanned}，修复 ${data.fixed}`)
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '标题修复失败')
  } finally {
    fixingTitles.value = false
  }
}

async function sleep(ms: number) {
  await new Promise((r) => setTimeout(r, ms))
}

async function waitCollectFinished(timeoutMs = 600_000) {
  const started = Date.now()
  let last: Awaited<ReturnType<typeof collectReportsStatus>>['data'] | null = null
  while (Date.now() - started < timeoutMs) {
    const { data } = await collectReportsStatus()
    last = data
    if (!data.running) return data
    await sleep(2000)
  }
  throw new Error(last?.last_error || '采集超时，请稍后刷新查看')
}

async function onSync() {
  syncing.value = true
  try {
    const { data: started } = await collectReportsRun()
    if (started.accepted === false && started.running) {
      notify.info('已有采集在进行中，等待完成…')
    } else {
      notify.info('采集已在后台开始，完成后自动刷新')
    }
    const data = await waitCollectFinished()
    notifyCollectorStatus(data, { forcePopup: true })
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '同步失败')
  } finally {
    syncing.value = false
  }
}

async function onViewContent(row: ReportItem) {
  activeReportId.value = row.report_id
  drawerWidth.value = Math.min(960, Math.max(640, Math.floor(window.innerWidth * 0.45)))
  drawerShow.value = true
  contentLoading.value = true
  content.value = {
    report_id: row.report_id,
    title: row.title || row.filename,
    filename: row.filename,
    content: '',
    meta: row.meta || {},
    news_summary_status: summaryStatusOf(row),
    news_summary: (row.meta?.news_summary as Record<string, unknown>) || null,
  }
  try {
    const { data } = await getReportContent(row.report_id)
    content.value = data
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '加载原文失败')
    drawerShow.value = false
  } finally {
    contentLoading.value = false
  }
}

async function onRefetchPdf() {
  const id = activeReportId.value
  if (!id) return
  refetchingPdf.value = true
  try {
    const { data } = await refetchReportPdf(id)
    notify.success(`PDF 重抓成功（${data.pdf_bytes} bytes），已重新入队摘要`)
    const { data: refreshed } = await getReportContent(id)
    content.value = refreshed
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '重抓 PDF 失败')
  } finally {
    refetchingPdf.value = false
  }
}

async function onResummarize() {
  const id = activeReportId.value
  if (!id) return
  summarizing.value = true
  try {
    const { data } = await summarizeReport(id, true)
    if (data.status === 'done') {
      notify.success('摘要已更新')
    } else if (data.status === 'failed') {
      notify.error(data.error || '摘要失败')
    } else {
      notify.info(`摘要状态：${data.status}`)
    }
    const { data: refreshed } = await getReportContent(id)
    content.value = refreshed
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '重新摘要失败')
  } finally {
    summarizing.value = false
  }
}

async function doAnalyze(row: ReportItem) {
  analyzingId.value = row.report_id
  try {
    const { data } = await createJobFromReport({
      report_id: row.report_id,
      title: row.title || row.filename,
    })
    notify.success(`已创建因子分析任务 ${data.job_id}`)
    router.push(`/jobs/${data.job_id}`)
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '创建分析失败')
  } finally {
    analyzingId.value = null
  }
}

function onAnalyze(row: ReportItem) {
  const advice = analyzeAdvice(row)
  if (!advice.skip) {
    void doAnalyze(row)
    return
  }
  dialog.warning({
    title: '不建议因子分析',
    content: `${advice.reason}。资讯摘要会照常生成；仍要强制发起因子流水线吗？`,
    positiveText: '仍要分析',
    negativeText: '取消',
    onPositiveClick: () => {
      void doAnalyze(row)
    },
  })
}

onMounted(load)
</script>

<style scoped>
.page {
  width: 100%;
  max-width: none;
  box-sizing: border-box;
}
.title-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
h2 { margin: 0; font-size: 20px; }
.sub {
  margin-top: 4px;
  color: #64748b;
  font-size: 13px;
  max-width: 72ch;
  line-height: 1.5;
}
.block { border-top: 1px solid #e2e8f0; padding-top: 10px; }
.block-title { font-size: 13px; font-weight: 600; color: #334155; margin-bottom: 8px; }
.headline { font-size: 16px; font-weight: 600; color: #0f172a; margin-bottom: 8px; }
.summary-text { margin: 0 0 8px; font-size: 14px; line-height: 1.65; color: #1e293b; white-space: pre-wrap; }
.points { margin: 0; padding-left: 18px; color: #334155; font-size: 13px; line-height: 1.55; }
.meta-line { margin-top: 8px; font-size: 12px; color: #64748b; }
.impl { margin: 8px 0 0; font-size: 13px; color: #475569; line-height: 1.55; }
.report-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #0f172a;
  max-height: calc(100vh - 280px);
  overflow: auto;
}
</style>
