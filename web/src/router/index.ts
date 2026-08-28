import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { canAccess } from './guard'
import { applicationBasePath, embeddedUserManagementHidden } from '../platform'

const router = createRouter({
  history: createWebHistory(applicationBasePath()),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { title: '登录' },
    },
    {
      path: '/',
      component: () => import('../layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      redirect: '/chat',
      children: [
        {
          path: 'kb',
          name: 'kb-list',
          component: () => import('../views/KbListView.vue'),
          meta: { title: '知识库' },
        },
        {
          path: 'chat',
          name: 'chat',
          component: () => import('../views/ChatView.vue'),
          meta: { title: '对话' },
        },
        {
          path: 'video',
          name: 'video-analysis',
          component: () => import('../views/VideoAnalysisView.vue'),
          meta: { title: 'AI 视频分析' },
        },
        {
          path: 'video/library',
          name: 'video-library',
          component: () => import('../views/VideoLibraryView.vue'),
          meta: { title: '视频数据库' },
        },
        {
          path: 'joom',
          name: 'joom-pricing',
          component: () => import('../views/JoomPricingView.vue'),
          meta: { title: 'JOOM 定价器' },
        },
        {
          path: 'settings',
          name: 'system-settings',
          component: () => import('../views/SystemSettingsView.vue'),
          meta: { title: '系统设置', roles: ['account_admin'] },
        },
        {
          path: 'settings/rag',
          name: 'kb-settings',
          component: () => import('../views/SettingsView.vue'),
          meta: { title: '知识库设置', roles: ['account_admin'] },
        },
        {
          path: 'settings/users',
          name: 'users',
          component: () => import('../views/UsersView.vue'),
          meta: { title: '用户管理', roles: ['account_admin'] },
        },
        {
          path: 'settings/video',
          name: 'video-settings',
          component: () => import('../views/VideoSettingsView.vue'),
          meta: { title: 'AI 视频设置' },
        },
        {
          path: 'settings/mapping-templates',
          name: 'mapping-templates',
          component: () => import('../views/MappingTemplatesView.vue'),
          meta: { title: '映射模板', roles: ['account_admin'] },
        },
        {
          // 旧路径兼容：/video/settings → /settings/video
          path: 'video/settings',
          redirect: '/settings/video',
        },
        {
          path: 'kb/:id',
          component: () => import('../views/KbDetailLayout.vue'),
          meta: { requiresAuth: true, roles: ['account_admin'] },
          redirect: (to) => ({ name: 'kb-docs', params: { id: to.params.id } }),
          children: [
            {
              path: 'docs',
              name: 'kb-docs',
              component: () => import('../views/KbDocsView.vue'),
              meta: { title: '文档', roles: ['account_admin'] },
            },
            {
              path: 'docs/:docId',
              name: 'kb-chunks',
              component: () => import('../views/KbChunksView.vue'),
              meta: { title: '分块', roles: ['account_admin'] },
            },
            {
              path: 'testing',
              name: 'kb-testing',
              component: () => import('../views/KbTestingView.vue'),
              meta: { title: '检索测试', roles: ['account_admin'] },
            },
            {
              path: 'config',
              name: 'kb-config',
              component: () => import('../views/KbConfigView.vue'),
              meta: { title: '配置', roles: ['account_admin'] },
            },
          ],
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach(async (to) => {
  // 用户管理属于本地账号体系，生产嵌入由主系统管角色：嵌入模式屏蔽该路由
  if (embeddedUserManagementHidden() && to.name === 'users') {
    return { name: 'kb-list' }
  }
  const auth = useAuthStore()
  if (!auth.initialized) {
    await auth.init()
  }
  if (to.meta.requiresAuth && !auth.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && auth.user) {
    return { name: 'chat' }
  }
  if (auth.user && !canAccess(auth.user.role, to.meta.roles)) {
    return { name: 'kb-list' }
  }
})

export default router
