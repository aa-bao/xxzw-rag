import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<{ id: number; username: string; role: string } | null>(null)
  const loading = ref(false)
  const initialized = ref(false)

  async function init() {
    try {
      const resp = await api.me()
      user.value = resp.data
    } catch {
      user.value = null
    } finally {
      initialized.value = true
    }
  }

  async function login(username: string, password: string) {
    const resp = await api.login(username, password)
    user.value = resp.data
  }

  async function logout() {
    await api.logout()
    user.value = null
  }

  return { user, loading, initialized, init, login, logout }
})
