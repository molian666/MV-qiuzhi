/**
 * 打断判定的单元测试。
 *
 * 跑法：`node --test frontend/src/audio/bargein.test.mjs`
 *
 * 这块原来是一个固定常量（`BARGE_IN_LEVEL = 0.02`），换到手机上会退化成两种坏情况。
 * 下面的用例就是围绕这两种情况写的：
 * - **安静环境**：不能被一点电流声触发（阈值有绝对下限）；
 * - **外放环境**（手机扬声器离麦克风 1cm）：面试官自己的声音不能触发打断。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  BargeInDetector,
  DEFAULT_COOLDOWN_MS,
  MAX_THRESHOLD,
  MIN_THRESHOLD,
  NOISE_WINDOW,
  noiseFloorOf,
  thresholdFor,
} from './bargein.ts'

test('底噪取分位数，不会被一个离群大值带偏', () => {
  const quiet = Array.from({ length: 20 }, () => 0.004)
  assert.ok(Math.abs(noiseFloorOf(quiet) - 0.004) < 1e-9)

  // 中间混进说话声：分位数应仍然贴近安静那批
  const mixed = [...quiet, ...Array.from({ length: 6 }, () => 0.4)]
  const floor = noiseFloorOf(mixed)
  assert.ok(floor < 0.05, `离群值把底噪抬太高了：${floor}`)
})

test('底噪对空数组/异常值安全', () => {
  assert.equal(noiseFloorOf([]), 0)
  assert.equal(noiseFloorOf([Number.NaN, -1, Number.POSITIVE_INFINITY]), 0)
  assert.ok(Number.isFinite(noiseFloorOf([0.01, Number.NaN])))
})

test('阈值被夹在绝对上下限之间', () => {
  // 极安静：不能被电流声触发
  assert.equal(thresholdFor(0), MIN_THRESHOLD)
  assert.ok(thresholdFor(0.0001) >= MIN_THRESHOLD)
  // 极吵：说话仍要能过
  assert.equal(thresholdFor(10), MAX_THRESHOLD)
  assert.ok(thresholdFor(0.2) <= MAX_THRESHOLD)
  // 异常底噪
  for (const floor of [Number.NaN, -5, Number.POSITIVE_INFINITY]) {
    const threshold = thresholdFor(floor)
    assert.ok(Number.isFinite(threshold), `floor=${floor} 得到 ${threshold}`)
    assert.ok(threshold >= MIN_THRESHOLD && threshold <= MAX_THRESHOLD)
  }
})

test('安静房间里一点噪声不该触发打断', () => {
  const detector = new BargeInDetector()
  for (let i = 0; i < NOISE_WINDOW; i += 1) {
    detector.observe(0.003)
  }
  // 底噪 0.003 -> 倍数后仍低于绝对下限，所以阈值是 MIN_THRESHOLD
  assert.ok(detector.threshold >= MIN_THRESHOLD)
  assert.equal(detector.shouldTrigger(0.006, 1000), false)
})

test('手机外放时面试官的声音不会把自己打断', () => {
  // 场景：面试官正在说话，麦克风收到约 0.05 的回采电平
  const detector = new BargeInDetector()
  for (let i = 0; i < NOISE_WINDOW; i += 1) {
    detector.observe(0.05)
  }
  // 阈值随底噪抬高（0.05 × 3.2 = 0.16，未到上限）
  const threshold = detector.threshold
  assert.ok(threshold > 0.05, `阈值没有随底噪抬高：${threshold}`)
  // 同样电平的回采不该被当成用户开口
  assert.equal(detector.shouldTrigger(0.05, 1000), false)
  // 用户真开口（明显高出一截）才触发
  assert.equal(detector.shouldTrigger(Math.min(0.18, threshold + 0.01), 1000), true)
})

test('触发后进入冷却，避免一次开口被反复计', () => {
  const detector = new BargeInDetector({ windowSize: 4 })
  for (let i = 0; i < 4; i += 1) {
    detector.observe(0.002)
  }
  const loud = 0.5

  assert.equal(detector.shouldTrigger(loud, 10_000), true)
  detector.markTriggered(10_000)
  assert.equal(detector.inCooldown(10_000), true)
  // 冷却期内同一句话不再重复触发
  assert.equal(detector.shouldTrigger(loud, 10_000 + DEFAULT_COOLDOWN_MS - 1), false)
  // 冷却结束，可以再触发
  assert.equal(detector.shouldTrigger(loud, 10_000 + DEFAULT_COOLDOWN_MS), true)
})

test('冷却时长可配置，0 表示不冷却', () => {
  const detector = new BargeInDetector({ cooldownMs: 0, windowSize: 4 })
  for (let i = 0; i < 4; i += 1) {
    detector.observe(0.002)
  }
  detector.markTriggered(500)
  assert.equal(detector.inCooldown(500), false)
  assert.equal(detector.shouldTrigger(0.5, 500), true)
})

test('shouldTrigger 对异常输入一律返回 false，不抛错', () => {
  const detector = new BargeInDetector()
  // 非有限电平被直接拒绝：Infinity 看起来"肯定超过阈值"，
  // 但它只可能来自坏掉的采集回调，宁可漏判一次也不要让它去打断面试官
  for (const level of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
    assert.equal(detector.shouldTrigger(level, 0), false, `level=${level}`)
  }
  // now 非有限值时 inCooldown 判为 false，不该崩
  assert.doesNotThrow(() => detector.shouldTrigger(0.5, Number.NaN))
})

test('窗口只保留最近 N 个样本（否则换环境后阈值会僵住）', () => {
  const detector = new BargeInDetector({ windowSize: 10 })
  for (let i = 0; i < 10; i += 1) {
    detector.observe(0.5)
  }
  const noisy = detector.noiseFloor
  // 换成安静环境：旧样本必须被挤出去
  for (let i = 0; i < 10; i += 1) {
    detector.observe(0.002)
  }
  assert.ok(detector.noiseFloor < noisy, '窗口没有淘汰旧样本')
  assert.ok(detector.noiseFloor <= 0.01)
})

test('reset 清空底噪与冷却状态', () => {
  const detector = new BargeInDetector({ windowSize: 4 })
  for (let i = 0; i < 4; i += 1) {
    detector.observe(0.3)
  }
  detector.markTriggered(1000)
  detector.reset()
  assert.equal(detector.noiseFloor, 0)
  assert.equal(detector.inCooldown(1000), false)
  assert.equal(detector.lastObservedLevel, 0)
})

test('observe 忽略非法电平（不能让 NaN 污染分位数）', () => {
  const detector = new BargeInDetector({ windowSize: 5 })
  detector.observe(0.01)
  detector.observe(Number.NaN)
  detector.observe(-1)
  detector.observe(Number.POSITIVE_INFINITY)
  assert.equal(detector.noiseFloor, 0.01)
})

test('阈值下限必须低于正常说话电平，否则永远触发不了', () => {
  // 这条是防"以后有人把 MIN_THRESHOLD 调大"：正常说话大约 0.1 上下
  assert.ok(MIN_THRESHOLD < 0.05, `下限太高，可能永远触发不了：${MIN_THRESHOLD}`)
  assert.ok(MAX_THRESHOLD > MIN_THRESHOLD)
})
