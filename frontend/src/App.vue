<template>
  <n-config-provider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-notification-provider placement="top-right" :max="5">
        <n-dialog-provider>
          <NotifyBridge />
          <router-view />
        </n-dialog-provider>
      </n-notification-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NNotificationProvider,
  zhCN,
  dateZhCN,
} from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import NotifyBridge from '@/components/NotifyBridge.vue'

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#0d9488',
    primaryColorHover: '#14b8a6',
    primaryColorPressed: '#0f766e',
    borderRadius: '6px',
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  Button: {
    colorPrimary: '#0d9488',
  },
  Menu: {
    itemTextColor: '#cbd5e1',
    itemTextColorHover: '#fff',
    itemTextColorActive: '#fff',
    itemColorActive: 'rgba(13, 148, 136, 0.18)',
    itemIconColor: '#94a3b8',
    itemIconColorActive: '#5eead4',
  },
}
</script>
