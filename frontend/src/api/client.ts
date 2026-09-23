/** 后端 REST 客户端。所有请求走 Vite 代理的 /api，不直连端口。 */

import type {
  AnswerDraft,
  AnswerMeta,
  AnswerResult,
  ApplicationItem,
  ApplicationStats,
  ApplyExecutionOutcome,
  ApplyQueueSnapshot,
  ApplyQueueTask,
  ApplyRule,
  ApplySearchOutcome,
  ApplySettings,
  ApplyStatus,
  AtsIssue,
  ContractReport,
  CreateRecordingPayload,
  CreateResumePayload,
  CreateSessionPayload,
  CreateTargetPayload,
  DecisionOptions,
  ExpressionReport,
  Health,
  InterviewOptions,
  ImportBatch,
  ImportOutcome,
  JobTarget,
  MasteryEvent,
  MasteryOverview,
  MatchBandReport,
  MatchResult,
  Negotiation,
  OfferItem,
  OfferMatrix,
  ProfileDetail,
  ProfileListItem,
  ProviderStatus,
  Recording,
  RecordingDetail,
  ReportSummary,
  ResumeItem,
  RiskReport,
  ScoreReport,
  SessionDetail,
  SessionSummary,
  StoryCardItem,
  StoryList,
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

  // 投递数据（M6）
  applicationStats: (weeks = 8) => request<ApplicationStats>(`/applications/stats?weeks=${weeks}`),
  listApplications: (channel?: string, status?: string) => {
    const query = new URLSearchParams()
    if (channel) {
      query.set('channel', channel)
    }
    if (status) {
      query.set('status', status)
    }
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return request<ApplicationItem[]>(`/applications${suffix}`)
  },
  importApplications: (text: string, channel = 'other') =>
    request<ImportOutcome>('/applications/import', {
      method: 'POST',
      body: JSON.stringify({ text, channel }),
    }),
  createApplication: (payload: { company: string; title?: string; channel?: string; status?: string }) =>
    request<ApplicationItem>('/applications', { method: 'POST', body: JSON.stringify(payload) }),
  updateApplicationStatus: (id: number, toStatus: string) =>
    request<ApplicationItem>(`/applications/${id}/status`, {
      method: 'POST',
      body: JSON.stringify({ to_status: toStatus }),
    }),
  deleteApplication: (id: number) => request<void>(`/applications/${id}`, { method: 'DELETE' }),
  listImportBatches: () => request<ImportBatch[]>('/applications/imports'),

  // 半自动投递（M10）
  applyStatus: () => request<ApplyStatus>('/apply/status'),
  applySettings: () => request<ApplySettings>('/apply/settings'),
  updateApplySettings: (payload: Partial<ApplySettings>) =>
    request<ApplySettings>('/apply/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  applyRules: () => request<ApplyRule[]>('/apply/rules'),
  updateApplyRule: (id: number, payload: Partial<ApplyRule>) =>
    request<ApplyRule>(`/apply/rules/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  applyQueue: (status?: string) => {
    const suffix = status ? `?status=${status}` : ''
    return request<ApplyQueueTask[]>(`/apply/queue${suffix}`)
  },
  applyQueueSnapshot: () => request<ApplyQueueSnapshot>('/apply/queue/snapshot'),
  /** 抓岗位 → 过滤 → 打分 → 进待投队列（**不会投递**） */
  searchApplyCandidates: (keywords: string[], limit: number) =>
    request<ApplySearchOutcome>('/apply/search', {
      method: 'POST',
      body: JSON.stringify({ keywords, limit }),
    }),
  approveApplyTasks: (taskIds: number[], overrides: Record<number, string> = {}) =>
    request<{ approved: number }>('/apply/queue/approve', {
      method: 'POST',
      body: JSON.stringify({ task_ids: taskIds, say_hi_overrides: overrides }),
    }),
  skipApplyTasks: (taskIds: number[]) =>
    request<{ skipped: number }>('/apply/queue/skip', {
      method: 'POST',
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  /** 执行已确认的投递（受限速与熔断约束） */
  executeApply: (limit: number, dryRun = false) =>
    request<ApplyExecutionOutcome>('/apply/execute', {
      method: 'POST',
      body: JSON.stringify({ limit, dry_run: dryRun }),
    }),

  // 简历智能与话术（M8）
  matchResume: (resumeId: number, payload: { jd_text?: string; job_title?: string; application_id?: number }) =>
    request<MatchResult>(`/resumes/${resumeId}/match`, { method: 'POST', body: JSON.stringify(payload) }),
  resumeAts: (resumeId: number) => request<AtsIssue[]>(`/resumes/${resumeId}/ats`, { method: 'POST' }),
  cloneResume: (resumeId: number, payload: { name?: string; variant_label?: string }) =>
    request<ResumeItem>(`/resumes/${resumeId}/clone`, { method: 'POST', body: JSON.stringify(payload) }),
  resumeVersions: (resumeId: number) => request<ResumeItem[]>(`/resumes/${resumeId}/versions`),

  listStories: () => request<StoryList>('/stories'),
  generateStories: (resumeId: number | null, count = 4) =>
    request<StoryCardItem[]>('/stories/generate', {
      method: 'POST',
      body: JSON.stringify({ resume_id: resumeId, count }),
    }),
  createStory: (payload: Partial<StoryCardItem>) =>
    request<StoryCardItem>('/stories', { method: 'POST', body: JSON.stringify(payload) }),
  updateStory: (id: number, payload: Partial<StoryCardItem>) =>
    request<StoryCardItem>(`/stories/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteStory: (id: number) => request<void>(`/stories/${id}`, { method: 'DELETE' }),

  answerMeta: () => request<AnswerMeta>('/answers/meta'),
  listAnswers: (kind?: string) => {
    const suffix = kind ? `?kind=${kind}` : ''
    return request<AnswerDraft[]>(`/answers${suffix}`)
  },
  composeSelfIntro: (payload: { resume_id?: number | null; seconds: number; jd_text?: string }) =>
    request<AnswerDraft>('/answers/self-intro', { method: 'POST', body: JSON.stringify(payload) }),
  composeSensitive: (payload: { kind: string; resume_id?: number | null; jd_text?: string }) =>
    request<AnswerDraft>('/answers/sensitive', { method: 'POST', body: JSON.stringify(payload) }),
  composeQuestions: (payload: {
    jd_text: string
    company?: string
    resume_id?: number | null
    count?: number
  }) => request<AnswerDraft>('/answers/questions', { method: 'POST', body: JSON.stringify(payload) }),
  deleteAnswer: (id: number) => request<void>(`/answers/${id}`, { method: 'DELETE' }),

  matchBands: (resumeId?: number | null) => {
    const suffix = resumeId ? `?resume_id=${resumeId}` : ''
    return request<MatchBandReport>(`/applications/match-bands${suffix}`)
  },

  // 表达体检（M9）
  getExpression: (sessionId: number) => request<ExpressionReport>(`/sessions/${sessionId}/expression`),
  /** 生成表达体检（GET 只读已有报告，POST 才会真的读音频分析） */
  createExpression: (sessionId: number, withAudio = true) =>
    request<ExpressionReport>(`/sessions/${sessionId}/expression?with_audio=${withAudio ? 'true' : 'false'}`, {
      method: 'POST',
    }),

  // 压力模式与多角色（M11）
  interviewOptions: () => request<InterviewOptions>('/interview-options'),

  // Offer 与决策（M12）
  decisionOptions: () => request<DecisionOptions>('/decision/options'),
  listOffers: () => request<OfferItem[]>('/offers'),
  createOffer: (payload: Partial<OfferItem>) =>
    request<OfferItem>('/offers', { method: 'POST', body: JSON.stringify(payload) }),
  updateOffer: (id: number, payload: Partial<OfferItem>) =>
    request<OfferItem>(`/offers/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteOffer: (id: number) => request<void>(`/offers/${id}`, { method: 'DELETE' }),
  /** 权重一变排名就可能翻转，所以每次都重新算 */
  offerMatrix: (weights: Record<string, number>) =>
    request<OfferMatrix>('/offers/matrix', { method: 'POST', body: JSON.stringify({ weights }) }),
  scanRisk: (text: string) =>
    request<RiskReport>('/decision/risk', { method: 'POST', body: JSON.stringify({ text }) }),
  scanContract: (text: string) =>
    request<ContractReport>('/decision/contract', { method: 'POST', body: JSON.stringify({ text }) }),

  listNegotiations: () => request<Negotiation[]>('/negotiations'),
  startNegotiation: (payload: {
    company?: string
    title?: string
    bottom_line?: number
    target?: number
    ceiling?: number | null
    script?: string
  }) => request<Negotiation>('/negotiations', { method: 'POST', body: JSON.stringify(payload) }),
  getNegotiation: (id: number) => request<Negotiation>(`/negotiations/${id}`),
  replyNegotiation: (id: number, text: string) =>
    request<Negotiation>(`/negotiations/${id}/reply`, { method: 'POST', body: JSON.stringify({ text }) }),
}
