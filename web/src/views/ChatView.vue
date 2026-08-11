<template>
  <div class="chat-view">
    <!-- ══ 单卡片：左会话列表 + 中消息流，分隔线隔开 ══ -->
    <div class="chat-view__card glass-surface">
      <!-- 左：知识库选择 + 会话列表 -->
      <aside class="chat-view__side" aria-label="会话管理">
        <div class="chat-view__side-head">
          <h2 class="chat-view__side-title">对话</h2>
          <el-button
            class="btn-press"
            size="small"
            aria-label="新对话"
            @click="handleNewConversation"
          >
            <el-icon class="chat-view__side-plus"><Plus /></el-icon>
            新对话
          </el-button>
        </div>

        <nav class="chat-view__convs" aria-label="历史会话">
          <ul v-if="sortedConversations.length" class="chat-view__conv-list">
            <li
              v-for="conv in sortedConversations"
              :key="conv.id"
              class="chat-view__conv"
            >
              <button
                type="button"
                class="chat-view__conv-btn btn-press"
                :class="{ 'chat-view__conv-btn--active': conv.id === activeConversationId }"
                @click="switchConversation(conv)"
                @dblclick="startRename(conv)"
              >
                <span v-if="renamingId === conv.id" class="chat-view__conv-rename">
                  <input
                    ref="renameInputEl"
                    v-model="renameDraft"
                    class="chat-view__conv-rename-input"
                    type="text"
                    :maxlength="100"
                    :placeholder="conv.title || '未命名对话'"
                    @click.stop
                    @keydown.enter.prevent="commitRename(conv)"
                    @keydown.esc.prevent="cancelRename"
                    @blur="commitRename(conv)"
                  />
                </span>
                <span v-else class="chat-view__conv-title" :title="conv.title || ''">
                  {{ conv.title || '未命名对话' }}
                </span>
                <span class="chat-view__conv-kb">{{ convKbLabel(conv) }}</span>
              </button>
              <el-button
                text
                class="chat-view__conv-del btn-press"
                aria-label="删除会话"
                @click.stop="handleDeleteConversation(conv)"
              >
                <el-icon><Delete /></el-icon>
              </el-button>
            </li>
          </ul>
          <p v-else class="chat-view__convs-empty">暂无会话，点击「新对话」创建</p>
        </nav>
      </aside>

      <!-- 中：消息流 + 底部输入 -->
      <section class="chat-view__main" aria-label="消息列表">
        <!-- 当前会话知识库栏：新对话为可编辑多选，已有会话为只读标签 -->
        <div class="chat-view__kb-bar">
          <template v-if="activeConversationId === null">
            <span class="chat-view__kb-bar-title">本次对话使用</span>
            <template v-if="kbs.length">
              <button
                v-for="kb in kbs"
                :key="`kb-pick-${kb.id}`"
                type="button"
                class="chat-view__kb-pick btn-press"
                :class="{ 'chat-view__kb-pick--on': newConversationKbIds.includes(kb.id) }"
                :aria-pressed="newConversationKbIds.includes(kb.id)"
                @click="toggleNewConversationKb(kb.id)"
              >
                {{ kb.name }}
              </button>
            </template>
            <span v-else class="chat-view__kb-bar-hint">知识库加载失败，请刷新页面</span>
          </template>
          <template v-else-if="activeConversationKbIds.length">
            <span class="chat-view__kb-bar-title">会话知识库</span>
            <div class="chat-view__active-kbs" readonly>
              <span
                v-for="(name, i) in activeConversationKbNames"
                :key="`active-kb-${activeConversationId}-${i}`"
                class="chat-view__kb-tag"
              >
                {{ name }}
              </span>
            </div>
          </template>
          <template v-else>
            <span class="chat-view__kb-bar-title">会话知识库</span>
            <span class="chat-view__kb-bar-hint">暂无知识库</span>
          </template>
        </div>

        <div ref="messagesEl" class="chat-view__messages">
          <div v-if="!messages.length && !streaming" class="chat-view__empty" v-motion="emptyMotion">
            <el-icon class="chat-view__empty-icon"><ChatDotRound /></el-icon>
            <h3>开始对话</h3>
            <p v-if="activeConversationId === null">{{ isNewConversationKbReady ? '输入问题，助手将检索所选知识库内容作答' : '请选择本次对话使用的知识库' }}</p>
            <p v-else>输入问题，助手将检索会话知识库内容作答</p>
          </div>

          <div
            v-for="(msg, i) in messages"
            :key="`chat-message-${activeConversationId ?? 'new'}-${i}`"
            class="chat-view__msg"
            :class="msg.role === 'user' ? 'chat-view__msg--user' : 'chat-view__msg--assistant'"
            v-motion="msgMotion"
          >
            <!-- 助手头像：优先当前知识库封面，无封面用渐变缩略图 -->
            <div v-if="msg.role === 'assistant'" class="chat-view__avatar" :style="avatarStyle" aria-hidden="true">
              <img v-if="hasCover" :src="activeCoverUrl" class="chat-view__avatar-img" alt="" />
              <el-icon v-else class="chat-view__avatar-icon"><Document /></el-icon>
            </div>
            <span class="chat-view__role">{{ msg.role === 'user' ? '你' : '助手' }}</span>
            <div class="chat-view__bubble" :class="bubbleClass(msg)">
              <div
                v-if="msg.role === 'user'"
                class="chat-view__text chat-view__text--plain"
              >{{ msg.content }}</div>
              <div
                v-else
                class="chat-view__text chat-view__text--md"
                v-html="renderChatMarkdown(msg.content, msg.references.length)"
                @click="handleCitationClick($event, msg)"
              ></div>

              <!-- 文献引用角标 [1] [2] …（仅助手消息） -->
              <div
                v-if="msg.role === 'assistant' && msg.references.length"
                class="chat-view__cites"
              >
                <button
                  v-for="(ref, j) in msg.references"
                  :key="`${i}-${j}`"
                  type="button"
                  class="chat-view__cite btn-press"
                  :title="ref.kb_name ? `${ref.kb_name} · ${ref.title || '未知来源'}` : ref.title || '未知来源'"
                  @click.stop="openRefs(msg, j)"
                >
                  {{ j + 1 }}
                </button>
              </div>
            </div>
          </div>

          <div v-if="streaming" class="chat-view__msg chat-view__msg--assistant">
            <div class="chat-view__avatar" :style="avatarStyle" aria-hidden="true">
              <img v-if="hasCover" :src="activeCoverUrl" class="chat-view__avatar-img" alt="" />
              <el-icon v-else class="chat-view__avatar-icon"><Document /></el-icon>
            </div>
            <span class="chat-view__role">助手</span>
            <div v-if="streamingContent" class="chat-view__bubble chat-view__bubble--assistant" aria-live="polite">
              <div
                class="chat-view__text chat-view__text--md"
                v-html="renderChatMarkdown(streamingContent, 0)"
              ></div>
            </div>
            <div v-else class="chat-view__thinking glass-surface" aria-live="polite">
              <span class="chat-view__thinking-label">正在思考</span>
              <span class="chat-view__thinking-dots" aria-hidden="true">
                <i v-for="n in 3" :key="`thinking-dot-${n}`" class="chat-view__thinking-dot" v-motion="dotMotion"></i>
              </span>
            </div>
          </div>
        </div>

        <!-- Gemini 风格输入：圆角大框 + 内嵌圆形发送按钮 -->
        <form class="chat-view__composer" @submit.prevent="handleSend">
          <el-input
            v-model="question"
            type="textarea"
            :rows="1"
            :autosize="{ minRows: 1, maxRows: 6 }"
            resize="none"
            placeholder="输入你的问题…"
            aria-label="输入问题"
            :disabled="loading"
            @keydown.enter.exact.prevent="handleSend"
          />
          <button
            type="submit"
            class="chat-view__send-btn btn-press"
            :disabled="!canSend"
            :aria-label="loading ? '回答中' : '发送'"
          >
            <el-icon v-if="!loading" class="chat-view__send-icon"><Promotion /></el-icon>
            <span v-else class="chat-view__send-dots" aria-hidden="true">
              <i v-for="n in 3" :key="`send-dot-${n}`" class="chat-view__send-dot" v-motion="dotMotion"></i>
            </span>
          </button>
        </form>
      </section>
    </div>

    <!-- ══ 引用抽屉：默认隐藏，点击 [n] 弹出；点击遮罩或卡片区域收起 ══ -->
    <transition name="chat-view__fade">
      <div
        v-if="drawerOpen"
        class="chat-view__mask"
        aria-hidden="true"
        @click="drawerOpen = false"
      ></div>
    </transition>
    <transition name="chat-view__drawer">
      <aside
        v-if="drawerOpen"
        class="chat-view__drawer glass-strong"
        aria-label="参考来源"
      >
        <div class="chat-view__drawer-head">
          <h3 class="chat-view__drawer-title">参考来源</h3>
          <button
            type="button"
            class="chat-view__drawer-close btn-press"
            aria-label="关闭参考来源"
            @click="drawerOpen = false"
          >
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <div v-if="activeRefs.length" class="chat-view__drawer-refs">
          <div
            v-for="(ref, j) in activeRefs"
            :key="`${activeRefMessageKey}-${j}`"
            class="chat-view__drawer-ref"
          >
            <div class="chat-view__drawer-ref-head">
              <span class="chat-view__drawer-ref-idx">{{ activeRefStart + j + 1 }}</span>
              <el-icon class="chat-view__drawer-ref-icon"><Document /></el-icon>
              <span class="chat-view__drawer-ref-title">{{ ref.title || '未知来源' }}</span>
              <span v-if="ref.page" class="chat-view__drawer-ref-tag">第 {{ ref.page }} 页</span>
              <span v-if="ref.kb_name" class="chat-view__drawer-ref-kb">{{ ref.kb_name }}</span>
            </div>
            <p v-if="ref.score != null" class="chat-view__drawer-ref-score">{{ scoreText(ref.score) }}</p>
            <p v-if="ref.snippet" class="chat-view__drawer-ref-snippet">{{ ref.snippet }}</p>
          </div>
        </div>
        <p v-else class="chat-view__drawer-empty">暂无引用</p>
      </aside>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Close, Delete, Document, Plus, Promotion } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createConversation,
  deleteConversation,
  getMessages,
  listConversations,
  queryRaw,
  renameConversation,
} from '../api/chat'
import type { ChatMessageInfo, ConversationInfo, ReferenceInfo } from '../api/chat'
import { listKbs } from '../api/kb'
import type { KbInfo } from '../api/kb'
import { streamSse } from '../api/sse'
import { citationIndexFromClick, renderChatMarkdown } from '../utils/chatCitations'

