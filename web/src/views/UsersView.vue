<template>
  <div class="page">
    <!-- 顶部：标题 + 新建用户 -->
    <header class="page__header">
      <h1 class="page__title">用户管理</h1>
      <div class="page__actions">
        <el-button type="primary" class="btn-press" @click="openCreateDialog">
          <el-icon class="page__new-icon"><Plus /></el-icon>
          新建用户
        </el-button>
      </div>
    </header>

    <!-- 用户表格 -->
    <div v-loading="loading" class="table-card glass-surface">
      <el-table v-if="users.length" :data="users" class="users-table" empty-text="暂无用户">
        <el-table-column label="用户名" min-width="180">
          <template #default="{ row }">
            <span class="cell-username">
              <span class="cell-username__name">{{ row.username }}</span>
              <span v-if="row.id === currentUserId" class="cell-username__me">我</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="130">
          <template #default="{ row }">
            <span class="role-pill" :class="`role-pill--${row.role}`">{{ roleLabel(row.role) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="status-pill" :class="`status-pill--${row.status}`">{{ statusLabel(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">
            <span class="cell-num cell-time">{{ formatDateTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="220">
          <template #default="{ row }">
            <div class="row-actions">
              <el-switch
                :model-value="row.status === 'active'"
                :disabled="row.id === currentUserId || togglingId === row.id"
                :loading="togglingId === row.id"
                @change="(value: string | number | boolean) => handleToggle(row, value)"
              />
              <el-button
                text
                size="small"
                class="row-actions__link btn-press"
                :disabled="row.id === currentUserId"
                @click="openEditDialog(row)"
              >
                编辑
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 空状态 -->
      <div v-else-if="!loading" class="users-empty">
        <el-icon class="users-empty__icon"><User /></el-icon>
        <p class="users-empty__title">还没有用户</p>
        <p class="users-empty__hint">点击「新建用户」添加第一个成员</p>
      </div>
    </div>

    <!-- 新建用户弹窗 -->
    <el-dialog v-model="createVisible" title="新建用户" width="420px" @closed="resetCreateForm">
      <el-form :model="createForm" label-position="top" :rules="createRules" ref="createFormRef" @submit.prevent>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option label="成员" value="user" />
            <el-option label="管理员" value="account_admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button class="btn-press" @click="createVisible = false">取消</el-button>
        <el-button type="primary" class="btn-press" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑用户弹窗 -->
    <el-dialog v-model="editVisible" title="编辑用户" width="420px" @closed="resetEditForm">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input :model-value="editForm.username" disabled />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%">
            <el-option label="成员" value="user" />
            <el-option label="管理员" value="account_admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="启用" value="active" />
            <el-option label="禁用" value="disabled" />
          </el-select>
        </el-form-item>
        <el-form-item label="重置密码（可选）">
          <el-input v-model="editForm.password" type="password" show-password placeholder="留空则不修改密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button class="btn-press" @click="editVisible = false">取消</el-button>
        <el-button type="primary" class="btn-press" :loading="updating" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Plus, User } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { createUser, listUsers, updateUser } from '../api/users'
import type { UpdateUserInput, UserInfo, UserRole, UserStatus } from '../api/users'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const currentUserId = auth.user?.id ?? -1

const users = ref<UserInfo[]>([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    users.value = await listUsers()
  } catch {
    ElMessage.error('用户列表加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

/* ── 标签展示 ── */
function roleLabel(role: UserRole): string {
  return role === 'account_admin' ? '管理员' : '成员'
}

function statusLabel(status: UserStatus): string {
  return status === 'active' ? '启用' : '已禁用'
}

/* ── 状态快捷开关 ── */
const togglingId = ref<number | null>(null)

async function handleToggle(row: UserInfo, value: string | number | boolean) {
  if (row.id === currentUserId) return
  const status: UserStatus = value ? 'active' : 'disabled'
  togglingId.value = row.id
  try {
    await updateUser(row.id, { status })
    users.value = users.value.map((u) => (u.id === row.id ? { ...u, status } : u))
    ElMessage.success(status === 'active' ? '已启用' : '已禁用')
  } catch {
    ElMessage.error('操作失败，请重试')
  } finally {
    togglingId.value = null
  }
}

/* ── 新建用户 ── */
const createVisible = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({ username: '', password: '', role: 'user' as UserRole })

const createRules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
}

function openCreateDialog() {
  createVisible.value = true
}

function resetCreateForm() {
  createForm.username = ''
  createForm.password = ''
  createForm.role = 'user'
  createFormRef.value?.clearValidate()
}

async function handleCreate() {
  const valid = await createFormRef.value?.validate().catch(() => false)
  if (!valid) return
  creating.value = true
  try {
    await createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role: createForm.role,
    })
    createVisible.value = false
    ElMessage.success('用户创建成功')
    load()
  } catch {
    ElMessage.error('创建失败，请重试')
  } finally {
    creating.value = false
  }
}

/* ── 编辑用户 ── */
const editVisible = ref(false)
const updating = ref(false)
const editForm = reactive({
  id: 0,
  username: '',
  role: 'user' as UserRole,
  status: 'active' as UserStatus,
  password: '',
})

function openEditDialog(row: UserInfo) {
  editForm.id = row.id
  editForm.username = row.username
  editForm.role = row.role
  editForm.status = row.status
  editForm.password = ''
  editVisible.value = true
}

function resetEditForm() {
  editForm.password = ''
}

async function handleEdit() {
  const payload: UpdateUserInput = { role: editForm.role, status: editForm.status }
  const password = editForm.password.trim()
  if (password) {
    if (password.length < 6) {
      ElMessage.warning('新密码至少 6 位')
      return
    }
    payload.password = password
  }
  updating.value = true
  try {
    await updateUser(editForm.id, payload)
    editVisible.value = false
    ElMessage.success('已保存')
    load()
  } catch {
    ElMessage.error('保存失败，请重试')
  } finally {
    updating.value = false
  }
}

/* ── 时间格式化 ── */
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(
    date.getMinutes(),
  )}`
}
</script>

<style scoped>
.page {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}

/* ── 顶部 ── */
.page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.page__title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text-primary);
}

.page__new-icon {
  margin-right: 6px;
}

/* ── 表格卡片（规范 7 DataTable：透明底、细分隔线、行悬浮微亮） ── */
.table-card {
  padding: 8px 16px 16px;
  border-radius: var(--radius-3xl);
}

.users-table {
  width: 100%;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: transparent;
  --el-table-header-text-color: var(--text-secondary);
  --el-table-text-color: var(--text-primary);
  --el-table-border-color: var(--border-subtle);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--text-primary) 3%, transparent);
}

.users-table :deep(.el-table__inner-wrapper::before),
.users-table :deep(.el-table::before) {
  display: none;
}

.users-table :deep(th.el-table__cell) {
  background: transparent;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-subtle);
  padding: 12px 0;
}

.users-table :deep(td.el-table__cell) {
  background: transparent;
  border-bottom: 1px solid var(--border-subtle);
  padding: 12px 0;
}

.cell-username {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.cell-username__name {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
}

.cell-username__me {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.cell-num {
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: var(--text-primary);
}

.cell-time {
  color: var(--text-secondary);
}

/* 角色/状态胶囊：状态色 10-12% 透明底 + 同色文字 */
.role-pill,
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.role-pill--account_admin {
  background: color-mix(in srgb, var(--accent-indigo) 12%, transparent);
  color: var(--accent-indigo);
}

.role-pill--user {
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
}

.status-pill--active {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.status-pill--disabled {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

/* ── 操作列 ── */
.row-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.row-actions__link {
  padding: 0;
  font-size: 12px;
  color: var(--accent-blue);
}

.row-actions__link:disabled {
  color: var(--text-tertiary);
}

/* ── 空状态 ── */
.users-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.users-empty__icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.users-empty__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.users-empty__hint {
  font-size: 13px;
  color: var(--text-secondary);
}
</style>
