import { describe, expect, it } from 'vitest';
import { bufferToWav, resampleAudio } from './audioUtils.js';

describe('录音重采样', () => {
  it('将 48kHz 音频转换为相同时长的 16kHz 音频', () => {
    const input = new Float32Array(48000).map((_, index) => Math.sin(index / 20));
    const output = resampleAudio(input, 48000, 16000);
    expect(output).toHaveLength(16000);
  });

  it('在采样率一致时复制数据而不复用原数组', () => {
    const input = new Float32Array([0, 0.5, -0.5]);
    const output = resampleAudio(input, 16000, 16000);
    expect(Array.from(output)).toEqual(Array.from(input));
    expect(output).not.toBe(input);
  });
});

describe('WAV 编码', () => {
  it('写入正确的 16kHz 单声道 PCM 头', async () => {
    const blob = bufferToWav(new Float32Array(1600), 16000);
    const arrayBuffer = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(blob);
    });
    const view = new DataView(arrayBuffer);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint16(34, true)).toBe(16);
    expect(blob.size).toBe(44 + 1600 * 2);
  });
});