const route = useRoute()

/* ── 状态 ── */
const kbs = ref<KbInfo[]>([])
/** 已有会话绑定的只读知识库集合（切换会话时恢复，仅展示） */
const activeConversationKbIds = ref<number[]>([])
/** 新对话创建前用户选择的知识库集合；不参与历史会话列表过滤 */
const newConversationKbIds = ref<number[]>([])
const conversations = ref<ConversationInfo[]>([])
const activeConversationId = ref<string | null>(null)
const messages = ref<ChatMessageInfo[]>([])
const question = ref('')
const loading = ref(false)
const streaming = ref(false)
const streamingContent = ref('')
const streamingRefs = ref<ReferenceInfo[]>([])
const messagesEl = ref<HTMLElement | null>(null)

/* ── 会话重命名状态：双击标题进入编辑 ── */
const renamingId = ref<string | null>(null)
const renameDraft = ref('')
const renameInputEl = ref<HTMLInputElement | null>(null)

/** 加载历史消息的竞态令牌：快速切换会话/KB 时丢弃过期响应 */
let loadToken = 0

/* ── 引用抽屉状态：点击 [n] 打开，展示对应消息的全部引用 ── */
const drawerOpen = ref(false)
const activeRefs = ref<ReferenceInfo[]>([])
const activeRefStart = ref(0)
const activeRefMessageKey = ref('')

