<template>
  <aside class="sidebar">
    <div class="sidebar__brand">
      <span class="sidebar__logo">—</span>
      RAG
    </div>

    <nav class="sidebar__user" v-if="auth.user">
      <span class="sidebar__username">{{ auth.user.username }}</span>
      <el-button text size="small" @click="handleLogout">退出</el-button>
    </nav>

    <section class="sidebar__kbs">
      <div class="sidebar__section-header">
        <span>知识库</span>
        <el-button text size="small" @click="showCreateKb = true">+ 新建</el-button>
      </div>
      <ul class="sidebar__kb-list" v-if="kbs.length">
        <li
          v-for="kb in kbs"
          :key="kb.id"
          class="sidebar__kb-item"
          :class="{ 'sidebar__kb-item--active': selectedKb?.id === kb.id }"
          @click="selectKb(kb)"
        >
          {{ kb.name }}
        </li>
      </ul>
      <p class="sidebar__empty" v-else>暂无知识库</p>
    </section>
  </aside>

  <main class="workspace">
    <template v-if="!selectedKb">
      <div class="workspace__empty">
        <h2>选择一个知识库</h2>
        <p>从左侧列表选择或创建一个知识库开始</p>
      </div>
    </template>

    <template v-else>
      <!-- Upload Zone -->
      <section class="workspace__upload">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          :on-change="handleFileChange"
          accept=".txt,text/plain"
          drag
        >
          <div class="workspace__upload-inner">
            <p class="workspace__upload-label">拖拽 TXT 文件到此处，或点击上传</p>
            <p class="workspace__upload-hint">仅支持 UTF-8 编码的 .txt 文件</p>
          </div>
        </el-upload>
        <p v-if="uploading" class="workspace__upload-status">上传中&hellip;</p>
        <p v-if="uploadError" class="workspace__upload-error">{{ uploadError }}</p>
        <p v-if="uploadOk" class="workspace__upload-ok">文件已提交，正在入库&hellip;</p>
      </section>

      <!-- Chat Panel -->
      <section class="workspace__chat">
        <ChatPanel :kb-id="selectedKb.id" :key="selectedKb.id" />
      </section>
    </template>
  </main>

  <!-- Create KB Dialog -->
  <el-dialog v-model="showCreateKb" title="新建知识库" width="420px">
    <el-form :model="kbForm" label-position="top">
      <el-form-item label="名称">
        <el-input v-model="kbForm.name" placeholder="给知识库起个名字" />
      </el-form-item>
      <el-form-item label="描述（可选）">
        <el-input v-model="kbForm.desc" type="textarea" :rows="2" placeholder="用一句话描述内容" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showCreateKb = false">取消</el-button>
      <el-button type="primary" :loading="creatingKb" @click="handleCreateKb">创建</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import ChatPanel from '../components/ChatPanel.vue'

interface Kb {
  id: number
  name: string
  active_collection: string
  index_status: string
}

const auth = useAuthStore()
const router = useRouter()
const kbs = ref<Kb[]>([])
const selectedKb = ref<Kb | null>(null)
const showCreateKb = ref(false)
const creatingKb = ref(false)
const uploading = ref(false)
const uploadError = ref('')
const uploadOk = ref(false)
const kbForm = reactive({ name: '', desc: '' })

onMounted(async () => {
  const resp = await api.listKbs()
  kbs.value = resp.data
})

function selectKb(kb: Kb) {
  selectedKb.value = kb
}

async function handleCreateKb() {
  creatingKb.value = true
  try {
    await api.createKb(kbForm.name, kbForm.desc || undefined)
    showCreateKb.value = false
    kbForm.name = ''
    kbForm.desc = ''
    const resp = await api.listKbs()
    kbs.value = resp.data
  } finally {
    creatingKb.value = false
  }
}

async function handleFileChange(file: any) {
  if (!selectedKb.value || !file.raw) return
  uploading.value = true
  uploadError.value = ''
  uploadOk.value = false
  try {
    const resp = await api.uploadDoc(selectedKb.value.id, file.raw)
    if (resp.success) {
      uploadOk.value = true
    }
  } catch {
    uploadError.value = '上传失败'
  } finally {
    uploading.value = false
  }
}

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.workspace-page {
  display: flex;
  height: 100vh;
}

/* ── Sidebar ── */
.sidebar {
  width: 260px;
  flex-shrink: 0;
  background: var(--white);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 24px 20px;
}

.sidebar__brand {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 2px;
  margin-bottom: 24px;
}

.sidebar__logo {
  color: var(--olive);
  margin-right: 6px;
}

.sidebar__user {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.sidebar__username {
  font-weight: 600;
  color: var(--ink);
}

.sidebar__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--dust);
  text-transform: uppercase;
  letter-spacing: 1px;
}

.sidebar__kb-list {
  list-style: none;
  flex: 1;
  overflow-y: auto;
}

.sidebar__kb-item {
  padding: 10px 12px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--ink);
  font-size: 14px;
  transition: background 0.15s;
}

.sidebar__kb-item:hover {
  background: var(--surface);
}

.sidebar__kb-item--active {
  background: var(--surface);
  font-weight: 600;
  border-left: 3px solid var(--olive);
  padding-left: 9px;
}

.sidebar__empty {
  font-size: 13px;
  color: var(--dust);
  padding: 8px 0;
}

/* ── Workspace ── */
.workspace {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--paper);
  overflow-y: auto;
}

.workspace__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--dust);
}

.workspace__empty h2 {
  font-size: 22px;
  color: var(--ink);
  margin-bottom: 8px;
}

.workspace__upload {
  padding: 24px;
}

.workspace__upload-inner {
  padding: 32px;
  text-align: center;
}

.workspace__upload-label {
  font-size: 15px;
  color: var(--ink);
  margin-bottom: 6px;
}

.workspace__upload-hint {
  font-size: 12px;
  color: var(--dust);
}

.workspace__upload-status,
.workspace__upload-error,
.workspace__upload-ok {
  text-align: center;
  margin-top: 8px;
  font-size: 13px;
}

.workspace__upload-error {
  color: var(--el-color-danger);
}

.workspace__upload-ok {
  color: var(--olive);
}

.workspace__chat {
  flex: 1;
  padding: 24px;
  display: flex;
  flex-direction: column;
}
</style>
