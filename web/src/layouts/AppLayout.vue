<template>
  <div class="app-layout">
    <div class="app-layout__glow app-layout__glow--1" aria-hidden="true"></div>
    <div class="app-layout__glow app-layout__glow--2" aria-hidden="true"></div>

    <aside v-if="!embedded" class="app-sidebar glass-surface">
      <div class="app-sidebar__brand">
        <span class="app-sidebar__logo" aria-hidden="true">AI 工作台</span>
        <span class="app-sidebar__brand-name">rag-database</span>
      </div>

      <nav class="app-sidebar__nav" aria-label="主导航">
        <div v-for="group in navGroups" :key="group.id" class="app-sidebar__group">
          <button
            class="app-sidebar__group-toggle btn-press"
            :aria-expanded="group.open"
            :aria-label="group.label"
            @click="toggleGroup(group)"
          >
            <span class="app-sidebar__nav-bar" aria-hidden="true"></span>
            <el-icon class="app-sidebar__nav-icon" aria-hidden="true"><component :is="group.icon" /></el-icon>
            <span class="app-sidebar__group-name">{{ group.label }}</span>
            <el-icon
              class="app-sidebar__group-caret"
              :class="{ 'is-open': group.open }"
              aria-hidden="true"
            ><ArrowDown /></el-icon>
          </button>

          <div v-show="group.open" class="app-sidebar__group-items">
            <RouterLink
              v-for="item in group.items"
              :key="item.name"
              :to="{ name: item.name }"
              class="app-sidebar__nav-item btn-press"
              :class="{ 'app-sidebar__nav-item--active': isActive(item.name) }"
              v-motion="navMotion"
            >
              <el-icon class="app-sidebar__nav-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </RouterLink>
          </div>
        </div>
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
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  ChatDotRound,
  Collection,
  Files,
  Setting,
  SwitchButton,
  User,
  VideoCamera,
} from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { embeddedUserManagementHidden, isEmbedded } from '../platform'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const embedded = isEmbedded()
/** 用户管理属于本地账号体系，生产嵌入由主系统管角色：嵌入模式隐藏入口 */
const userManagementHidden = embeddedUserManagementHidden()
/** 一级模块展开状态（本地会话内保存） */
const groupOpenMap = ref<Record<string, boolean>>({
  rag: true,
  video: false,
})

interface NavGroup {
  id: string
  label: string
  icon: unknown
  open: boolean
  items: Array<{ name: string; label: string; icon: unknown }>
}

const navGroups = computed<NavGroup[]>(() => {
  const ragItems: Array<{ name: string; label: string; icon: unknown }> = [
    { name: 'kb-list', label: '知识库', icon: Files },
    { name: 'chat', label: '对话', icon: ChatDotRound },
  ]
  if (auth.isAdmin) {
    ragItems.push({ name: 'settings', label: '设置', icon: Setting })
    if (!userManagementHidden) {
      ragItems.push({ name: 'users', label: '用户管理', icon: User })
    }
  }
  return [
    {
      id: 'rag',
      label: 'RAG 知识库',
      icon: Collection,
      open: groupOpenMap.value.rag,
      items: ragItems,
    },
    {
      id: 'video',
      label: '视频解析',
      icon: VideoCamera,
      open: groupOpenMap.value.video,
      items: [{ name: 'video-analysis', label: '视频解析', icon: VideoCamera }],
    },
  ]
})

function toggleGroup(group: NavGroup): void {
  groupOpenMap.value[group.id] = !group.open
}

function isActive(name: string): boolean {
  return route.name === name
}

/** 当前路由所属的一级模块自动展开 */
watch(
  () => route.name,
  () => {
    const name = route.name as string
    if (name === 'video-analysis') groupOpenMap.value.video = true
    else if (name && name !== 'login') groupOpenMap.value.rag = true
  },
  { immediate: true },
)

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

.app-sidebar__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.app-sidebar__group-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.app-sidebar__group-toggle:hover {
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.app-sidebar__group-name {
  flex: 1;
}

.app-sidebar__group-caret {
  font-size: 14px;
  color: var(--text-tertiary);
  transition: none;
}

.app-sidebar__group-caret.is-open {
  transform: rotate(180deg);
}

.app-sidebar__group-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
  padding-left: 22px;
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
