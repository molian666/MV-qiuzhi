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
import type { ProviderStatus, SystemSettings, Usage } from '../api/types'

/** 界面上的分组。只列用户真正会改的，技术性太强的（ASR 模型名之类）收在「更多」里。 */
const PRIMARY_KEYS = ['LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_API_KEY', 'DASHSCOPE_API_KEY', 'EXPORT_TOKEN']
const ADVANCED_KEYS = [
  'LLM_CHAT_MODEL',
  'LLM_REASONER_MODEL',
  'SPEECH_PROVIDER',
  'DASHSCOPE_REALTIME_ENDPOINT',
  'DASHSCOPE_ASR_MODEL',
  'ASR_PROVIDER',
  'DASHSCOPE_ASR_FILE_MODEL',
  'TRUST_ENV_PROXY',
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

  const renderField = (key: string) => {
    const item = settings?.settings[key]
    if (!item) {
      return null
    }
    const isLlmProvider = key === 'LLM_PROVIDER'
    return (
      <label key={key} className="block">
        <span className="text-xs font-medium text-slate-700">{key}</span>
        {item.is_secret ? (
          <input
            type="password"
            autoComplete="off"
            placeholder={item.configured ? `已配置（${item.masked}），留空表示不改` : '未配置'}
            value={draft[key] ?? ''}
            onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          />
        ) : isLlmProvider ? (
          <select
            value={draft[key] ?? item.value}
            onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          >
            <option value="mock">mock —— 不需要 Key，但题目与评分都是固定示例</option>
            <option value="openai">openai —— 真的调用模型（任意 OpenAI 兼容平台）</option>
            <option value="deepseek">deepseek —— 旧的等价写法，等同 openai</option>
          </select>
        ) : (
          <input
            type="text"
            autoComplete="off"
            value={draft[key] ?? item.value}
            onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          />
        )}
        {item.note && <span className="mt-0.5 block text-[10px] text-slate-400">{item.note}</span>}
      </label>
    )
  }

  const hasChanges = Object.keys(draft).length > 0

  return (
    <div className="space-y-4">
      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-slate-800">模型与密钥</h2>
          {providers?.llm && (
            <span
              className={
                providers.llm.provider === 'mock'
                  ? 'rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700'
                  : providers.llm.ready
                    ? 'rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700'
                    : 'rounded-full bg-rose-50 px-2 py-0.5 text-xs text-rose-700'
              }
            >
              当前：{providers.llm.label || providers.llm.provider}
              {providers.llm.provider === 'mock' ? '（示例内容）' : providers.llm.ready ? ' 就绪' : ' 未就绪'}
            </span>
          )}
        </div>

        {providers?.llm?.provider === 'mock' && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900">
            <strong className="font-medium">现在跑的是 Mock，所以题目和评分都是固定的示例。</strong>
            <br />
            模拟面试问的还是那份通用脚本（自我介绍、项目深挖……），不看你填的岗位 JD，
            也不看简历——所以会显得「没深度、没场景」。填上 LLM_BASE_URL 与 LLM_API_KEY、
            把上面切到 openai 之后，才会真的按岗位和简历出题。
          </div>
        )}

        {!settings ? (
          <p className="mt-3 text-sm text-slate-500">加载中…</p>
        ) : (
          <>
            <div className="mt-3 grid gap-3 md:grid-cols-2">{PRIMARY_KEYS.map(renderField)}</div>

            <button
              type="button"
              onClick={() => setShowAdvanced((value) => !value)}
              className="mt-3 text-xs text-slate-500 hover:underline"
            >
              {showAdvanced ? '收起高级项' : '高级项（模型名、语音、代理）'}
            </button>
            {showAdvanced && (
              <div className="mt-2 grid gap-3 border-t border-slate-100 pt-3 md:grid-cols-2">
                {ADVANCED_KEYS.map(renderField)}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving || !hasChanges}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white disabled:bg-slate-300"
              >
                {saving ? '保存中…' : '保存'}
              </button>
              <button
                type="button"
                onClick={() => setDraft({})}
                disabled={!hasChanges}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 disabled:text-slate-300"
              >
                撤销改动
              </button>
              <span className="text-[11px] text-slate-500">
                {settings.note} 密钥框留空表示保持不变。
              </span>
            </div>

            {savedNote && (
              <p className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs text-emerald-800">
                {savedNote}
              </p>
            )}
          </>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-base font-semibold text-slate-800">提供方</h2>
        {!providers ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {[
              { title: 'LLM（思考/出题/批改）', info: providers.llm },
              { title: '实时语音（听说）', info: providers.speech },
            ].map(({ title, info }) => (
              <div key={title} className="rounded-lg border border-slate-200 p-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium text-slate-700">{title}</h3>
                  <span
                    className={
                      info.ready
                        ? 'rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700'
                        : 'rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700'
                    }
                  >
                    {info.ready ? '就绪' : '未就绪'}
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-600">
                  提供方：{info.label ? `${info.provider} · ${info.label}` : info.provider}
                </p>
                {info.base_url && <p className="text-xs text-slate-500">接口地址：{info.base_url}</p>}
                {info.chat_model && <p className="text-xs text-slate-500">对话模型：{info.chat_model}</p>}
                {info.reasoner_model && <p className="text-xs text-slate-500">推理模型：{info.reasoner_model}</p>}
                {info.hint && <p className="mt-2 text-xs text-amber-700">{info.hint}</p>}
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">
          模型密钥写进后端 <code className="rounded bg-slate-100 px-1">backend/.env</code>，
          <strong className="font-medium">不入库、不出现在导出里</strong>，前端也读不到完整值。
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-base font-semibold text-slate-800">
          LLM 用量（近 {usage?.window_days ?? 7} 天）
        </h2>
        {!usage ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : usage.items.length === 0 ? (
          <p className="text-sm text-slate-500">
            这段时间还没有调用记录。切到真实模型跑一场面试，这里就会出现用量。
          </p>
        ) : (
          <>
            <p className="mb-3 text-xs text-slate-500">
              总调用 {usage.total_calls} 次 · 失败 {usage.failed_calls} 次 · {usage.note}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">任务</th>
                    <th className="px-3 py-2 font-medium">模型</th>
                    <th className="px-3 py-2 font-medium">调用</th>
                    <th className="px-3 py-2 font-medium">输入 token</th>
                    <th className="px-3 py-2 font-medium">输出 token</th>
                    <th className="px-3 py-2 font-medium">平均延迟</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.items.map((item) => (
                    <tr key={`${item.task}-${item.model}`} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-slate-800">{item.task}</td>
                      <td className="px-3 py-2 text-slate-600">{item.model}</td>
                      <td className="px-3 py-2 text-slate-600">{item.calls}</td>
                      <td className="px-3 py-2 text-slate-600">{item.prompt_tokens}</td>
                      <td className="px-3 py-2 text-slate-600">{item.completion_tokens}</td>
                      <td className="px-3 py-2 text-slate-600">{item.avg_latency_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
