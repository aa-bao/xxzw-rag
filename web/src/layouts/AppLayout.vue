<template>
  <div class="app-layout">
    <!-- 全局环境光 -->
    <div class="app-layout__glow app-layout__glow--1" aria-hidden="true"></div>
    <div class="app-layout__glow app-layout__glow--2" aria-hidden="true"></div>

    <aside v-if="!embedded" class="app-sidebar">
      <!-- 侧栏内部环境光（透过玻璃可见） -->
      <div class="app-sidebar__glow app-sidebar__glow--a" aria-hidden="true"></div>
      <div class="app-sidebar__glow app-sidebar__glow--b" aria-hidden="true"></div>

      <div class="app-sidebar__brand">
        <div class="app-sidebar__company" aria-label="想象之外">
          <span class="app-sidebar__company-badge">xxzw</span>
          <span class="app-sidebar__company-name">想象之外</span>
        </div>
        <span class="app-sidebar__logo">AI 工作台</span>
        <span class="app-sidebar__brand-name">AI Workbench</span>
      </div>

      <nav class="app-sidebar__nav" aria-label="主导航">
        <div v-for="group in navGroups" :key="group.id" class="app-sidebar__group">
          <button
            class="app-sidebar__group-toggle btn-press"
            :class="{ 'app-sidebar__group-toggle--open': group.open }"
            :aria-expanded="group.open"
            :aria-label="group.label"
            @click="toggleGroup(group)"
          >
            <span class="app-sidebar__group-icon">
              <el-icon aria-hidden="true"><component :is="group.icon" /></el-icon>
            </span>
            <span class="app-sidebar__group-name">{{ group.label }}</span>
            <span class="app-sidebar__group-caret" :class="{ 'is-open': group.open }" aria-hidden="true">
              <el-icon><ArrowDown /></el-icon>
            </span>
          </button>

          <div v-show="group.open" class="app-sidebar__group-items" role="list">
            <RouterLink
              v-for="item in group.items"
              :key="item.name"
              :to="{ name: item.name }"
              class="app-sidebar__nav-item btn-press"
              :class="{ 'app-sidebar__nav-item--active': isActive(item.name) }"
              role="listitem"
              v-motion="navMotion"
            >
              <span class="app-sidebar__nav-dot" aria-hidden="true"></span>
              <el-icon class="app-sidebar__nav-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
              <span class="app-sidebar__nav-label">{{ item.label }}</span>
            </RouterLink>
          </div>
        </div>
      </nav>

      <div class="app-sidebar__user" v-if="auth.user">
        <div class="app-sidebar__avatar" aria-hidden="true">
          <img v-if="!avatarBroken" class="app-sidebar__avatar-img" :src="avatarImg" alt="" @error="avatarBroken = true" />
          <span v-else class="app-sidebar__avatar-char">{{ avatarChar }}</span>
        </div>
        <div class="app-sidebar__user-info">
          <span class="app-sidebar__username">{{ auth.user.username }}</span>
          <span class="app-sidebar__role">{{ auth.isAdmin ? '管理员' : '成员' }}</span>
        </div>
        <el-button
          text
          size="small"
          class="app-sidebar__logout btn-press"
          aria-label="退出登录"
          @click="handleLogout"
        >
          <el-icon class="app-sidebar__logout-icon"><SwitchButton /></el-icon>
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
  FolderOpened,
  Setting,
  SwitchButton,
  User,
  VideoCamera,
  VideoPlay,
} from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { embeddedUserManagementHidden, isEmbedded } from '../platform'
import avatarImg from '../assets/avatar.jpg'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const embedded = isEmbedded()
/** 用户管理属于本地账号体系，生产嵌入由主系统管角色：嵌入模式隐藏入口 */
const userManagementHidden = embeddedUserManagementHidden()
/** 一级模块展开状态（本地会话内保存） */
const groupOpenMap = ref<Record<string, boolean>>({
  rag: true,
  aiVideo: false,
})

interface NavItem {
  name: string
  label: string
  icon: unknown
}

interface NavGroup {
  id: string
  label: string
  icon: unknown
  open: boolean
  items: NavItem[]
}