function openRefs(msg: ChatMessageInfo, index: number) {
  activeRefs.value = msg.references.slice(index)
  activeRefStart.value = index
  activeRefMessageKey.value = `${msg.role}-${msg.content.slice(0, 20)}-${index}`
  drawerOpen.value = true
}

function handleCitationClick(event: MouseEvent, msg: ChatMessageInfo) {
  const index = citationIndexFromClick(event, msg.references.length)
  if (index !== null) openRefs(msg, index)
}

/* ── 派生状态 ── */
/** 全部历史会话：知识库选择不参与过滤，按最后更新时间倒序展示；
 *  前端本地新建的会话 updated_at 尚为 null，视为最新排在最前 */
const sortedConversations = computed(() =>
  [...conversations.value].sort((a, b) => {
    if (a.updated_at === null && b.updated_at === null) return 0
    if (a.updated_at === null) return -1
    if (b.updated_at === null) return 1
    return b.updated_at.localeCompare(a.updated_at)
  }),
)

/** 新对话知识库选择是否满足发送条件（至少一个） */
const isNewConversationKbReady = computed(() => newConversationKbIds.value.length > 0)

/** 发送按钮可用性：有内容、非加载中，且新对话必须已选知识库 */
const canSend = computed(
  () =>
    question.value.trim().length > 0 &&
    !loading.value &&
    (activeConversationId.value !== null || isNewConversationKbReady.value),
)

/** 已有会话绑定的知识库名（按 kb_ids 顺序） */
const activeConversationKbNames = computed(() =>
  activeConversationKbIds.value.map((id) => kbNameOf(id)),
)

/** 会话副标题：绑定的知识库名列表；超过 2 个显示前 2 个 + 总数 */
function convKbLabel(conv: ConversationInfo): string {
  const names = conv.kb_names ?? (conv.kb_id != null ? [kbNameOf(conv.kb_id)] : [])
  if (names.length === 0) return ''
  if (names.length <= 2) return names.join(' / ')
  return `${names.slice(0, 2).join(' / ')} 等${names.length}个`
}

/* ── 助手头像：优先当前会话知识库封面图，无封面用渐变缩略图 ── */
const THUMB_GRADIENTS = [
  'linear-gradient(135deg, rgba(0, 122, 255, 0.25), rgba(88, 86, 214, 0.15))',
  'linear-gradient(135deg, rgba(88, 86, 214, 0.25), rgba(0, 122, 255, 0.15))',
  'linear-gradient(135deg, rgba(255, 149, 0, 0.22), rgba(255, 59, 48, 0.12))',
  'linear-gradient(135deg, rgba(52, 199, 89, 0.22), rgba(0, 122, 255, 0.12))',
]

