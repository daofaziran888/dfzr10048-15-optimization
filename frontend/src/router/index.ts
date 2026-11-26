import { createRouter, createWebHistory } from 'vue-router'

import MissingScanView from '@/views/MissingScanView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/missing-scan', name: 'missing-scan', component: MissingScanView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
