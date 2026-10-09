/**
 * 断线重连退避的单元测试。
 *
 * 用 Node 自带的 `node --test`（与 `audio/thinking.test.mjs` 同一套跑法）：
 * `node --test frontend/src/api/backoff.test.mjs`
 *
 * 这些断言针对的是**真实会伤到用户的几种退化**：
 * - 第一次重试太慢 → 用户以为程序死了；
 * - 退避不封顶 → 长时间面试里堆出成百上千次重试；
 * - 异常输入算出 NaN → `setTimeout(fn, NaN)` 变成 0ms，退化成忙等循环；
 * - 没有抖动 → 多设备同时重连"齐步走"。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  BASE_DELAY_MS,
  MAX_DELAY_MS,
  MAX_RECONNECT_ATTEMPTS,
  ReconnectPlan,
  canRetry,
  reconnectDelayMs,
} from './backoff.ts'

const noJitter = { random: () => 0 }

test('第一次重试很快，之后逐次变长', () => {
  const delays = [1, 2, 3, 4].map((attempt) => reconnectDelayMs(attempt, noJitter))
  assert.equal(delays[0], BASE_DELAY_MS)
  for (let i = 1; i < delays.length; i += 1) {
    assert.ok(delays[i] > delays[i - 1], `第 ${i + 1} 次没有更长：${delays[i - 1]} -> ${delays[i]}`)
  }
  // 第一次必须足够快，否则用户会以为卡死
  assert.ok(delays[0] <= 1000, `首次重试太慢：${delays[0]}ms`)
})

test('延迟封顶，不会无限增长', () => {
  for (const attempt of [5, 10, 50, 1000]) {
    const delay = reconnectDelayMs(attempt, noJitter)
    assert.ok(delay <= MAX_DELAY_MS, `第 ${attempt} 次的延迟越界：${delay}`)
  }
})

test('异常输入不会产生 NaN / 负数 / Infinity', () => {
  const bad = [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 0, -3, 1.7]
  for (const attempt of bad) {
    const delay = reconnectDelayMs(attempt, noJitter)
    assert.ok(Number.isFinite(delay), `attempt=${attempt} 得到非有限值：${delay}`)
    assert.ok(delay > 0, `attempt=${attempt} 得到非正延迟：${delay}`)
    assert.ok(delay <= MAX_DELAY_MS)
  }
})

test('随机源异常时也不会算出 NaN', () => {
  for (const value of [Number.NaN, Number.POSITIVE_INFINITY, -1, 2]) {
    const delay = reconnectDelayMs(3, { random: () => value })
    assert.ok(Number.isFinite(delay), `random=${value} 得到 ${delay}`)
    assert.ok(delay > 0 && delay <= MAX_DELAY_MS)
  }
})

test('抖动带来差异，但不会突破上限', () => {
  const low = reconnectDelayMs(3, { random: () => 0 })
  const high = reconnectDelayMs(3, { random: () => 1 })
  assert.ok(high > low, '抖动没有起作用')
  assert.ok(high <= MAX_DELAY_MS, `抖动把延迟顶出上限：${high}`)
})

test('抖动幅度可配置，0 抖动时稳定可预期', () => {
  const a = reconnectDelayMs(3, { jitterRatio: 0 })
  const b = reconnectDelayMs(3, { jitterRatio: 0 })
  assert.equal(a, b)
  assert.equal(a, BASE_DELAY_MS * 4)
})

test('canRetry 在达到上限后为 false', () => {
  assert.equal(canRetry(1), true)
  assert.equal(canRetry(MAX_RECONNECT_ATTEMPTS), true)
  assert.equal(canRetry(MAX_RECONNECT_ATTEMPTS + 1), false)
  assert.equal(canRetry(Number.NaN), false)
})

test('ReconnectPlan：递增、到上限后放弃、reset 后可重来', () => {
  const plan = new ReconnectPlan(noJitter)
  assert.equal(plan.attempt, 0)

  const delays = []
  for (let i = 0; i < MAX_RECONNECT_ATTEMPTS; i += 1) {
    const delay = plan.next()
    assert.notEqual(delay, null, `第 ${i + 1} 次就放弃了`)
    delays.push(delay)
  }
  assert.equal(plan.attempt, MAX_RECONNECT_ATTEMPTS)
  // 用尽之后再问一次还是 null（幂等，不能"复活"）
  assert.equal(plan.next(), null)
  assert.equal(plan.next(), null)
  assert.equal(plan.exhausted, true)

  // 延迟单调不减
  for (let i = 1; i < delays.length; i += 1) {
    assert.ok(delays[i] >= delays[i - 1])
  }

  plan.reset()
  assert.equal(plan.attempt, 0)
  assert.equal(plan.exhausted, false)
  assert.equal(plan.next(), BASE_DELAY_MS)
})

test('ReconnectPlan 第一次的延迟与纯函数一致（避免两处逻辑漂移）', () => {
  const plan = new ReconnectPlan(noJitter)
  assert.equal(plan.next(), reconnectDelayMs(1, noJitter))
})
