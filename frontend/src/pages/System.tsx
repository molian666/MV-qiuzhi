/** 系统与用量：模型配置（可改，写进 backend/.env）、提供方状态、LLM 调用统计。
 *
 * 这一页解决一个很实际的问题：**之前只能手改 backend/.env 才能接上模型**，
 * 而"当前到底跑的是 Mock 还是真模型"在界面上看不出来——于是模拟面试
 * 问出来的题显得没深度、没场景，却不知道原因在哪。
 *
 * 两处刻意的设计：
 *
 * 1. **密钥不回显**：读回来的只有「配了没有」和末四位。后端也不会把完整密钥
 *    发给前端，界面里更没有能显示它的地方。
 * 2. **写进 `.env` 而不是数据库**：这是项目原本的约定
 *    （`ProviderConfig.secret_ref` 注释写着「密钥统一放 backend/.env，不存明文」）。
 *    好处是导出、备份、直接看库都拿不到密钥。
 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { LlmTest, ProviderStatus, SystemSettings, Usage } from '../api/types'

/** 模型服务的字段。顺序就是配置的顺序：先选平台地址，再填 Key，最后确认模型名。
 *
 *  **不写死任何厂商**：这四项填什么就调什么，DeepSeek / 百炼 / 智谱 / 硅基流动 /
 *  OpenRouter / 本地 Ollama 都是同一套字段。
 */
type FieldSpec = {
  key: string
  label: string
  placeholder: string
  hint: string
}

const MODEL_FIELDS: FieldSpec[] = [
  {
    key: 'LLM_BASE_URL',
    label: '接口地址',
    placeholder: 'https://api.deepseek.com',
    hint: '所选平台的 OpenAI 兼容地址，带不带结尾的 /v1 都可以',
  },
  {
    key: 'LLM_API_KEY',
    label: 'API Key',
    placeholder: 'sk-…',
    hint: '只写进 backend/.env，不入库、不导出、不回显完整值',
  },
  {
    key: 'LLM_CHAT_MODEL',
    label: '对话模型',
    placeholder: 'deepseek-chat',
    hint: '模拟面试的实时对话与追问用，延迟优先',
  },
  {
    key: 'LLM_REASONER_MODEL',
    label: '推理模型',
    placeholder: 'deepseek-reasoner',
    hint: '评分、纠错、简历诊断用，质量优先；留空则复用对话模型',
  },
]

/** 常见平台的接口地址，点一下直接填进「接口地址」。
 *
 *  **只填地址，不预设模型名**：地址是各平台长期稳定的公开端点，
 *  模型名却换得很勤（而且各家命名规则不一样），填错会让人以为是程序有问题。
 *  模型名见上面的占位提示，以平台自己的文档为准。
 */
