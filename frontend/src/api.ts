import type { UploadTicket } from './types'

let csrf = ''

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后再试'
}

export function setCsrf(value: string): void {
  csrf = value
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** 同时处理代理返回的 HTML 错误，避免向用户展示难以理解的 JSON 解析异常。 */
async function readResponse<T>(response: Response): Promise<T> {
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new ApiError(
      response.ok ? '服务返回了无法识别的数据' : `服务暂时不可用（${response.status}）`,
      response.status,
    )
  }
  if (!response.ok) {
    const details = record(data) && record(data.error) ? data.error : null
    const message =
      details && typeof details.message === 'string' ? details.message : '请求失败，请稍后再试'
    throw new ApiError(
      message,
      response.status,
      details && typeof details.code === 'string' ? details.code : null,
    )
  }
  // 成功响应的数据结构遵循 types.ts 中声明的服务端接口约定。
  return data as T
}

export async function api<T = unknown>(
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return readResponse<T>(response)
}

function requestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  // LAN HTTP pages lack randomUUID; getRandomValues also works outside secure contexts.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function action<T = unknown>(name: string, body: unknown = {}, write = false): Promise<T> {
  // 不自动重试写操作；若结果不确定，调用方重试时必须沿用原请求标识。
  return api<T>(`/api/actions/${name}`, body, write ? { 'Idempotency-Key': requestId() } : {})
}

export async function putUpload(ticket: UploadTicket, file: File): Promise<void> {
  // 第一版使用本地文件传输接口；通过相对路径，让 Vite 开发代理
  // 与生产环境的同源服务共用相同的请求逻辑。
  const path = new URL(ticket.upload_url).pathname
  if (!path.startsWith('/api/transfers/upload/'))
    throw new Error('上传地址不属于当前支持的文件服务')
  const response = await fetch(path, { method: 'PUT', body: file })
  await readResponse(response)
}

export function size(bytes: number): string {
  if (!bytes) return '0 B'
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1048576
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`
}

export function date(value?: number | null): string {
  return value
    ? new Date(value * 1000).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—'
}
