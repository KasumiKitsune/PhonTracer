import { describe, expect, it } from 'vitest';
import { createVadEngine } from './vadEngine.js';

describe('自适应 VAD', () => {
  it('忽略短促噪声，并在有效语音后的静音期结束', () => {
    const vad = createVadEngine({ minSpeechMs: 200, trailingSilenceMs: 600 });
    vad.reset(0);
    expect(vad.process(0.03, 100).event).toBe(null);
    expect(vad.process(0.001, 180).event).toBe(null);
    vad.process(0.04, 300);
    expect(vad.process(0.04, 520).event).toBe('speech-start');
    vad.process(0.001, 700);
    expect(vad.process(0.001, 1310).event).toBe('speech-end');
  });

  it('在长时间没有语音时给出超时事件', () => {
    const vad = createVadEngine({ noSpeechTimeoutMs: 1000 });
    vad.reset(0);
    vad.process(0.001, 500);
    expect(vad.process(0.001, 1001).event).toBe('no-speech-timeout');
  });

  it('使用退出滞回，避免阈值附近反复跳变', () => {
    const vad = createVadEngine({ minSpeechMs: 100, trailingSilenceMs: 500 });
    vad.reset(0);
    vad.process(0.03, 10);
    vad.process(0.03, 120);
    expect(vad.process(0.004, 200).state).toBe('speaking');
  });
});
