/**
 * 「思考停顿」的单元测试。
 *
 * 用 Node 自带的 `node --test`（Node 18+ 起内置，**不引新依赖**），
 * 与项目"优先手写 + 可测、不加重量级依赖"的纪律一致。
 *
 * 跑法：`node --test frontend/src/audio/thinking.test.mjs`
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { MAX_THINKING_MS, MIN_THINKING_MS, thinkingPauseMs } from './thinking.ts'

test('开场（没有上一轮回答）用最短停顿', () => {
  assert.equal(thinkingPauseMs(''), MIN_THINKING_MS)
  assert.equal(thinkingPauseMs('   '), MIN_THINKING_MS)
})

test('停顿始终落在区间内', () => {
  for (const text of ['', '短答', 'x'.repeat(500), 'y'.repeat(5000)]) {
    for (const r of [0, 0.5, 0.999, 1]) {
      const value = thinkingPauseMs(text, { random: () => r })
      assert.ok(
        value >= MIN_THINKING_MS && value <= MAX_THINKING_MS,
        `超出区间：text=${text.length} random=${r} -> ${value}`,
      )
    }
  }
})

test('长回答之后的停顿不短于短回答', () => {
  const short = thinkingPauseMs('很短', { random: () => 0 })
  const long = thinkingPauseMs('x'.repeat(300), { random: () => 0 })
  assert.ok(long >= short, `长回答停顿更短了：${long} < ${short}`)
})

test('停顿上限刻意很小——模型出题本身已经要好几秒', () => {
  // 这条是防"以后有人把它调大"：非流式出题 2-5 秒，再叠明显停顿会让人以为卡住
  assert.ok(MAX_THINKING_MS <= 800, `上限太大了：${MAX_THINKING_MS}`)
})

test('随机会带来差异，但不是决定性的', () => {
  const low = thinkingPauseMs('x'.repeat(100), { random: () => 0 })
  const high = thinkingPauseMs('x'.repeat(100), { random: () => 1 })
  assert.ok(high >= low)
  // 差异不该大到"有时秒回、有时像卡住"
  assert.ok(high - low < MAX_THINKING_MS - MIN_THINKING_MS, '随机影响过大')
})

test('随机源返回异常值时不会产生 NaN', () => {
  for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, -5, 99]) {
    const value = thinkingPauseMs('测试', { random: () => bad })
    assert.ok(Number.isFinite(value), `得到非有限值：${value}`)
    assert.ok(value >= MIN_THINKING_MS && value <= MAX_THINKING_MS)
  }
})
