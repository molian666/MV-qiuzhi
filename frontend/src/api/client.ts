/** 后端 REST 客户端。所有请求走 Vite 代理的 /api，不直连端口。 */

import type {
  AnswerResult,
  CreateResumePayload,
  CreateSessionPayload,
  CreateTargetPayload,
  Health,
  JobTarget,
  MasteryEvent,
  MasteryOverview,
  ProviderStatus,
  ReportSummary,
  ResumeItem,
  ScoreReport,
  SessionDetail,
  SessionSummary,
  TrainingGrade,
  TrainingQuestion,
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

  // 报告（M3）：GET 只读已有报告，没有就 404；POST 才会真的跑模型
  getReport: (sessionId: number) => request<ScoreReport>(`/sessions/${sessionId}/report`),
  createReport: (sessionId: number, force = true) =>
    request<ScoreReport>(`/sessions/${sessionId}/report?force=${force ? 'true' : 'false'}`, { method: 'POST' }),
  listReports: () => request<ReportSummary[]>('/reports'),

  // 薄弱点与训练（M5）
  masteryOverview: () => request<MasteryOverview>('/skills/mastery'),
  masteryHistory: (topicId: number) => request<MasteryEvent[]>(`/skills/${topicId}/history`),
  // POST 是因为缺题时后端会真的生成题目并写库（有副作用）
  nextTraining: (limit = 4, weakCount = 3, targetId: number | null = null) => {
    const query = new URLSearchParams({ limit: String(limit), weak_count: String(weakCount) })
    if (targetId !== null) {
      query.set('target_id', String(targetId))
    }
    return request<TrainingQuestion[]>(`/training/next?${query.toString()}`, { method: 'POST' })
  },
  gradeTraining: (questionId: number, answer: string) =>
    request<TrainingGrade>('/training/answer', {
      method: 'POST',
      body: JSON.stringify({ question_id: questionId, answer }),
    }),
}
