import { ADMIN_ROLE } from '../stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 角色白名单：只有含该角色的用户可访问（不标 = 全员） */
    roles?: string[]
    /** 页面标题（导航/面包屑用） */
    title?: string
  }
}

export function canAccess(role: string | undefined, metaRoles?: string[]): boolean {
  if (!metaRoles || metaRoles.length === 0) return true
  if (!role) return false
  return metaRoles.includes(role)
}

export const ADMIN_ONLY = { roles: [ADMIN_ROLE] }