const navGroups = computed<NavGroup[]>(() => {
  const ragItems: NavItem[] = [
    { name: 'kb-list', label: '知识库', icon: Files },
    { name: 'chat', label: '对话', icon: ChatDotRound },
  ]
  if (auth.isAdmin) {
    ragItems.push({ name: 'settings', label: '设置', icon: Setting })
    if (!userManagementHidden) {
      ragItems.push({ name: 'users', label: '用户管理', icon: User })
    }
  }
  const videoItems: NavItem[] = [
    { name: 'video-analysis', label: '视频分析 agent', icon: VideoPlay },
    { name: 'video-library', label: '视频数据库', icon: FolderOpened },
    { name: 'video-settings', label: '设置', icon: Setting },
  ]
  return [
    {
      id: 'rag',
      label: 'RAG 知识库',
      icon: Collection,
      open: groupOpenMap.value.rag,
      items: ragItems,
    },
    {
      id: 'aiVideo',
      label: 'AI 视频分析',
      icon: VideoCamera,
      open: groupOpenMap.value.aiVideo,
      items: videoItems,
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
const GROUP_BY_ROUTE: Record<string, string> = {
  'video-analysis': 'aiVideo',
  'video-library': 'aiVideo',
  'video-settings': 'aiVideo',
}

watch(
  () => route.name,
  () => {
    const name = route.name as string
    const groupId = GROUP_BY_ROUTE[name] ?? 'rag'
    if (name && name !== 'login') {
      groupOpenMap.value[groupId] = true
    }
  },
  { immediate: true },
)

const navMotion = {
  initial: { opacity: 0, x: -8 },
  enter: { opacity: 1, x: 0, transition: { type: 'spring', stiffness: 250, damping: 25 } },
}

const avatarChar = computed(() => (auth.user?.username?.[0] ?? 'U').toUpperCase())

/** 头像图加载失败时回退为首字母占位 */
const avatarBroken = ref(false)

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

/* ── 全局环境光 ── */
.app-layout__glow {
  position: absolute;
  border-radius: var(--radius-full);
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
}

.app-layout__glow--1 {
  width: 560px;
  height: 560px;
  top: -180px;
  left: 80px;
  background: var(--glow-blue);
}

.app-layout__glow--2 {
  width: 480px;
  height: 480px;
  bottom: -160px;
  right: -40px;
  background: var(--glow-violet);
}

/* ── 侧边栏：Apple 玻璃 ── */
.app-sidebar {
  position: relative;
  z-index: 1;
  width: 236px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 28px 14px 20px;
  border-right: 1px solid var(--border-subtle);
  border-radius: 0;
  background: var(--bg-glass);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow: inset -1px 0 0 rgba(255,255,255,0.4);
  overflow: hidden;
}

/* 侧栏内部环境光：透过玻璃形成光晕 */
.app-sidebar__glow {
  position: absolute;
  border-radius: var(--radius-full);
  filter: blur(80px);
  pointer-events: none;
  z-index: -1;
}

.app-sidebar__glow--a {
  width: 300px;
  height: 300px;
  top: -80px;
  left: -60px;
  background: rgba(0, 122, 255, 0.10);
}

.app-sidebar__glow--b {
  width: 260px;
  height: 260px;
  bottom: 40px;
  right: -80px;
  background: rgba(88, 86, 214, 0.08);
}

/* ── 品牌区 ── */
.app-sidebar__brand {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 4px 10px 0;
  margin-bottom: 24px;
}

.app-sidebar__company {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.app-sidebar__company-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-indigo) 12%, transparent);
  color: var(--accent-indigo);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.app-sidebar__company-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
}

.app-sidebar__logo {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-primary);
}

.app-sidebar__brand-name {
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-tertiary);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

/* ── 导航 ── */
.app-sidebar__nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
}

.app-sidebar__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 一级模块按钮 */
.app-sidebar__group-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 11px 12px;
  border: none;
  border-radius: var(--radius-2xl);
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  cursor: pointer;
  text-align: left;
  transition: background-color 0.15s ease;
}

.app-sidebar__group-toggle:hover {
  background: color-mix(in srgb, var(--text-primary) 5%, transparent);
}

.app-sidebar__group-toggle--open {
  background: color-mix(in srgb, var(--text-primary) 3%, transparent);
}

.app-sidebar__group-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  flex-shrink: 0;
}

.app-sidebar__group-icon .el-icon {
  font-size: 16px;
}

.app-sidebar__group-name {
  flex: 1;
}

.app-sidebar__group-caret {
  display: flex;
  color: var(--text-tertiary);
  transition: transform 0.2s ease;
}

.app-sidebar__group-caret .el-icon {
  font-size: 14px;
}

.app-sidebar__group-caret.is-open {
  transform: rotate(180deg);
}

/* 二级菜单：更深缩进 + 圆角胶囊 */
.app-sidebar__group-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 4px 0 6px;
  padding-left: 40px;
}

.app-sidebar__nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: var(--radius-2xl);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  text-decoration: none;
  position: relative;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.app-sidebar__nav-item:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 5%, transparent);
}

.app-sidebar__nav-item--active {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-weight: 600;
}

/* 激活指示点：融入圆角背景，不再硬突 */
.app-sidebar__nav-dot {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full);
  background: transparent;
  flex-shrink: 0;
  transition: background-color 0.15s ease;
}

.app-sidebar__nav-item--active .app-sidebar__nav-dot {
  background: var(--accent-blue);
}

.app-sidebar__nav-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.app-sidebar__nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── 用户区 ── */
.app-sidebar__user {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 12px 6px;
  margin-top: 12px;
  border-top: 1px solid var(--border-subtle);
}

.app-sidebar__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-full);
  background: var(--bg-elevated);
  color: var(--text-primary);
  border: 1px solid var(--border-strong);
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
  overflow: hidden;
}

.app-sidebar__avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.app-sidebar__avatar-char {
  line-height: 1;
}

.app-sidebar__user-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
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
  letter-spacing: 0.02em;
}

.app-sidebar__logout {
  flex-shrink: 0;
  color: var(--text-tertiary);
}

.app-sidebar__logout:hover {
  color: var(--accent-red);
}

.app-sidebar__logout-icon {
  margin-right: 0;
  font-size: 15px;
}

/* ── 主内容区 ── */
.app-main {
  position: relative;
  z-index: 1;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 主内容区背景：细腻网格铺满全页面 */
.app-main::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background-image:
    linear-gradient(var(--chart-grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--chart-grid) 1px, transparent 1px);
  background-size: 56px 56px;
}
</style>
