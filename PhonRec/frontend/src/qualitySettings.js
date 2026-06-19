export const QUALITY_LEVELS = [
  { value: 'low', label: '宽松' },
  { value: 'medium', label: '标准' },
  { value: 'high', label: '严格' },
];

export const QUALITY_ITEMS = [
  { key: 'speech', label: '有效语音', description: '检测录音长度、语音占比与漏录' },
  { key: 'volume', label: '有效音量', description: '依据有效语音判断音量过小或过大' },
  { key: 'clipping', label: '音频截断', description: '检测削波与数字满幅失真' },
  { key: 'noise', label: '背景噪声', description: '根据信噪比判断环境噪声' },
  { key: 'creak', label: '嘎裂声', description: '提示可能的嘎裂声，仅用于复核' },
  { key: 'dc_offset', label: '直流偏移', description: '检测录音设备产生的波形偏移' },
];

export const createDefaultQualityRules = (enabled = true) => Object.fromEntries(
  QUALITY_ITEMS.map(({ key }) => [key, { enabled, level: 'medium' }])
);

export const normalizeQualityRules = (rules, legacyEnabled = true) => {
  const defaults = createDefaultQualityRules(legacyEnabled);
  if (!rules || typeof rules !== 'object') return defaults;
  return Object.fromEntries(QUALITY_ITEMS.map(({ key }) => {
    const source = rules[key];
    const level = ['low', 'medium', 'high'].includes(source?.level) ? source.level : 'medium';
    return [key, { enabled: source ? source.enabled !== false : defaults[key].enabled, level }];
  }));
};

export const hasEnabledQualityRule = (rules) => QUALITY_ITEMS.some(({ key }) => rules[key]?.enabled);
