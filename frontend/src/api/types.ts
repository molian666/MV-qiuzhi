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
  plan: { max_questions?: number; style?: string; pacing?: ProfilePolicy }
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
  /** M4：选一个面试官画像，这场面试按它的节奏来 */
  interviewer_profile_id?: number | null
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

// --------------------------------------------------------------------------- //
// 节奏学习（M4）
// --------------------------------------------------------------------------- //
export interface Recording {
  id: number
  title: string
  kind: string
  company: string
  role: string
  status: string
  duration_ms: number | null
  error: string
  created_at: string
  has_audio: boolean
  has_text: boolean
}

export interface CreateRecordingPayload {
  title?: string
  kind?: string
  company?: string
  role?: string
  /** 对话文稿：Mock 转写模式下的文字替身 */
  raw_text?: string
}

export interface TranscriptSegment {
  idx: number
  speaker: string
  text: string
  start_ms: number | null
  end_ms: number | null
}

export interface RecordingDetail extends Recording {
  segments: TranscriptSegment[]
}

export interface PacingSample {
  question_type: string
  topic: string
  question: string
  ask_duration_ms: number
  wait_ms: number | null
  answer_ms: number | null
  followup_count: number
  interrupted: boolean
}

/** 把画像翻译成"这场面试会怎么进行" */
export interface ProfilePolicy {
  profile_id: number | null
  profile_name: string
  silence_ms: number
  max_followup_depth: number
  followup_triggers: string[]
  topic_mix: Record<string, number>
  style_tags: string[]
  stress_level: string
  interviewer_wpm: number
  opening_pattern: string
  closing_pattern: string
}

export interface InterviewerProfile {
  id: number
  name: string
  source: string
  style_tags: string[]
  recording_ids: number[]
  created_at: string
}

export interface ProfileListItem extends InterviewerProfile {
  question_count: number
  stress_level: string
  sample_count: number
}

export interface ProfileDetail extends InterviewerProfile {
  params: Record<string, unknown>
  policy: ProfilePolicy
  samples: PacingSample[]
}

// --------------------------------------------------------------------------- //
// 投递数据（M6）
// --------------------------------------------------------------------------- //
export interface FunnelStep {
  stage: string
  label: string
  count: number
  rate_from_previous: number
  rate_from_first: number
}

export interface ChannelStats {
  channel: string
  total: number
  responded: number
  interviewed: number
  offers: number
  response_rate: number
  interview_rate: number
  offer_rate: number
}

export interface WeekPoint {
  week: string
  applied: number
  responded: number
  interviewed: number
}

export interface ResumeVersionStats {
  version: string
  total: number
  responded: number
  response_rate: number
}

export interface ResponseTimeStats {
  samples: number
  median_hours: number | null
  average_hours: number | null
  fastest_hours: number | null
  slowest_hours: number | null
}

export interface FollowUpItem {
  application_id: number
  company: string
  title: string
  channel: string
  status: string
  days_since: number
}

export interface ApplicationStats {
  total: number
  responded: number
  interviewed: number
  offers: number
  response_rate: number
  interview_rate: number
  offer_rate: number
  funnel: FunnelStep[]
  by_channel: ChannelStats[]
  weekly: WeekPoint[]
  resume_versions: ResumeVersionStats[]
  response_time: ResponseTimeStats
  follow_ups: FollowUpItem[]
}

export interface ApplicationItem {
  id: number
  company: string
  title: string
  channel: string
  status: string
  applied_at: string
  salary_range: string
  city: string
  resume_version: string
  jd_url: string
  note: string
  source: string
  created_at: string
}

export interface ImportOutcome {
  batch_id: number
  source: string
  rows: number
  inserted: number
  updated: number
  skipped: number
  matched_columns: Record<string, string>
  unmatched_headers: string[]
  skipped_details: [number, string][]
  note: string
}

export interface ImportBatch {
  id: number
  channel: string
  source: string
  at: string
  rows: number
  inserted: number
  updated: number
  skipped: number
  note: string
}

// -------------------------------------------------------------------------- //
// 半自动投递（M10）
// -------------------------------------------------------------------------- //

export interface ApplySettings {
  enabled: boolean
  risk_acknowledged: boolean
  platform: string
  default_channel: string
  want_salary_min: number
  per_minute: number
  per_day: number
  jitter_min_seconds: number
  jitter_max_seconds: number
  selectors: Record<string, string>
  user_data_dir: string
  /** 硬上限：配置会被夹到这个范围内 */
  max_per_minute: number
  max_per_day: number
}

export interface ApplyStatus {
  enabled: boolean
  risk_acknowledged: boolean
  platform: string
  circuit_broken_until: string | null
  broken: boolean
  daily_sent: number
  daily_limit: number
  remaining_quota: number
  adapter_ready: boolean
  adapter_note: string
}

