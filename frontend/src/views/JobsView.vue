<template>
  <div class="page">
    <div class="title-row">
      <h2>研报分析</h2>
      <n-button type="primary" @click="openCreate">新建分析</n-button>
    </div>

    <n-card size="small">
      <n-data-table :columns="columns" :data="jobs" :loading="loading" size="small" />
    </n-card>

    <n-modal v-model:show="show" preset="card" title="新建研报分析" style="width: 720px" @after-leave="resetForm">
      <n-form label-placement="top">
        <n-form-item label="标题">
          <n-input v-model:value="form.title" placeholder="可选；上传 PDF 时默认用文件名" />
        </n-form-item>
        <n-tabs v-model:value="form.mode" type="segment" size="small" style="margin-bottom: 12px">
          <n-tab-pane name="upload" tab="上传文件" />
          <n-tab-pane name="text" tab="粘贴文本" />
        </n-tabs>
        <n-form-item v-if="form.mode === 'upload'" label="研报文件">
          <n-upload
            :default-upload="false"
            :max="1"
            accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain"
            :file-list="fileList"
            @update:file-list="onFileList"
            @before-upload="beforeUpload"
          >
            <n-upload-dragger>
              <div class="upload-hint">
                <div class="upload-title">点击或拖拽上传研报</div>
                <div class="upload-sub">支持 PDF / TXT / Markdown</div>
              </div>
            </n-upload-dragger>
          </n-upload>
        </n-form-item>
        <n-form-item v-else label="研报正文">
          <n-input v-model:value="form.content" type="textarea" :rows="12" placeholder="粘贴研报文本" />
        </n-form-item>
      </n-form>
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
import { h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSpace,
  NTabPane,
  NTabs,
  NTag,
  NUpload,
  NUploadDragger,
} from 'naive-ui'
import type { DataTableColumns, UploadFileInfo } from 'naive-ui'
import { createJobFromUpload, createJobText, cancelJob, listJobs, rerunJob, type JobSummary } from '@/api/client'
import { useModuleNotify } from '@/composables/useAppNotify'

const notify = useModuleNotify('研报分析')
const router = useRouter()
const loading = ref(false)
const submitting = ref(false)
const rerunningId = ref<string | null>(null)
const show = ref(false)
const jobs = ref<JobSummary[]>([])
const fileList = ref<UploadFileInfo[]>([])
const form = reactive({
  title: '',
  content: '',
  mode: 'upload' as 'upload' | 'text',
})
let timer: number | undefined

const columns: DataTableColumns<JobSummary> = [
  { title: '任务ID', key: 'job_id', width: 180, ellipsis: { tooltip: true } },
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.status === 'succeeded' ? 'success' : 'default',
        },
        () => row.status,
      ),
  },
  { title: '进度', key: 'percent', width: 80, render: (row) => `${row.progress?.percent ?? 0}%` },
  {
    title: '消息',
    key: 'message',
    ellipsis: { tooltip: true },
    render: (row) => row.progress?.message || '-',
  },
  {
    title: '操作',
    key: 'op',
    width: 220,
    render: (row) =>
      h(
        NSpace,
        { size: 8 },
        {
          default: () => [
            h(NButton, { text: true, type: 'primary', onClick: () => router.push(`/jobs/${row.job_id}`) }, () => '详情'),
            h(
              NButton,
              {
                text: true,
                type: 'info',
                disabled: !row.report_id || rerunningId.value === row.job_id,
                loading: rerunningId.value === row.job_id,
                onClick: () => onRerun(row),
              },
              () => '再次分析',
            ),
            row.status === 'running' || row.status === 'queued'
              ? h(
                  NButton,
                  {
                    text: true,
                    type: 'warning',
                    onClick: () => onCancel(row.job_id),
                  },
                  () => '取消',
                )
              : null,
          ],
        },
      ),
  },
]

function openCreate() {
  resetForm()
  show.value = true
}

function resetForm() {
  form.title = ''
  form.content = ''
  form.mode = 'upload'
  fileList.value = []
}

function onFileList(list: UploadFileInfo[]) {
  fileList.value = list.slice(-1)
}

function beforeUpload(data: { file: UploadFileInfo; fileList: UploadFileInfo[] }) {
  const name = (data.file.name || '').toLowerCase()
  const ok =
    name.endsWith('.pdf') ||
    name.endsWith('.txt') ||
    name.endsWith('.md') ||
    name.endsWith('.markdown')
  if (!ok) {
    notify.warning('仅支持 PDF / TXT / Markdown')
    return false
  }
  const raw = data.file.file
  if (raw && raw.size > 40 * 1024 * 1024) {
    notify.warning('文件过大（上限约 40MB）')
    return false
  }
  return true
}

async function onCancel(jobId: string) {
  try {
    await cancelJob(jobId)
    notify.success('已请求取消')
    await load()
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '取消失败')
  }
}

async function onRerun(row: JobSummary) {
  if (!row.report_id) {
    notify.warning('该任务无关联研报，无法再次分析')
    return
  }
  rerunningId.value = row.job_id
  try {
    const { data } = await rerunJob(row.job_id)
    notify.success(`已创建再次分析任务 ${data.job_id}`)
    await load()
    router.push(`/jobs/${data.job_id}`)
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '再次分析失败')
  } finally {
    rerunningId.value = null
  }
}

function hasActiveJobs(list: JobSummary[]) {
  return list.some((j) => j.status === 'queued' || j.status === 'running')
}

async function load() {
  loading.value = true
  try {
    const { data } = await listJobs(50)
    jobs.value = data
    // 无进行中任务时降低轮询频率（仍保留慢轮询以便新建后可见）
    if (timer) clearInterval(timer)
    timer = window.setInterval(load, hasActiveJobs(data) ? 4000 : 15000)
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    let data: JobSummary
    if (form.mode === 'upload') {
      const file = fileList.value[0]?.file
      if (!file) {
        notify.warning('请上传研报 PDF 或文本文件')
        return
      }
      const title =
        form.title.trim() ||
        (fileList.value[0]?.name || '').replace(/\.(pdf|txt|md|markdown)$/i, '') ||
        undefined
      ;({ data } = await createJobFromUpload(file, { title }))
    } else {
      if (!form.content.trim()) {
        notify.warning('请填写研报正文')
        return
      }
      ;({ data } = await createJobText({
        title: form.title || undefined,
        content: form.content,
      }))
    }
    notify.success('已创建任务')
    show.value = false
    resetForm()
    router.push(`/jobs/${data.job_id}`)
  } catch (e: any) {
    notify.error(e?.response?.data?.detail || e.message || '创建失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  load()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
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
.upload-hint {
  padding: 24px 12px;
  text-align: center;
}
.upload-title {
  font-size: 15px;
  color: #334155;
}
.upload-sub {
  margin-top: 6px;
  font-size: 13px;
  color: #64748b;
}
</style>
