/** 后端契约类型。字段名与 `backend/app/schemas/` 保持一致。 */

export type Speaker = 'interviewer' | 'candidate' | 'system'

export interface Turn {
  id: number
  idx: number
  speaker: Speaker | string
  text: string
  kind: string
  topic: string
  is_followup: boolean
  start_ms: number | null
  end_ms: number | null
  created_at: string
}

export interface SessionSummary {
  id: number
  mode: string
  status: string
  target_id: number | null
  resume_id: number | null
  provider: string
  plan: { max_questions?: number; style?: string }
  created_at: string
  started_at: string | null
  ended_at: string | null
  duration_ms: number | null
  target_label: string
  question_count: number
  answer_count: number
}

export interface SessionDetail extends SessionSummary {
  turns: Turn[]
}

export interface AnswerResult {
  session: SessionDetail
  question: Turn | null
  finished: boolean
  message: string
}

export interface CreateSessionPayload {
  target_id?: number | null
  resume_id?: number | null
  mode?: 'text' | 'realtime'
  max_questions?: number | null
  style?: string | null
}

export interface JobTarget {
  id: number
  company: string
  title: string
  level: string
  jd_url: string
  jd_text: string
  tags: string[]
  created_at: string
}

export interface CreateTargetPayload {
  company?: string
  title?: string
  level?: string
  jd_url?: string
  jd_text?: string
  tags?: string[]
}

export interface ResumeItem {
  id: number
  name: string
  content_text: string
  variant_label: string
  version: number
  target_job_id: number | null
  created_at: string
}

export interface CreateResumePayload {
  name?: string
  content_text?: string
  variant_label?: string
  target_job_id?: number | null
}

export interface ProviderInfo {
  provider: string
  ready: boolean
  chat_model?: string
  reasoner_model?: string
  base_url?: string
  hint: string
}

export interface ProviderStatus {
  llm: ProviderInfo
  speech: ProviderInfo
}

export interface UsageItem {
  task: string
  model: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  avg_latency_ms: number
}

export interface Usage {
  window_days: number
  total_calls: number
  failed_calls: number
  items: UsageItem[]
  note: string
}

export interface Health {
  status: string
  app: string
  llm_provider: string
  data_dir: string
}
