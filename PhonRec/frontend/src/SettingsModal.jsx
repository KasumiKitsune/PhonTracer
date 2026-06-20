import React, { useState, useEffect } from 'react';

// --- Local inline SVGs ---
const GearIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: 'var(--color-accent)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

const MicIcon = ({ active }) => (
  <svg style={{ width: '16px', height: '16px', color: active ? 'var(--color-success)' : 'var(--text-muted)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
);

const FormatIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  </svg>
);

const SaveIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
    <polyline points="17 21 17 13 7 13 7 21" />
    <polyline points="7 3 7 8 15 8" />
  </svg>
);

const CheckIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const ResetIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
  </svg>
);

const ChartIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </svg>
);

const InfoIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);

const KeyboardIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="4" width="20" height="16" rx="2" ry="2" />
    <line x1="6" y1="8" x2="6.01" y2="8" />
    <line x1="10" y1="8" x2="10.01" y2="8" />
    <line x1="14" y1="8" x2="14.01" y2="8" />
    <line x1="18" y1="8" x2="18.01" y2="8" />
    <line x1="6" y1="12" x2="6.01" y2="12" />
    <line x1="18" y1="12" x2="18.01" y2="12" />
    <line x1="7" y1="16" x2="17" y2="16" />
    <line x1="10" y1="12" x2="14" y2="12" />
  </svg>
);