/** 当前会话第一个知识库的封面 URL（无则空串） */
const activeCoverUrl = computed(() => {
  const kb = kbs.value.find((k) => activeKbIdsForAvatar.value.includes(k.id))
  return kb?.cover_url ?? ''
})

const avatarStyle = computed(() => {
  // 头像取当前会话（已有会话或新对话选择）的第一个知识库封面
  const kb = kbs.value.find((k) => activeKbIdsForAvatar.value.includes(k.id))
  if (kb?.cover_url) {
    return { overflow: 'hidden' }
  }
  return { background: THUMB_GRADIENTS[Math.abs((kb?.id ?? 0)) % THUMB_GRADIENTS.length] }
})

/** 当前 KB 是否有封面：有封面时头像不叠加文档图标（取当前会话第一个知识库） */
const hasCover = computed(() => {
  const kb = kbs.value.find((k) => activeKbIdsForAvatar.value.includes(k.id))
  return !!kb?.cover_url
})

/** 头像取用的知识库集合：已有会话用其绑定集合，新对话用已选集合 */
const activeKbIdsForAvatar = computed(() =>
  activeConversationId.value !== null ? activeConversationKbIds.value : newConversationKbIds.value,
)

/* ── 动效（规范 4.4：内容进入弹簧 y 8px → 0 + 淡入） ── */
const msgMotion = {
  initial: { y: 8, opacity: 0 },
  enter: {
    y: 0,
    opacity: 1,
    transition: { type: 'spring', stiffness: 250, damping: 25 },
  },
}

const emptyMotion = {
  initial: { y: 8, opacity: 0 },
  enter: {
    y: 0,
    opacity: 1,
    transition: { type: 'spring', stiffness: 250, damping: 25 },
  },
}

// 「正在思考」三连点：v-motion 循环 opacity（规范：动效一律弹簧，禁止 CSS keyframes）
const dotMotion = {
  initial: { opacity: 0.2 },
  enter: {
    opacity: 1,
    transition: {
      type: 'tween',
      duration: 0.45,
      repeat: Infinity,
      repeatType: 'reverse',
      ease: 'easeInOut',
    },
  },
}

/* ── 工具函数 ── */
function kbNameOf(kbId: number): string {
  return kbs.value.find((k) => k.id === kbId)?.name ?? `#${kbId}`
}

function scoreText(score: number): string {
  return `相似度 ${(score * 100).toFixed(1)}%`
}

function bubbleClass(msg: ChatMessageInfo): string {
  const base =
    msg.role === 'user' ? 'chat-view__bubble--user' : 'chat-view__bubble--assistant'
  return msg.status === 'failed' ? `${base} chat-view__bubble--failed` : base
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}

function resetStream() {
  streaming.value = false
  streamingContent.value = ''
  streamingRefs.value = []
}

/* ── 数据加载 ── */
async function loadKbs() {
  try {
    kbs.value = await listKbs()
  } catch {
    kbs.value = []
    ElMessage.error('知识库列表加载失败')
  }
}

async function loadConversations() {
  try {
    conversations.value = await listConversations()
  } catch {
    conversations.value = []
    ElMessage.error('会话列表加载失败')
  }
}

/* ── 会话操作 ── */
async function createNewConversation(kbIds: number[]): Promise<string | null> {
  const token = ++loadToken
  try {
    const conv = await createConversation(kbIds)
    // 以服务端确认绑定的知识库为准（可能对 kb_ids 做规范化），参数仅作兜底
    const boundKbIds =
      Array.isArray(conv.kb_ids) && conv.kb_ids.length > 0 ? conv.kb_ids : kbIds
    conversations.value = [
      {
        id: conv.id,
        kb_ids: boundKbIds,
        kb_names: boundKbIds.map((id) => kbNameOf(id)),
        kb_id: boundKbIds[0] ?? null,
        kb_name: boundKbIds[0] != null ? kbNameOf(boundKbIds[0]) : undefined,
        title: null,
        created_at: null,
        updated_at: null,
      },
      ...conversations.value,
    ]
    if (token !== loadToken) return null
    activeConversationId.value = conv.id
    activeConversationKbIds.value = [...boundKbIds]
    messages.value = []
    resetStream()
    await scrollToBottom()
    return conv.id
  } catch {
    if (token === loadToken) ElMessage.error('创建会话失败')
    return null
  }
}

async function switchConversation(conv: ConversationInfo) {
  if (conv.id === activeConversationId.value && messages.value.length) return
  const token = ++loadToken
  activeConversationId.value = conv.id
  activeConversationKbIds.value = [...(conv.kb_ids ?? (conv.kb_id != null ? [conv.kb_id] : []))]
  messages.value = []
  resetStream()
  try {
    const history = await getMessages(conv.id)
    if (token !== loadToken) return
    messages.value = history
    await scrollToBottom()
  } catch {
    if (token === loadToken) ElMessage.error('历史消息加载失败')
  }
}

