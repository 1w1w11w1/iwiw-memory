const BASE = '/api'
type PermissionProfile = 'read_only' | 'guided' | 'workspace' | 'full_access'

let permissionProfile: PermissionProfile = 'guided'

export function setPermissionProfile(profile: PermissionProfile): void {
  permissionProfile = profile
}

function requestHeaders(options: RequestInit): Headers {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  headers.set('X-IwIw-Permission-Profile', permissionProfile)
  return headers
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: requestHeaders(options),
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail) detail = body.detail
    } catch {
      // keep status text
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function streamRequest(
  path: string,
  options: RequestInit = {},
  onEvent: (event: any) => void | Promise<void>,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: requestHeaders(options),
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail) detail = body.detail
    } catch {
      // keep status text
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const lines = frame.split('\n')
      const data = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (!data) continue
      await onEvent(JSON.parse(data))
    }
  }
  if (buffer.trim()) {
    const data = buffer
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (data) await onEvent(JSON.parse(data))
  }
}
