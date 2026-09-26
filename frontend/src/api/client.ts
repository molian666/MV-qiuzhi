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
  AuthStatus,
  ContractReport,
  ExportPreview,
  LoginResult,
  CreateRecordingPayload,
  CreateResumePayload,
  CreateSessionPayload,
  CreateTargetPayload,
  DecisionOptions,
  DocCheck,
  ExpressionReport,
  Health,
  InterviewOptions,
  ImportBatch,
  ImportOutcome,
  JobTarget,
  LlmTest,
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
  VisionAttempt,
  VisionDraft,
  VisionGenerateIn,
  VisionGenerateResult,
  VisionImportResult,
  VisionMeta,
  VisionQuestion,
  VisionQuestionBrief,
  VisionUpload,
  SystemSettings,
  SystemSettingsUpdated,
} from './types'

const TOKEN_KEY = 'mianshi.token'

/** 会话失效时抛这个，让 UI 能区分「要重新登录」与普通业务报错。 */
export class UnauthorizedError extends Error {}

export function getToken(): string {
  return window.localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token)
  } else {
    window.localStorage.removeItem(TOKEN_KEY)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 只有 JSON 字符串才设 Content-Type：multipart 上传必须让浏览器自己带 boundary
  const hasStringBody = typeof init?.body === 'string'
  const token = getToken()
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(hasStringBody ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
  })

  if (response.status === 401) {
    // 会话过期（或服务端换了签名密钥）：本地 token 已无效，清掉让 App 回到登录页。
    // 登录接口本身失败也走这里——那时本来就没有 token，清掉无害，而且能拿到
    // 服务端给的具体原因（「密码不正确」比「未登录」有用得多）。
    const detail = await readDetail(response, '未登录或登录已过期，请重新登录')
    setToken('')
    throw new UnauthorizedError(detail)
  }

  if (!response.ok) {
    throw new Error(await readDetail(response, `${response.status} ${response.statusText}`))
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

async function readDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const raw = (body as { detail: unknown }).detail
      return typeof raw === 'string' ? raw : JSON.stringify(raw)
    }
  } catch {
    // 非 JSON 错误体，用调用方给的兜底描述
  }
  return fallback
}

export const api = {
  health: () => request<Health>('/health'),

  // 系统配置（M13 补充）：写进 backend/.env，密钥不回显、不入库
  systemSettings: () => request<SystemSettings>('/system/settings'),
  updateSystemSettings: (values: Record<string, string>) =>
    request<SystemSettingsUpdated>('/system/settings', {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }),
  /** 用当前配置真发一次请求。失败也返回 200，看结果里的 ok 字段。 */
  testLlm: () => request<LlmTest>('/system/test-llm', { method: 'POST' }),

  // 删除（M13 补充）：会话会连带回合、作答、报告与录音文件
  deleteSession: (sessionId: number) => request<void>(`/sessions/${sessionId}`, { method: 'DELETE' }),

  // 登录（部署到云服务器时启用；服务端没配 AUTH_PASSWORD 时 auth_enabled=false）
  authStatus: () => request<AuthStatus>('/auth/status'),
  login: async (password: string) => {
    const result = await request<LoginResult>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    })
    setToken(result.token)
    return result
  },
  logout: async () => {
    const result = await request<{ ok: boolean }>('/auth/logout', { method: 'POST' })
    setToken('')
    return result
  },

  // 机器视觉面试题库（M13 的主线功能）
  visionMeta: () => request<VisionMeta>('/vision/meta'),
  visionQuestions: (params?: {
    role?: string
    category?: string
    maxDifficulty?: number
    keyword?: string
  }) => {
    const search = new URLSearchParams()
    if (params?.role) search.set('role', params.role)
    if (params?.category) search.set('category', params.category)
    if (params?.maxDifficulty) search.set('max_difficulty', String(params.maxDifficulty))
    if (params?.keyword) search.set('keyword', params.keyword)
    const query = search.toString()
    return request<VisionQuestionBrief[]>(`/vision/questions${query ? `?${query}` : ''}`)
  },
  visionQuestion: (questionId: number) => request<VisionQuestion>(`/vision/questions/${questionId}`),
  rateVisionQuestion: (questionId: number, rating: string, note = '') =>
    request<VisionAttempt>(`/vision/questions/${questionId}/attempt`, {
      method: 'POST',
      body: JSON.stringify({ rating, note }),
    }),
  /** 上传题库文档。FormData 不设 Content-Type，让浏览器自己带 boundary。 */
  visionImport: (file: File, role = 'uploaded') => {
    const form = new FormData()
    form.append('file', file)
    form.append('role', role)
    return request<VisionImportResult>('/vision/import', { method: 'POST', body: form })
  },
  visionUploads: () => request<VisionUpload[]>('/vision/uploads'),
  visionDeleteUpload: (batch: string) =>
    request<{ removed: number }>(`/vision/uploads/${encodeURIComponent(batch)}`, {
      method: 'DELETE',
    }),

  // 让模型出题：先存为草稿，采纳后才进抽题池
  visionGenerate: (payload: VisionGenerateIn) =>
    request<VisionGenerateResult>('/vision/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  visionDrafts: () => request<VisionDraft[]>('/vision/drafts'),
  visionAdoptDraft: (batch: string) =>
    request<{ adopted: number }>(`/vision/drafts/${encodeURIComponent(batch)}/adopt`, {
      method: 'POST',
    }),
  /** 按题采纳：只把选中的这几道草稿放进抽题池。已采纳的会被忽略，不重复计数。 */
  visionAdoptQuestions: (questionIds: number[]) =>
    request<{ adopted: number }>('/vision/questions/adopt', {
      method: 'POST',
      body: JSON.stringify({ question_ids: questionIds }),
    }),
  visionDiscardDraft: (batch: string) =>
    request<{ removed: number }>(`/vision/drafts/${encodeURIComponent(batch)}`, {
      method: 'DELETE',
    }),


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
  /** 上传对话文稿文件（.txt/.md）：后端自动识别编码，并当场落库段落 */
  uploadDocument: (file: File, title = '', company = '', role = '') => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', title)
    form.append('company', company)
    form.append('role', role)
    return request<Recording>('/recordings/document', { method: 'POST', body: form })
  },
  /** 只体检不落库：上传前先告诉用户能切出多少问答、缺什么 */
  inspectDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocCheck>('/recordings/inspect', { method: 'POST', body: form })
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


  exportPreview: (token: string) => request<ExportPreview>(`/export/preview?token=${encodeURIComponent(token)}`),
  /** 导出文件的下载地址（带令牌，直接挂到 <a href> 上） */
  exportFileUrl: (format: 'markdown' | 'csv' | 'json', token: string) =>
    `/api/export/${format}?token=${encodeURIComponent(token)}`,
}
