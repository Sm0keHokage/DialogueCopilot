export class ApiError extends Error {
  code: string
  field: string | null
  status: number
  details?: { field: string; message: string }[]

  constructor(
    status: number,
    code: string,
    message: string,
    field: string | null = null,
    details?: { field: string; message: string }[],
  ) {
    super(message)
    this.status = status
    this.code = code
    this.field = field
    this.details = details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: 'include',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (resp.status === 204) return undefined as T
  let body: unknown = null
  try {
    body = await resp.json()
  } catch {
    body = null
  }
  if (!resp.ok) {
    const err = (body as { error?: { code?: string; message?: string; field?: string | null; details?: { field: string; message: string }[] } } | null)?.error
    throw new ApiError(
      resp.status,
      err?.code ?? 'http_error',
      err?.message ?? `HTTP ${resp.status}`,
      err?.field ?? null,
      err?.details,
    )
  }
  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }),
  put: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
