/**
 * Thin fetch wrapper around the Django API.
 *
 * Auth is cookie-based: Django sets a session cookie on login and the browser
 * sends it automatically. For unsafe methods Django also requires the CSRF
 * token to be echoed in a header, which is what `csrfHeader` does.
 */

const API_BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  /** Field-level errors as DRF reports them, e.g. { email: ["..."] }. */
  readonly data: unknown

  constructor(status: number, message: string, data: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

function csrfHeader(): Record<string, string> {
  const token = readCookie('csrftoken')
  return token ? { 'X-CSRFToken': token } : {}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...csrfHeader(),
      ...init.headers,
    },
  })

  if (response.status === 204) {
    return undefined as T
  }

  const isJson = response.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await response.json() : await response.text()

  if (!response.ok) {
    const detail =
      (isJson && typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : null) ?? `Anmodningen fejlede (${response.status})`
    throw new ApiError(response.status, detail, body)
  }

  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** Primes the csrftoken cookie. Call once before the first unsafe request. */
  ensureCsrf: () => request<{ detail: string }>('/auth/csrf/', { method: 'GET' }),
}
