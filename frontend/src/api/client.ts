/** 后端 REST 客户端。所有请求走 Vite 代理的 /api，不直连端口。 */

import type {
  AnswerResult,
  CreateResumePayload,
  CreateSessionPayload,
  CreateTargetPayload,
  Health,
  JobTarget,
  ProviderStatus,
  ResumeItem,
  SessionDetail,
  SessionSummary,
  Usage,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const raw = (body as { detail: unknown }).detail
        detail = typeof raw === 'string' ? raw : JSON.stringify(raw)
      }
    } catch {
      // 非 JSON 错误体，保留状态码描述
    }
    throw new Error(detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<Health>('/health'),

  listTargets: () => request<JobTarget[]>('/targets'),
  createTarget: (payload: CreateTargetPayload) =>
    request<JobTarget>('/targets', { method: 'POST', body: JSON.stringify(payload) }),

  listResumes: () => request<ResumeItem[]>('/resumes'),
  createResume: (payload: CreateResumePayload) =>
    request<ResumeItem>('/resumes', { method: 'POST', body: JSON.stringify(payload) }),

  listSessions: () => request<SessionSummary[]>('/sessions'),
  createSession: (payload: CreateSessionPayload) =>
    request<SessionDetail>('/sessions', { method: 'POST', body: JSON.stringify(payload) }),
  getSession: (id: number) => request<SessionDetail>(`/sessions/${id}`),
  answer: (id: number, text: string) =>
    request<AnswerResult>(`/sessions/${id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  finish: (id: number) => request<SessionDetail>(`/sessions/${id}/finish`, { method: 'POST' }),

  providers: () => request<ProviderStatus>('/system/providers'),
  usage: () => request<Usage>('/system/usage'),
}
