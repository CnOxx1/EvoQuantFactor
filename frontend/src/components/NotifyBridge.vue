<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import { useNotification } from 'naive-ui'
import { bindNotificationApi } from '@/composables/useAppNotify'

const api = useNotification()
bindNotificationApi(api)
onBeforeUnmount(() => bindNotificationApi(null))
</script>

<template></template>
