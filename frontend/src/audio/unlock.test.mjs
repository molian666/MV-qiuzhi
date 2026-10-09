/**
 * 音频解锁的单元测试。
 *
 * 跑法：`node --test frontend/src/audio/unlock.test.mjs`
 *
 * 这条链路要钉住的是**"在用户手势之外做了本该公司在手势里做的事"**这个错误：
 * iOS 上 `AudioContext` 与 `speechSynthesis` 都只有在手势调用栈里才生效，
 * 失败时**不抛异常**，只是没声音——所以只能靠测试守住行为，不能靠报错。
 *
 * Node 里没有真的 `AudioContext` / `speechSynthesis`，所以这里全部注入假实现。
 * 这正好也是要测的部分：真实浏览器里"到底创建了几个 context""suspended 有没有被 resume"。
 */

import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'

import { resetUnlockState, unlockAudio } from './unlock.ts'

/** 假的 AudioContext：只记录 resume 是否被调用 */
class FakeAudioContext {
  static instances = 0
  static resumed = 0
  state = 'suspended'

  constructor() {
    FakeAudioContext.instances += 1
  }

  resume() {
    FakeAudioContext.resumed += 1
    this.state = 'running'
    return Promise.resolve()
  }
}

/** 假的 speechSynthesis：记录被 speak 的文本 */
class FakeSynth {
  spoken = []
  speak(utterance) {
    this.spoken.push(utterance.text)
  }
  cancel() {}
}

class FakeUtterance {
  constructor(text) {
    this.text = text
  }
}

beforeEach(() => {
  resetUnlockState()
  FakeAudioContext.instances = 0
  FakeAudioContext.resumed = 0
})

function deps(synth = new FakeSynth()) {
  return {
    audioContextCtor: FakeAudioContext,
    speechSynthesis: synth,
    utteranceCtor: FakeUtterance,
  }
}

test('首次调用会创建 AudioContext 并预热语音合成', () => {
  const synth = new FakeSynth()
  const result = unlockAudio(deps(synth))

  assert.equal(result.unlocked, true)
  assert.equal(FakeAudioContext.instances, 1)
  // 空 utterance：只为把 iOS 的合成器"点亮"，不能读出内容
  assert.deepEqual(synth.spoken, [''])
  assert.ok(result.context)
})

test('重复调用是幂等的：不再新建 context、不再重复预热', () => {
  const synth = new FakeSynth()
  const first = unlockAudio(deps(synth))
  const second = unlockAudio(deps(synth))
  const third = unlockAudio(deps(synth))

  assert.equal(first.unlocked, true)
  assert.equal(second.unlocked, false)
  assert.equal(third.unlocked, false)
  assert.equal(FakeAudioContext.instances, 1, '重复解锁创建了多个 AudioContext')
  assert.deepEqual(synth.spoken, [''], '重复预热了语音合成')
  // 同一个 context 被复用（iOS 上多开会互抢音频焦点）
  assert.equal(first.context, second.context)
  assert.equal(second.context, third.context)
})

test('context 处于 suspended 时会被 resume', async () => {
  unlockAudio(deps())
  assert.equal(FakeAudioContext.resumed, 1)
  // 再解锁一次不该重复 resume（已经是 running）
  resetUnlockState()
})

test('没有 AudioContext 时降级但不抛错，并说明原因', () => {
  const synth = new FakeSynth()
  const result = unlockAudio({
    audioContextCtor: null,
    speechSynthesis: synth,
    utteranceCtor: FakeUtterance,
  })
  assert.equal(result.context, null)
  assert.match(result.skipped, /AudioContext/)
  // 语音合成仍然要被预热——少一个能力不该拖垮另一个
  assert.deepEqual(synth.spoken, [''])
})

test('没有 speechSynthesis 时也降级，不抛错', () => {
  const result = unlockAudio({
    audioContextCtor: FakeAudioContext,
    speechSynthesis: null,
    utteranceCtor: null,
  })
  assert.equal(result.unlocked, true)
  assert.equal(FakeAudioContext.instances, 1)
  assert.match(result.skipped, /speechSynthesis/)
})

test('构造函数抛错时被吞掉并记录，不让整个开始流程崩', () => {
  class Exploding {
    constructor() {
      throw new Error('boom')
    }
  }
  const result = unlockAudio({
    audioContextCtor: Exploding,
    speechSynthesis: new FakeSynth(),
    utteranceCtor: FakeUtterance,
  })
  assert.equal(result.context, null)
  assert.match(result.skipped, /创建 AudioContext 失败/)
})

test('speak() 抛错时也不抛出去（iOS 上失败是静默的，我们必须容错）', () => {
  const angry = {
    speak() {
      throw new Error('not allowed')
    },
    cancel() {},
  }
  const result = unlockAudio({
    audioContextCtor: FakeAudioContext,
    speechSynthesis: angry,
    utteranceCtor: FakeUtterance,
  })
  assert.match(result.skipped, /预热 speechSynthesis 失败/)
})

test('resume() 返回 rejected promise 不会产生未捕获拒绝', async () => {
  class Rejecting extends FakeAudioContext {
    resume() {
      return Promise.reject(new Error('nope'))
    }
  }
  const result = unlockAudio({
    audioContextCtor: Rejecting,
    speechSynthesis: new FakeSynth(),
    utteranceCtor: FakeUtterance,
  })
  assert.ok(result.context)
  // 给微任务一个机会：如果没被 catch，Node 会在这里报 unhandled rejection
  await new Promise((resolve) => setTimeout(resolve, 0))
})
