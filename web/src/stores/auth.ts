import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'

export interface AuthUser {
  id: number
  username: string
  role: string
}

export const ADMIN_ROLE = 'account_admin'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const loading = ref(false)
  const initialized = ref(false)

  const isAdmin = computed(() => user.value?.role === ADMIN_ROLE)

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

  return { user, loading, initialized, isAdmin, init, login, logout }
})
