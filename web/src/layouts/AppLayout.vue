<template>
  <div class="app-layout">
    <div class="app-layout__glow app-layout__glow--1" aria-hidden="true"></div>
    <div class="app-layout__glow app-layout__glow--2" aria-hidden="true"></div>

    <aside class="app-sidebar glass-surface">
      <div class="app-sidebar__brand">
        <span class="app-sidebar__logo" aria-hidden="true">想象之外</span>
        <span class="app-sidebar__brand-name">RAG 知识库</span>
      </div>

      <nav class="app-sidebar__nav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="app-sidebar__nav-item btn-press"
          :class="{ 'app-sidebar__nav-item--active': isActive(item.name) }"
          v-motion="navMotion"
        >
          <span class="app-sidebar__nav-bar" aria-hidden="true"></span>
          <el-icon class="app-sidebar__nav-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="app-sidebar__user" v-if="auth.user">
        <div class="app-sidebar__user-info">
          <span class="app-sidebar__username">{{ auth.user.username }}</span>
          <span class="app-sidebar__role" v-if="auth.isAdmin">管理员</span>
          <span class="app-sidebar__role" v-else>成员</span>
        </div>
        <el-button
          text
          size="small"
          class="app-sidebar__logout btn-press"
          aria-label="退出登录"
          @click="handleLogout"
        >
          <el-icon class="app-sidebar__logout-icon"><SwitchButton /></el-icon>
          退出
        </el-button>
      </div>
    </aside>

    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, Files, Setting, SwitchButton, User } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const navItems = computed(() => {
  const items: Array<{ name: string; label: string; icon: unknown }> = [
    { name: 'kb-list', label: '知识库', icon: Files },
    { name: 'chat', label: '对话', icon: ChatDotRound },
  ]
  if (auth.isAdmin) {
    items.push({ name: 'settings', label: '设置', icon: Setting })
    items.push({ name: 'users', label: '用户管理', icon: User })
  }
  return items
})

function isActive(name: string): boolean {
  return route.name === name
}

const navMotion = {
  initial: { opacity: 0, x: -8 },
  enter: { opacity: 1, x: 0, transition: { type: 'spring', stiffness: 250, damping: 25 } },
}

async function handleLogout() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<style scoped>
.app-layout {
  position: relative;
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--bg-base);
}

.app-layout__glow {
  position: absolute;
  border-radius: var(--radius-full);
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
}

.app-layout__glow--1 {
  width: 460px;
  height: 460px;
  top: -140px;
  left: 140px;
  background: rgba(191, 219, 254, 0.6);
}

.app-layout__glow--2 {
  width: 400px;
  height: 400px;
  bottom: -120px;
  right: -80px;
  background: rgba(233, 213, 255, 0.5);
}

.app-sidebar {
  position: relative;
  z-index: 1;
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 24px 20px;
  border-right: 1px solid var(--border-subtle);
  border-radius: 0;
}

.app-sidebar__brand {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 28px;
}

.app-sidebar__logo {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.02em;
}

.app-sidebar__brand-name {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.app-sidebar__nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.app-sidebar__nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  text-decoration: none;
}

.app-sidebar__nav-item:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.app-sidebar__nav-item--active {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-weight: 600;
}

.app-sidebar__nav-bar {
  width: 5px;
  height: 16px;
  flex-shrink: 0;
  border-radius: var(--radius-full);
  background: transparent;
}

.app-sidebar__nav-item--active .app-sidebar__nav-bar {
  background: var(--accent-blue);
}

.app-sidebar__nav-icon {
  font-size: 16px;
}

.app-sidebar__user {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle);
}

.app-sidebar__user-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.app-sidebar__username {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidebar__role {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.app-sidebar__logout {
  flex-shrink: 0;
}

.app-sidebar__logout-icon {
  margin-right: 6px;
}

.app-main {
  position: relative;
  z-index: 1;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
