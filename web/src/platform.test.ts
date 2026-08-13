import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  applicationBasePath,
  applicationUrl,
  embeddedUserManagementHidden,
  isEmbedded,
  needsPlatformSession,
  readPlatformSession,
  sessionExpiredTarget,
} from './platform'

afterEach(() => {
  window.history.replaceState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('platform deployment paths', () => {
  it('keeps standalone deployments rooted at the origin', () => {
    window.history.replaceState({}, '', '/kb')
    expect(applicationBasePath()).toBe('/')
    expect(applicationUrl('api/kb')).toBe(`${window.location.origin}/api/kb`)
    expect(isEmbedded()).toBe(false)
  })

  it('resolves APIs against the embedded application root', () => {
    window.history.replaceState(
      {},
      '',
      '/project-apps/rag-database/kb/12/docs?embedded=true',
    )
    expect(applicationBasePath()).toBe('/project-apps/rag-database/')
    expect(applicationUrl('/api/kb')).toBe(
      `${window.location.origin}/project-apps/rag-database/api/kb`,
    )
    expect(isEmbedded()).toBe(true)
  })
})

describe('embedded UI gating', () => {
  it('hides user management and relies on the platform session when embedded', () => {
    window.history.replaceState({}, '', '/project-apps/rag-database/kb?embedded=true')
    expect(embeddedUserManagementHidden()).toBe(true)
    expect(needsPlatformSession()).toBe(true)
  })

  it('keeps user management and the local login flow in standalone deployments', () => {
    window.history.replaceState({}, '', '/kb')
    expect(embeddedUserManagementHidden()).toBe(false)
    expect(needsPlatformSession()).toBe(false)
  })
})

describe('session expiry targets', () => {
  it('returns the main system side (mount prefix stripped) when embedded', () => {
    window.history.replaceState({}, '', '/project-apps/rag-database/kb?embedded=true')
    expect(sessionExpiredTarget()).toBe(`${window.location.origin}/`)
  })

  it('strips the mount prefix even when the app lives under a sub path', () => {
    window.history.replaceState({}, '', '/console/project-apps/rag-database/kb?embedded=true')
    expect(sessionExpiredTarget()).toBe(`${window.location.origin}/console`)
  })

  it('returns /login when standalone and not already there', () => {
    window.history.replaceState({}, '', '/kb')
    expect(sessionExpiredTarget()).toBe('/login')
  })

  it('returns null on the standalone login page to avoid redirect loops', () => {
    window.history.replaceState({}, '', '/login')
    expect(sessionExpiredTarget()).toBe(null)
  })
})

describe('platform session reads', () => {
  it('reads the session via the relative endpoint with same-origin credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: { userId: '1' } }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const session = await readPlatformSession()

    expect(session).toEqual({ userId: '1' })
    expect(fetchMock).toHaveBeenCalledWith('./platform/session', { credentials: 'same-origin' })
  })

  it('returns null when the platform session is missing or expired (401)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })))
    expect(await readPlatformSession()).toBe(null)
  })

  it('returns null when the response is not a success envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: false }), { status: 200 })),
    )
    expect(await readPlatformSession()).toBe(null)
  })

  it('returns null on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')))
    expect(await readPlatformSession()).toBe(null)
  })
})
