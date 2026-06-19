import { describe, expect, it } from 'vitest';
import { createDefaultQualityRules, hasEnabledQualityRule, normalizeQualityRules } from './qualitySettings.js';

describe('质量检测设置', () => {
  it('兼容旧版总开关', () => {
    expect(hasEnabledQualityRule(normalizeQualityRules(undefined, false))).toBe(false);
    expect(hasEnabledQualityRule(normalizeQualityRules(undefined, true))).toBe(true);
  });

  it('修正非法档位并保留多选状态', () => {
    const rules = normalizeQualityRules({ speech: { enabled: false, level: 'unknown' }, noise: { enabled: true, level: 'high' } });
    expect(rules.speech).toEqual({ enabled: false, level: 'medium' });
    expect(rules.noise).toEqual({ enabled: true, level: 'high' });
  });

  it('部分新版配置仍继承旧版关闭状态', () => {
    const rules = normalizeQualityRules({ noise: { enabled: true, level: 'high' } }, false);
    expect(rules.noise.enabled).toBe(true);
    expect(rules.volume.enabled).toBe(false);
  });

  it('允许全部不选', () => {
    expect(hasEnabledQualityRule(createDefaultQualityRules(false))).toBe(false);
  });
});
