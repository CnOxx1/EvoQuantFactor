<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">因子提取</div>
      <n-menu
        :value="activeKey"
        :options="menuOptions"
        :collapsed="false"
        inverted
        @update:value="onMenu"
      />
    </aside>
    <section class="main">
      <header class="topbar">
        <div class="crumb">{{ crumb }}</div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon } from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import {
  HomeOutline,
  DocumentTextOutline,
  MailOutline,
  LayersOutline,
  FlaskOutline,
  HardwareChipOutline,
  OptionsOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

function icon(comp: any) {
  return () => h(NIcon, null, { default: () => h(comp) })
}

const route = useRoute()
const router = useRouter()

const menuOptions: MenuOption[] = [
  { label: '工作台', key: '/dashboard', icon: icon(HomeOutline) },
  { label: '资讯分析', key: '/reports', icon: icon(MailOutline) },
  { label: '研报分析', key: '/jobs', icon: icon(DocumentTextOutline) },
  { label: '批量任务', key: '/batches', icon: icon(LayersOutline) },
  { label: '因子库', key: '/factors', icon: icon(FlaskOutline) },
  { label: 'LLM配置', key: '/llm', icon: icon(HardwareChipOutline) },
  { label: '提示词管理', key: '/prompts', icon: icon(OptionsOutline) },
  { label: '系统设置', key: '/settings', icon: icon(SettingsOutline) },
]

const activeKey = computed(() => {
  const p = route.path
  if (p.startsWith('/jobs')) return '/jobs'
  if (p.startsWith('/batches')) return '/batches'
  if (p.startsWith('/reports')) return '/reports'
  return p
})

const crumb = computed(() => String(route.meta.title || '工作台'))

function onMenu(key: string) {
  router.push(key)
}
</script>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  min-height: 100vh;
}
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  color: var(--sidebar-text);
  padding: 16px 0;
}
.brand {
  padding: 8px 20px 20px;
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.5px;
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--page-bg);
}
.topbar {
  height: 52px;
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 24px;
}
.crumb {
  font-size: 14px;
  color: #64748b;
}
.content {
  flex: 1;
  padding: 20px 24px 32px;
  overflow: auto;
}
</style>
