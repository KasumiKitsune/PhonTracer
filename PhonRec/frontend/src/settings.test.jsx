import { describe, expect, it } from 'vitest';
import {
  buildRecordedItem,
  formatPlaybackTime,
  mergeAudioDevices,
  selectAvailableAudioSource,
} from './appUtils.js';

describe('播放时间格式化', () => {
  it('将秒数格式化为两位小数', () => {
    expect(formatPlaybackTime(0)).toBe('0.00s');
    expect(formatPlaybackTime(1.2345)).toBe('1.23s');
    expect(formatPlaybackTime(10.5)).toBe('10.50s');
  });

  it('异常值返回安全结果', () => {
    expect(formatPlaybackTime(NaN)).toBe('0.00s');
    expect(formatPlaybackTime(Infinity)).toBe('0.00s');
    expect(formatPlaybackTime(undefined)).toBe('0.00s');
  });
});

describe('录音设备合并', () => {
  it('使用浏览器 deviceId 表示麦克风，并保留原生回环设备', () => {
    const devices = mergeAudioDevices(
      [{ id: 'loopback:扬声器', name: '系统声音回环', is_loopback: true }],
      [{ kind: 'audioinput', deviceId: 'browser-device-id', label: 'USB 麦克风' }],
    );
    expect(devices.map((device) => device.id)).toEqual([
      'default',
      'browser-device-id',
      'loopback:扬声器',
    ]);
    expect(selectAvailableAudioSource('已拔出的设备', devices)).toBe('default');
  });
});

describe('录音条目元数据', () => {
  it('保留后端返回的日志字段', () => {
    const item = buildRecordedItem(
      { label: '妈', meta: { 拼音: 'ma1' } },
      {
        path: 'audio/spk/item.wav',
        quality: {},
        recorded_at: '2026-06-19T00:00:00Z',
        duration_ms: 650,
        sample_rate_hz: 16000,
        channels: 1,
        format: 'wav',
        source: 'USB 麦克风',
      },
    );
    expect(item.duration_ms).toBe(650);
    expect(item.sample_rate_hz).toBe(16000);
    expect(item.source).toBe('USB 麦克风');
  });
});
