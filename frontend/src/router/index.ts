import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppLayout,
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '工作台' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '研报分析' } },
        { path: 'jobs/:id', name: 'job-detail', component: () => import('@/views/JobDetailView.vue'), meta: { title: '任务详情' } },
        { path: 'batches', name: 'batches', component: () => import('@/views/BatchesView.vue'), meta: { title: '批量任务' } },
        { path: 'batches/:id', name: 'batch-detail', component: () => import('@/views/BatchDetailView.vue'), meta: { title: '批次详情' } },
        { path: 'factors', name: 'factors', component: () => import('@/views/FactorsView.vue'), meta: { title: '因子库' } },
        { path: 'llm', name: 'llm', component: () => import('@/views/LlmConfigView.vue'), meta: { title: 'LLM配置' } },
        { path: 'prompts', name: 'prompts', component: () => import('@/views/PromptsView.vue'), meta: { title: '提示词管理' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置' } },
      ],
    },
  ],
})

export default router
