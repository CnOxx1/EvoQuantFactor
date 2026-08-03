<template>
  <div class="page">
    <h2>系统设置</h2>
    <n-card size="small" style="margin-top: 16px; max-width: 640px">
      <n-form label-placement="left" label-width="120">
        <n-form-item label="API Token">
          <n-input v-model:value="token" type="password" show-password-on="click" placeholder="Bearer Token（AUTH_DISABLED=false 时需要）" />
        </n-form-item>
        <n-form-item label="后端健康">
          <n-space>
            <n-tag :type="healthOk ? 'success' : 'error'" :bordered="false">{{ healthOk ? 'OK' : '不可用' }}</n-tag>
            <n-button size="small" @click="check">刷新</n-button>
          </n-space>
        </n-form-item>
        <n-button type="primary" @click="save">保存 Token</n-button>
      </n-form>
      <n-divider />
      <pre class="meta">{{ metaText }}</pre>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NCard, NDivider, NForm, NFormItem, NInput, NSpace, NTag } from 'naive-ui'
import { healthApi, metaApi } from '@/api/client'
import { useModuleNotify } from '@/composables/useAppNotify'

const notify = useModuleNotify('系统设置')
const token = ref(localStorage.getItem('api_token') || '')
const healthOk = ref(false)
const metaText = ref('')

function save() {
  localStorage.setItem('api_token', token.value.trim())
  notify.success('已保存到本地')
  check()
}

async function check() {
  try {
    const h = await healthApi()
    healthOk.value = h.data?.status === 'ok'
    const m = await metaApi()
    metaText.value = JSON.stringify(m.data, null, 2)
  } catch (e: any) {
    healthOk.value = false
    metaText.value = e?.message || '无法连接后端'
  }
}

onMounted(check)
</script>

<style scoped>
h2 { margin: 0; font-size: 20px; }
.meta {
  background: #f1f5f9;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  overflow: auto;
  max-height: 360px;
}
</style>
