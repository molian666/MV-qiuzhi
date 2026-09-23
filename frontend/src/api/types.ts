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
  /** 该回合的录音切片地址（M1 起有；空表示该回合没有音频） */
  audio_url: string | null
}

export interface SessionSummary {
  id: number
  mode: string
  status: string
  target_id: number | null
  resume_id: number | null
  provider: string
  plan: { max_questions?: number; style?: string }
  /** 生成报告后回填的摘要 */
  summary: string
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

// --------------------------------------------------------------------------- //
// 评分报告（M3）
// --------------------------------------------------------------------------- //
export interface DimensionScore {
  key: string
  label: string
  score: number
  comment: string
  evidence_turn_ids: number[]
}

export interface EvidenceTurn {
  turn_id: number
  speaker: string
  text: string
  start_ms: number | null
  end_ms: number | null
  audio_url: string | null
}

export interface ImprovementItem {
  id: number
  severity: 'high' | 'medium' | 'low' | string
  dimension: string
  dimension_label: string
  problem: string
  suggestion: string
  corrected_answer: string
  drill: string
  topics: string[]
  evidence: EvidenceTurn[]
}

export interface ScoreReport {
  id: number
  session_id: number | null
  overall: number
  summary: string
  model: string
  generated_at: string
  target_label: string
  /** true = 直接复用了已有报告，没有重新跑模型 */
  reused: boolean
  dimensions: DimensionScore[]
  items: ImprovementItem[]
}

export interface ReportSummary {
  id: number
  session_id: number | null
  overall: number
  generated_at: string
  summary: string
}

// --------------------------------------------------------------------------- //
// 薄弱点与训练（M5）
// --------------------------------------------------------------------------- //
export interface MasteryItem {
  topic_id: number
  name: string
  category: string
  mastery: number
  attempts: number
  correct_hits: number
  updated_at: string | null
  high_risk: boolean
}

export interface MasteryOverview {
  items: MasteryItem[]
  high_risk_count: number
  average_mastery: number
  due_count: number
}

export interface MasteryEvent {
  id: number
  at: string
  mastery_before: number
  mastery_after: number
  score: number
  source: string
  note: string
}

export interface TrainingQuestion {
  question_id: number
  text: string
  type: string
  difficulty: number
  tags: string[]
  topic_names: string[]
  origin: 'bank' | 'generated' | string
  kind: 'weak' | 'random' | string
  /** 打分依据：薄弱度 / 岗位匹配 / 到期 / 难度 / 题型罚项 */
  reason: Record<string, number>
}

export interface MasteryChange {
  topic_id: number
  name: string
  before: number
  after: number
}

export interface TrainingGrade {
  question_id: number
  attempt_id: number
  score: number
  comment: string
  better_answer: string
  mastery: MasteryChange[]
}
