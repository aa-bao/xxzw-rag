export const APP_KEY = 'rag-database'

const EMBEDDED_ROOT_MARKER = `/project-apps/${APP_KEY}/`

/**
 * 平台会话契约端点（规范 10 §3.1）：必须相对应用根读取，
 * 禁止 fetch('/platform/session') 这类根路径写法。
 */
const PLATFORM_SESSION_PATH = './platform/session'

export function isEmbedded(): boolean {
  return new URLSearchParams(window.location.search).get('embedded') === 'true'
}

export function applicationBasePath(pathname = window.location.pathname): string {
  const markerIndex = pathname.indexOf(EMBEDDED_ROOT_MARKER)
  if (markerIndex < 0) return '/'
  return pathname.slice(0, markerIndex + EMBEDDED_ROOT_MARKER.length)
}

export function applicationUrl(path: string): string {
  const relativePath = path.replace(/^\/+/, '')
  return new URL(relativePath, `${window.location.origin}${applicationBasePath()}`).toString()
}

/** 嵌入模式（EMBEDDED_APP）：会话由平台 SSO 建立，前端只经 ./platform/session 或 401 判断。 */
export function needsPlatformSession(): boolean {
  return isEmbedded()
}

/**
 * 用户管理属于本地账号体系（本地账号 + 角色）；生产嵌入由主系统管角色，
 * 嵌入模式必须隐藏用户管理入口与路由。
 */
export function embeddedUserManagementHidden(): boolean {
  return isEmbedded()
}

/**
 * 读取平台会话。会话 Cookie（rag_database_session）是 HttpOnly 的，前端不能直接读，
 * 会话判断只通过本接口响应或 401 完成；无有效会话（401/网络失败）返回 null。
 */
export async function readPlatformSession(): Promise<unknown | null> {
  try {
    const resp = await fetch(PLATFORM_SESSION_PATH, { credentials: 'same-origin' })
    if (!resp.ok) return null
    const body = (await resp.json()) as { success?: boolean; data?: unknown }
    return body.success === true ? (body.data ?? null) : null
  } catch {
    return null
  }
}

/** 应用挂载前缀之外的路径（主系统一侧）；未挂载时回退到站点根。 */
function hostRootPath(pathname = window.location.pathname): string {
  const markerIndex = pathname.indexOf(EMBEDDED_ROOT_MARKER)
  if (markerIndex < 0) return window.location.origin
  return new URL(pathname.slice(0, markerIndex), window.location.origin).toString()
}

/**
 * 401 会话过期的跳转目标：
 * - 嵌入模式：回到主系统（把应用挂载前缀去掉，即规范 10 的"回主系统通用容器"）；
 * - 独立部署：回到 /login；已在登录页返回 null（避免刷新循环）。
 */
export function sessionExpiredTarget(): string | null {
  if (needsPlatformSession()) {
    return hostRootPath()
  }
  if (window.location.pathname === '/login') return null
  return '/login'
}

/** 会话过期统一处理：按 sessionExpiredTarget 导航；无目标（已在登录页）则不动作。 */
export function redirectToSessionExpiry(): void {
  const target = sessionExpiredTarget()
  if (target) window.location.assign(target)
}
