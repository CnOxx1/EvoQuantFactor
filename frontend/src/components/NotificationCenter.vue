<template>
  <n-popover
    v-model:show="open"
    trigger="click"
    placement="bottom-end"
    :width="380"
    display-directive="show"
    @update:show="onOpenChange"
  >
    <template #trigger>
      <n-badge :value="unreadCount" :max="99" :show-zero="false">
        <n-button quaternary circle class="bell-btn" aria-label="通知中心">
          <n-icon size="20">
            <NotificationsOutline />
          </n-icon>
        </n-button>
      </n-badge>
    </template>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">通知</span>
        <n-space :size="4">
          <n-button text size="tiny" :disabled="!unreadCount" @click="markAllRead">全部已读</n-button>
          <n-button text size="tiny" :disabled="!items.length" @click="clear">清空</n-button>
        </n-space>
      </div>

      <n-scrollbar style="max-height: 420px">
        <div v-if="!items.length" class="empty">暂无通知</div>
        <div
          v-for="row in items"
          :key="row.id"
          class="item"
          :class="{ unread: !row.read, [row.level]: true }"
          @click="markRead(row.id)"
        >
          <div class="item-top">
            <n-tag size="tiny" :type="tagType(row.level)" :bordered="false">{{ row.module }}</n-tag>
            <span class="time">{{ formatTime(row.createdAt) }}</span>
          </div>
          <div class="item-title">{{ row.title }}</div>
          <div class="item-body">{{ row.content }}</div>
        </div>
      </n-scrollbar>
    </div>
  </n-popover>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  NBadge,
  NButton,
  NIcon,
  NPopover,
  NScrollbar,
  NSpace,
  NTag,
} from 'naive-ui'
import { NotificationsOutline } from '@vicons/ionicons5'
import { collectReportsStatus } from '@/api/client'
import { notifyCollectorStatus, useAppNotify } from '@/composables/useAppNotify'

const { items, unreadCount, markRead, markAllRead, clear } = useAppNotify()
const open = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null
let lastFingerprint = ''

function tagType(level: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  if (level === 'success') return 'success'
  if (level === 'warning') return 'warning'
  if (level === 'error') return 'error'
  if (level === 'info') return 'info'
  return 'default'
}

function formatTime(ts: number) {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function onOpenChange(show: boolean) {
  if (show) markAllRead()
}

async function pollCollector() {
  try {
    const { data } = await collectReportsStatus()
    const fp = [
      data.last_finished_at || '',
      data.last_error || '',
      String(data.last_added ?? ''),
      String(data.last_skipped ?? ''),
      data.luobo_configured ? '1' : '0',
      (data.last_sources || []).join(','),
    ].join('|')
    if (fp === lastFingerprint) return
    lastFingerprint = fp
    notifyCollectorStatus(data)
  } catch {
    /* 后端未启动时静默 */
  }
}

onMounted(() => {
  pollCollector()
  pollTimer = setInterval(pollCollector, 60_000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.bell-btn {
  color: #64748b;
}
.panel {
  margin: -4px -8px;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px 10px;
  border-bottom: 1px solid var(--border);
}
.panel-title {
  font-weight: 600;
  font-size: 14px;
}
.empty {
  padding: 28px 12px;
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
}
.item {
  padding: 10px 8px;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}
.item:hover {
  background: #f8fafc;
}
.item.unread {
  background: #f0fdfa;
}
.item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.time {
  font-size: 11px;
  color: #94a3b8;
  flex-shrink: 0;
}
.item-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 2px;
}
.item-body {
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.item.error .item-title {
  color: #b91c1c;
}
.item.warning .item-title {
  color: #b45309;
}
</style>