const PLATFORMS: { label: string; url: string }[] = [
  { label: 'DeepSeek', url: 'https://api.deepseek.com' },
  { label: '阿里云百炼', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: '智谱', url: 'https://open.bigmodel.cn/api/paas/v4' },
  { label: '硅基流动', url: 'https://api.siliconflow.cn/v1' },
  { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  { label: '本地 Ollama', url: 'http://localhost:11434/v1' },
]

/** 「其他配置」：与模型无关的那些，收在折叠区里 */
const OTHER_KEYS = [
  'DASHSCOPE_API_KEY',
  'SPEECH_PROVIDER',
  'DASHSCOPE_REALTIME_ENDPOINT',
  'DASHSCOPE_ASR_MODEL',
  'ASR_PROVIDER',
  'DASHSCOPE_ASR_FILE_MODEL',
  'TRUST_ENV_PROXY',
  'EXPORT_TOKEN',
  // 旧名（只支持 DeepSeek 时的配置）：仍然可改、仍然生效，仅在新键为空时兜底
  'DEEPSEEK_API_KEY',
  'DEEPSEEK_BASE_URL',
  'DEEPSEEK_CHAT_MODEL',
  'DEEPSEEK_REASONER_MODEL',
]

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function System() {
  const [providers, setProviders] = useState<ProviderStatus | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedNote, setSavedNote] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<LlmTest | null>(null)
  const [error, setError] = useState('')

  const load = () => {
    Promise.all([api.providers(), api.usage(), api.systemSettings()])
      .then(([providerStatus, usageStats, config]) => {
        setProviders(providerStatus)
        setUsage(usageStats)
        setSettings(config)
        setDraft({})
      })
      .catch((err: unknown) => setError(errorMessage(err)))
  }

  useEffect(load, [])

  const save = async () => {
    // 只提交真正改过的：密钥框留空 = 不动它，否则会把已配的密钥清掉
    const changed: Record<string, string> = {}
    for (const [key, value] of Object.entries(draft)) {
      const current = settings?.settings[key]
      if (!current) {
        continue
      }
      if (current.is_secret) {
        if (value.trim()) {
          changed[key] = value
        }
      } else if (value !== current.value) {
        changed[key] = value
      }
    }

    if (Object.keys(changed).length === 0) {
      setSavedNote('没有改动。密钥框留空表示保持不变。')
      return
    }

    setSaving(true)
    setSavedNote('')
    setError('')
    try {
      const result = await api.updateSystemSettings(changed)
      setSettings({ settings: result.settings, note: result.note })
      setProviders(result.providers)
      setDraft({})
      setSavedNote(`已更新：${result.updated.join('、')}。${result.note}`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  /** 用**当前保存好的配置**真发一次请求。
   *
   *  测试失败的两种情况要分清：接口返回 200 且 `ok=false`（配置问题，
   *  有原始错误可看），和请求本身发不出去（网络/服务没起来）——后者
   *  在这里兜住，否则界面上什么都不显示。
   */
  const runTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.testLlm())
    } catch (err) {
      setTestResult({
        ok: false,
        reply: '',
        model: '',
        latency_ms: 0,
        error: errorMessage(err),
        hint: '请求没发出去——确认后端服务在运行。',
      })
    } finally {
      setTesting(false)
    }
  }

  /** 通用字段渲染（「其他配置」用）。标签用后端给的键名 + 说明。 */
  const renderField = (key: string) => {
    const item = settings?.settings[key]
    if (!item) {
      return null
    }
    return (
      <label key={key} className="block">
        <span className="font-mono text-[11px] text-ink-6">{key}</span>
        <input
          type={item.is_secret ? 'password' : 'text'}
          autoComplete="off"
          placeholder={item.is_secret && item.configured ? `已配置（${item.masked}），留空表示不改` : item.note}
          value={draft[key] ?? item.value}
          onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
          className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-xs placeholder:text-ink-3"
        />
      </label>
    )
  }

  /** 模型字段：中文标签，键名放小字，示例放占位符。 */
  const renderModelField = (field: FieldSpec) => {
    const item = settings?.settings[field.key]
    if (!item) {
      return null
    }
    return (
      <label key={field.key} className="block">
        <span className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium text-ink-7">{field.label}</span>
          <span className="font-mono text-[10px] text-ink-4">{field.key}</span>
        </span>
        <input
          type={item.is_secret ? 'password' : 'text'}
          autoComplete="off"
          placeholder={
            item.is_secret && item.configured
              ? `已配置（${item.masked}），留空表示不改`
              : field.placeholder
          }
          value={draft[field.key] ?? item.value}
          onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
          className="mt-1 w-full rounded-control border border-ink-2 px-2 py-1.5 text-xs placeholder:text-ink-3"
        />
        <span className="mt-0.5 block text-[10px] leading-relaxed text-ink-4">{field.hint}</span>
      </label>
    )
  }

  const hasChanges = Object.keys(draft).length > 0

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {error && (
        <p className="shrink-0 rounded-control bg-ng-soft px-3 py-2 text-sm text-ng">{error}</p>
      )}

      {/* 左右分栏：模型与密钥是"要填的东西"，提供方状态与用量是"看的东西"。
          竖着堆会一屏放不下，而这两类内容本来就不必抢同一块纵向空间。 */}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
        <section className="min-h-0 overflow-y-auto rounded-panel border border-ink-1 bg-surface p-4 xl:pr-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-ink-9">模型与密钥</h2>
          {providers?.llm && (
            <span
              className={
                providers.llm.provider === 'mock'
                  ? 'rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn'
                  : providers.llm.ready
                    ? 'rounded-full bg-ok-soft px-2 py-0.5 text-xs text-ok'
                    : 'rounded-full bg-ng-soft px-2 py-0.5 text-xs text-ng'
              }
            >
              当前：{providers.llm.label || providers.llm.provider}
              {providers.llm.provider === 'mock' ? '（示例内容）' : providers.llm.ready ? ' 就绪' : ' 未就绪'}
            </span>
          )}
        </div>

        {providers?.llm?.provider === 'mock' && (
          <div className="mt-2 rounded-control border border-warn bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">
            <strong className="font-medium">现在跑的是 Mock，所以题目和评分都是固定的示例。</strong>
            <br />
            模拟面试问的还是那份通用脚本（自我介绍、项目深挖……），不看你填的岗位 JD，
            也不看简历——所以会显得「没深度、没场景」。填上 LLM_BASE_URL 与 LLM_API_KEY、
            把上面切到 openai 之后，才会真的按岗位和简历出题。
          </div>
        )}

        {!settings ? (
          <p className="mt-3 text-sm text-ink-6">加载中…</p>
        ) : (
          <>
            {/* 提供方：就两个选项，用按钮组比下拉更直接 */}
            <div className="mt-3 flex items-center gap-1 self-start rounded-card border border-ink-1 bg-paper p-0.5">
              {[
                { key: 'mock', label: 'mock', hint: '不联网，内置示例' },
                { key: 'openai', label: 'openai', hint: '真调模型' },
              ].map((item) => {
                const value = draft.LLM_PROVIDER ?? settings.settings.LLM_PROVIDER?.value ?? 'mock'
                const active = value === item.key
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setDraft({ ...draft, LLM_PROVIDER: item.key })}
                    title={item.hint}
                    className={
                      active
                        ? 'rounded-control bg-ink-9 px-3 py-1 font-mono text-[11px] text-surface'
                        : 'rounded-control px-3 py-1 font-mono text-[11px] text-ink-6 transition-colors hover:bg-surface'
                    }
                  >
                    {item.label}
                  </button>
                )
              })}
              <span className="px-2 text-[10px] text-ink-4">
                填好下面四项后切到 openai
              </span>
            </div>

            {/* 常用平台的地址，点一下填进去 */}
            <div className="mt-3">
              <div className="mb-1 text-[10px] text-ink-4">常用平台（点一下填地址）</div>
              <div className="flex flex-wrap gap-1">
                {PLATFORMS.map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => setDraft({ ...draft, LLM_BASE_URL: item.url })}
                    title={item.url}
                    className="rounded-control border border-ink-1 bg-paper px-2 py-1 text-[10px] text-ink-6 transition-colors hover:border-ink-2 hover:bg-surface"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-3 space-y-3">
              {MODEL_FIELDS.map(renderModelField)}
            </div>

            <button
              type="button"
              onClick={() => setShowAdvanced((value) => !value)}
              className="mt-3 text-xs text-ink-6 hover:underline"
            >
              {showAdvanced ? '收起其他配置' : '其他配置（语音、导出令牌、旧键）'}
            </button>
            {showAdvanced && (
              <div className="mt-2 grid gap-3 border-t border-ink-1 pt-3 md:grid-cols-2">
                {OTHER_KEYS.map(renderField)}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving || !hasChanges}
                className="rounded-control bg-ink-9 px-3 py-1.5 text-xs text-surface disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              >
                {saving ? '保存中…' : '保存'}
              </button>
              <button
                type="button"
                onClick={() => setDraft({})}
                disabled={!hasChanges}
                className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-xs text-ink-6 disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              >
                撤销改动
              </button>
              {/* 测试连接：唯一能确定"到底通没通"的动作。
                  保存只验证白名单与格式，地址写错、Key 过期、模型名不存在
                  都要真发一次请求才会暴露。 */}
              <button
                type="button"
                onClick={() => void runTest()}
                disabled={testing || hasChanges}
                title={hasChanges ? '先保存改动，再测试' : '用当前配置发一次真实请求'}
                className="rounded-control border border-ink-2 bg-surface px-3 py-1.5 text-xs text-ink-7 transition-colors hover:bg-paper disabled:border-ink-1 disabled:bg-sunken disabled:text-ink-6"
              >
                {testing ? '测试中…' : '测试连接'}
              </button>
              <span className="text-[11px] text-ink-6">
                {settings.note} 密钥框留空表示保持不变。
              </span>
            </div>

            {savedNote && (
              <p className="mt-2 rounded-control border border-ok bg-ok-soft px-3 py-1.5 text-xs text-ok">
                {savedNote}
              </p>
            )}

            {testResult && (
              <div
                className={
                  testResult.ok
                    ? 'mt-2 rounded-control border border-ok bg-ok-soft px-3 py-2 text-xs leading-relaxed text-ok'
                    : 'mt-2 rounded-control border border-ng bg-ng-soft px-3 py-2 text-xs leading-relaxed text-ng'
                }
              >
                {testResult.ok ? (
                  <>
                    <div className="font-medium">
                      通了：{testResult.model} 在 {testResult.latency_ms} ms 内回话
                    </div>
                    <div className="mt-1 text-ink-6">模型说：{testResult.reply}</div>
                  </>
                ) : (
                  <>
                    <div className="font-medium">没通</div>
                    {/* 原始错误原样给出：401 和"模型不存在"是完全不同的修法 */}
                    <div className="mt-1 font-mono text-[11px] break-all">{testResult.error}</div>
                    {testResult.hint && (
                      <div className="mt-1.5 text-ink-7">{testResult.hint}</div>
                    )}
                  </>
                )}
              </div>
            )}
          </>
        )}
      </section>

        {/* 右栏：看的两块。各自不长，合起来滚动一次就够 */}
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
          <section className="rounded-panel border border-ink-1 bg-surface p-4">
            <h2 className="mb-3 text-base font-semibold text-ink-9">提供方</h2>
        {!providers ? (
          <p className="text-sm text-ink-6">加载中…</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {[
              { title: 'LLM（思考/出题/批改）', info: providers.llm },
              { title: '实时语音（听说）', info: providers.speech },
            ].map(({ title, info }) => (
              <div key={title} className="rounded-card border border-ink-1 p-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium text-ink-7">{title}</h3>
                  <span
                    className={
                      info.ready
                        ? 'rounded-full bg-ok-soft px-2 py-0.5 text-xs text-ok'
                        : 'rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn'
                    }
                  >
                    {info.ready ? '就绪' : '未就绪'}
                  </span>
                </div>
                <p className="mt-2 text-sm text-ink-6">
                  提供方：{info.label ? `${info.provider} · ${info.label}` : info.provider}
                </p>
                {info.base_url && <p className="text-xs text-ink-6">接口地址：{info.base_url}</p>}
                {info.chat_model && <p className="text-xs text-ink-6">对话模型：{info.chat_model}</p>}
                {info.reasoner_model && <p className="text-xs text-ink-6">推理模型：{info.reasoner_model}</p>}
                {info.hint && <p className="mt-2 text-xs text-warn">{info.hint}</p>}
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-ink-6">
          模型密钥写进后端 <code className="rounded-control bg-sunken px-1">backend/.env</code>，
          <strong className="font-medium">不入库、不出现在导出里</strong>，前端也读不到完整值。
        </p>
      </section>

      <section className="rounded-panel border border-ink-1 bg-surface p-4">
        <h2 className="mb-3 text-base font-semibold text-ink-9">
          LLM 用量（近 {usage?.window_days ?? 7} 天）
        </h2>
        {!usage ? (
          <p className="text-sm text-ink-6">加载中…</p>
        ) : usage.items.length === 0 ? (
          <p className="text-sm text-ink-6">
            这段时间还没有调用记录。切到真实模型跑一场面试，这里就会出现用量。
          </p>
        ) : (
          <>
            <p className="mb-3 flex flex-wrap items-baseline gap-x-1.5 text-xs text-ink-6">
              <span>
                总调用 <span className="font-mono text-ink-7">{usage.total_calls}</span> 次
              </span>
              <span className="text-ink-3">|</span>
              <span>
                失败 <span className="font-mono text-ink-7">{usage.failed_calls}</span> 次
              </span>
              <span className="text-ink-3">|</span>
              <span>{usage.note}</span>
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-paper text-left text-xs text-ink-6">
                  <tr>
                    <th className="px-3 py-2 font-medium">任务</th>
                    <th className="px-3 py-2 font-medium">模型</th>
                    <th className="px-3 py-2 text-right font-medium">调用</th>
                    <th className="px-3 py-2 text-right font-medium">输入 token</th>
                    <th className="px-3 py-2 text-right font-medium">输出 token</th>
                    <th className="px-3 py-2 text-right font-medium">平均延迟</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.items.map((item) => (
                    <tr key={`${item.task}-${item.model}`} className="border-t border-ink-1">
                      <td className="px-3 py-2 text-ink-9">{item.task}</td>
                      <td className="px-3 py-2 font-mono text-xs text-ink-6">{item.model}</td>
                      <td className="px-3 py-2 text-right font-mono text-ink-6">{item.calls}</td>
                      <td className="px-3 py-2 text-right font-mono text-ink-6">
                        {item.prompt_tokens}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-ink-6">
                        {item.completion_tokens}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-ink-6">
                        {item.avg_latency_ms} ms
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
          </section>
        </div>
      </div>
    </div>
  )
}
