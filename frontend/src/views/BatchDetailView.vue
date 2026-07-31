<template>
  <div class="page" v-if="batch">
    <div class="title-row">
      <div>
        <h2>批次详情</h2>
        <div class="sub">{{ batch.batch_id }} · {{ batch.title }}</div>
      </div>
      <n-space>
        <n-tag :bordered="false">{{ batch.status }}</n-tag>
        <n-button @click="onCancel" :disabled="batch.percent >= 100">取消整批</n-button>
      </n-space>
    </div>
    <n-progress type="line" :percentage="batch.percent" style="margin-bottom: 16px" />
    <n-card size="small" title="子任务">
      <n-data-table :columns="columns" :data="batch.jobs" size="small" />
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { h, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NProgress, NSpace, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { cancelBatch, getBatch, type BatchSummary, type JobSummary } from '@/api/client'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const batch = ref<BatchSummary | null>(null)
let timer: number | undefined

const columns: DataTableColumns<JobSummary> = [
  { title: '任务ID', key: 'job_id', ellipsis: { tooltip: true } },
  { title: '标题', key: 'title' },
  { title: '状态', key: 'status' },
  { title: '进度', key: 'p', render: (r) => `${r.progress?.percent ?? 0}%` },
  {
    title: '操作',
    key: 'op',
    render: (r) => h(NButton, { text: true, type: 'primary', onClick: () => router.push(`/jobs/${r.job_id}`) }, () => '详情'),
  },
]

async function load() {
  try {
    const { data } = await getBatch(String(route.params.id))
    batch.value = data
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败')
  }
}

async function onCancel() {
  try {
    const { data } = await cancelBatch(String(route.params.id))
    batch.value = data
    message.success('已请求取消')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '取消失败')
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 3000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title-row h2 { margin: 0; font-size: 20px; }
.sub { margin-top: 4px; color: #64748b; font-size: 13px; }
</style>
