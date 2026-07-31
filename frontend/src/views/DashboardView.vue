<template>
  <div class="page">
    <div class="title-row">
      <h2>工作台</h2>
      <n-space>
        <n-button type="primary" @click="$router.push('/jobs')">新建分析</n-button>
        <n-button @click="$router.push('/batches')">批量任务</n-button>
      </n-space>
    </div>

    <n-grid cols="3" x-gap="16" y-gap="16" responsive="screen">
      <n-gi>
        <n-card size="small" class="stat"><div class="num">{{ stats.running }}</div><div class="label">进行中</div></n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat"><div class="num">{{ stats.succeeded }}</div><div class="label">已完成</div></n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat"><div class="num">{{ stats.failed }}</div><div class="label">失败/超时</div></n-card>
      </n-gi>
    </n-grid>

    <n-card title="最近任务" class="mt" size="small">
      <n-data-table :columns="columns" :data="jobs" :bordered="true" size="small" :loading="loading" />
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NGi, NGrid, NSpace, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { listJobs, type JobSummary } from '@/api/client'

const message = useMessage()
const router = useRouter()
const loading = ref(false)
const jobs = ref<JobSummary[]>([])
const stats = reactive({ running: 0, succeeded: 0, failed: 0 })

const statusType = (s: string) => {
  if (s === 'succeeded') return 'success'
  if (s === 'failed' || s === 'timed_out') return 'error'
  if (s === 'running') return 'info'
  if (s === 'cancelled') return 'warning'
  return 'default'
}

const columns: DataTableColumns<JobSummary> = [
  { title: '任务ID', key: 'job_id', ellipsis: { tooltip: true } },
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    render: (row) => h(NTag, { type: statusType(row.status), size: 'small', bordered: false }, () => row.status),
  },
  { title: '进度', key: 'progress', render: (row) => `${row.progress?.percent ?? 0}%` },
  {
    title: '操作',
    key: 'actions',
    render: (row) =>
      h(NButton, { text: true, type: 'primary', onClick: () => router.push(`/jobs/${row.job_id}`) }, () => '详情'),
  },
]

async function load() {
  loading.value = true
  try {
    const { data } = await listJobs(20)
    jobs.value = data
    stats.running = data.filter((j) => j.status === 'running' || j.status === 'queued').length
    stats.succeeded = data.filter((j) => j.status === 'succeeded').length
    stats.failed = data.filter((j) => j.status === 'failed' || j.status === 'timed_out').length
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败，请确认后端已启动')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title-row h2 {
  margin: 0;
  font-size: 20px;
}
.stat {
  text-align: center;
}
.num {
  font-size: 28px;
  font-weight: 700;
  color: #0d9488;
}
.label {
  color: #64748b;
  margin-top: 4px;
}
.mt {
  margin-top: 16px;
}
</style>
