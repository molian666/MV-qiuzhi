/** 系统与用量：模型提供方状态 + LLM 调用统计（成本可见）。 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ProviderStatus, Usage } from '../api/types'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function System() {
  const [providers, setProviders] = useState<ProviderStatus | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api.providers(), api.usage()])
      .then(([providerStatus, usageStats]) => {
        setProviders(providerStatus)
        setUsage(usageStats)
      })
      .catch((err: unknown) => setError(errorMessage(err)))
  }, [])

  return (
    <div className="space-y-4">
      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

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
                <p className="mt-2 text-sm text-slate-600">提供方：{info.provider}</p>
                {info.chat_model && <p className="text-xs text-slate-500">对话模型：{info.chat_model}</p>}
                {info.reasoner_model && <p className="text-xs text-slate-500">推理模型：{info.reasoner_model}</p>}
                {info.hint && <p className="mt-2 text-xs text-amber-700">{info.hint}</p>}
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">
          模型密钥只存在后端 <code className="rounded bg-slate-100 px-1">backend/.env</code> 里，前端不接触、不入库。
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-base font-semibold text-slate-800">
          LLM 用量（近 {usage?.window_days ?? 7} 天）
        </h2>
        {!usage ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : usage.items.length === 0 ? (
          <p className="text-sm text-slate-500">这段时间还没有调用记录。</p>
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
