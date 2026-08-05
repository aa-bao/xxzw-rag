<template>
  <main class="login-page">
    <section class="login-card">
      <h1 class="login-card__title">RAG 知识库</h1>
      <p class="login-card__subtitle">登入以管理你的私有文档</p>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleLogin"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="输入用户名" size="large" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="输入密码" size="large" show-password />
        </el-form-item>

        <el-alert v-if="error" :title="error" type="error" :closable="false" class="login-card__error" />

        <el-button type="primary" size="large" :loading="loading" native-type="submit" class="login-card__btn">
          登录
        </el-button>

        <el-button
          v-if="devBypass"
          size="large"
          class="login-card__btn login-card__dev"
          @click="handleDevLogin"
        >
          开发模式（跳过登录）
        </el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const error = ref('')

const devBypass = import.meta.env.DEV && import.meta.env.VITE_DEV_BYPASS_AUTH === 'true'

const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleDevLogin() {
  await auth.init()
  router.push('/rag')
}

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    router.push('/rag')
  } catch {
    error.value = '用户名或密码错误'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(180deg, var(--paper) 0%, var(--surface) 100%);
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 48px 40px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 2px 16px rgba(44, 36, 22, 0.06);
}

.login-card__title {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--ink);
  margin-bottom: 8px;
}

.login-card__subtitle {
  font-size: 15px;
  color: var(--dust);
  margin-bottom: 32px;
}

.login-card__error {
  margin-bottom: 16px;
}

.login-card__btn {
  width: 100%;
  margin-top: 8px;
}

.login-card__dev {
  margin-top: 4px;
}
</style>