async function handleNewConversation() {
  if (loading.value) return
  loadToken++ // 作废 in-flight 的历史消息请求
  activeConversationId.value = null
  activeConversationKbIds.value = []
  newConversationKbIds.value = []
  messages.value = []
  resetStream()
  question.value = ''
}

function toggleNewConversationKb(kbId: number) {
  if (loading.value) return
  newConversationKbIds.value = newConversationKbIds.value.includes(kbId)
    ? newConversationKbIds.value.filter((id) => id !== kbId)
    : [...newConversationKbIds.value, kbId]
}

async function handleDeleteConversation(conv: ConversationInfo) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${conv.title || '未命名对话'}」吗？`,
      '删除会话',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await deleteConversation(conv.id)
  } catch {
    ElMessage.error('删除会话失败')
    return
  }
  conversations.value = conversations.value.filter((c) => c.id !== conv.id)
  if (conv.id === activeConversationId.value) {
    loadToken++
    activeConversationId.value = null
    activeConversationKbIds.value = []
    messages.value = []
    resetStream()
  }
  ElMessage.success('已删除')
}

function startRename(conv: ConversationInfo) {
  renamingId.value = conv.id
  renameDraft.value = conv.title ?? ''
  nextTick(() => renameInputEl.value?.select())
}

function cancelRename() {
  renamingId.value = null
  renameDraft.value = ''
}

async function commitRename(conv: ConversationInfo) {
  if (renamingId.value !== conv.id) return
  const title = renameDraft.value.trim()
  renamingId.value = null
  if (!title || title === (conv.title ?? '')) return
  try {
    await renameConversation(conv.id, title)
    conv.title = title
  } catch {
    ElMessage.error('重命名会话失败')
  }
}

/* ── 发送与流式 ── */
function pushAssistantError(cid: string | null, text: string) {
  if (!cid || activeConversationId.value !== cid) return
  messages.value.push({
    role: 'assistant',
    content: text,
    status: 'failed',
    created_at: null,
    references: [],
  })
}

async function handleSend() {
  const q = question.value.trim()
  if (!q || loading.value) return
  if (activeConversationId.value === null && newConversationKbIds.value.length === 0) {
    ElMessage.warning('请先选择知识库')
    return
  }

  loading.value = true
  try {
    if (!activeConversationId.value) {
      const id = await createNewConversation(newConversationKbIds.value)
      if (id === null) return
    }
    const cid = activeConversationId.value
    if (cid === null) return
    messages.value.push({
      role: 'user',
      content: q,
      status: 'completed',
      created_at: null,
      references: [],
    })
    question.value = ''
    // 立即滚动到最新一条（用户消息），并让助手头像 +「正在思考」即刻出现，
    // 不等 HTTP 建连/模型响应，避免发送后空白等待
    await scrollToBottom()
    streaming.value = true
    streamingContent.value = ''
    streamingRefs.value = []

    const resp = await queryRaw(cid, q)
    if (!resp.ok) {
      streaming.value = false
      pushAssistantError(cid, '查询失败，请稍后重试')
      return
    }
    await streamAnswer(resp, cid)
  } catch {
    streaming.value = false
    pushAssistantError(activeConversationId.value, '查询失败，请稍后重试')
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

async function streamAnswer(resp: Response, cid: string) {
  streaming.value = true
  streamingContent.value = ''
  streamingRefs.value = []
  // 流式期间切换/删除会话后，不再把结果写入已被清空的消息流
  const sessionLive = () => activeConversationId.value === cid

  try {
    for await (const ev of streamSse(resp)) {
      if (ev.event === 'chunk') {
        const { content } = JSON.parse(ev.data) as { content?: string }
        streamingContent.value += content || ''
        await scrollToBottom()
      } else if (ev.event === 'references') {
        const { items } = JSON.parse(ev.data) as { items?: ReferenceInfo[] }
        streamingRefs.value = items || []
      } else if (ev.event === 'done') {
        streaming.value = false
        const finalContent = streamingContent.value || 'OK'
        if (sessionLive()) {
          messages.value.push({
            role: 'assistant',
            content: finalContent,
            status: 'completed',
            created_at: null,
            references: streamingRefs.value,
          })
        }
        streamingContent.value = ''
        streamingRefs.value = []
        return
      } else if (ev.event === 'error') {
        streaming.value = false
        const errContent = streamingContent.value || '回答出错，请稍后重试'
        if (sessionLive()) {
          messages.value.push({
            role: 'assistant',
            content: errContent,
            status: 'failed',
            created_at: null,
            references: streamingRefs.value,
          })
        }
        streamingContent.value = ''
        streamingRefs.value = []
        return
      }
    }
  } catch {
    // 流读取中断：保留已收到的内容
  }

  // 流意外结束（无 done/error 事件）：追加已收到的内容，保持会话完整
  streaming.value = false
  if (sessionLive() && streamingContent.value) {
    messages.value.push({
      role: 'assistant',
      content: streamingContent.value,
      status: 'completed',
      created_at: null,
      references: streamingRefs.value,
    })
  }
  streamingContent.value = ''
  streamingRefs.value = []
}

/* ── query 参数：员工从知识库列表点卡跳入（?kb={id}），直接进入绑定该库的新对话 ── */
async function applyKbQuery() {
  const raw = route.query.kb
  const id = typeof raw === 'string' ? Number(raw) : NaN
  if (!Number.isInteger(id) || id <= 0) return
  if (!kbs.value.some((k) => k.id === id)) {
    ElMessage.warning('知识库不存在或无权访问')
    return
  }
  loadToken++
  activeConversationId.value = null
  activeConversationKbIds.value = []
  newConversationKbIds.value = [id]
  messages.value = []
  resetStream()
}

onMounted(async () => {
  await loadKbs()
  await loadConversations()
  await applyKbQuery()
})
</script>

<style scoped>
/* ═══════════ 单卡片布局：左会话列表 + 中消息流，内部分隔线 ═══════════ */
.chat-view {
  position: relative;
  height: 100%;
  min-height: 0;
  padding: 24px;
}

.chat-view__card {
  display: flex;
  height: 100%;
  min-height: 0;
  border-radius: var(--radius-3xl);
  overflow: hidden;
}

/* ═══════════ 左栏 ═══════════ */
.chat-view__side {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 20px 16px;
  border-right: 1px solid var(--border-subtle);
}

.chat-view__side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;
  padding: 0 4px;
}

.chat-view__side-title {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--text-primary);
}

.chat-view__side-plus {
  margin-right: 6px;
  font-size: 16px;
}

.chat-view__convs {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}

.chat-view__conv-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chat-view__conv {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: var(--radius-3xl);
}

.chat-view__conv:hover,
.chat-view__conv:focus-within {
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.chat-view__conv-btn {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 9px 12px;
  border: none;
  border-radius: var(--radius-3xl);
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  user-select: none;
}

.chat-view__conv-btn--active {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.chat-view__conv-title {
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.chat-view__conv-btn--active .chat-view__conv-title {
  font-weight: 600;
}

.chat-view__conv-rename {
  width: 100%;
}

.chat-view__conv-rename-input {
  width: 100%;
  padding: 2px 6px;
  border: 1px solid var(--accent-blue);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  outline: none;
}

.chat-view__conv-kb {
  max-width: 100%;
  font-size: 11px;
  color: var(--text-tertiary);
  letter-spacing: 0.03em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-view__conv-btn--active .chat-view__conv-kb {
  color: var(--accent-blue);
}

.chat-view__conv-del {
  flex-shrink: 0;
  opacity: 0;
  padding: 4px;
  color: var(--text-secondary);
}

.chat-view__conv:hover .chat-view__conv-del,
.chat-view__conv:focus-within .chat-view__conv-del {
  opacity: 1;
}

.chat-view__conv-del:hover {
  color: var(--accent-red);
}

.chat-view__convs-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 4px;
  line-height: 1.5;
}

/* ═══════════ 当前会话知识库栏（新对话多选 / 已有会话只读标签） ═══════════ */
.chat-view__kb-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 28px;
  margin-top: 20px;
  min-height: 26px;
}

.chat-view__kb-bar-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.03em;
  margin-right: 2px;
}

.chat-view__kb-pick {
  padding: 4px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-full);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.chat-view__kb-pick--on {
  border-color: color-mix(in srgb, var(--accent-blue) 55%, transparent);
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-weight: 600;
}

.chat-view__kb-bar-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

.chat-view__active-kbs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chat-view__kb-tag {
  padding: 4px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-full);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
}

/* ═══════════ 中栏 ═══════════ */
.chat-view__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.chat-view__messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 28px;
}

.chat-view__empty {
  margin: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  text-align: center;
}

.chat-view__empty-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.chat-view__empty h3 {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.chat-view__empty p {
  font-size: 13px;
}

.chat-view__msg {
  display: flex;
  flex-direction: column;
  max-width: 100%;
}

.chat-view__msg--user {
  align-items: flex-end;
}

/* 助手消息：头像 + 角色标签 + 气泡 横向排布 */
.chat-view__msg--assistant {
  flex-direction: row;
  align-items: flex-start;
  gap: 10px;
}

.chat-view__msg--assistant .chat-view__role {
  margin-bottom: 0;
  margin-top: 2px;
}

.chat-view__msg--assistant .chat-view__bubble {
  margin-top: 0;
}

/* ── 助手头像：KB 渐变缩略图 ── */
.chat-view__avatar {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.95);
  box-shadow: var(--shadow-card);
  margin-top: 1px;
}

.chat-view__avatar-icon {
  font-size: 15px;
}

.chat-view__avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
  border-radius: var(--radius-lg);
}

.chat-view__role {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  line-height: 1.4;
  margin-bottom: 6px;
}

.chat-view__bubble {
  max-width: 78%;
  padding: 12px 16px;
  border-radius: var(--radius-3xl);
}

.chat-view__bubble--user {
  background: color-mix(in srgb, var(--accent-indigo) 12%, transparent);
  border-bottom-right-radius: var(--radius-lg);
}

/* 助手气泡用强玻璃（0.85），避免玻璃面板上再叠半透明层导致对比度塌陷（规范 2.1） */
.chat-view__bubble--assistant {
  background: var(--bg-glass-strong);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border: 1px solid var(--border-subtle);
  box-shadow: var(--shadow-card);
  border-bottom-left-radius: var(--radius-lg);
}

.chat-view__bubble--failed {
  border-color: color-mix(in srgb, var(--accent-red) 35%, transparent);
}

.chat-view__text {
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.chat-view__text--plain {
  white-space: pre-wrap;
}

.chat-view__text--md {
  white-space: normal;
}

/* ── 文献引用角标 [1] [2] … ── */
.chat-view__cites {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.chat-view__cite {
  min-width: 26px;
  height: 22px;
  padding: 0 6px;
  border: 1px solid color-mix(in srgb, var(--accent-blue) 35%, transparent);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
  color: var(--accent-blue);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  cursor: pointer;
  transition: none;
}

.chat-view__cite:hover {
  background: color-mix(in srgb, var(--accent-blue) 18%, transparent);
}

.chat-view__text--md :deep(.chat-view__inline-cite) {
  display: inline;
  padding: 1px 5px;
  border: 1px solid color-mix(in srgb, var(--accent-blue) 35%, transparent);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
  color: var(--accent-blue);
  font: inherit;
  font-size: 0.86em;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
}

.chat-view__text--md :deep(.chat-view__inline-cite:hover) {
  background: color-mix(in srgb, var(--accent-blue) 18%, transparent);
}

.chat-view__text--md :deep(.chat-view__inline-cite:focus-visible) {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

/* ── Markdown 内容（v-html 由 markdown-it 渲染，html: false 已转义） ── */
.chat-view__text--md :deep(p) {
  margin: 0 0 8px;
}

.chat-view__text--md :deep(p:last-child) {
  margin-bottom: 0;
}

.chat-view__text--md :deep(h1),
.chat-view__text--md :deep(h2),
.chat-view__text--md :deep(h3),
.chat-view__text--md :deep(h4) {
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.3;
  margin: 14px 0 6px;
}

.chat-view__text--md :deep(h1) {
  font-size: 18px;
}

.chat-view__text--md :deep(h2) {
  font-size: 17px;
}

.chat-view__text--md :deep(h3) {
  font-size: 16px;
}

.chat-view__text--md :deep(h4) {
  font-size: 15px;
}

.chat-view__text--md :deep(ul),
.chat-view__text--md :deep(ol) {
  margin: 0 0 8px;
  padding-left: 22px;
}

.chat-view__text--md :deep(li) {
  margin: 2px 0;
}

.chat-view__text--md :deep(strong) {
  font-weight: 700;
}

.chat-view__text--md :deep(a) {
  color: var(--accent-blue);
  text-decoration: none;
}

.chat-view__text--md :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-subtle);
  margin: 12px 0;
}

.chat-view__text--md :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.9em;
  background: color-mix(in srgb, var(--text-primary) 6%, transparent);
  padding: 1px 5px;
  border-radius: var(--radius-lg);
}

.chat-view__text--md :deep(pre) {
  background: color-mix(in srgb, var(--text-primary) 6%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
  overflow-x: auto;
  margin: 0 0 10px;
}

.chat-view__text--md :deep(pre code) {
  background: none;
  padding: 0;
  font-size: 12px;
  line-height: 1.6;
}

.chat-view__text--md :deep(blockquote) {
  border-left: 3px solid var(--border-strong);
  padding-left: 12px;
  margin: 0 0 10px;
  color: var(--text-secondary);
}

.chat-view__text--md :deep(table) {
  border-collapse: collapse;
  margin: 0 0 10px;
  max-width: 100%;
}

.chat-view__text--md :deep(th),
.chat-view__text--md :deep(td) {
  border: 1px solid var(--border-subtle);
  padding: 6px 10px;
  font-size: 13px;
  text-align: left;
}

.chat-view__text--md :deep(th) {
  background: var(--bg-subtle);
  font-weight: 600;
}

/* 「正在思考」指示：三连跳动点 + 玻璃条 */
.chat-view__thinking {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-3xl);
  border-bottom-left-radius: var(--radius-lg);
}

.chat-view__thinking-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.chat-view__thinking-dots {
  display: inline-flex;
  gap: 5px;
}

.chat-view__thinking-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--accent-indigo);
}

/* ═══════════ Gemini 风格输入区 ═══════════ */
.chat-view__composer {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 28px 24px;
  padding: 12px 12px 12px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  border-radius: 28px;
  box-shadow: var(--shadow-card);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.chat-view__composer:focus-within {
  border-color: color-mix(in srgb, var(--accent-blue) 60%, transparent);
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--accent-blue) 12%, transparent),
    var(--shadow-card);
}

.chat-view__composer :deep(.el-textarea) {
  flex: 1;
}

.chat-view__composer :deep(.el-textarea__inner) {
  background: transparent;
  border: none;
  box-shadow: none;
  padding: 0 4px;
  font-size: 14px;
  line-height: 20px;
  font-family: inherit;
  color: var(--text-primary);
}

.chat-view__composer :deep(.el-textarea__inner:focus) {
  border: none;
  box-shadow: none;
}

.chat-view__composer :deep(.el-textarea__inner::placeholder) {
  color: var(--text-tertiary);
  line-height: 20px;
}

/* 内嵌圆形发送按钮 */
.chat-view__send-btn {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-full);
  background: var(--accent-blue);
  color: #fff;
  cursor: pointer;
}

.chat-view__send-btn:hover:not(:disabled) {
  background: var(--el-color-primary-hover);
}

.chat-view__send-btn:disabled {
  background: color-mix(in srgb, var(--text-tertiary) 25%, transparent);
  cursor: not-allowed;
}

.chat-view__send-icon {
  font-size: 17px;
}

/* 发送中：三点呼吸（v-motion 驱动，禁 CSS keyframes） */
.chat-view__send-dots {
  display: inline-flex;
  gap: 3px;
}

.chat-view__send-dot {
  width: 4px;
  height: 4px;
  border-radius: var(--radius-full);
  background: #fff;
}

/* ═══════════ 引用抽屉 ═══════════ */
/* 遮罩：覆盖主卡片区，点击收起抽屉（不盖住抽屉本身） */
.chat-view__mask {
  position: absolute;
  inset: 24px;
  z-index: 15;
  border-radius: var(--radius-3xl);
  background: rgba(0, 0, 0, 0.08);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}

.chat-view__fade-enter-active,
.chat-view__fade-leave-active {
  transition: opacity 0.2s ease;
}

.chat-view__fade-enter-from,
.chat-view__fade-leave-to {
  opacity: 0;
}

.chat-view__drawer {
  position: absolute;
  top: 24px;
  right: 24px;
  bottom: 24px;
  width: 340px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-3xl);
  padding: 20px;
  overflow: hidden;
}

.chat-view__drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.chat-view__drawer-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.chat-view__drawer-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 15px;
}

.chat-view__drawer-close:hover {
  background: color-mix(in srgb, var(--text-primary) 6%, transparent);
  color: var(--text-primary);
}

.chat-view__drawer-refs {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-view__drawer-ref {
  padding: 12px 14px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-left: 3px solid var(--accent-indigo);
  border-radius: var(--radius-lg);
}

.chat-view__drawer-ref-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.chat-view__drawer-ref-idx {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--accent-indigo);
  flex-shrink: 0;
}

.chat-view__drawer-ref-icon {
  font-size: 13px;
  color: var(--accent-indigo);
  flex-shrink: 0;
}

.chat-view__drawer-ref-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-view__drawer-ref-tag {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
  background: var(--bg-base);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

/* 引用来源的知识库名标签（浅色小圆角，与页码 tag 并列） */
.chat-view__drawer-ref-kb {
  font-size: 11px;
  color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-view__drawer-ref-score {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.chat-view__drawer-ref-snippet {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.chat-view__drawer-empty {
  margin: auto;
  font-size: 12px;
  color: var(--text-tertiary);
}

/* 抽屉滑入动画（过渡由 v-if + transition 驱动） */
.chat-view__drawer-enter-active {
  transition: transform 0.25s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.2s ease;
}

.chat-view__drawer-leave-active {
  transition: transform 0.2s ease, opacity 0.15s ease;
}

.chat-view__drawer-enter-from,
.chat-view__drawer-leave-to {
  transform: translateX(24px);
  opacity: 0;
}

/* ═══════════ 滚动条美化（规范：细滚动条 + 圆角 + 悬浮加深） ═══════════ */
.chat-view__messages::-webkit-scrollbar,
.chat-view__convs::-webkit-scrollbar,
.chat-view__drawer-refs::-webkit-scrollbar {
  width: 6px;
}

.chat-view__messages::-webkit-scrollbar-track,
.chat-view__convs::-webkit-scrollbar-track,
.chat-view__drawer-refs::-webkit-scrollbar-track {
  background: transparent;
}

.chat-view__messages::-webkit-scrollbar-thumb,
.chat-view__convs::-webkit-scrollbar-thumb,
.chat-view__drawer-refs::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-tertiary) 35%, transparent);
  border-radius: var(--radius-full);
}

.chat-view__messages::-webkit-scrollbar-thumb:hover,
.chat-view__convs::-webkit-scrollbar-thumb:hover,
.chat-view__drawer-refs::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--text-tertiary) 60%, transparent);
}

.chat-view__messages,
.chat-view__convs,
.chat-view__drawer-refs {
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--text-tertiary) 35%, transparent) transparent;
}

/* 响应式：窄屏左栏折叠 */
@media (max-width: 900px) {
  .chat-view__side {
    width: 180px;
  }
  .chat-view__drawer {
    width: 280px;
  }
}
</style>
