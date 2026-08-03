<template>
  <div class="page" v-if="job">
    <div class="title-row">
      <div>
        <h2>任务详情</h2>
        <div class="sub">{{ job.job_id }} · {{ job.title || '未命名' }}</div>
      </div>
      <n-space align="center">
        <n-tag size="small" :bordered="false">{{ job.progress?.message || '-' }}</n-tag>
        <n-tag :type="statusType" :bordered="false">{{ job.status }}</n-tag>
        <n-button
          size="small"
          type="info"
          secondary
          :disabled="!job.report_id"
          :loading="rerunning"
          @click="onRerun"
        >
          再次分析
        </n-button>
      </n-space>
    </div>

    <div class="stack">
      <n-card title="执行步骤" size="small">
        <StepFlowChart :steps="steps" />
      </n-card>

      <n-card title="因子公式" size="small">
        <n-data-table :columns="factorCols" :data="factors" size="small" :bordered="true">
          <template #empty>
            <div class="empty-tip">
              {{
                job.status === 'succeeded'
                  ? '暂无因子（未提取到公式，或结果尚未回写）'
                  : '任务完成后展示最终/候选因子公式'
              }}
            </div>
          </template>
        </n-data-table>
      </n-card>
    </div>
  </div>
  <n-spin v-else style="margin-top: 40px" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import StepFlowChart from '@/components/StepFlowChart.vue'
import { getFactors, getJob, getSteps, rerunJob, type FactorFormula, type JobSummary, type StepDetail } from '@/api/client'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const job = ref<JobSummary | null>(null)
const factors = ref<FactorFormula[]>([])
const steps = ref<StepDetail[]>([])
const rerunning = ref(false)
let timer: number | undefined

const statusType = computed(() => {
  const s = job.value?.status
  if (s === 'succeeded') return 'success'
  if (s === 'failed' || s === 'timed_out') return 'error'
  if (s === 'cancelled') return 'warning'
  if (s === 'running') return 'info'
  return 'default'
})

const factorCols: DataTableColumns<FactorFormula> = [
  { title: 'ID', key: 'factor_id', width: 70 },
  { title: '名称', key: 'name_zh' },
  { title: '类别', key: 'category', width: 100 },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => (row.status === 'SAVE' ? 'SAVE' : row.status === 'DROP' || row.status === 'CANDIDATE' ? '淘汰' : row.status),
  },
  { title: '因子公式', key: 'formula_or_rule', ellipsis: { tooltip: true } },
  { title: '评分', key: 'final_score', width: 70 },
  { title: '中位分', key: 'median_score', width: 70 },
]

async function load() {
  const id = String(route.params.id)
  try {
    const j = await getJob(id)
    job.value = j.data
    const s = await getSteps(id)
    steps.value = [...s.data].sort((a, b) => a.seq - b.seq)
    if (j.data.status === 'succeeded' || j.data.saved_count > 0) {
      try {
        const f = await getFactors(id)
        factors.value = f.data
      } catch {
        factors.value = []
      }
    } else {
      factors.value = []
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败')
  }
}

async function onRerun() {
  if (!job.value?.report_id) {
    message.warning('该任务无关联研报，无法再次分析')
    return
  }
  rerunning.value = true
  try {
    const { data } = await rerunJob(job.value.job_id)
    message.success(`已创建再次分析任务 ${data.job_id}`)
    router.push(`/jobs/${data.job_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '再次分析失败')
  } finally {
    rerunning.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 3000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.title-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.title-row h2 {
  margin: 0;
  font-size: 20px;
}
.sub {
  margin-top: 4px;
  color: #64748b;
  font-size: 13px;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.empty-tip {
  padding: 16px;
  color: #94a3b8;
  font-size: 13px;
}
</style>
