<template>
  <div class="page">
    <h2>LLM 配置</h2>
    <n-card size="small" style="margin-top: 16px; max-width: 720px">
      <n-form label-placement="left" label-width="120" :model="form">
        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item label="Mock 模式">
          <n-switch v-model:value="form.use_mock" />
        </n-form-item>
        <n-form-item label="API 格式">
          <n-select
            v-model:value="form.api_format"
            :options="formatOptions"
            style="width: 280px"
          />
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="form.base_url" :placeholder="baseUrlPlaceholder" />
        </n-form-item>
        <n-form-item label="API Key">
          <n-input
            v-model:value="form.api_key"
            type="password"
            show-password-on="click"
            :placeholder="form.api_key_set ? form.api_key_masked || '已配置，留空不修改' : '请输入 Key'"
          />
        </n-form-item>
        <n-form-item label="Step1 模型">
          <n-input
            v-model:value="form.model_step1"
            :placeholder="form.api_format === 'cursor' ? 'composer-2.5 或 GET /v1/models' : ''"
          />
        </n-form-item>
        <n-form-item label="评审模型">
          <n-input
            v-model:value="form.model_review"
            :placeholder="form.api_format === 'cursor' ? 'composer-2.5 / auto' : ''"
          />
        </n-form-item>
        <n-form-item label="超时(秒)">
          <n-input-number
            v-model:value="form.timeout_sec"
            :min="10"
            :max="1800"
          />
        </n-form-item>
        <n-alert
          v-if="form.api_format === 'cursor'"
          type="info"
          :bordered="false"
          style="margin-bottom: 12px"
          title="Cursor Cloud Agents"
        >
          使用无仓库 Agent：POST /v1/agents → 轮询 run.result。API Key 来自
          Cursor Dashboard → API Keys。Cloud Agent 通常比普通 Chat 更慢，建议超时 ≥ 300 秒。
        </n-alert>
        <n-space>
          <n-button type="primary" :loading="saving" @click="save">保存</n-button>
          <n-button :loading="testing" @click="onTest">测试连通</n-button>
        </n-space>
      </n-form>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSwitch,
  useMessage,
} from 'naive-ui'
import { getLlmConfig, putLlmConfig, testLlm } from '@/api/client'

const message = useMessage()
const saving = ref(false)
const testing = ref(false)
const formatOptions = [
  { label: 'OpenAI Chat Completions', value: 'openai' },
  { label: 'Anthropic Messages (Opus/Claude)', value: 'anthropic' },
  { label: 'Cursor Cloud Agents API', value: 'cursor' },
]
const form = reactive<any>({
  enabled: true,
  use_mock: true,
  api_format: 'openai',
  base_url: '',
  api_key: '',
  api_key_set: false,
  api_key_masked: '',
  model_step1: '',
  model_review: '',
  timeout_sec: 120,
})

const baseUrlPlaceholder = computed(() => {
  if (form.api_format === 'anthropic') return 'https://api.anthropic.com/v1'
  if (form.api_format === 'cursor') return 'https://api.cursor.com'
  return 'https://api.openai.com/v1'
})

async function load() {
  const { data } = await getLlmConfig()
  Object.assign(form, data, { api_key: '', api_format: data.api_format || 'openai' })
}

async function save() {
  saving.value = true
  try {
    const payload: any = {
      enabled: form.enabled,
      use_mock: form.use_mock,
      api_format: form.api_format,
      base_url: form.base_url,
      model_step1: form.model_step1,
      model_review: form.model_review,
      timeout_sec: form.timeout_sec,
    }
    if (form.api_key) payload.api_key = form.api_key
    const { data } = await putLlmConfig(payload)
    Object.assign(form, data, { api_key: '' })
    message.success('已保存')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onTest() {
  testing.value = true
  try {
    const payload: any = {
      enabled: form.enabled,
      use_mock: form.use_mock,
      api_format: form.api_format,
      base_url: form.base_url,
      model_step1: form.model_step1,
      model_review: form.model_review,
      timeout_sec: form.timeout_sec,
    }
    if (form.api_key) payload.api_key = form.api_key
    const { data } = await testLlm(payload)
    if (data.ok) message.success(data.message || '连通成功')
    else message.warning(data.message || '连通失败')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    const msg = typeof detail === 'string' ? detail : e?.response?.data?.message || e.message
    message.error(msg || '测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  try {
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载失败')
  }
})
</script>

<style scoped>
h2 { margin: 0; font-size: 20px; }
</style>
