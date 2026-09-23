/** 后端 REST 客户端。所有请求走 Vite 代理的 /api，不直连端口。 */

import type {
  AnswerResult,
  CreateRecordingPayload,
  CreateResumePayload,
  CreateSessionPayload,
  CreateTargetPayload,
  Health,
  JobTarget,
  MasteryEvent,
  MasteryOverview,
  ProfileDetail,
  ProfileListItem,
  ProviderStatus,
  Recording,
  RecordingDetail,
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
  // 只有 JSON 字符串才设 Content-Type：multipart 上传必须让浏览器自己带 boundary
  const hasStringBody = typeof init?.body === 'string'
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(hasStringBody ? { 'Content-Type': 'application/json' } : {}),
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
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

  // 节奏学习（M4）
  listRecordings: () => request<Recording[]>('/recordings'),
  createRecording: (payload: CreateRecordingPayload) =>
    request<Recording>('/recordings', { method: 'POST', body: JSON.stringify(payload) }),
  getRecording: (id: number) => request<RecordingDetail>(`/recordings/${id}`),
  deleteRecording: (id: number) => request<void>(`/recordings/${id}`, { method: 'DELETE' }),
  uploadRecording: (file: File, title: string, rawText: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', title)
    form.append('raw_text', rawText)
    return request<Recording>('/recordings/upload', { method: 'POST', body: form })
  },
  listProfiles: () => request<ProfileListItem[]>('/interviewer-profiles'),
  createProfile: (recordingIds: number[], name: string) =>
    request<ProfileDetail>('/interviewer-profiles', {
      method: 'POST',
      body: JSON.stringify({ recording_ids: recordingIds, name }),
    }),
  getProfile: (id: number) => request<ProfileDetail>(`/interviewer-profiles/${id}`),
  deleteProfile: (id: number) => request<void>(`/interviewer-profiles/${id}`, { method: 'DELETE' }),
}