export interface ApplyRule {
  id: number
  name: string
  kind: string
  enabled: boolean
  priority: number
  params: Record<string, unknown>
}

export interface ApplyCandidate {
  job_posting_id: number
  external_id: string
  company: string
  title: string
  salary_range: string
  city: string
  jd_url: string
  matched_score: number
  passed: boolean
  blocked_by: string[]
  reasons: string[]
  say_hi: string
  task_id: number | null
}

export interface ApplySearchOutcome {
  fetched: number
  passed: number
  blocked: number
  queued: number
  duplicates: number
  candidates: ApplyCandidate[]
  notes: string[]
}

export interface ApplyQueueTask {
  id: number
  status: string
  company: string
  title: string
  salary_range: string
  city: string
  jd_url: string
  matched_score: number | null
  say_hi_text: string
  attempts: number
  error: string
  created_at: string
  approved_at: string | null
  sent_at: string | null
}

export interface ApplyQueueSnapshot {
  total: number
  pending: number
  approved: number
  sent: number
  failed: number
  by_status: Record<string, number>
}

export interface ApplyExecutionItem {
  task_id: number
  company: string
  title: string
  ok: boolean
  detail: string
}

export interface ApplyExecutionOutcome {
  attempted: number
  sent: number
  failed: number
  stopped_reason: string
  tripped: boolean
  breaker_message: string
  items: ApplyExecutionItem[]
}

// -------------------------------------------------------------------------- //
// 简历智能与话术（M8）
// -------------------------------------------------------------------------- //

export interface JdKeyword {
  word: string
  weight: number
  group: string
  group_label: string
  mentions: number
  sentence: string
}

export interface AtsIssue {
  severity: 'error' | 'warning' | 'info'
  kind: string
  message: string
  suggestion: string
}

export interface MatchSuggestion {
  kind: string
  target: string
  why: string
  where: string
  draft: string
  weight: number
}

export interface MatchResult {
  score: number
  grade: string
  coverage: number
  jd_keyword_count: number
  covered: JdKeyword[]
  missing: JdKeyword[]
  extra: string[]
  missing_groups: string[]
  years_required: number | null
  years_actual: number | null
  years_ok: boolean | null
  ats: AtsIssue[]
  suggestions: MatchSuggestion[]
  summary: string
}

export interface StoryCardItem {
  id: number
  title: string
  situation: string
  task: string
  action: string
  result: string
  metrics: string
  tags: string[]
  topics: string[]
  starred: boolean
  source_resume_id: number | null
  used_count: number
  created_at: string
  updated_at: string
}

export interface StoryList {
  items: StoryCardItem[]
  coverage: Record<string, number>
  missing_tags: string[]
  tag_labels: Record<string, string>
}

export interface AnswerDraft {
  id: number
  kind: string
  title: string
  resume_id: number | null
  content: string
  target_seconds: number
  basis: Record<string, unknown>
  created_at: string
}

export interface SensitiveQuestion {
  key: string
  label: string
  question: string
  strategy: string
}

export interface SelfIntroOption {
  seconds: number
  label: string
  target_chars: number
}

export interface AnswerMeta {
  sensitive: SensitiveQuestion[]
  self_intro_options: SelfIntroOption[]
  tag_labels: Record<string, string>
}

export interface BandStat {
  label: string
  min_score: number
  count: number
  responded: number
  interviewed: number
  offers: number
  response_rate: number
  interview_rate: number
}

export interface MatchBandReport {
  resume_id: number | null
  total_records: number
  matched_records: number
  bands: BandStat[]
  recommendation: string
  confidence_note: string
}

// -------------------------------------------------------------------------- //
// 表达体检（M9）
// -------------------------------------------------------------------------- //

export interface FillerHit {
  word: string
  count: number
  weight: number
  strong: boolean
  weighted: number
}

export interface PaceWindow {
  start_ms: number
  end_ms: number
  chars: number
  cpm: number
  rms: number
}

export interface PauseSpan {
  start_ms: number
  duration_ms: number
  kind: 'normal' | 'long' | 'very_long'
  turn_index: number | null
  before_text: string
  after_text: string
  seconds: number
}

export interface TurnIssue {
  kind: string
  severity: 'warning' | 'info'
  at_ms: number
  excerpt: string
  detail: string
  turn_index: number | null
}

export interface ExpressionReport {
  id: number
  session_id: number | null
  recording_id: number | null
  overall: number
  summary: string
  has_audio: boolean
  frame_ms: number
  generated_at: string

  speaking_ms: number
  chars: number
  overall_cpm: number
  filler_total: number
  filler_weighted: number
  filler_per_kchar: number
  reliable: boolean
  fillers: FillerHit[]
  pauses: PauseSpan[]
  longest_pause: PauseSpan | null
  windows: PaceWindow[]
  pace_cv: number
  volume_cv: number
  tension: number
  issues: TurnIssue[]
  suggestions: string[]
  audio_url: string | null
}
