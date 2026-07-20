<template>
  <div class="chat">
    <div class="chat__messages" ref="messagesEl">
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="chat__msg"
        :class="msg.role === 'user' ? 'chat__msg--user' : 'chat__msg--assistant'"
      >
        <span class="chat__role">{{ msg.role === 'user' ? '你' : '助手' }}</span>
        <div class="chat__content" v-html="msg.content" />
        <!-- Source references -->
        <div v-if="msg.references && msg.references.length" class="chat__refs">
          <div class="chat__refs-label">参考来源</div>
          <div v-for="ref in msg.references" :key="ref.chunk_id" class="chat__ref">
            <span class="chat__ref-title">{{ ref.title }}</span>
            <span v-if="ref.page" class="chat__ref-page">第{{ ref.page }}页</span>
            <p class="chat__ref-snippet">{{ ref.snippet }}</p>
          </div>
        </div>
      </div>

      <div v-if="streaming" class="chat__msg chat__msg--assistant">
        <span class="chat__role">助手</span>
        <div class="chat__content">{{ streamingContent }}</div>
      </div>
    </div>

    <form class="chat__input" @submit.prevent="handleSend">
      <el-input
        v-model="question"
        placeholder="输入你的问题&hellip;"
        :disabled="loading"
        size="large"
      >
        <template #append>
          <el-button native-type="submit" :loading="loading" :disabled="!question.trim()">
            发送
          </el-button>
        </template>
      </el-input>
    </form>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { api } from '../api/client'
import { streamSse } from '../api/sse'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  references?: Array<{
    chunk_id: string
    title: string
    page: number | null
    snippet: string
    score: number
  }>
}

const props = defineProps<{ kbId: number }>()

const messages = ref<ChatMessage[]>([])
const question = ref('')
const loading = ref(false)
const streaming = ref(false)
const streamingContent = ref('')
const messagesEl = ref<HTMLElement | null>(null)

async function handleSend() {
  const q = question.value.trim()
  if (!q || loading.value) return

  loading.value = true
  messages.value.push({ role: 'user', content: q })
  question.value = ''

  // Create conversation first time
  // Reuse previous conversation_id for simplicity
  const convResp = await api.createConversation(props.kbId)
  const cid = convResp.data.id

  const resp = await api.query(cid, q)
  if (!resp.ok) {
    messages.value.push({ role: 'assistant', content: '查询失败' })
    loading.value = false
    return
  }

  streaming.value = true
  streamingContent.value = ''
  const references: any[] = []

  try {
    for await (const ev of streamSse(resp)) {
      if (ev.event === 'chunk') {
        const { content } = JSON.parse(ev.data)
        streamingContent.value += content || ''
      } else if (ev.event === 'references') {
        const { items } = JSON.parse(ev.data)
        references.push(...(items || []))
      } else if (ev.event === 'done') {
        streaming.value = false
        streamingContent.value = ''
        messages.value.push({
          role: 'assistant',
          content: streamingContent.value || 'OK',
          references: references.length ? references : undefined,
        })
      } else if (ev.event === 'error') {
        streaming.value = false
        const errContent = streamingContent.value || '出错了'
        messages.value.push({ role: 'assistant', content: errContent })
        streamingContent.value = ''
      }
    }
  } catch {
    streaming.value = false
    if (streamingContent.value) {
      messages.value.push({ role: 'assistant', content: streamingContent.value })
    }
    streamingContent.value = ''
  }

  loading.value = false
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}
</script>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 780px;
  margin: 0 auto;
  width: 100%;
}

.chat__messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 0;
}

.chat__msg {
  margin-bottom: 20px;
}

.chat__role {
  font-size: 12px;
  font-weight: 600;
  color: var(--dust);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 6px;
  display: block;
}

.chat__msg--assistant .chat__role {
  color: var(--olive);
}

.chat__content {
  font-size: 15px;
  line-height: 1.7;
  white-space: pre-wrap;
}

/* ── Reference cards ── */
.chat__refs {
  margin-top: 16px;
}

.chat__refs-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--dust);
  letter-spacing: 1px;
  margin-bottom: 8px;
}

.chat__ref {
  border-left: 3px solid var(--olive);
  padding: 10px 14px;
  margin-bottom: 8px;
  background: var(--white);
  border-radius: 0 4px 4px 0;
}

.chat__ref-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--ink);
  margin-right: 8px;
}

.chat__ref-page {
  font-size: 12px;
  color: var(--dust);
  background: var(--surface);
  padding: 1px 6px;
  border-radius: 2px;
}

.chat__ref-snippet {
  font-size: 13px;
  color: var(--dust);
  margin-top: 4px;
  line-height: 1.5;
}

/* ── Input ── */
.chat__input {
  padding: 16px 0;
  border-top: 1px solid var(--border);
}
</style>