const NetworkIcon = () => (
  <svg style={{ width: '15px', height: '15px', color: 'var(--color-accent)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12.55a11 11 0 0 1 14.08 0" />
    <path d="M1.42 9a16 16 0 0 1 21.16 0" />
    <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
    <line x1="12" y1="20" x2="12.01" y2="20" strokeWidth="3" />
  </svg>
);

// --- CustomSelect Helper ---
const CustomSelect = ({ value, onChange, options, style, disabled, placement = 'bottom' }) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = React.useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedOption = options.find(opt => opt.value === value) || options[0];

  return (
    <div className={`custom-select-container ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`} ref={dropdownRef} style={style}>
      <div
        className={`custom-select-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
      >
        <span>{selectedOption ? selectedOption.label : ''}</span>
        <svg style={{ width: '14px', height: '14px', transform: isOpen ? 'rotate(180deg)' : 'rotate(0)', transition: 'transform 0.2s', marginLeft: '8px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
      {isOpen && !disabled && (
        <div className={`custom-select-dropdown ${placement === 'top' ? 'placement-top' : ''}`}>
          {options.map(opt => (
            <div
              key={opt.value}
              className={`custom-select-option ${value === opt.value ? 'selected' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setIsOpen(false);
              }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default function SettingsModal({
  isOpen,
  isClosing,
  onClose,
  settings,
  onUpdate,
  onReset,
  audioDevices,
  onRefreshDevices,
  micBlocked,
  onCheckPermission,
  onOpenPrivacy,
  onSelectFolder,
  onSelectWavExportFolder,
  isRecording,
  isProcessing,
  runtimeMode = 'engine',
  capabilities = { spectrogram: true, fullQuality: true },
  metaKeyOptions = []
}) {
  const [activeTab, setActiveTab] = useState('appearance');
  const [localProjName, setLocalProjName] = useState('');

  const THEME_COLORS = [
    { id: 'navy', label: '深蓝色', hex: '#0b2559' },
    { id: 'red', label: '红色', hex: '#ef4444' },
    { id: 'green', label: '绿色', hex: '#10b981' },
    { id: 'blue', label: '蓝色', hex: '#0284c7' },
    { id: 'purple', label: '紫色', hex: '#8b5cf6' }
  ];

  // Sync local default project name state when settings are loaded/changed
  useEffect(() => {
    if (settings?.default_project_name !== undefined) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalProjName(settings.default_project_name);
    }
  }, [settings?.default_project_name]);

  if (!isOpen) return null;

  const handleProjNameBlur = () => {
    // Sanitize filename on blur
    let sanitized = localProjName.replace(/[<>:"/\\|?*]/g, '').trim();
    if (sanitized) {
      if (!sanitized.endsWith('.teproj')) {
        sanitized += '.teproj';
      }
      setLocalProjName(sanitized);
      onUpdate({ default_project_name: sanitized });
    } else {
      // Revert if empty
      setLocalProjName(settings.default_project_name || 'PhonRec_Project.teproj');
    }
  };

  // Keyboard shortcut preset helper text
  const getShortcutPresetDetails = (preset) => {
    switch (preset) {
      case 'standard':
        return '【空格 Space】录音 / 停止  •  【左/右方向键 ← / →】切换字词  •  【R 键】播放录音';
      case 'left':
        return '【空格 Space】录音 / 停止  •  【A / D 键】切换字词  •  【S 键】播放录音';
      case 'right':
        return '【回车 Enter】录音 / 停止  •  【J / L 键】切换字词  •  【K 键】播放录音';
      case 'disabled':
        return '已禁用所有键盘快捷键';
      default:
        return '未知预设';
    }
  };

  const QUALITY_ITEMS = [
    { key: 'speech', label: '有效语音', description: '检测录音长度、语音占比与漏录' },
    { key: 'volume', label: '有效音量', description: '依据有效语音判断音量过小或过大' },
    { key: 'clipping', label: '音频截断', description: '检测削波与数字满幅失真' },
    { key: 'noise', label: '背景噪声', description: '根据信噪比判断环境噪声' },
    { key: 'creak', label: '嘎裂声', description: '提示可能的嘎裂声，仅用于复核' },
    { key: 'dc_offset', label: '直流偏移', description: '检测录音设备产生的波形偏移' },
  ];

  const isStandalone = runtimeMode === 'standalone';
  const isQualityAvailable = (key) => capabilities.fullQuality || key === 'volume' || key === 'clipping';
  const availableQualityItems = QUALITY_ITEMS.filter(({ key }) => isQualityAvailable(key));

  const handleToggleAllQuality = (enabled) => {
    const rules = { ...settings.quality_rules };
    availableQualityItems.forEach(({ key }) => {
      rules[key] = { ...rules[key], enabled };
    });
    onUpdate({ realtime_quality: enabled, quality_rules: rules });
  };

  const handleUpdateRule = (key, updates) => {
    const rules = {
      ...settings.quality_rules,
      [key]: { ...settings.quality_rules[key], ...updates }
    };
    const anyEnabled = availableQualityItems.some(({ key: k }) => rules[k]?.enabled);
    onUpdate({ realtime_quality: anyEnabled, quality_rules: rules });
  };

  const enabledRulesCount = availableQualityItems.filter(({ key }) => settings.quality_rules?.[key]?.enabled).length;
  const availableQualityEnabled = enabledRulesCount > 0;

  return (
    <div
      className={`modal-overlay settings-overlay ${isClosing ? 'is-closing' : 'is-open'}`}
      style={{ zIndex: 9999 }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-content settings-modal" role="dialog" aria-modal="true" aria-label="设置中心">
        <div className="modal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontWeight: 650, fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <GearIcon /> 设置中心
          </span>
          <button className="btn-icon" onClick={onClose} style={{ fontSize: '1.2rem' }}>
            &times;
          </button>
        </div>

        <div className="modal-body" style={{ padding: '1rem 1.25rem' }}>
          <div className="settings-tabs">
            <button
              className={`settings-tab-btn ${activeTab === 'appearance' ? 'active' : ''}`}
              onClick={() => setActiveTab('appearance')}
            >
              外观与显示
            </button>
            <button
              className={`settings-tab-btn ${activeTab === 'recording' ? 'active' : ''}`}
              onClick={() => setActiveTab('recording')}
            >
              录音与导航
            </button>
            <button
              className={`settings-tab-btn ${activeTab === 'quality' ? 'active' : ''}`}
              onClick={() => setActiveTab('quality')}
            >
              质量检测
            </button>
            <button
              className={`settings-tab-btn ${activeTab === 'storage' ? 'active' : ''}`}
              onClick={() => setActiveTab('storage')}
            >
              {isStandalone ? '保存与导出' : '工程与保存'}
            </button>
            <button
              className={`settings-tab-btn ${activeTab === 'device' ? 'active' : ''}`}
              onClick={() => setActiveTab('device')}
            >
              设备与系统
            </button>
          </div>

          <div className="settings-form">
            {/* Tab 1: Appearance & Display */}
            {activeTab === 'appearance' && (
              <div className="settings-panel-transition" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div className="settings-card">
                  <div className="settings-card-title">
                    <GearIcon /> 界面主题与动效
                  </div>
                  <div className="settings-grid">
                    <div className="form-group">
                      <label className="form-label">外观主题</label>
                      <CustomSelect
                        value={settings.theme}
                        onChange={(theme) => onUpdate({ theme })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'light', label: '浅色主题 (默认)' },
                          { value: 'dark', label: '深色主题' },
                          { value: 'system', label: '跟随系统' }
                        ]}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label" style={{ marginBottom: '0.5rem' }}>颜色主题</label>
                      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', height: '36px' }}>
                        {THEME_COLORS.map(color => (
                          <button
                            key={color.id}
                            type="button"
                            className={`color-dot-btn ${settings.accent_color === color.id ? 'active' : ''}`}
                            style={{ 
                              width: '28px', 
                              height: '28px', 
                              borderRadius: '50%', 
                              border: 'none',
                              backgroundColor: color.hex, 
                              cursor: 'pointer',
                              padding: 0,
                              outline: 'none',
                              transition: 'transform 0.15s ease, box-shadow 0.15s ease',
                              boxShadow: settings.accent_color === color.id 
                                ? `0 0 0 2px var(--bg-primary), 0 0 0 4px ${color.hex}` 
                                : 'none',
                              transform: settings.accent_color === color.id ? 'scale(1.1)' : 'scale(1)'
                            }}
                            onMouseEnter={(e) => {
                              if (settings.accent_color !== color.id) {
                                e.currentTarget.style.transform = 'scale(1.15)';
                              }
                            }}
                            onMouseLeave={(e) => {
                              if (settings.accent_color !== color.id) {
                                e.currentTarget.style.transform = 'scale(1)';
                              }
                            }}
                            onClick={() => onUpdate({ accent_color: color.id })}
                            title={color.label}
                          />
                        ))}
                      </div>
                    </div>
                    <div className="form-group">
                      <label className="form-label">界面缩放</label>
                      <CustomSelect
                        value={settings.ui_scale}
                        onChange={(ui_scale) => onUpdate({ ui_scale })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: '90%', label: '90% (偏小)' },
                          { value: '100%', label: '100% (标准)' },
                          { value: '110%', label: '110% (偏大)' },
                          { value: '125%', label: '125% (宽松)' }
                        ]}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">布局密度</label>
                      <CustomSelect
                        value={settings.ui_density}
                        onChange={(ui_density) => onUpdate({ ui_density })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'compact', label: '紧凑布局' },
                          { value: 'standard', label: '标准布局' },
                          { value: 'loose', label: '宽松布局' }
                        ]}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">界面过度动效</label>
                      <div style={{ display: 'flex', alignItems: 'center', height: '36px' }}>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={settings.animations_enabled}
                            disabled={isRecording || isProcessing}
                            onChange={(e) => onUpdate({ animations_enabled: e.target.checked })}
                          />
                          <span className="slider"></span>
                        </label>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                          {settings.animations_enabled ? '已启用过渡效果' : '已关闭动效'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card-title">
                    <ChartIcon /> 默认视图与字段
                  </div>
                  <div className="settings-grid">
                    <div className="form-group">
                      <label className="form-label">默认图形</label>
                      <CustomSelect
                        value={capabilities.spectrogram ? settings.default_plot : 'waveform'}
                        onChange={(default_plot) => onUpdate({ default_plot })}
                        disabled={isRecording || isProcessing || !capabilities.spectrogram}
                        options={capabilities.spectrogram
                          ? [
                              { value: 'waveform', label: '波形图' },
                              { value: 'spectrogram', label: '语谱图' }
                            ]
                          : [{ value: 'waveform', label: '波形图（独立模式）' }]}
                      />
                      {!capabilities.spectrogram && (
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                          语谱图仅在完整模式可用；此处不会修改您保存的默认图形。
                        </div>
                      )}
                    </div>
                    <div className="form-group">
                      <label className="form-label">字表角标字段</label>
                      <CustomSelect
                        value={settings.badge_meta_key}
                        onChange={(badge_meta_key) => onUpdate({ badge_meta_key })}
                        options={metaKeyOptions}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">中心提示显示字段</label>
                      <CustomSelect
                        value={settings.primary_meta_key}
                        onChange={(primary_meta_key) => onUpdate({ primary_meta_key })}
                        options={metaKeyOptions}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label" style={{ marginBottom: '0.5rem' }}>显示录音质量检测结果</label>
                      <div style={{ display: 'flex', alignItems: 'center', height: '36px' }}>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={settings.show_quality_results !== false}
                            disabled={isRecording || isProcessing}
                            onChange={(e) => onUpdate({ show_quality_results: e.target.checked })}
                          />
                          <span className="slider"></span>
                        </label>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                          {settings.show_quality_results !== false ? '在录音结束后显示判定与打分面板' : '隐藏录音结果判定面板'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card-title">
                    <FormatIcon /> 字体字号调节
                  </div>
                  <div className="form-group">
                    <label className="form-label" style={{ marginBottom: '0.4rem' }}>中央字词字号 ({settings.char_font_size}px)</label>
                    <div className="custom-slider-pill">
                      <span className="slider-label-small">A</span>
                      <input
                        type="range"
                        min="40"
                        max="200"
                        value={settings.char_font_size}
                        className="custom-range-slider"
                        onChange={(e) => onUpdate({ char_font_size: Number(e.target.value) })}
                      />
                      <span className="slider-label-large">A</span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 2: Recording & Navigation */}
            {activeTab === 'recording' && (
              <div className="settings-panel-transition" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div className="settings-card">
                  <div className="settings-card-title">
                    <MicIcon active={true} /> 录制与监听行为
                  </div>
                  <div className="settings-grid">
                    <div className="form-group">
                      <label className="form-label">录音模式</label>
                      <CustomSelect
                        value={settings.record_mode}
                        onChange={(record_mode) => onUpdate({ record_mode })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'click', label: '点击开关 (手动)' },
                          { value: 'hold', label: '按住录制 (对讲机)' },
                          { value: 'vad', label: '智能 VAD 触发自动跳转' }
                        ]}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">录制顺序</label>
                      <CustomSelect
                        value={settings.record_order}
                        onChange={(record_order) => onUpdate({ record_order })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'wordlist', label: '字表顺序' },
                          { value: 'random', label: '随机乱序' }
                        ]}
                      />
                    </div>
                    <div className="form-group settings-grid-full">
                      <label className="form-label">待机音频输入监听</label>
                      <div style={{ display: 'flex', alignItems: 'center', height: '36px' }}>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={settings.live_input_monitor}
                            disabled={isRecording || isProcessing}
                            onChange={(e) => onUpdate({ live_input_monitor: e.target.checked })}
                          />
                          <span className="slider"></span>
                        </label>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                          {settings.live_input_monitor ? '始终处于监听态 (显示实时音量)' : '仅在点击录制后请求麦克风'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card-title">
                    <InfoIcon /> 智能 VAD 断句阈值设置
                  </div>
                  <div className="form-group">
                    <label className="form-label">VAD 灵敏度级别</label>
                    <CustomSelect
                      value={settings.vad_preset}
                      onChange={(vad_preset) => onUpdate({ vad_preset })}
                      disabled={isRecording || isProcessing}
                      options={[
                        { value: 'robust', label: '稳健型 (推荐：抗环境噪音，延时自动结束)' },
                        { value: 'standard', label: '标准型' },
                        { value: 'sensitive', label: '灵敏型 (适合极度安静的环境，极速断句)' }
                      ]}
                    />
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                      {settings.vad_preset === 'robust' && '稳健预设支持 350ms 的语音阈值确认以及 1000ms 的尾随静音判定。'}
                      {settings.vad_preset === 'standard' && '标准预设支持 220ms 的语音阈值确认以及 700ms 的尾随静音判定。'}
                      {settings.vad_preset === 'sensitive' && '灵敏预设仅需 150ms 语音确认及 450ms 静音即完成跳转。'}
                    </div>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card-title">
                    <KeyboardIcon /> 键盘全局快捷键
                  </div>
                  <div className="settings-grid">
                    <div className="form-group">
                      <label className="form-label">按键快捷预设</label>
                      <CustomSelect
                        value={settings.shortcut_preset}
                        onChange={(shortcut_preset) => onUpdate({ shortcut_preset })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'standard', label: '标准布局 (Space / 方向键 / R)' },
                          { value: 'left', label: '左手专用 (Space / A D / S)' },
                          { value: 'right', label: '右手专用 (Enter / J L / K)' },
                          { value: 'disabled', label: '关闭全局快捷键' }
                        ]}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">操作提示显示</label>
                      <div style={{ display: 'flex', alignItems: 'center', height: '36px' }}>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={settings.show_shortcut_hints !== false}
                            onChange={(e) => onUpdate({ show_shortcut_hints: e.target.checked })}
                          />
                          <span className="slider"></span>
                        </label>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                          {settings.show_shortcut_hints !== false ? '显示快捷键操作提示' : '已隐藏快捷键操作提示'}
                        </span>
                      </div>
                    </div>
                    <div className="form-group settings-grid-full" style={{ background: 'var(--bg-secondary)', padding: '0.65rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: '0.2rem' }}>
                        当前按键映射绑定：
                      </span>
                      <code style={{ fontSize: '0.75rem', color: 'var(--color-accent)', fontWeight: 600, fontFamily: 'var(--font-mono, monospace)' }}>
                        {getShortcutPresetDetails(settings.shortcut_preset)}
                      </code>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 3: Quality Rules */}
            {activeTab === 'quality' && (
              <div className="quality-settings-panel settings-panel-transition">
                <div className="quality-settings-summary">
                  <div>
                    <strong>录音质量判定条件</strong>
                    <span>已启用 {enabledRulesCount} / {availableQualityItems.length} 项可用检测</span>
                  </div>
                  <label className="switch" title={availableQualityEnabled ? '关闭全部检测' : '启用全部检测'}>
                    <input
                      type="checkbox"
                      checked={isStandalone ? availableQualityEnabled : settings.realtime_quality}
                      disabled={isRecording || isProcessing}
                      onChange={(e) => handleToggleAllQuality(e.target.checked)}
                    />
                    <span className="slider"></span>
                  </label>
                </div>
                <p className="quality-settings-hint">可独立配置各子规则。检测条件越严格，越容易判定为需重录或建议人工复核。</p>

                <div className="quality-rule-list">
                  {QUALITY_ITEMS.map((item) => {
                    const rule = settings.quality_rules?.[item.key] || { enabled: true, level: 'medium' };
                    const available = isQualityAvailable(item.key);
                    return (
                      <div className={`quality-rule-card ${available && rule.enabled ? 'enabled' : 'disabled'}`} key={item.key}>
                        <label className="quality-rule-toggle">
                          <span className="switch" style={{ flex: '0 0 40px' }}>
                            <input
                              type="checkbox"
                              checked={available && rule.enabled}
                              disabled={!available || isRecording || isProcessing}
                              onChange={(e) => handleUpdateRule(item.key, { enabled: e.target.checked })}
                            />
                            <span className="slider"></span>
                          </span>
                          <span>
                            <strong>{item.label}</strong>
                            <small>{available ? item.description : '完整模式可用'}</small>
                          </span>
                        </label>
                        <div className="quality-level-selector" aria-label={`${item.label}级别选择`}>
                          {[
                            { value: 'low', label: '宽松' },
                            { value: 'medium', label: '标准' },
                            { value: 'high', label: '严格' }
                          ].map((level) => (
                            <button
                              type="button"
                              key={level.value}
                              className={rule.level === level.value ? 'active' : ''}
                              disabled={!available || !rule.enabled || isRecording || isProcessing}
                              onClick={() => handleUpdateRule(item.key, { level: level.value })}
                            >
                              {level.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Tab 4: Project & Save */}
            {activeTab === 'storage' && (
              <div className="settings-panel-transition" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {isStandalone ? (
                  <div className="settings-card">
                    <div className="settings-card-title">
                      <SaveIcon /> 独立模式保存与导出
                    </div>
                    <div className="settings-grid">
                      <div className="form-group settings-grid-full">
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                          录音会自动保存到本机受控工作区，并可随时读写 .teproj；安装主程序后可直接继续分析同一工程。
                        </div>
                      </div>
                      <div className="form-group settings-grid-full">
                        <label className="form-label">默认 WAV 导出目录</label>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <input
                            type="text"
                            className="form-input form-input-readonly"
                            value={settings.wav_export_path || ''}
                            style={{ flex: 1, textOverflow: 'ellipsis' }}
                            readOnly
                            placeholder="导出时选择目录，或在此预先设置..."
                          />
                          <button
                            className="btn-secondary"
                            style={{ borderRadius: '9999px', padding: '0.4rem 1rem', fontSize: '0.8rem', whiteSpace: 'nowrap' }}
                            disabled={isRecording || isProcessing}
                            onClick={onSelectWavExportFolder}
                          >
                            选择路径
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="settings-card">
                    <div className="settings-card-title">
                      <SaveIcon /> 存储目录与归档
                    </div>
                    <div className="settings-grid">
                    <div className="form-group settings-grid-full">
                      <label className="form-label">默认工程保存格式</label>
                      <CustomSelect
                        value={settings.save_format}
                        onChange={(save_format) => onUpdate({ save_format })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: 'teproj', label: '.teproj 单个工程压缩包' },
                          { value: 'folder', label: '外部工程文件夹目录 (解包暴露模式)' }
                        ]}
                      />
                    </div>

                    {settings.save_format === 'folder' && (
                      <div className="form-group settings-grid-full">
                        <label className="form-label">默认工作目录文件夹路径</label>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <input
                            type="text"
                            className="form-input form-input-readonly"
                            value={settings.folder_path || ''}
                            style={{ flex: 1, textOverflow: 'ellipsis' }}
                            readOnly
                            placeholder="请选择保存目录..."
                          />
                          <button
                            className="btn-secondary"
                            style={{ borderRadius: '9999px', padding: '0.4rem 1rem', fontSize: '0.8rem', whiteSpace: 'nowrap' }}
                            disabled={isRecording || isProcessing}
                            onClick={onSelectFolder}
                          >
                            选择路径
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="form-group settings-grid-full">
                      <label className="form-label">默认工程文件名</label>
                      <input
                        type="text"
                        className="form-input"
                        value={localProjName}
                        disabled={isRecording || isProcessing}
                        onChange={(e) => setLocalProjName(e.target.value)}
                        onBlur={handleProjNameBlur}
                        placeholder="请输入工程名，如 PhonRec_Project"
                      />
                    </div>
                    </div>
                  </div>
                )}

                <div className="settings-card" style={{ borderLeft: '4px solid var(--color-accent)', background: 'var(--color-accent-glow)' }}>
                  <div style={{ fontSize: '0.8rem', lineHeight: '1.5', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                    <strong style={{ color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                      <NetworkIcon /> 自动保存状态指示：已开启
                    </strong>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.76rem' }}>
                      PhonRec 会在您每次修改发音人、录制完词项或修改字表时，自动保存最新改动至本地底层工作区目录中。无需频繁点击保存。该机制以录音数据安全为首要任务，不支持关闭。
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Tab 5: Device & Permission */}
            {activeTab === 'device' && (
              <div className="settings-panel-transition" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div className="settings-card">
                  <div className="settings-card-title">
                    <MicIcon active={true} /> 音频硬件输入设置
                  </div>
                  <div className="settings-grid">
                    <div className="form-group settings-grid-full">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.2rem' }}>
                        <label className="form-label">麦克风输入源</label>
                        <button
                          className="btn-secondary"
                          style={{ padding: '0.1rem 0.4rem', fontSize: '0.7rem' }}
                          onClick={onRefreshDevices}
                          disabled={isRecording || isProcessing}
                        >
                          刷新硬件设备
                        </button>
                      </div>
                      <CustomSelect
                        value={settings.record_source}
                        onChange={(record_source) => onUpdate({ record_source })}
                        disabled={isRecording || isProcessing}
                        options={audioDevices.map(d => ({ value: d.id, label: d.name }))}
                      />
                      {settings.record_source?.startsWith('loopback:') && (
                        <div style={{ fontSize: '0.72rem', color: 'var(--color-accent)', marginTop: '0.2rem', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                          <InfoIcon /> 提示：系统声音回环录制仅在 Windows 系统下支持。
                        </div>
                      )}
                    </div>

                    <div className="form-group">
                      <label className="form-label">目标采样率</label>
                      <CustomSelect
                        value={String(settings.sample_rate)}
                        onChange={(val) => onUpdate({ sample_rate: Number(val) })}
                        disabled={isRecording || isProcessing}
                        options={[
                          { value: '16000', label: '16 kHz (推荐：语音识别与分析)' },
                          { value: '44100', label: '44.1 kHz (高保真CD级)' },
                          { value: '48000', label: '48 kHz (广播级采样)' }
                        ]}
                      />
                    </div>

                    <div className="form-group" style={{ opacity: 0.75 }}>
                      <label className="form-label">录音通道 (只读)</label>
                      <input type="text" className="form-input form-input-readonly" value="单声道 (Mono)" readOnly />
                    </div>

                    <div className="form-group settings-grid-full" style={{ opacity: 0.75 }}>
                      <label className="form-label">存储音频格式 (只读)</label>
                      <input type="text" className="form-input form-input-readonly" value="16-bit 线性 PCM (Microsoft WAV)" readOnly />
                    </div>
                  </div>
                </div>

                <div className="settings-card">
                  <div className="settings-card-title">
                    <CheckIcon /> 麦克风系统权限
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem' }}>
                      <span className={`indicator-led ${micBlocked === true ? 'red' : micBlocked === false ? 'green' : ''}`}></span>
                      <span>
                        {settings.record_source?.startsWith('loopback:')
                          ? '系统声音回环录制，无需请求物理麦克风权限'
                          : micBlocked === true
                            ? '已拒绝：应用程序无麦克风访问权限'
                            : micBlocked === false
                              ? '已授权：可正常访问麦克风设备'
                              : '尚未检测麦克风权限'}
                      </span>
                    </div>

                    <div style={{ display: 'flex', gap: '0.5rem', width: '100%' }}>
                      <button
                        className="btn-primary"
                        style={{ flex: 1, fontSize: '0.78rem', padding: '0.5rem', justifyContent: 'center' }}
                        onClick={onCheckPermission}
                      >
                        重新请求并检测权限
                      </button>
                      <button
                        className="btn-secondary"
                        style={{ flex: 1, fontSize: '0.78rem', padding: '0.5rem', justifyContent: 'center' }}
                        onClick={onOpenPrivacy}
                      >
                        打开系统隐私设置
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
          <button
            className="btn-secondary"
            style={{ padding: '0.4rem 1.25rem', fontSize: '0.85rem', color: 'var(--color-danger)', borderColor: 'var(--border-color)' }}
            disabled={isRecording || isProcessing}
            onClick={onReset}
          >
            <ResetIcon /> 恢复默认
          </button>
          <button
            className="btn-primary"
            style={{ padding: '0.4rem 1.5rem', fontSize: '0.85rem' }}
            onClick={onClose}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
