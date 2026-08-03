<template>
  <div class="page">
    <div class="title-row">
      <h2>批量任务</h2>
      <n-button type="primary" @click="show = true">+ 新建批量分析</n-button>
    </div>

    <n-grid cols="3" x-gap="16" class="mb">
      <n-gi><n-card size="small" class="stat"><div class="num">{{ running }}</div><div class="label">进行中</div></n-card></n-gi>
      <n-gi><n-card size="small" class="stat"><div class="num">{{ done }}</div><div class="label">已完成</div></n-card></n-gi>
      <n-gi><n-card size="small" class="stat"><div class="num">{{ failed }}</div><div class="label">失败</div></n-card></n-gi>
    </n-grid>

    <n-card size="small">
      <n-data-table :columns="columns" :data="batches" :loading="loading" size="small" />
    </n-card>

    <n-modal v-model:show="show" preset="card" title="新建批量分析" style="width: 820px">
      <n-dynamic-input v-model:value="items" :on-create="() => ({ title: '', content: '' })">
        <template #default="{ value }">
          <div class="item">
            <n-input v-model:value="value.title" placeholder="标题" style="margin-bottom: 8px" />
            <n-input v-model:value="value.content" type="textarea" :rows="4" placeholder="研报正文" />
          </div>
        </template>
      </n-dynamic-input>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="submit">提交</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NDataTable,
  NDynamicInput,
  NGi,
  NGrid,
  NInput,
  NModal,
  NProgress,
  NSpace,
  NTag,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { createBatch, listBatches, type BatchSummary } from '@/api/client'
import { useModuleNotify } from '@/composables/useAppNotify'

const notify = useModuleNotify('批量任务')
const router = useRouter()
const loading = ref(false)
const submitting = ref(false)
const show = ref(false)
const batches = ref<BatchSummary[]>([])
const items = ref([{ title: '', content: '' }, { title: '', content: '' }])
let timer: number | undefined

const running = computed(() => batches.value.filter((b) => b.status === 'running' || b.status === 'queued').length)
const done = computed(() => batches.value.filter((b) => b.status === 'succeeded').length)
const failed = computed(() => batches.value.filter((b) => String(b.status).includes('error') || b.status === 'failed').length)

const columns: DataTableColumns<BatchSummary> = [
  { title: '批次ID', key: 'batch_id', width: 170, ellipsis: { tooltip: true } },
  { title: '标题', key: 'title' },
  {
    title: '进度',
    key: 'percent',
    width: 160,
    render: (row) => h(NProgress, { type: 'line', percentage: row.percent || 0, indicatorPlacement: 'inside', height: 16 }),
  },
  {
    title: '状态',
    key: 'status',
    width: 140,
    render: (row) => h(NTag, { size: 'small', bordered: false }, () => row.status),
  },
  { title: '任务数', key: 'total', width: 80 },
  {
    title: '操作',
    key: 'op',
    width: 90,
    render: (row) => h(NButton, { text: true, type: 'primary', onClick: () => router.push(`/batches/${row.batch_id}`) }, () => '详情'),
  },
]

async function load() {
  loading.value = true
  try {
    const { data } = await listBatches(50)
    batches.value = data
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  const valid = items.value.filter((i) => i.content.trim())
  if (!valid.length) {
    notify.warning('请至少填写一份研报')
    return
  }
  submitting.value = true
  try {
    const { data } = await createBatch({
      title: `批量-${valid.length}`,
      items: valid,
    })
    notify.success('批次已创建')
    show.value = false
    router.push(`/batches/${data.batch_id}`)
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '创建失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 4000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title-row h2 { margin: 0; font-size: 20px; }
.mb { margin-bottom: 16px; }
.stat { text-align: center; }
.num { font-size: 28px; font-weight: 700; color: #0d9488; }
.label { color: #64748b; margin-top: 4px; }
.item { width: 100%; padding: 8px 0; }
</style>
