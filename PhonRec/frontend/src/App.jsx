import { useCallback, useEffect, useRef, useState } from 'react';
import { save, open } from '@tauri-apps/plugin-dialog';
import { writeFile } from '@tauri-apps/plugin-fs';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { apiFetch } from './engineApi.js';
import { bufferToWav, resampleAudio } from './audioUtils.js';
import { createVadEngine } from './vadEngine.js';
import {
  createDefaultQualityRules,
  hasEnabledQualityRule,
  normalizeQualityRules,
} from './qualitySettings.js';
import {
  buildRecordedItem,
  formatPlaybackTime,
  mergeAudioDevices,
  selectAvailableAudioSource,
} from './appUtils.js';
import SettingsModal from './SettingsModal.jsx';
import PlainWordlistModal from './PlainWordlistModal.jsx';
import { useRuntimeClient } from './runtimeContext.js';

// --- Inline SVG Icons ---
const CloseIcon = () => (
  <svg style={{ width: '12px', height: '12px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);
const ImportIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </svg>
);

const ExportIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);

const UserIcon = () => (
  <svg style={{ width: '15px', height: '15px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </svg>
);

const TrashIcon = () => (
  <svg style={{ width: '14px', height: '14px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
);

const BookIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const SweepIcon = () => (
  <svg style={{ width: '15px', height: '15px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18" />
    <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
    <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
  </svg>
);

const CheckIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const ChartIcon = () => (
  <svg style={{ width: '16px', height: '16px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </svg>
);

const ChevronLeft = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="15 18 9 12 15 6" />
  </svg>
);

const ChevronRight = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="9 18 15 12 9 6" />
  </svg>
);

const KeyboardIcon = () => (
  <svg style={{ width: '14px', height: '14px', display: 'inline-block', verticalAlign: 'middle', marginRight: '4px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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

const MicIcon = ({ active }) => (
  <svg style={{ width: '16px', height: '16px', marginRight: '4px', color: active ? 'var(--color-success)' : 'var(--text-muted)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
);

const PlayIcon = () => (
  <svg style={{ width: '18px', height: '18px', marginLeft: '2px' }} viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="5 3 19 12 5 21 5 3" />
  </svg>
);

const PauseIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="6" y="4" width="4" height="16" />
    <rect x="14" y="4" width="4" height="16" />
  </svg>
);

const GearIcon = () => (
  <svg className="settings-gear-btn" style={{ width: '18px', height: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

const InfoIcon = () => (
  <svg style={{ width: '14px', height: '14px', marginRight: '4px', display: 'inline-block', verticalAlign: 'middle', color: 'var(--color-accent)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);

const MenuIcon = () => (
  <svg style={{ width: '20px', height: '20px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

const CustomSelect = ({ value, onChange, options, style, placement = 'bottom' }) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

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
    <div className={`custom-select-container ${isOpen ? 'open' : ''}`} ref={dropdownRef} style={style}>
      <div
        className={`custom-select-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <span>{selectedOption ? selectedOption.label : ''}</span>
        <svg style={{ width: '14px', height: '14px', transform: isOpen ? 'rotate(180deg)' : 'rotate(0)', transition: 'transform 0.2s', marginLeft: '8px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
      {isOpen && (
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

const generateSpeakerId = () => {
  return 'spk_' + Math.random().toString(36).substring(2, 11);
};

export default function App() {
  const runtime = useRuntimeClient();
  const { capabilities } = runtime;
  const isStandalone = runtime.mode === 'standalone';
  const hasRuntimeQualityRule = (rules) => isStandalone
    ? Boolean(rules?.volume?.enabled || rules?.clipping?.enabled)
    : hasEnabledQualityRule(rules);
  // --- State Variables ---
  const [, setConnectionStatus] = useState(true);
  const [showMicGuidance, setShowMicGuidance] = useState(false);
  const [micBlocked, setMicBlocked] = useState(null);
  const [isLeftSidebarOpen, setIsLeftSidebarOpen] = useState(false);
  const [speakers, setSpeakers] = useState({});
  const [activeSpeakerId, setActiveSpeakerId] = useState('');
  const [groups, setGroups] = useState([]);

  // Navigation index & active group (can be index or 'all')
  const [activeGroupIndex, setActiveGroupIndex] = useState('all'); // default to All
  const [activeItemIndex, setActiveItemIndex] = useState(0);

  // Settings
  const [recordingMode, setRecordingMode] = useState('click');
  const [qualityChecksEnabled, setQualityChecksEnabled] = useState(true);
  const [qualityRules, setQualityRules] = useState(() => createDefaultQualityRules());
  const [randomizeOrder, setRandomizeOrder] = useState(false);

  const [audioDevices, setAudioDevices] = useState([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState('default');
  const [sampleRateSetting, setSampleRateSetting] = useState(16000);
  const [saveFormatSetting, setSaveFormatSetting] = useState('teproj');
  const [folderPathSetting, setFolderPathSetting] = useState('');
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  // Redesigned visual and behavioral settings states
  const [themeSetting, setThemeSetting] = useState('light');
  const [accentColorSetting, setAccentColorSetting] = useState('navy');
  const [uiScaleSetting, setUiScaleSetting] = useState('100%');
  const [uiDensitySetting, setUiDensitySetting] = useState('standard');
  const [animationsEnabledSetting, setAnimationsEnabledSetting] = useState(true);
  const [vadPresetSetting, setVadPresetSetting] = useState('standard');
  const [shortcutPresetSetting, setShortcutPresetSetting] = useState('standard');
  const [liveInputMonitorSetting, setLiveInputMonitorSetting] = useState(true);
  const [defaultProjectNameSetting, setDefaultProjectNameSetting] = useState('PhonRec_Project.teproj');
  const [showShortcutHintsSetting, setShowShortcutHintsSetting] = useState(true);

  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [settingsModalClosing, setSettingsModalClosing] = useState(false);

  const [showImportModal, setShowImportModal] = useState(false);
  const [showPlainWordlistModal, setShowPlainWordlistModal] = useState(false);
  const [plainWordlistDraft, setPlainWordlistDraft] = useState({ key: 0, text: '', title: '粘贴字表' });
  const [importResult, setImportResult] = useState(null);

  const [playbackState, setPlaybackState] = useState({
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    currentSpeakerId: '',
    currentWordId: ''
  });

  // Shuffled items computed state
  const [displayedItems, setDisplayedItems] = useState([]);
  const [isSmallScreen, setIsSmallScreen] = useState(window.innerWidth <= 768);

  const [visualizerTab, setVisualizerTab] = useState('waveform');
  const effectiveVisualizerTab = capabilities.spectrogram ? visualizerTab : 'waveform';
  const [showZoomedSpectrogram, setShowZoomedSpectrogram] = useState(false);

  // Recording states
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [spectrogramUrl, setSpectrogramUrl] = useState('');
  const [qualityResults, setQualityResults] = useState(null);

  // Inline Speaker states
  const [isAddingSpeaker, setIsAddingSpeaker] = useState(false);
  const [newSpeakerName, setNewSpeakerName] = useState('');

  // VAD & live volume states
  const [vadLevel, setVadLevel] = useState(0);
  const [vadSpeaking, setVadSpeaking] = useState(false);
  const [liveVolume, setLiveVolume] = useState(0);

  // Metadata field customization keys
  const [primaryMetaKey, setPrimaryMetaKey] = useState('拼音');
  const [badgeMetaKey, setBadgeMetaKey] = useState('拼音');

  // Custom font size for character display
  const [charFontSize, setCharFontSize] = useState(120);

  // Custom confirm dialog state
  const [confirmDialog, setConfirmDialog] = useState(null);

  // Drag and drop overlay
  const [isDragging, setIsDragging] = useState(false);

  const [showStandaloneBanner, setShowStandaloneBanner] = useState(() => {
    try {
      return localStorage.getItem('hideStandaloneBanner') !== 'true';
    } catch {
      return true;
    }
  });

  const [wordlistInfo, setWordlistInfo] = useState({ title: '无字表', count: 0 });

  // --- Refs ---
  const audioContextRef = useRef(null);
  const streamRef = useRef(null);
  const processorNodeRef = useRef(null);
  const audioChunksRef = useRef([]);
  const canvasRef = useRef(null);
  const fileInputRef = useRef(null);
  const projectInputRef = useRef(null);
  const audioPlayerRef = useRef(null);
  const activeCaptureSourceRef = useRef(null);
  const captureLifecycleRef = useRef(Promise.resolve());
  const settingsSaveQueueRef = useRef(Promise.resolve());
  const settingsSnapshotRef = useRef(null);
  const settingsRevisionRef = useRef(0);
  const charFontSizeSaveTimeoutRef = useRef(null);
  const charFontSizeRollbackRef = useRef(null);

  const vadEngineRef = useRef(null);
  if (!vadEngineRef.current) vadEngineRef.current = createVadEngine();
  const vadRetryRef = useRef(new Map());
  const handleVadSampleRef = useRef(null);

  const isRecordingRef = useRef(isRecording);
  isRecordingRef.current = isRecording;

  const isProcessingRef = useRef(isProcessing);
  isProcessingRef.current = isProcessing;

  const isMouseDownRef = useRef(false);
  const isStartingRef = useRef(false);
  const shouldCancelRef = useRef(false);

  const qualityChecksEnabledRef = useRef(qualityChecksEnabled);
  qualityChecksEnabledRef.current = qualityChecksEnabled;
  const qualityRulesRef = useRef(qualityRules);
  qualityRulesRef.current = qualityRules;

  const recordingModeRef = useRef(recordingMode);
  recordingModeRef.current = recordingMode;

  const speakersRef = useRef(speakers);
  speakersRef.current = speakers;

  const activeSpeakerIdRef = useRef(activeSpeakerId);
  activeSpeakerIdRef.current = activeSpeakerId;

  const groupsRef = useRef(groups);
  groupsRef.current = groups;
  const projectStateRef = useRef({ version: '1.0' });

  const displayedItemsRef = useRef(displayedItems);
  displayedItemsRef.current = displayedItems;

  const activeItemIndexRef = useRef(activeItemIndex);
  activeItemIndexRef.current = activeItemIndex;

  const stopRecordingRef = useRef(null);

  settingsSnapshotRef.current = {
    version: 1,
    realtime_quality: qualityChecksEnabled,
    quality_rules: qualityRules,
    default_plot: visualizerTab,
    record_order: randomizeOrder ? 'random' : 'wordlist',
    record_mode: recordingMode,
    record_source: selectedDeviceId,
    sample_rate: Number(sampleRateSetting),
    save_format: saveFormatSetting,
    folder_path: folderPathSetting,
    theme: themeSetting,
    accent_color: accentColorSetting,
    ui_scale: uiScaleSetting,
    ui_density: uiDensitySetting,
    animations_enabled: animationsEnabledSetting,
    primary_meta_key: primaryMetaKey,
    badge_meta_key: badgeMetaKey,
    char_font_size: Number(charFontSize),
    vad_preset: vadPresetSetting,
    shortcut_preset: shortcutPresetSetting,
    live_input_monitor: liveInputMonitorSetting,
    default_project_name: defaultProjectNameSetting,
    show_shortcut_hints: showShortcutHintsSetting,
    channels: 1,
    format: 'wav'
  };

  handleVadSampleRef.current = (rms) => {
    if (!isRecordingRef.current || recordingModeRef.current !== 'vad') {
      vadEngineRef.current.observeNoise(rms);
      return;
    }
    const result = vadEngineRef.current.process(rms);
    setVadSpeaking(result.state === 'speaking' || result.state === 'trailing');
    if (result.event === 'speech-end' || result.event === 'max-duration') {
      stopRecordingRef.current?.(true, true);
    } else if (result.event === 'no-speech-timeout') {
      stopRecordingRef.current?.(false, true, 'no_speech');
    }
  };

  // Settings helpers
  const loadAndApplySettings = async () => {
    try {
      const settings = await invoke('load_settings');
      if (settings) {
        const loadedQualityRules = normalizeQualityRules(settings.quality_rules, settings.realtime_quality);
        setQualityRules(loadedQualityRules);
        setQualityChecksEnabled(hasRuntimeQualityRule(loadedQualityRules));
        setVisualizerTab(settings.default_plot || 'waveform');
        setRecordingMode(settings.record_mode || 'click');
        setSelectedDeviceId(settings.record_source || 'default');
        setSampleRateSetting(settings.sample_rate || 16000);
        setSaveFormatSetting(settings.save_format || 'teproj');
        setFolderPathSetting(settings.folder_path || '');
        setRandomizeOrder(settings.record_order === 'random');

        // New fields
        setThemeSetting(settings.theme || 'light');
        setAccentColorSetting(settings.accent_color || 'navy');
        setUiScaleSetting(settings.ui_scale || '100%');
        setUiDensitySetting(settings.ui_density || 'standard');
        setAnimationsEnabledSetting(settings.animations_enabled !== false);
        setPrimaryMetaKey(settings.primary_meta_key || '拼音');
        setBadgeMetaKey(settings.badge_meta_key || '拼音');
        setCharFontSize(settings.char_font_size || 120);
        setVadPresetSetting(settings.vad_preset || 'standard');
        setShortcutPresetSetting(settings.shortcut_preset || 'standard');
        setLiveInputMonitorSetting(settings.live_input_monitor !== false);
        setDefaultProjectNameSetting(settings.default_project_name || 'PhonRec_Project.teproj');
        setShowShortcutHintsSetting(settings.show_shortcut_hints !== false);
      }
      return settings;
    } catch (err) {
      console.error('读取设置失败:', err);
      return null;
    }
  };

  const fetchAudioDevices = async (preferredSource = selectedDeviceId) => {
    try {
      const nativeDevices = await invoke('list_audio_devices');
      let mediaDevices = [];
      if (navigator.mediaDevices?.enumerateDevices) {
        mediaDevices = await navigator.mediaDevices.enumerateDevices();
      }
      const devices = mergeAudioDevices(nativeDevices, mediaDevices);
      const availableSource = selectAvailableAudioSource(preferredSource, devices);
      setAudioDevices(devices);
      if (availableSource !== preferredSource) {
        setSelectedDeviceId(availableSource);
        if (settingsLoaded) {
          await updateSettings({ record_source: availableSource });
        }
      }
      return { devices, availableSource };
    } catch (err) {
      console.error('枚举录音设备失败:', err);
      const fallbackDevices = mergeAudioDevices([], []);
      setAudioDevices(fallbackDevices);
      setSelectedDeviceId('default');
      return { devices: fallbackDevices, availableSource: 'default' };
    }
  };

  const applySettingsSnapshot = (snapshot) => {
    setQualityRules(snapshot.quality_rules);
    setQualityChecksEnabled(snapshot.realtime_quality);
    setVisualizerTab(snapshot.default_plot);
    setRandomizeOrder(snapshot.record_order === 'random');
    setRecordingMode(snapshot.record_mode);
    setSelectedDeviceId(snapshot.record_source);
    setSampleRateSetting(Number(snapshot.sample_rate));
    setSaveFormatSetting(snapshot.save_format);
    setFolderPathSetting(snapshot.folder_path);
    setThemeSetting(snapshot.theme);
    setAccentColorSetting(snapshot.accent_color);
    setUiScaleSetting(snapshot.ui_scale);
    setUiDensitySetting(snapshot.ui_density);
    setAnimationsEnabledSetting(snapshot.animations_enabled);
    setPrimaryMetaKey(snapshot.primary_meta_key);
    setBadgeMetaKey(snapshot.badge_meta_key);
    setCharFontSize(Number(snapshot.char_font_size));
    setVadPresetSetting(snapshot.vad_preset);
    setShortcutPresetSetting(snapshot.shortcut_preset);
    setLiveInputMonitorSetting(snapshot.live_input_monitor);
    setDefaultProjectNameSetting(snapshot.default_project_name);
    setShowShortcutHintsSetting(snapshot.show_shortcut_hints !== false);

    qualityRulesRef.current = snapshot.quality_rules;
    qualityChecksEnabledRef.current = snapshot.realtime_quality;
    recordingModeRef.current = snapshot.record_mode;
  };

  const enqueueSettingsSave = (snapshot) => {
    settingsSaveQueueRef.current = settingsSaveQueueRef.current
      .catch(() => {})
      .then(() => invoke('save_settings', { settings: snapshot }));
    return settingsSaveQueueRef.current;
  };

  const handleSettingsSaveFailure = async (error, rollbackSnapshot, revision) => {
    console.error('保存设置失败:', error);
    if (settingsRevisionRef.current !== revision) return false;

    settingsSnapshotRef.current = rollbackSnapshot;
    applySettingsSnapshot(rollbackSnapshot);
    await customAlert(`保存设置失败，已恢复修改前的设置：${error}`);
    return false;
  };

  const updateSettings = async (updates) => {
    const previousSnapshot = settingsSnapshotRef.current;
    const nextSnapshot = {
      ...previousSnapshot,
      ...updates,
      sample_rate: updates.sample_rate !== undefined
        ? Number(updates.sample_rate)
        : previousSnapshot.sample_rate,
      char_font_size: updates.char_font_size !== undefined
        ? Number(updates.char_font_size)
        : previousSnapshot.char_font_size,
      channels: 1,
      format: 'wav'
    };

    if (updates.quality_rules !== undefined) {
      nextSnapshot.quality_rules = updates.quality_rules;
      nextSnapshot.realtime_quality = updates.realtime_quality !== undefined
        ? updates.realtime_quality
        : hasRuntimeQualityRule(updates.quality_rules);
    } else if (updates.realtime_quality !== undefined) {
      nextSnapshot.quality_rules = Object.fromEntries(
        Object.entries(previousSnapshot.quality_rules).map(([key, rule]) => [
          key,
          {
            ...rule,
            enabled: !isStandalone || key === 'volume' || key === 'clipping'
              ? updates.realtime_quality
              : rule.enabled,
          }
        ])
      );
      nextSnapshot.realtime_quality = updates.realtime_quality;
    }

    settingsSnapshotRef.current = nextSnapshot;
    const revision = ++settingsRevisionRef.current;
    applySettingsSnapshot(nextSnapshot);

    const onlyFontSize = Object.keys(updates).length === 1 && updates.char_font_size !== undefined;
    if (onlyFontSize) {
      if (!charFontSizeSaveTimeoutRef.current) {
        charFontSizeRollbackRef.current = previousSnapshot;
      } else {
        clearTimeout(charFontSizeSaveTimeoutRef.current);
      }

      charFontSizeSaveTimeoutRef.current = window.setTimeout(async () => {
        charFontSizeSaveTimeoutRef.current = null;
        const latestSnapshot = settingsSnapshotRef.current;
        const rollbackSnapshot = charFontSizeRollbackRef.current;
        charFontSizeRollbackRef.current = null;
        try {
          await enqueueSettingsSave(latestSnapshot);
        } catch (error) {
          await handleSettingsSaveFailure(error, rollbackSnapshot, revision);
        }
      }, 400);
      return true;
    }

    let rollbackSnapshot = previousSnapshot;
    if (charFontSizeSaveTimeoutRef.current) {
      clearTimeout(charFontSizeSaveTimeoutRef.current);
      charFontSizeSaveTimeoutRef.current = null;
      rollbackSnapshot = charFontSizeRollbackRef.current || previousSnapshot;
      charFontSizeRollbackRef.current = null;
    }

    try {
      await enqueueSettingsSave(nextSnapshot);
      return true;
    } catch (error) {
      return handleSettingsSaveFailure(error, rollbackSnapshot, revision);
    }
  };

  const resetAllSettings = async () => {
    if (charFontSizeSaveTimeoutRef.current) {
      clearTimeout(charFontSizeSaveTimeoutRef.current);
      charFontSizeSaveTimeoutRef.current = null;
      charFontSizeRollbackRef.current = null;
    }

    const revision = ++settingsRevisionRef.current;
    settingsSaveQueueRef.current = settingsSaveQueueRef.current
      .catch(() => {})
      .then(() => invoke('reset_settings'));
    const defaults = await settingsSaveQueueRef.current;
    const qualityRules = normalizeQualityRules(defaults.quality_rules, defaults.realtime_quality);
    const normalizedDefaults = {
      ...defaults,
      quality_rules: qualityRules,
      realtime_quality: hasEnabledQualityRule(qualityRules)
    };

    if (settingsRevisionRef.current === revision) {
      settingsSnapshotRef.current = normalizedDefaults;
      applySettingsSnapshot(normalizedDefaults);
    }
    return normalizedDefaults;
  };

  const updateQualityChecksEnabled = async (val) => {
    await updateSettings({ realtime_quality: val });
  };
  const updateRecordingMode = async (val) => {
    await updateSettings({ record_mode: val });
  };
  const updateVisualizerTab = async (val) => {
    if (val === 'spectrogram' && !capabilities.spectrogram) return;
    await updateSettings({ default_plot: val });
  };
  const updateRandomizeOrder = async (val) => {
    await updateSettings({ record_order: val ? 'random' : 'wordlist' });
  };
  const updateFolderPathSetting = async (val) => {
    await updateSettings({ folder_path: val });
  };
  const updateBadgeMetaKey = async (val) => {
    await updateSettings({ badge_meta_key: val });
  };
  const updatePrimaryMetaKey = async (val) => {
    await updateSettings({ primary_meta_key: val });
  };
  const updateCharFontSize = async (val) => {
    await updateSettings({ char_font_size: val });
  };

  const handleCloseStandaloneBanner = () => {
    setShowStandaloneBanner(false);
    try {
      localStorage.setItem('hideStandaloneBanner', 'true');
    } catch (e) {
      console.error('Failed to save standalone banner preference', e);
    }
  };

  const openSettingsModal = () => {
    setSettingsModalClosing(false);
    setShowSettingsModal(true);
  };
  const closeSettingsModal = () => {
    if (settingsModalClosing) return;
    setSettingsModalClosing(true);
    window.setTimeout(() => {
      setShowSettingsModal(false);
      setSettingsModalClosing(false);
    }, 180);
  };

  useEffect(() => {
    if (!showSettingsModal) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') closeSettingsModal();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showSettingsModal, settingsModalClosing]);

  const handleSeekChange = (e) => {
    const player = audioPlayerRef.current;
    if (!player?.src) return;
    const time = Number(e.target.value);
    player.currentTime = time;
    setPlaybackState(prev => ({ ...prev, currentTime: time }));
  };

  const resetPlayback = useCallback(() => {
    const player = audioPlayerRef.current;
    if (player) {
      player.pause();
      player.currentTime = 0;
      if (player.src?.startsWith('blob:')) {
        URL.revokeObjectURL(player.src);
      }
      player.removeAttribute('src');
      player.load();
    }
    setPlaybackState({
      isPlaying: false,
      currentTime: 0,
      duration: 0,
      currentSpeakerId: '',
      currentWordId: '',
    });
  }, []);

  // --- Custom Confirm/Alert Helpers ---
  const customConfirm = (message) => {
    return new Promise((resolve) => {
      setConfirmDialog({
        message,
        onConfirm: () => resolve(true),
        onCancel: () => resolve(false)
      });
    });
  };

  const customAlert = (message) => {
    return new Promise((resolve) => {
      setConfirmDialog({
        message,
        onConfirm: () => resolve(true),
        onCancel: null
      });
    });
  };

  // --- Background Microphone & Audio Listeners ---
  const ensureMicStream = async (sourceId = selectedDeviceId, { force = false } = {}) => {
    if (sourceId.startsWith('loopback:')) {
      if (!force && activeCaptureSourceRef.current === sourceId) return;
      try {
        await invoke('start_loopback_listener', { deviceName: sourceId });
        activeCaptureSourceRef.current = sourceId;
        setMicBlocked(false);
        setShowMicGuidance(false);
      } catch (err) {
        console.error('Tauri loopback listener start failed:', err);
        setMicBlocked(true);
        throw err;
      }
      return;
    }

    if (
      !force
      && activeCaptureSourceRef.current === sourceId
      && streamRef.current
      && audioContextRef.current
      && audioContextRef.current.state !== 'closed'
    ) {
      return streamRef.current;
    }

    let showOverlay = false;
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const status = await navigator.permissions.query({ name: 'microphone' });
        if (status.state === 'prompt') {
          showOverlay = true;
        } else if (status.state === 'denied') {
          setMicBlocked(true);
          showOverlay = true;
        }
      } else {
        showOverlay = true;
      }
    } catch {
      showOverlay = true;
    }

    if (showOverlay) {
      setShowMicGuidance(true);
    }

    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      // 让 WebView 使用设备的真实采样率，停止录音时再统一重采样。
      audioContextRef.current = new AudioContext();

      const constraints = {
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        }
      };

      if (sourceId && sourceId !== 'default') {
        constraints.audio.deviceId = { exact: sourceId };
      }

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;
      activeCaptureSourceRef.current = sourceId;
      setMicBlocked(false);
      setShowMicGuidance(false);

      const source = audioContextRef.current.createMediaStreamSource(stream);
      const processor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      processorNodeRef.current = processor;

      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);

        // Save chunks and draw real-time waveform only when actively recording
        if (isRecordingRef.current) {
          const chunkCopy = new Float32Array(inputData.length);
          chunkCopy.set(inputData);
          audioChunksRef.current.push(chunkCopy);

          const canvas = canvasRef.current;
          if (canvas) {
            const ctx = canvas.getContext('2d');
            const width = canvas.width;
            const height = canvas.height;
            ctx.fillStyle = '#f8fafc';
            ctx.fillRect(0, 0, width, height);
            ctx.lineWidth = 2;
            ctx.strokeStyle = '#6366f1';
            ctx.beginPath();

            const sliceWidth = width / inputData.length;
            let x = 0;

            for (let i = 0; i < inputData.length; i++) {
              const v = inputData[i];
              const y = (v + 1) * (height / 2);
              if (i === 0) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
              x += sliceWidth;
            }
            ctx.stroke();
          }
        }

        // Always compute live volume
        let sum = 0;
        for (let i = 0; i < inputData.length; i++) {
          sum += inputData[i] * inputData[i];
        }
        const rms = Math.sqrt(sum / inputData.length);
        const levelPercent = Math.min(100, Math.round(rms * 400));
        setLiveVolume(levelPercent);

        // If recording, update vadLevel and check silent duration for VAD auto-advance
        if (isRecordingRef.current) {
          setVadLevel(levelPercent);

          handleVadSampleRef.current?.(rms);
        }
      };

      source.connect(processor);
      processor.connect(audioContextRef.current.destination);

      return stream;
    } catch (err) {
      console.error(err);
      activeCaptureSourceRef.current = null;
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        await audioContextRef.current.close();
      }
      audioContextRef.current = null;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setMicBlocked(true);
        setShowMicGuidance(true);
      } else {
        setShowMicGuidance(false);
      }
      throw err;
    }
  };

  const closeMicStream = async () => {
    const activeSource = activeCaptureSourceRef.current;
    activeCaptureSourceRef.current = null;
    if (activeSource?.startsWith('loopback:')) {
      try {
        await invoke('stop_loopback_listener');
      } catch (error) {
        console.error('停止系统声音监听失败:', error);
      }
    }
    if (processorNodeRef.current) {
      processorNodeRef.current.disconnect();
      processorNodeRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      const context = audioContextRef.current;
      audioContextRef.current = null;
      if (context.state !== 'closed') {
        await context.close();
      }
    }
    setLiveVolume(0);
    setVadLevel(0);
    setVadSpeaking(false);
  };

  // --- Single Instance Audio Player & Tauri Event Listeners ---
  useEffect(() => {
    const player = new Audio();
    audioPlayerRef.current = player;

    const onPlay = () => setPlaybackState(prev => ({ ...prev, isPlaying: true }));
    const onPause = () => setPlaybackState(prev => ({ ...prev, isPlaying: false }));
    const onEnded = () => {
      player.currentTime = 0;
      setPlaybackState(prev => ({ ...prev, isPlaying: false, currentTime: 0 }));
    };
    const onTimeUpdate = () => setPlaybackState(prev => ({ ...prev, currentTime: player.currentTime }));
    const onDurationChange = () => setPlaybackState(prev => ({
      ...prev,
      duration: Number.isFinite(player.duration) ? player.duration : 0,
    }));

    player.addEventListener('play', onPlay);
    player.addEventListener('pause', onPause);
    player.addEventListener('ended', onEnded);
    player.addEventListener('timeupdate', onTimeUpdate);
    player.addEventListener('durationchange', onDurationChange);

    // Tauri Listeners
    let unlistenPreview;
    let unlistenError;

    const setupTauriListeners = async () => {
      unlistenPreview = await listen('loopback-preview', (event) => {
        const { volume, waveform } = event.payload;
        setLiveVolume(volume);

        if (isRecordingRef.current) {
          setVadLevel(volume);

          // Draw wave preview
          const canvas = canvasRef.current;
          if (canvas) {
            const ctx = canvas.getContext('2d');
            const width = canvas.width;
            const height = canvas.height;
            const styles = getComputedStyle(document.documentElement);
            ctx.fillStyle = styles.getPropertyValue('--bg-primary').trim() || '#f8fafc';
            ctx.fillRect(0, 0, width, height);
            ctx.lineWidth = 2;
            ctx.strokeStyle = styles.getPropertyValue('--color-accent').trim() || '#6366f1';
            ctx.beginPath();

            const sliceWidth = width / waveform.length;
            let x = 0;
            for (let i = 0; i < waveform.length; i++) {
              const v = waveform[i];
              const y = (v + 1) * (height / 2);
              if (i === 0) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
              x += sliceWidth;
            }
            ctx.stroke();
          }

          handleVadSampleRef.current?.(volume / 400.0);
        }
      });

      unlistenError = await listen('loopback-error', (event) => {
        customAlert('音频捕获流错误：' + event.payload);
      });
    };

    setupTauriListeners();

    return () => {
      player.pause();
      if (player.src?.startsWith('blob:')) {
        URL.revokeObjectURL(player.src);
      }
      player.removeAttribute('src');
      player.removeEventListener('play', onPlay);
      player.removeEventListener('pause', onPause);
      player.removeEventListener('ended', onEnded);
      player.removeEventListener('timeupdate', onTimeUpdate);
      player.removeEventListener('durationchange', onDurationChange);

      if (unlistenPreview) unlistenPreview();
      if (unlistenError) unlistenError();
    };
  }, []);

  // --- Load State & Settings on Mount ---
  useEffect(() => {
    fetchProjectState();
    let cancelled = false;
    const initializeSettings = async () => {
      const settings = await loadAndApplySettings();
      const preferredSource = settings?.record_source || 'default';
      const { availableSource } = await fetchAudioDevices(preferredSource);
      if (settings && availableSource !== preferredSource) {
        await invoke('save_settings', {
          settings: { ...settings, record_source: availableSource },
        });
      }
      if (!cancelled) setSettingsLoaded(true);
    };
    initializeSettings().catch(error => {
      console.error('初始化设置失败:', error);
      if (!cancelled) setSettingsLoaded(true);
    });

    const interval = isStandalone ? null : setInterval(async () => {
      try {
        const res = await apiFetch('/project/state');
        if (res.ok) setConnectionStatus(true);
      } catch {
        setConnectionStatus(false);
      }
    }, 5000);

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
      closeMicStream();
    };
  }, []);

  // Apply visual settings (theme, scale, density, animations)
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleSystemThemeChange = () => {
      if (themeSetting === 'system') {
        const activeTheme = mediaQuery.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', activeTheme);
      }
    };

    if (themeSetting === 'system') {
      const activeTheme = mediaQuery.matches ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', activeTheme);
      mediaQuery.addEventListener('change', handleSystemThemeChange);
    } else {
      document.documentElement.setAttribute('data-theme', themeSetting);
    }

    document.documentElement.style.fontSize = uiScaleSetting;
    document.documentElement.setAttribute('data-density', uiDensitySetting);
    document.documentElement.setAttribute('data-motion', animationsEnabledSetting ? 'enabled' : 'disabled');

    return () => {
      mediaQuery.removeEventListener('change', handleSystemThemeChange);
    };
  }, [themeSetting, uiScaleSetting, uiDensitySetting, animationsEnabledSetting]);

  // Apply accent color theme
  useEffect(() => {
    document.documentElement.setAttribute('data-accent', accentColorSetting || 'blue');
  }, [accentColorSetting]);

  // Background stream lifecycle
  useEffect(() => {
    if (!settingsLoaded) return;
    captureLifecycleRef.current = captureLifecycleRef.current
      .catch(() => {})
      .then(async () => {
        await closeMicStream();
        if (qualityChecksEnabled && liveInputMonitorSetting) {
          await ensureMicStream(selectedDeviceId);
        }
      })
      .catch(err => {
        console.error('初始化实时音量监听失败:', err);
      });
  }, [settingsLoaded, qualityChecksEnabled, selectedDeviceId, liveInputMonitorSetting]);

  // Pause playing audio when navigating items
  useEffect(() => {
    resetPlayback();
  }, [activeSpeakerId, activeGroupIndex, activeItemIndex, resetPlayback]);

  // Sync keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      const activeEl = document.activeElement;
      if (
        !activeEl ||
        activeEl.tagName === 'INPUT' ||
        activeEl.tagName === 'SELECT' ||
        activeEl.tagName === 'TEXTAREA' ||
        activeEl.tagName === 'BUTTON' ||
        activeEl.isContentEditable ||
        activeEl.closest('[role="dialog"]') ||
        activeEl.closest('.modal-content')
      ) {
        return;
      }

      if (shortcutPresetSetting === 'disabled') return;

      let isRecordKey = false;
      let isPrevKey = false;
      let isNextKey = false;
      let isPlayKey = false;

      if (shortcutPresetSetting === 'standard') {
        isRecordKey = e.code === 'Space';
        isPrevKey = e.code === 'ArrowLeft';
        isNextKey = e.code === 'ArrowRight';
        isPlayKey = e.code === 'KeyR';
      } else if (shortcutPresetSetting === 'left') {
        isRecordKey = e.code === 'Space';
        isPrevKey = e.code === 'KeyA';
        isNextKey = e.code === 'KeyD';
        isPlayKey = e.code === 'KeyS';
      } else if (shortcutPresetSetting === 'right') {
        isRecordKey = e.code === 'Enter' || e.code === 'NumpadEnter';
        isPrevKey = e.code === 'KeyJ';
        isNextKey = e.code === 'KeyL';
        isPlayKey = e.code === 'KeyK';
      }

      if (isRecordKey) {
        e.preventDefault();
        if (isProcessingRef.current) return;
        if (recordingMode === 'click' || recordingMode === 'vad') {
          if (isRecordingRef.current) {
            stopRecording();
          } else {
            startRecording();
          }
        }
      } else if (isPrevKey) {
        e.preventDefault();
        if (isRecordingRef.current || isProcessingRef.current) return;
        navigateItem(-1);
      } else if (isNextKey) {
        e.preventDefault();
        if (isRecordingRef.current || isProcessingRef.current) return;
        navigateItem(1);
      } else if (isPlayKey && !isRecordingRef.current && !isProcessingRef.current) {
        e.preventDefault();
        playRecordedAudio();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [displayedItems, activeItemIndex, speakers, activeSpeakerId, recordingMode, shortcutPresetSetting]);

  // Handle window resize for small screen detection
  useEffect(() => {
    const handleResize = () => {
      setIsSmallScreen(window.innerWidth <= 768);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Compute displayed items whenever groups, active group select, or randomize order changes
  useEffect(() => {
    let baseItems = [];
    if (isSmallScreen || activeGroupIndex === 'all') {
      baseItems = groups.flatMap(g => g.items || []);
    } else {
      const idx = parseInt(activeGroupIndex);
      if (!isNaN(idx) && groups[idx]) {
        baseItems = groups[idx].items || [];
      }
    }

    // Save previous active item ID
    const prevActiveId = displayedItemsRef.current[activeItemIndexRef.current]?.id;
    let finalItems = baseItems;

    if (randomizeOrder && baseItems.length > 0) {
      // Create a stable random shuffle
      const shuffled = [...baseItems];
      for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
      }
      finalItems = shuffled;
    }

    setDisplayedItems(finalItems);

    // Attempt to restore previous active item index
    if (prevActiveId) {
      const idx = finalItems.findIndex(item => item.id === prevActiveId);
      if (idx !== -1) {
        setActiveItemIndex(idx);
      } else {
        setActiveItemIndex(0);
      }
    } else {
      setActiveItemIndex(0);
    }
  }, [groups, activeGroupIndex, randomizeOrder, isSmallScreen]);

  // Load static waveform / spectrogram on active item change or visualizer tab change
  useEffect(() => {
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) {
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      return;
    }

    const recordMeta = speakers[activeSpeakerId]?.items?.[activeItem.id];
    if (recordMeta) {
      // Retrieve quality from state if present, or query analysis
      if (recordMeta.quality) {
        setQualityResults(recordMeta.quality);
      }
      if (effectiveVisualizerTab === 'spectrogram') {
        analyzeAudio(activeSpeakerId, activeItem.id);
      }
      drawStaticWaveformFromUrl();
    } else {
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
    }
  }, [activeItemIndex, activeSpeakerId, displayedItems, effectiveVisualizerTab, themeSetting, accentColorSetting]);

  // Auto-scroll word list to center active item
  useEffect(() => {
    const activeItem = getActiveItem();
    if (activeItem) {
      const el = document.getElementById(`word-item-${activeItem.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [activeItemIndex, displayedItems]);

  // --- API Wrappers ---
  const fetchProjectState = async () => {
    try {
      const data = await runtime.loadProject();
      projectStateRef.current = data;

      if (data.speakers) setSpeakers(data.speakers);
      if (data.active_speaker_id) setActiveSpeakerId(data.active_speaker_id);
      if (data.groups) {
        setGroups(data.groups);
        setWordlistInfo({
          title: '已载入工程字表',
          count: data.groups.reduce((acc, g) => acc + g.items.length, 0)
        });
      }
    } catch (err) {
      console.error('Failed to load project state:', err);
    }
  };

  const saveProjectState = async (updatedSpeakers, updatedGroups = groupsRef.current, customActiveSpeakerId = null) => {
    const state = {
      ...projectStateRef.current,
      version: "1.0",
      software_version: "PhonRec-1.0.0",
      save_time: new Date().toISOString(),
      active_speaker_id: customActiveSpeakerId !== null ? customActiveSpeakerId : activeSpeakerIdRef.current,
      speakers: updatedSpeakers,
      groups: updatedGroups
    };

    try {
      const saved = await runtime.saveProject(state);
      projectStateRef.current = saved.state || state;
    } catch (err) {
      console.error('Failed to save project state:', err);
      await customAlert(`保存项目状态失败：${err.message || err}`);
      throw err;
    }
  };

  const getActiveItem = () => {
    if (displayedItems.length === 0) return null;
    return displayedItems[activeItemIndex] || null;
  };

  const getCompletedCount = () => {
    if (!activeSpeakerId || !speakers[activeSpeakerId]) return 0;
    const items = speakers[activeSpeakerId].items || {};
    return Object.values(items).filter(item => item && item.path).length;
  };

  const getTotalCount = () => {
    return groups.reduce((acc, g) => acc + g.items.length, 0);
  };

  // --- Speaker Controls (Inline) ---
  const handleInlineSpeakerSubmit = async () => {
    const name = newSpeakerName.trim();
    if (!name) {
      setIsAddingSpeaker(false);
      return;
    }
    const id = generateSpeakerId();

    const updated = {
      ...speakers,
      [id]: {
        id: id,
        name: name,
        tab_mode: "多条独立音频",
        pending_batch_paths: [],
        items: {}
      }
    };

    setSpeakers(updated);
    setActiveSpeakerId(id);
    try {
      await saveProjectState(updated, groupsRef.current, id);
    } catch (err) {
      console.error('Failed to save project state:', err);
    }
    setNewSpeakerName('');
    setIsAddingSpeaker(false);
  };

  const handleInlineSpeakerKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleInlineSpeakerSubmit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setNewSpeakerName('');
      setIsAddingSpeaker(false);
    }
  };

  const handleInlineSpeakerBlur = () => {
    handleInlineSpeakerSubmit();
  };

  // --- Metadata custom keys ---
  const getAvailableMetaKeys = () => {
    const keys = [];
    if (groups && groups.length > 0) {
      for (const g of groups) {
        if (g.items && g.items.length > 0) {
          for (const item of g.items) {
            if (item.meta) {
              const metaKeys = Object.keys(item.meta);
              for (const k of metaKeys) {
                if (!keys.includes(k)) {
                  keys.push(k);
                }
              }
            }
          }
          break; // Scanning the first group with items is usually enough
        }
      }
    }
    if (keys.length === 0) {
      keys.push('拼音');
    }
    return keys;
  };

  const handleDeleteSpeaker = async (id, e) => {
    e.stopPropagation();
    const ok = await customConfirm('确定删除该发音人及其录音记录吗？');
    if (!ok) return;

    const updated = { ...speakers };
    delete updated[id];

    setSpeakers(updated);
    let nextActiveId = activeSpeakerId;
    if (activeSpeakerId === id) {
      const keys = Object.keys(updated);
      nextActiveId = keys.length > 0 ? keys[0] : '';
      setActiveSpeakerId(nextActiveId);
    }
    try {
      await saveProjectState(updated, groupsRef.current, nextActiveId);
    } catch (err) {
      console.error('Failed to delete speaker / save project state:', err);
    }
  };

  // --- Upload Files ---
  const triggerWordlistUpload = () => fileInputRef.current.click();
  const triggerProjectUpload = () => projectInputRef.current.click();
  const openPlainWordlistModal = (text = '', title = '粘贴字表') => {
    setPlainWordlistDraft(previous => ({ key: previous.key + 1, text, title }));
    setShowPlainWordlistModal(true);
  };

  const handleStandaloneWordlistImport = async ({ groups: importedGroups, title, count }) => {
    const hasRecordings = Object.values(speakersRef.current).some(speaker =>
      Object.values(speaker.items || {}).some(item => item?.path)
    );
    if (groupsRef.current.length > 0 || hasRecordings) {
      const ok = await customConfirm('导入新字表将替换当前字表，并清除所有发音人的现有录音。确定继续吗？');
      if (!ok) return;
    }

    const clearedSpeakers = Object.fromEntries(
      Object.entries(speakersRef.current).map(([speakerId, speaker]) => [
        speakerId,
        { ...speaker, items: {} },
      ])
    );
    try {
      setIsProcessing(true);
      setGroups(importedGroups);
      groupsRef.current = importedGroups;
      setSpeakers(clearedSpeakers);
      speakersRef.current = clearedSpeakers;
      setActiveGroupIndex('all');
      setActiveItemIndex(0);
      setWordlistInfo({ title, count });
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      await saveProjectState(clearedSpeakers, importedGroups);
      setShowPlainWordlistModal(false);
    } catch (error) {
      await customAlert(`导入普通字表失败：${error.message || error}`);
      await fetchProjectState();
    } finally {
      setIsProcessing(false);
    }
  };

  const uploadWordlistFile = async (file) => {
    const ok = await customConfirm('确定要更换字表吗？更换字表将替换当前所有字词且清除所有发音人的录音记录！');
    if (!ok) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
      setIsProcessing(true);
      const res = await apiFetch('/wordlist/import', {
        method: 'POST',
        body: formData
      });
      if (!res.ok) throw new Error('解析字表失败');
      const data = await res.json();

      setGroups(data.groups);
      setActiveGroupIndex('all'); // Default to show all items
      setActiveItemIndex(0);
      setWordlistInfo({
        title: file.name,
        count: data.groups.reduce((acc, g) => acc + g.items.length, 0)
      });

      // Auto select first meta key if available
      if (data.groups && data.groups.length > 0) {
        const firstItem = data.groups[0].items?.[0];
        if (firstItem && firstItem.meta) {
          const keys = Object.keys(firstItem.meta);
          if (keys.length > 0) {
            setPrimaryMetaKey(keys[0]);
            setBadgeMetaKey(keys[0]);
          }
        }
      }

      // Clear all speaker items
      const clearedSpeakers = {};
      Object.keys(speakersRef.current).forEach(spkId => {
        clearedSpeakers[spkId] = {
          ...speakersRef.current[spkId],
          items: {}
        };
      });
      setSpeakers(clearedSpeakers);

      await saveProjectState(clearedSpeakers, data.groups);
    } catch (err) {
      await customAlert(err.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const uploadProjectFile = async (file) => {
    const formData = new FormData();
    formData.append('file', file);

    try {
      setIsProcessing(true);

      const res = await apiFetch('/project/import', {
        method: 'POST',
        body: formData
      });
      if (!res.ok) throw new Error('导入工程失败');
      const data = await res.json();
      resetPlayback();
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      const state = data.state;
      projectStateRef.current = state;

      // Apply imported project state (with fallback defaults to clear old data)
      const importedSpeakers = state.speakers || {};
      setSpeakers(importedSpeakers);

      const importedActiveSpeakerId = state.active_speaker_id || '';
      setActiveSpeakerId(importedActiveSpeakerId);

      const importedGroups = state.groups || [];
      setGroups(importedGroups);

      if (importedGroups.length > 0) {
        setWordlistInfo({
          title: '已导入工程字表',
          count: importedGroups.reduce((acc, g) => acc + g.items.length, 0)
        });

        // Auto select first meta key if available
        const firstItem = importedGroups[0].items?.[0];
        if (firstItem && firstItem.meta) {
          const keys = Object.keys(firstItem.meta);
          if (keys.length > 0) {
            setPrimaryMetaKey(keys[0]);
            setBadgeMetaKey(keys[0]);
          }
        }
      } else {
        setWordlistInfo({
          title: '无字表',
          count: 0
        });
      }

      setActiveGroupIndex('all');
      setActiveItemIndex(0);
      setImportResult({
        warnings: data.warnings,
        summary: data.summary
      });
    } catch (err) {
      await customAlert(err.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleWordlistUpload = (e) => {
    const file = e.target.files[0];
    if (file) uploadWordlistFile(file);
    e.target.value = null;
  };

  const handleProjectUpload = (e) => {
    const file = e.target.files[0];
    if (file) uploadProjectFile(file);
    e.target.value = null;
  };

  const handleImportButtonClick = () => {
    setShowImportModal(true);
  };

  const handleImportFolder = async () => {
    try {
      const selected = await open({
        directory: true,
        multiple: false
      });
      if (!selected) return;

      setIsProcessing(true);
      const res = await apiFetch('/project/import_folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: selected })
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || '导入文件夹失败');
      }

      const data = await res.json();
      resetPlayback();
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      const state = data.state;
      projectStateRef.current = state;

      // Apply imported project state
      const importedSpeakers = state.speakers || {};
      setSpeakers(importedSpeakers);

      const importedActiveSpeakerId = state.active_speaker_id || '';
      setActiveSpeakerId(importedActiveSpeakerId);

      const importedGroups = state.groups || [];
      setGroups(importedGroups);

      if (importedGroups.length > 0) {
        setWordlistInfo({
          title: '已导入文件夹工程',
          count: importedGroups.reduce((acc, g) => acc + g.items.length, 0)
        });

        // Auto select first meta key if available
        const firstItem = importedGroups[0].items?.[0];
        if (firstItem && firstItem.meta) {
          const keys = Object.keys(firstItem.meta);
          if (keys.length > 0) {
            setPrimaryMetaKey(keys[0]);
            setBadgeMetaKey(keys[0]);
          }
        }
      } else {
        setWordlistInfo({
          title: '无字表',
          count: 0
        });
      }

      setActiveGroupIndex('all');
      setActiveItemIndex(0);
      setImportResult({
        warnings: data.warnings,
        summary: data.summary
      });
    } catch (err) {
      await customAlert(err.message);
      fetchProjectState();
    } finally {
      setIsProcessing(false);
    }
  };

  const handleProjectExport = async () => {
    if (Object.keys(speakers).length === 0) {
      await customAlert('当前工程尚未添加任何发音人，禁止导出工程！');
      return;
    }

    try {
      setIsProcessing(true);
      await saveProjectState(speakers);
    } catch (err) {
      console.error('Failed to save project state before export:', err);
      return;
    } finally {
      setIsProcessing(false);
    }

    if (capabilities.wavFolderExport) {
      let destination = folderPathSetting;
      if (!destination) {
        destination = await open({ directory: true, multiple: false });
        if (destination) await updateFolderPathSetting(destination);
      }
      if (!destination) return;
      try {
        setIsProcessing(true);
        const result = await runtime.exportWavFolder(destination);
        await customAlert(`WAV 导出完成：成功 ${result.exported} 条，跳过 ${result.skipped} 条。\n保存位置：${result.output_dir}`);
      } catch (error) {
        console.error(error);
        await customAlert(`导出 WAV 失败：${error.message || error}`);
      } finally {
        setIsProcessing(false);
      }
      return;
    }

    if (saveFormatSetting === 'folder') {
      let path = folderPathSetting;
      if (!path) {
        const selected = await open({ directory: true, multiple: false });
        if (!selected) return;
        path = selected;
        updateFolderPathSetting(selected);
      }

      try {
        setIsProcessing(true);
        const res = await apiFetch('/project/export_folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_path: path })
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || '导出文件夹失败');
        }
        await customAlert('工程已成功导出到文件夹：' + path);
      } catch (error) {
        console.error(error);
        await customAlert(`导出文件夹工程失败：${error.message || error}`);
      } finally {
        setIsProcessing(false);
      }
    } else {
      try {
        setIsProcessing(true);
        const res = await apiFetch('/project/export');
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || '导出工程失败');
        }
        const sanitizeProjectName = (name) => {
          if (!name) return 'PhonRec_Project.teproj';
          let sanitized = name.replace(/[<>:"/\\|?*]/g, '').trim();
          if (!sanitized) sanitized = 'PhonRec_Project';
          if (!sanitized.endsWith('.teproj')) {
            sanitized += '.teproj';
          }
          return sanitized;
        };
        const destination = await save({
          defaultPath: sanitizeProjectName(defaultProjectNameSetting),
          filters: [{ name: 'PhonTracer 工程', extensions: ['teproj'] }],
        });
        if (!destination) return;
        await writeFile(destination, new Uint8Array(await res.arrayBuffer()));
        await customAlert('工程已成功保存！');
      } catch (error) {
        console.error(error);
        await customAlert(`导出工程失败：${error.message || error}`);
      } finally {
        setIsProcessing(false);
      }
    }
  };

  const handleProjectClear = async () => {
    const ok = await customConfirm('确定清空当前工作区，开始全新的录制吗？');
    if (!ok) return;
    try {
      await runtime.clearProject();
      resetPlayback();
      await closeMicStream();
      setSpeakers({});
      projectStateRef.current = { version: '1.0' };
      setActiveSpeakerId('');
      setGroups([]);
      setActiveGroupIndex('all');
      setActiveItemIndex(0);
      setWordlistInfo({ title: '无字表', count: 0 });
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();

      // 工作区只保存工程数据；清空后重新应用已保存设置，撤销面板因当前工程产生的临时显示调整。
      await settingsSaveQueueRef.current.catch(() => {});
      await loadAndApplySettings();

      await customAlert('工作区已清空，原有设置保持不变');
    } catch (err) {
      console.error(err);
      await customAlert(`清空工作区失败：${err.message || err}`);
    }
  };

  // --- Drag and Drop Handlers ---
  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    setIsDragging(false);
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      const file = files[0];
      const filename = file.name.toLowerCase();
      if (isStandalone) {
        if (filename.endsWith('.txt')) {
          try {
            openPlainWordlistModal(await file.text(), file.name);
          } catch (error) {
            await customAlert(`读取 TXT 文件失败：${error.message || error}`);
          }
        } else {
          await customAlert('独立模式只能导入 TXT 普通字表，不支持 CSV、PTWL 或 TEPROJ。');
        }
        return;
      }
      if (filename.endsWith('.teproj')) {
        await uploadProjectFile(file);
      } else if (filename.endsWith('.ptwl') || filename.endsWith('.txt') || filename.endsWith('.csv')) {
        await uploadWordlistFile(file);
      } else {
        await customAlert('不支持的文件格式！请拖入 .ptwl/.txt/.csv 字表文件或 .teproj 工程文件。');
      }
    }
  };

  // --- Recording Logic ---
  const startRecording = async () => {
    if (isProcessingRef.current || isRecordingRef.current || isStartingRef.current) return;
    isStartingRef.current = true;
    shouldCancelRef.current = false;

    if (!activeSpeakerId) {
      await customAlert('请先添加并选择一个发音人！');
      isStartingRef.current = false;
      return;
    }
    const activeItem = getActiveItem();
    if (!activeItem) {
      await customAlert('请导入字表以开始录音！');
      isStartingRef.current = false;
      return;
    }

    try {
      setIsRecording(true);
      isRecordingRef.current = true;
      setSpectrogramUrl('');
      setQualityResults(null);

      audioChunksRef.current = [];
      const vadPresetOptions = {
        robust: { minSpeechMs: 350, trailingSilenceMs: 1000, enterRatio: 3.5, exitRatio: 2.0 },
        standard: { minSpeechMs: 220, trailingSilenceMs: 700, enterRatio: 3.2, exitRatio: 1.8 },
        sensitive: { minSpeechMs: 150, trailingSilenceMs: 450, enterRatio: 2.8, exitRatio: 1.5 }
      }[vadPresetSetting] || { minSpeechMs: 220, trailingSilenceMs: 700, enterRatio: 3.2, exitRatio: 1.8 };
      vadEngineRef.current = createVadEngine(vadPresetOptions);
      vadEngineRef.current.reset();

      resetPlayback();

      // Ensure stream is active
      await ensureMicStream();

      // If user released the button or cancelled during setup, abort
      if (shouldCancelRef.current || (recordingModeRef.current === 'hold' && !isMouseDownRef.current)) {
        shouldCancelRef.current = false;
        setIsRecording(false);
        isRecordingRef.current = false;
        if (!qualityChecksEnabledRef.current || !liveInputMonitorSetting) {
          await closeMicStream();
        }
        isStartingRef.current = false;
        return;
      }

      if (selectedDeviceId.startsWith('loopback:')) {
        await invoke('start_loopback_recording');
      }

    } catch (err) {
      console.error(err);
      setIsRecording(false);
      isRecordingRef.current = false;
      await customAlert('录音访问失败，请确认权限并重试！');
    } finally {
      isStartingRef.current = false;
    }
  };

  const stopRecording = async (shouldAutoAdvance = true, isAutoVAD = false, discardReason = null) => {
    if (isStartingRef.current) {
      shouldCancelRef.current = true;
      return;
    }
    if (!isRecordingRef.current) return;

    isRecordingRef.current = false;
    setIsRecording(false);
    setIsProcessing(true);
    isProcessingRef.current = true;
    setVadLevel(0);
    setVadSpeaking(false);

    if (selectedDeviceId.startsWith('loopback:')) {
      try {
        const sampleRate = Number(sampleRateSetting);
        const wavBytes = await invoke('stop_loopback_recording', { sampleRate });
        const wavBlob = new Blob([new Uint8Array(wavBytes)], { type: 'audio/wav' });

        // draw static waveform
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const tempCtx = new AudioContext();
        const arrayBuffer = await wavBlob.arrayBuffer();
        const audioBuffer = await tempCtx.decodeAudioData(arrayBuffer);
        const floatBuffer = audioBuffer.getChannelData(0);
        drawStaticWaveform(floatBuffer);
        tempCtx.close();

        if (discardReason) {
          await handleVadDiscard(discardReason);
        } else {
          await uploadAudio(wavBlob, shouldAutoAdvance, isAutoVAD);
        }
      } catch (err) {
        console.error(err);
        await customAlert('保存回环录音失败：' + err.message);
        setIsProcessing(false);
      }
      return;
    }

    // Otherwise browser microphone
    if (!qualityChecksEnabledRef.current || !liveInputMonitorSetting) {
      await closeMicStream();
    }

    const totalLength = audioChunksRef.current.reduce((acc, chunk) => acc + chunk.length, 0);
    const floatBuffer = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of audioChunksRef.current) {
      floatBuffer.set(chunk, offset);
      offset += chunk.length;
    }

    drawStaticWaveform(floatBuffer);
    const sourceSampleRate = audioContextRef.current?.sampleRate || 16000;
    const targetSR = Number(sampleRateSetting);
    const resampledBuffer = resampleAudio(floatBuffer, sourceSampleRate, targetSR);
    const wavBlob = bufferToWav(resampledBuffer, targetSR);
    if (discardReason) {
      await handleVadDiscard(discardReason);
    } else {
      await uploadAudio(wavBlob, shouldAutoAdvance, isAutoVAD);
    }
  };

  const handleVadDiscard = async () => {
    const item = displayedItemsRef.current[activeItemIndexRef.current];
    const key = `${activeSpeakerIdRef.current}:${item?.id || ''}`;
    const retryCount = (vadRetryRef.current.get(key) || 0) + 1;
    vadRetryRef.current.set(key, retryCount);
    setIsProcessing(false);
    isProcessingRef.current = false;
    if (retryCount <= 2) {
      setTimeout(() => startRecording(), 600);
    } else {
      vadRetryRef.current.delete(key);
      await customAlert('连续三次未检测到有效语音，智能跳转已暂停。请检查输入设备或改用手动录音。');
    }
  };

  const uploadAudio = async (blob, shouldAutoAdvance, isAutoVAD = false) => {
    const activeItem = displayedItemsRef.current[activeItemIndexRef.current];
    const spkId = activeSpeakerIdRef.current;
    if (!activeItem || !spkId) return;

    const device = audioDevices.find(d => d.id === selectedDeviceId);
    const deviceName = device ? device.name : '未知设备';

    try {
      const data = await runtime.saveAudio({
        blob,
        speakerId: spkId,
        wordId: activeItem.id,
        source: deviceName,
        qualityRules: qualityRulesRef.current,
      });

      const needsRetry = qualityChecksEnabledRef.current && data.quality?.decision === 'retry';

      const updatedSpeakers = { ...speakersRef.current };
      if (!updatedSpeakers[spkId]) {
        console.warn(`Speaker ${spkId} no longer exists in speakers state. Skipping state update.`);
        return;
      }
      if (!updatedSpeakers[spkId].items) {
        updatedSpeakers[spkId].items = {};
      }

      if (needsRetry) {
        if (updatedSpeakers[spkId].items[activeItem.id]) {
          const item = updatedSpeakers[spkId].items[activeItem.id];
          updatedSpeakers[spkId].items[activeItem.id] = {
            ...item,
            path: null,
            quality: null,
            recorded_at: null,
            duration_ms: null,
            sample_rate_hz: null,
            channels: null,
            format: null,
            source: null
          };
        } else {
          updatedSpeakers[spkId].items[activeItem.id] = {
            id: activeItem.id,
            label: activeItem.label,
            note: activeItem.note,
            tags: activeItem.tags,
            aliases: activeItem.aliases || [],
            meta: activeItem.meta || {},
            metadata_source: activeItem.metadata_source || '录音软件',
            path: null,
            quality: null,
            recorded_at: null,
            duration_ms: null,
            sample_rate_hz: null,
            channels: null,
            format: null,
            source: null
          };
        }
      } else {
        updatedSpeakers[spkId].items[activeItem.id] = buildRecordedItem(activeItem, data);
      }

      setSpeakers(updatedSpeakers);
      await saveProjectState(updatedSpeakers);

      // Update local view states instantly
      if (data.spectrogram) setSpectrogramUrl(data.spectrogram);
      if (data.quality) setQualityResults(data.quality);

      if (needsRetry) {
        if (isAutoVAD) {
          const key = `${spkId}:${activeItem.id}`;
          const retryCount = (vadRetryRef.current.get(key) || 0) + 1;
          vadRetryRef.current.set(key, retryCount);
          if (retryCount <= 2) {
            setTimeout(() => startRecording(), 1000);
          } else {
            vadRetryRef.current.delete(key);
            await customAlert(`连续三次质量检测未通过：${data.quality.issues?.join('、') || '请检查录音环境'}。智能跳转已暂停。`);
          }
        }
      } else {
        vadRetryRef.current.delete(`${spkId}:${activeItem.id}`);
        if (shouldAutoAdvance) {
          setTimeout(() => {
            navigateItem(1);
            if (isAutoVAD) {
              setTimeout(() => {
                startRecording();
              }, 100);
            }
          }, 500);
        }
      }
    } catch (err) {
      console.error(err);
      alert('上传音频失败: ' + err.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const analyzeAudio = async (speakerId, wordId) => {
    if (!capabilities.fullQuality) return;

    try {
      const data = await runtime.analyzeAudio({
        speakerId,
        wordId,
        qualityRules: qualityRulesRef.current,
      });
      if (!data) return;

      if (data.spectrogram) setSpectrogramUrl(data.spectrogram);

      // If backend analysis returns quality checks, sync them back into speakers state
      if (data.quality) {
        setQualityResults(data.quality);
        const recordMeta = speakersRef.current[speakerId]?.items?.[wordId];
        if (recordMeta && !recordMeta.quality) {
          const updated = { ...speakersRef.current };
          updated[speakerId].items = {
            ...updated[speakerId].items,
            [wordId]: {
              ...updated[speakerId].items[wordId],
              quality: data.quality
            }
          };
          setSpeakers(updated);
          saveProjectState(updated);
        }
      }
    } catch (err) {
      console.error(err);
    }
  };

  const playRecordedAudio = async () => {
    if (isRecordingRef.current || isProcessingRef.current) return;
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) return;

    const recordMeta = speakers[activeSpeakerId]?.items?.[activeItem.id];
    if (!recordMeta || !recordMeta.path) return;

    const player = audioPlayerRef.current;
    if (!player) return;

    const isSameAudio = playbackState.currentSpeakerId === activeSpeakerId && playbackState.currentWordId === activeItem.id;

    if (isSameAudio) {
      if (playbackState.isPlaying) {
        player.pause();
      } else {
        try {
          await player.play();
        } catch (error) {
          console.error('播放失败:', error);
        }
      }
    } else {
      try {
        player.pause();
        if (player.src && player.src.startsWith('blob:')) {
          URL.revokeObjectURL(player.src);
        }

        const audioUrl = URL.createObjectURL(await runtime.readAudio({
          speakerId: activeSpeakerId,
          wordId: activeItem.id,
        }));
        player.src = audioUrl;

        setPlaybackState({
          isPlaying: false,
          currentTime: 0,
          duration: 0,
          currentSpeakerId: activeSpeakerId,
          currentWordId: activeItem.id
        });

        await player.play();
      } catch (error) {
        console.error('播放新音频失败:', error);
      }
    }
  };

  const discardRecordedAudio = async () => {
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) return;

    const recordMeta = speakers[activeSpeakerId]?.items?.[activeItem.id];
    if (!recordMeta || !recordMeta.path) return;

    const ok = await customConfirm('确定要丢弃当前词条的录音吗？');
    if (!ok) return;

    resetPlayback();
    const updatedSpeakers = {
      ...speakers,
      [activeSpeakerId]: {
        ...speakers[activeSpeakerId],
        items: { ...speakers[activeSpeakerId].items },
      },
    };
    if (updatedSpeakers[activeSpeakerId].items[activeItem.id]) {
      const item = updatedSpeakers[activeSpeakerId].items[activeItem.id];
      updatedSpeakers[activeSpeakerId].items[activeItem.id] = {
        ...item,
        path: null,
        quality: null,
        recorded_at: null,
        duration_ms: null,
        sample_rate_hz: null,
        channels: null,
        format: null,
        source: null
      };
    }
    setSpeakers(updatedSpeakers);
    await saveProjectState(updatedSpeakers);

    setSpectrogramUrl('');
    setQualityResults(null);
    clearCanvas();
  };

  stopRecordingRef.current = stopRecording;

  // --- Visualizer ---
  const clearCanvas = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const styles = getComputedStyle(document.documentElement);
    ctx.fillStyle = styles.getPropertyValue('--bg-primary').trim() || '#f8fafc';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  };

  const drawStaticWaveform = (floatBuffer) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    const styles = getComputedStyle(document.documentElement);
    ctx.fillStyle = styles.getPropertyValue('--bg-primary').trim() || '#f8fafc';
    ctx.fillRect(0, 0, width, height);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = styles.getPropertyValue('--color-success').trim() || '#10b981';
    ctx.beginPath();

    const step = Math.ceil(floatBuffer.length / width);
    const amp = height / 2;

    for (let i = 0; i < width; i++) {
      let min = 1.0;
      let max = -1.0;
      for (let j = 0; j < step; j++) {
        const dat = floatBuffer[i * step + j];
        if (dat < min) min = dat;
        if (dat > max) max = dat;
      }
      ctx.moveTo(i, (1 + min) * amp);
      ctx.lineTo(i, (1 + max) * amp);
    }
    ctx.stroke();
  };

  const drawStaticWaveformFromUrl = async () => {
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) return;

    try {
      const audioBlob = await runtime.readAudio({
        speakerId: activeSpeakerId,
        wordId: activeItem.id,
      });
      const arrayBuffer = await audioBlob.arrayBuffer();

      const AudioContext = window.AudioContext || window.webkitAudioContext;
      const tempCtx = new AudioContext();
      const audioBuffer = await tempCtx.decodeAudioData(arrayBuffer);
      const floatBuffer = audioBuffer.getChannelData(0);
      drawStaticWaveform(floatBuffer);
      tempCtx.close();
    } catch (err) {
      console.error(err);
    }
  };

  const navigateItem = (direction) => {
    if (displayedItems.length === 0) return;

    let newIndex = activeItemIndex + direction;
    if (newIndex >= 0 && newIndex < displayedItems.length) {
      setActiveItemIndex(newIndex);
    }
  };

  // Compute active item for use in JSX
  const activeItem = getActiveItem();
  const noteFontSize = Math.max(18, Math.round(charFontSize * 0.24));

  return (
    <div
      className="app-container"
      onDragOver={handleDragOver}
    >
      {isStandalone && showStandaloneBanner && (
        <div className="standalone-mode-banner">
          <div className="standalone-mode-banner-content">
            <InfoIcon />
            <span>独立录音模式：支持本地录音、播放、音量与削波检测及 WAV 导出；工程归档和高级分析需安装 PhonTracer。</span>
          </div>
          <button
            type="button"
            className="standalone-mode-banner-close"
            onClick={handleCloseStandaloneBanner}
            title="关闭提示"
          >
            <CloseIcon />
          </button>
        </div>
      )}
      {/* Floating Toggle Button for Drawer Sidebar (Visible on small screens) */}
      <button
        className={`sidebar-toggle-btn ${isLeftSidebarOpen ? 'drawer-open' : ''}`}
        onClick={() => setIsLeftSidebarOpen(!isLeftSidebarOpen)}
        title="展开/折叠发音人与字表"
      >
        <MenuIcon />
      </button>

      {/* Drawer Backdrop Overlay (Visible on small screens when drawer is open) */}
      <div
        className={`drawer-backdrop ${isLeftSidebarOpen ? 'active' : ''}`}
        onClick={() => setIsLeftSidebarOpen(false)}
      ></div>

      {/* Microphone Permission Guidance Overlay */}
      {showMicGuidance && (
        <div className="mic-guidance-overlay" style={{ zIndex: 99999 }}>
          <div className="mic-guidance-arrow">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="20" y1="20" x2="4" y2="4" />
              <polyline points="4 12 4 4 12 4" />
            </svg>
            <div className="arrow-pulse"></div>
          </div>

          <div className="mic-guidance-card">
            <div className="mic-guidance-icon-wrapper">
              <svg className={`mic-pulse-icon ${!micBlocked ? 'pulsing' : 'error'}`} width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
              </svg>
            </div>

            {micBlocked ? (
              <>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', justifyContent: 'center' }}>
                  <MicIcon active={false} /> 麦克风访问被拒绝
                </h3>
                <p>我们无法使用您的麦克风。请按照以下步骤重新授予权限：</p>
                <div className="mic-guidance-steps">
                  <div>1. 点击窗口左上角的 <strong>设置/锁</strong> 按钮</div>
                  <div>2. 找到 <strong>麦克风权限</strong> 并将其开启</div>
                  <div>3. 开启后点击下方“重新检测”或刷新软件</div>
                </div>
                <button className="btn-primary" style={{ marginTop: '1rem', width: '100%' }} onClick={() => setShowMicGuidance(false)}>
                  知道了
                </button>
              </>
            ) : (
              <>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', justifyContent: 'center' }}>
                  <MicIcon active={true} /> 请求麦克风使用权限
                </h3>
                <p>为了支持 <strong>实时音量检测</strong> 和 <strong>音频质量检测</strong>，我们需要使用您的麦克风。</p>
                <p className="mic-guidance-alert">请在窗口左上角弹出的系统提示中点击 <strong>「允许 (Allow)」</strong> 以继续。</p>
              </>
            )}
          </div>
        </div>
      )}

      {/* Drag and Drop Visual Overlay */}
      {isDragging && (
        <div
          className="modal-overlay"
          style={{ flexDirection: 'column', gap: '1rem', zIndex: 200 }}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onDragOver={e => e.preventDefault()}
        >
          <ImportIcon style={{ width: '48px', height: '48px', color: 'var(--color-accent)' }} />
          <span style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text-primary)' }}>
            释放文件以导入
          </span>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            {isStandalone
              ? '独立模式仅支持 TXT 普通字表'
              : '支持字表文件 (.ptwl / .txt / .csv) 或工程归档 (.teproj)'}
          </span>
        </div>
      )}

      {/* Main Workspace (3 columns in sequential order) */}
      <main className="app-workspace">

        {/* Column 1: Import and Speakers */}
        <section className={`glass-panel left-sidebar ${isLeftSidebarOpen ? 'drawer-open' : ''}`}>
          <div className="panel-header">
            <span className="panel-title">
              <BookIcon /> 发音人与字表
            </span>
          </div>
          <div className="panel-body">
            {/* Wordlist actions */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <button className="btn-primary" onClick={() => isStandalone ? openPlainWordlistModal() : triggerWordlistUpload()}>
                <ImportIcon /> 导入字表
              </button>
              {!isStandalone && (
                <input
                  type="file"
                  ref={fileInputRef}
                  style={{ display: 'none' }}
                  accept=".ptwl,.txt,.csv"
                  onChange={handleWordlistUpload}
                />
              )}
              <div className="info-card">
                <div className="info-row">
                  <span>当前字表:</span>
                  <span className="info-value" style={{ maxWidth: '110px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {wordlistInfo.title}
                  </span>
                </div>
                <div className="info-row">
                  <span>总词数:</span>
                  <span className="info-value">{wordlistInfo.count} 字</span>
                </div>
              </div>
            </div>

            {/* Custom display fields */}
            {!isStandalone && (
            <div className="info-card">
              <div className="panel-title" style={{ fontSize: '0.8rem', marginBottom: '0.25rem', textTransform: 'none' }}>
                <BookIcon /> 字段显示设置
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>列表角标:</span>
                  <CustomSelect
                    value={badgeMetaKey}
                    onChange={updateBadgeMetaKey}
                    options={[
                      { value: 'none', label: '无' },
                      { value: 'note', label: '提示信息 (note)' },
                      ...getAvailableMetaKeys().map(k => ({ value: k, label: k }))
                    ]}
                    style={{ width: '100%' }}
                  />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>中心提示:</span>
                  <CustomSelect
                    value={primaryMetaKey}
                    onChange={updatePrimaryMetaKey}
                    options={[
                      { value: 'none', label: '无' },
                      { value: 'note', label: '提示信息 (note)' },
                      ...getAvailableMetaKeys().map(k => ({ value: k, label: k }))
                    ]}
                    style={{ width: '100%' }}
                  />
                </div>
              </div>
            </div>

            )}

            {/* Speaker management */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', flex: 1, minHeight: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)' }}>发音人列表:</span>
                <button className="btn-secondary" style={{ padding: '0.2rem 0.6rem', fontSize: '0.75rem' }} onClick={() => { setIsAddingSpeaker(true); setNewSpeakerName(''); }}>
                  + 添加
                </button>
              </div>

              <div className="speaker-list">
                {Object.values(speakers).map(spk => (
                  <div
                    key={spk.id}
                    className={`speaker-item ${activeSpeakerId === spk.id ? 'active' : ''}`}
                    onClick={async () => {
                      setActiveSpeakerId(spk.id);
                      try {
                        await saveProjectState(speakers, groupsRef.current, spk.id);
                      } catch (err) {
                        console.error('Failed to switch speaker / save project state:', err);
                      }
                    }}
                  >
                    <span className="speaker-name">
                      <UserIcon /> {spk.name}
                    </span>
                    <span className="speaker-actions">
                      <button className="btn-icon" onClick={(e) => handleDeleteSpeaker(spk.id, e)}>
                        <TrashIcon />
                      </button>
                    </span>
                  </div>
                ))}
                {isAddingSpeaker && (
                  <div className="speaker-item active" style={{ padding: '0.35rem 0.8rem' }}>
                    <span className="speaker-name" style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <UserIcon />
                      <input
                        type="text"
                        className="speaker-inline-input"
                        placeholder="姓名..."
                        value={newSpeakerName}
                        onChange={(e) => setNewSpeakerName(e.target.value)}
                        onBlur={handleInlineSpeakerBlur}
                        onKeyDown={handleInlineSpeakerKeyDown}
                        autoFocus
                        style={{
                          background: 'transparent',
                          border: 'none',
                          outline: 'none',
                          color: 'var(--text-primary)',
                          fontSize: '0.85rem',
                          fontWeight: 500,
                          width: '100%',
                          padding: 0
                        }}
                      />
                    </span>
                  </div>
                )}
                {Object.keys(speakers).length === 0 && !isAddingSpeaker && (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center', padding: '1rem' }}>
                    请添加发音人
                  </div>
                )}
              </div>
            </div>

            {/* Active stats */}
            <div className="info-card">
              <div className="panel-title" style={{ fontSize: '0.8rem', marginBottom: '0.25rem', textTransform: 'none' }}>
                <ChartIcon /> 当前发音人进度
              </div>
              <div className="info-row">
                <span>已录制:</span>
                <span className="info-value">{getCompletedCount()} / {getTotalCount()}</span>
              </div>
              <div className="info-row">
                <span>进度百分比:</span>
                <span className="info-value">
                  {getTotalCount() > 0 ? Math.round((getCompletedCount() / getTotalCount()) * 100) : 0}%
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* Column 2: Merged Big Card (Word display & Word list) */}
        <section className="glass-panel main-recording-card" style={{ display: 'flex', flexDirection: 'row', padding: 0, minHeight: 0 }}>

          {/* Left side: Big Word display & Buttons */}
          <div className="center-column" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {activeItem ? (
            <div className="character-card">
              <span className="char-group-indicator">
                当前: {activeItemIndex + 1} / {displayedItems.length}
              </span>

              <div className="font-size-slider-container" title="调整字号大小">
                <span style={{ fontSize: '0.75rem' }}>A</span>
                <input
                  type="range"
                  min="60"
                  max="200"
                  value={charFontSize}
                  onChange={(e) => updateCharFontSize(Number(e.target.value))}
                  className="font-size-slider"
                />
                <span style={{ fontSize: '1.05rem', fontWeight: 700 }}>A</span>
              </div>
              <div className="char-display-container">
                <span className="char-display" style={{ fontSize: `${charFontSize}px`, transition: 'font-size 0.1s ease' }}>{activeItem.label}</span>
                {primaryMetaKey !== 'none' && (
                  <span
                    className="char-pinyin"
                    style={{ fontSize: `${noteFontSize}px`, transition: 'font-size 0.1s ease' }}
                  >
                    {primaryMetaKey === 'note' ? activeItem.note : (activeItem.meta?.[primaryMetaKey] || '')}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="character-card" style={{ color: 'var(--text-muted)', fontSize: '1rem', textAlign: 'center' }}>
              请导入字表（支持拖拽）或选择发音人
            </div>
          )}

          {/* Playback Seek Bar */}
          {activeItem && speakers[activeSpeakerId]?.items?.[activeItem.id]?.path && (
            <div className="playback-progress-container">
              <input
                type="range"
                className="playback-slider"
                min="0"
                max={playbackState.duration || 100}
                step="0.01"
                value={playbackState.currentTime}
                onChange={handleSeekChange}
                disabled={
                  isRecording
                  || isProcessing
                  || playbackState.currentSpeakerId !== activeSpeakerId
                  || playbackState.currentWordId !== activeItem.id
                }
              />
              <div className="playback-time">
                {formatPlaybackTime(playbackState.currentTime)} / {formatPlaybackTime(playbackState.duration)}
              </div>
            </div>
          )}

          {/* Controls Panel */}
          <div className="controls-card">
            <span className={`recording-status ${isRecording ? 'recording' : (isProcessing ? 'processing' : 'ready')}`}>
              {isRecording ? '录音中' : (isProcessing ? '处理中' : '准备就绪')}
            </span>

            {/* VAD progress visualizer */}
            {recordingMode === 'vad' && (
              <div style={{ width: '240px', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <MicIcon active={vadSpeaking} /> {vadSpeaking ? '说话中 (静音自动跳转)' : '静音中 (请发音)'}
                </span>
                <div className="vad-level-bar">
                  <div
                    className={`vad-level-fill ${vadSpeaking ? 'speaking' : ''}`}
                    style={{ width: `${vadLevel}%` }}
                  ></div>
                </div>
              </div>
            )}

            <div className="recording-buttons">
              {/* Play button */}
              <button
                className="nav-arrow play-btn"
                onClick={playRecordedAudio}
                disabled={isRecording || isProcessing || !activeItem || !speakers[activeSpeakerId]?.items?.[activeItem.id]?.path}
                title="播放录音 (R键)"
              >
                {playbackState.isPlaying && playbackState.currentSpeakerId === activeSpeakerId && playbackState.currentWordId === activeItem?.id ? (
                  <PauseIcon />
                ) : (
                  <PlayIcon />
                )}
              </button>

              {/* Previous arrow */}
              <button
                className="nav-arrow prev-btn"
                onClick={() => navigateItem(-1)}
                disabled={isRecording || isProcessing || activeItemIndex === 0}
                title="上一个 (左方向键)"
              >
                <ChevronLeft />
              </button>

              {/* Record Button */}
              <div className="record-btn-wrapper">
                <div className={`record-ring ${isRecording ? 'active' : ''}`}></div>
                <button
                  className={`btn-record ${isRecording ? 'recording' : ''} ${isProcessing ? 'processing' : ''}`}
                  onMouseDown={recordingMode === 'hold' ? () => {
                    isMouseDownRef.current = true;
                    startRecording();
                  } : null}
                  onMouseUp={recordingMode === 'hold' ? () => {
                    isMouseDownRef.current = false;
                    stopRecording(true);
                  } : null}
                  onMouseLeave={recordingMode === 'hold' && isRecording ? () => {
                    isMouseDownRef.current = false;
                    stopRecording(true);
                  } : null}
                  onTouchStart={recordingMode === 'hold' ? () => {
                    isMouseDownRef.current = true;
                    startRecording();
                  } : null}
                  onTouchEnd={recordingMode === 'hold' ? () => {
                    isMouseDownRef.current = false;
                    stopRecording(true);
                  } : null}
                  onClick={recordingMode !== 'hold' ? () => {
                    if (isProcessingRef.current) return;
                    if (isRecordingRef.current) {
                      stopRecording(true);
                    } else {
                      startRecording();
                    }
                  } : null}
                  disabled={isProcessing}
                  title={recordingMode === 'hold' ? '按住录音，松开停止' : '点击录音，再次点击停止 (空格键)'}
                >
                  <div className="record-core"></div>
                </button>
              </div>

              {/* Next arrow */}
              <button
                className="nav-arrow next-btn"
                onClick={() => navigateItem(1)}
                disabled={isRecording || isProcessing || activeItemIndex === displayedItems.length - 1}
                title="下一个 (右方向键)"
              >
                <ChevronRight />
              </button>

              {/* Discard button */}
              <button
                className="nav-arrow discard-btn"
                onClick={discardRecordedAudio}
                disabled={isRecording || isProcessing || !activeItem || !speakers[activeSpeakerId]?.items?.[activeItem.id]?.path}
                title="丢弃录音"
              >
                <TrashIcon />
              </button>
            </div>

            {/* Keyboard hints at the bottom of controls card */}
            {showShortcutHintsSetting && (
              <div className="keyboard-hints" style={{ display: 'flex', flexWrap: 'wrap', width: '100%', justifyContent: 'center', alignItems: 'center', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)', borderTop: '1px solid var(--border-color)', paddingTop: '0.75rem', marginTop: '0.5rem' }}>
                <span><KeyboardIcon /> [空格] 录音/停止</span>
                <span>[← / →] 切换字表词条</span>
                {activeItem && speakers[activeSpeakerId]?.items?.[activeItem.id]?.path && (
                  <span style={{ color: 'var(--color-accent)', cursor: 'pointer', fontWeight: 'bold' }} onClick={playRecordedAudio}>
                    [R] 播放录音
                  </span>
                )}
              </div>
            )}
          </div>
          
          {/* Center Column Bottom Bar: Project Management Actions */}
          <div className="center-bottom-bar">
            <div className="switch-container center-bottom-order">
              <span>随机排序录制</span>
              <label className="switch" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={randomizeOrder}
                  onChange={(e) => updateRandomizeOrder(e.target.checked)}
                />
                <span className="slider"></span>
              </label>
            </div>

            <div className="center-bottom-actions">
              {!isStandalone && (
                <>
                  <button className="btn-secondary" style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }} onClick={handleImportButtonClick}>
                    <ExportIcon /> 导入
                  </button>
                  <input
                    type="file"
                    ref={projectInputRef}
                    style={{ display: 'none' }}
                    accept=".teproj"
                    onChange={handleProjectUpload}
                  />
                </>
              )}
              <button className="btn-primary" style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }} onClick={handleProjectExport}>
                <ImportIcon /> {isStandalone ? '导出 WAV' : '保存'}
              </button>
            </div>
          </div>
          </div>

          {/* Right side: Word scroll list */}
          <div className="word-scroll-panel" style={{ borderLeft: '1px solid var(--border-color)', background: 'transparent' }}>
          <div className="panel-header" style={{ padding: '0.75rem 1rem' }}>
            <span className="panel-title" style={{ fontSize: '0.8rem' }}>
              <BookIcon /> 字表词条
            </span>
          </div>

          <div className="panel-body" style={{ padding: '0.75rem' }}>
            {/* Group selector - with "All" option */}
            <div className="word-group-selector" style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', marginBottom: '0.5rem' }}>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>当前发音组:</span>
              <CustomSelect
                style={{ width: '100%' }}
                value={activeGroupIndex}
                onChange={setActiveGroupIndex}
                options={[
                  { value: 'all', label: '【全部发音组】' },
                  ...groups.map((g, idx) => ({
                    value: String(idx),
                    label: `${g.name} (${g.items.length}字)`
                  }))
                ]}
              />
            </div>

            {/* Scroll list */}
            <div className="word-list-container">
              {displayedItems.map((item, idx) => {
                const isActive = activeItemIndex === idx;
                const recordMeta = speakers[activeSpeakerId]?.items?.[item.id];
                const isRecorded = !!(recordMeta && recordMeta.path);

                let itemClass = 'word-item';
                if (isActive) itemClass += ' active';
                if (isRecorded) itemClass += ' recorded';

                return (
                  <div
                    id={`word-item-${item.id}`}
                    key={item.id}
                    className={itemClass}
                    onClick={() => setActiveItemIndex(idx)}
                  >
                    <div className="word-item-header">
                      <span className="word-item-label">{item.label}</span>
                      {badgeMetaKey !== 'none' && (
                        <span className="word-badge">
                          {badgeMetaKey === 'note' ? item.note : (item.meta?.[badgeMetaKey] || '')}
                        </span>
                      )}
                    </div>

                    <div className="word-item-info-row">
                      <div className="word-item-status-wrapper">
                        <span className="word-item-status-dot"></span>
                        <span className="word-item-status-text">
                          {isActive ? '录制中' : (isRecorded ? '已录制' : '未录制')}
                        </span>
                      </div>

                      {/* Detailed quality labels */}
                      {isRecorded && recordMeta.quality && (
                        <div className="word-item-quality-tags">
                          {recordMeta.quality.clipping.abnormal && (
                            <span className="quality-tag abnormal">截断</span>
                          )}
                          {(recordMeta.quality.volume.status === 'too_quiet' || recordMeta.quality.volume.status === 'too_loud') && (
                            <span className="quality-tag warning">
                              {recordMeta.quality.volume.status === 'too_quiet' ? '偏小' : '偏大'}
                            </span>
                          )}
                          {recordMeta.quality.creak.abnormal && (
                            <span className="quality-tag warning">嘎裂</span>
                          )}
                          {!recordMeta.quality.clipping.abnormal &&
                           recordMeta.quality.volume.status === 'normal' &&
                           !recordMeta.quality.creak.abnormal && (
                            <span className="quality-tag normal">优质</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {displayedItems.length === 0 && (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', textAlign: 'center', padding: '2rem 1rem' }}>
                  字表为空，请导入
                </div>
              )}
            </div>
          </div>
          </div>
        </section>

        {/* Column 4: Visualizer & Quality Control */}
        <section className="glass-panel right-column">
          <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="panel-title">
              <CheckIcon /> 检测与配置
            </span>
            <button
              className="btn-icon"
              onClick={openSettingsModal}
              title="设置"
            >
              <GearIcon />
            </button>
          </div>

          <div className="panel-body">
            {/* Quality check panel */}
            <div className="info-card quality-card" style={{ padding: '0.75rem' }}>
              <div className="switch-container" style={{ marginBottom: '0.5rem' }}>
                <span style={{ fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <CheckIcon /> 实时质量检测
                </span>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={qualityChecksEnabled}
                    onChange={(e) => updateQualityChecksEnabled(e.target.checked)}
                  />
                  <span className="slider"></span>
                </label>
              </div>

              {/* Live volume detection bar */}
              {qualityChecksEnabled && (
                <div className="live-volume-container" style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', margin: '0.25rem 0 0.75rem 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                    <span>实时音量检测:</span>
                    <span>{liveVolume}%</span>
                  </div>
                  <div className="live-volume-bar">
                    <div
                      className="live-volume-fill"
                      style={{ width: `${liveVolume}%` }}
                    ></div>
                  </div>
                </div>
              )}

              {qualityChecksEnabled && qualityResults?.decision && (
                <div style={{
                  marginBottom: '0.65rem', padding: '0.5rem 0.6rem', borderRadius: '0.55rem',
                  background: qualityResults.decision === 'accept' ? 'rgba(34,197,94,0.1)' :
                    (qualityResults.decision === 'review' ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.1)'),
                  fontSize: '0.75rem', color: 'var(--text-secondary)'
                }}>
                  <strong>{qualityResults.grade}</strong> · {qualityResults.score} 分
                  {capabilities.fullQuality && qualityResults.metrics && (
                    <span> · 语音 {Math.round(qualityResults.metrics.speech_ratio * 100)}% · 信噪比 {qualityResults.metrics.snr_db.toFixed(1)} dB</span>
                  )}
                </div>
              )}

              <div className="quality-grid">
                {qualityRules.volume?.enabled !== false && (
                  <div className="quality-item">
                    <span>音量检测</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${
                        !qualityChecksEnabled || !qualityResults ? '' :
                        (qualityResults.volume.enabled === false ? '' : (qualityResults.volume.status === 'normal' ? 'green' : 'orange'))
                      }`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.volume.label}
                      </span>
                    </div>
                  </div>
                )}
                {capabilities.fullQuality && qualityRules.creak?.enabled !== false && (
                  <div className="quality-item">
                    <span>嘎裂声</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${
                        !qualityChecksEnabled || !qualityResults ? '' :
                        (qualityResults.creak.enabled === false ? '' : (qualityResults.creak.abnormal ? 'red' : 'green'))
                      }`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.creak.label}
                      </span>
                    </div>
                  </div>
                )}
                {qualityRules.clipping?.enabled !== false && (
                  <div className="quality-item">
                    <span>音频截断</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${
                        !qualityChecksEnabled || !qualityResults ? '' :
                        (qualityResults.clipping.enabled === false ? '' : (qualityResults.clipping.abnormal ? 'red' : 'green'))
                      }`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.clipping.label}
                      </span>
                    </div>
                  </div>
                )}
                {!isStandalone && !capabilities.fullQuality && ['有效语音', '背景噪声', '嘎裂声', '直流偏移'].map(label => (
                  <div className="quality-item" key={label} style={{ opacity: 0.65 }}>
                    <span>{label}</span>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.72rem' }}>完整模式可用</span>
                  </div>
                ))}
                {capabilities.fullQuality && qualityRules.speech?.enabled !== false && (
                  <div className="quality-item">
                    <span>有效语音</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${qualityResults?.speech?.enabled === false ? '' : (qualityResults?.speech?.abnormal ? 'red' : (qualityResults?.speech ? 'green' : ''))}`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults?.speech ? '未检测' : qualityResults.speech.label}
                      </span>
                    </div>
                  </div>
                )}
                {capabilities.fullQuality && qualityRules.noise?.enabled !== false && (
                  <div className="quality-item">
                    <span>背景噪声</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${qualityResults?.noise?.enabled === false ? '' : (qualityResults?.noise?.abnormal ? 'red' : (qualityResults?.noise ? 'green' : ''))}`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults?.noise ? '未检测' : qualityResults.noise.label}
                      </span>
                    </div>
                  </div>
                )}
                {capabilities.fullQuality && qualityRules.dc_offset?.enabled !== false && (
                  <div className="quality-item">
                    <span>直流偏移</span>
                    <div className="quality-indicator">
                      <span className={`indicator-led ${qualityResults?.dc_offset?.enabled === false ? '' : (qualityResults?.dc_offset?.abnormal ? 'red' : (qualityResults?.dc_offset ? 'green' : ''))}`}></span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {!qualityChecksEnabled || !qualityResults?.dc_offset ? '未检测' : qualityResults.dc_offset.label}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Visualizer panel */}
            <div className="info-card visualizer-card" style={{ padding: 0 }}>
              {!isStandalone && (
                <div className="visualizer-tabs">
                <button
                  className={`tab-btn ${effectiveVisualizerTab === 'waveform' ? 'active' : ''}`}
                  onClick={() => updateVisualizerTab('waveform')}
                >
                  波形图
                </button>
                <button
                  className={`tab-btn ${effectiveVisualizerTab === 'spectrogram' ? 'active' : ''}`}
                  onClick={() => updateVisualizerTab('spectrogram')}
                  disabled={!capabilities.spectrogram}
                  title={capabilities.spectrogram ? '查看语谱图' : '语谱图需安装 PhonTracer'}
                >
                  {capabilities.spectrogram ? '语谱图' : '语谱图（完整模式可用）'}
                </button>
              </div>

              )}

              <div className="visualizer-viewport">
                <canvas
                  ref={canvasRef}
                  className="visualizer-canvas"
                  style={{ display: effectiveVisualizerTab === 'waveform' ? 'block' : 'none' }}
                  width={300}
                  height={150}
                />

                {effectiveVisualizerTab === 'spectrogram' && (
                  spectrogramUrl ? (
                    <img 
                      src={spectrogramUrl} 
                      alt="语谱图" 
                      className="visualizer-image" 
                      onClick={() => setShowZoomedSpectrogram(true)}
                    />
                  ) : (
                    <div className="visualizer-placeholder">
                      语谱图将在录制结束后生成
                    </div>
                  )
                )}
              </div>
            </div>

            {/* Recording settings */}
            <div className="info-card" style={{ padding: '0.75rem', marginTop: 'auto' }}>
              <div className="panel-title" style={{ fontSize: '0.8rem', marginBottom: '0.5rem', textTransform: 'none' }}>
                <MicIcon active={isRecording} /> 录音模式设置
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                  <CustomSelect
                    value={recordingMode}
                    onChange={updateRecordingMode}
                    options={[
                      { value: 'hold', label: '按住 (对讲机)' },
                      { value: 'click', label: '点击开关' },
                      { value: 'vad', label: '智能 VAD 跳转' }
                    ]}
                    style={{ width: '100%' }}
                    placement="top"
                  />
                </div>
              </div>
            </div>

            <button className="btn-secondary" style={{ fontSize: '0.8rem', color: 'var(--color-danger)', borderColor: 'var(--border-color)', width: '100%', marginTop: '0.2rem' }} onClick={handleProjectClear}>
              <SweepIcon /> 清空工作区
            </button>


          </div>
        </section>

      </main>

      <PlainWordlistModal
        key={plainWordlistDraft.key}
        isOpen={showPlainWordlistModal}
        initialText={plainWordlistDraft.text}
        initialTitle={plainWordlistDraft.title}
        onClose={() => setShowPlainWordlistModal(false)}
        onImport={handleStandaloneWordlistImport}
      />

      {/* Redesigned Settings Modal */}
      <SettingsModal
        isOpen={showSettingsModal}
        isClosing={settingsModalClosing}
        onClose={closeSettingsModal}
        settings={{
          theme: themeSetting,
          accent_color: accentColorSetting,
          ui_scale: uiScaleSetting,
          ui_density: uiDensitySetting,
          animations_enabled: animationsEnabledSetting,
          default_plot: visualizerTab,
          record_order: randomizeOrder ? 'random' : 'wordlist',
          record_mode: recordingMode,
          record_source: selectedDeviceId,
          sample_rate: sampleRateSetting,
          save_format: saveFormatSetting,
          folder_path: folderPathSetting,
          primary_meta_key: primaryMetaKey,
          badge_meta_key: badgeMetaKey,
          char_font_size: charFontSize,
          vad_preset: vadPresetSetting,
          shortcut_preset: shortcutPresetSetting,
          live_input_monitor: liveInputMonitorSetting,
          default_project_name: defaultProjectNameSetting,
          realtime_quality: qualityChecksEnabled,
          quality_rules: qualityRules,
          show_shortcut_hints: showShortcutHintsSetting
        }}
        onUpdate={updateSettings}
        onReset={async () => {
          const ok = await customConfirm('确定要恢复所有设置到默认值吗？');
          if (!ok) return;
          try {
            await resetAllSettings();
            await customAlert('设置已恢复默认值');
          } catch (err) {
            await customAlert('恢复默认设置失败：' + err);
          }
        }}
        audioDevices={audioDevices}
        onRefreshDevices={() => fetchAudioDevices(selectedDeviceId)}
        micBlocked={micBlocked}
        onCheckPermission={async () => {
          try {
            if (selectedDeviceId.startsWith('loopback:')) {
              await customAlert('当前使用系统声音回环，不需要麦克风权限。');
              return;
            }
            await closeMicStream();
            const permission = navigator.permissions?.query
              ? await navigator.permissions.query({ name: 'microphone' })
              : null;
            if (permission?.state === 'denied') {
              await invoke('reset_microphone_permission');
              await customAlert('已清除应用内的权限拒绝记录。界面刷新后将重新请求麦克风权限；如果系统仍然阻止，请在下方打开系统隐私设置。');
              window.location.reload();
              return;
            }
            await ensureMicStream(selectedDeviceId, { force: true });
            await fetchAudioDevices(selectedDeviceId);
            await customAlert('麦克风权限检测成功！');
          } catch (error) {
            console.error('重新请求麦克风权限失败:', error);
            await customAlert('麦克风请求失败，请确保设备存在并允许权限。');
          }
        }}
        onOpenPrivacy={async () => {
          try {
            await invoke('open_system_permission_settings');
          } catch (err) {
            await customAlert('无法打开系统权限设置：' + err);
          }
        }}
        onSelectFolder={async () => {
          const selected = await open({ directory: true, multiple: false });
          if (selected) {
            updateFolderPathSetting(selected);
          }
        }}
        isRecording={isRecording}
        isProcessing={isProcessing}
        runtimeMode={runtime.mode}
        capabilities={capabilities}
        metaKeyOptions={[
          { value: 'none', label: '无' },
          { value: 'note', label: '提示信息 (note)' },
          ...getAvailableMetaKeys().map(k => ({ value: k, label: k }))
        ]}
      />

      {/* Import Choice Modal */}
      {showImportModal && (
        <div className="modal-overlay" style={{ zIndex: 9999 }}>
          <div className="modal-content" style={{ maxWidth: '380px' }}>
            <div className="modal-header">
              <span style={{ fontWeight: 650, fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <ExportIcon /> 导入工程
              </span>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '1.25rem 1rem' }}>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', padding: '0.6rem 1rem', fontSize: '0.85rem' }}
                onClick={() => {
                  setShowImportModal(false);
                  triggerProjectUpload();
                }}
              >
                导入 .teproj 工程文件
              </button>
              <button
                className="btn-secondary"
                style={{ justifyContent: 'center', padding: '0.6rem 1rem', fontSize: '0.85rem' }}
                onClick={() => {
                  setShowImportModal(false);
                  handleImportFolder();
                }}
              >
                导入工程文件夹目录
              </button>
            </div>
            <div className="modal-footer" style={{ padding: '0.75rem 1rem' }}>
              <button
                className="btn-secondary"
                style={{ padding: '0.4rem 1.25rem', fontSize: '0.8rem' }}
                onClick={() => setShowImportModal(false)}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Import Result Warnings & Summary Modal */}
      {importResult && (
        <div className="modal-overlay" style={{ zIndex: 9998 }}>
          <div className="modal-content" style={{ maxWidth: '500px', width: '90%' }}>
            <div className="modal-header" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
              <InfoIcon style={{ color: 'var(--color-accent)' }} />
              <span>工程导入详情与警告</span>
            </div>
            <div className="modal-body" style={{ fontSize: '0.85rem', color: 'var(--text-primary)', padding: '1.25rem 1rem', display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '400px', overflowY: 'auto' }}>
              <div>
                <strong style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>处理统计摘要：</strong>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginTop: '0.5rem', background: 'var(--bg-secondary)', padding: '0.75rem', borderRadius: '6px' }}>
                  <div>合并发音人数量: <strong style={{ color: 'var(--color-accent)' }}>{importResult.summary?.merged_speakers || 0}</strong></div>
                  <div>长音频切片数量: <strong style={{ color: 'var(--color-accent)' }}>{importResult.summary?.sliced_items || 0}</strong></div>
                  <div>缺失字表条目数: <strong style={{ color: 'var(--color-warning, #f59e0b)' }}>{importResult.summary?.missing_items || 0}</strong></div>
                  <div>降级未录制条数: <strong style={{ color: 'var(--color-error, #ef4444)' }}>{importResult.summary?.downgraded_items || 0}</strong></div>
                </div>
              </div>

              {importResult.warnings && importResult.warnings.length > 0 && (
                <div>
                  <strong style={{ fontSize: '0.9rem', color: 'var(--color-warning, #f59e0b)' }}>转换警告列表 ({importResult.warnings.length}):</strong>
                  <ul style={{ paddingLeft: '1.25rem', marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.35rem', maxHeight: '180px', overflowY: 'auto', background: '#fef3c7', border: '1px solid #fde68a', color: '#92400e', padding: '0.75rem 1.25rem', borderRadius: '6px', listStyleType: 'disc' }}>
                    {importResult.warnings.map((warn, idx) => (
                      <li key={idx} style={{ fontSize: '0.8rem', lineHeight: '1.4' }}>{warn}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(!importResult.warnings || importResult.warnings.length === 0) && (
                <div style={{ color: 'var(--color-success, #10b981)', fontWeight: 500 }}>
                  ✓ 工程完全兼容，无任何警告。
                </div>
              )}
            </div>
            <div className="modal-footer" style={{ borderTop: '1px solid var(--border-color)', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn-primary"
                style={{ padding: '0.4rem 1.5rem', fontSize: '0.8rem' }}
                onClick={() => setImportResult(null)}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Custom Confirm/Alert Dialog Modal */}
      {confirmDialog && (
        <div className="modal-overlay" style={{ zIndex: 9999 }}>
          <div className="modal-content" style={{ maxWidth: '380px' }}>
            <div className="modal-header" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
              <CheckIcon style={{ color: 'var(--color-accent)' }} />
              <span>提示</span>
            </div>
            <div className="modal-body" style={{ fontSize: '0.85rem', color: 'var(--text-primary)', padding: '1.25rem 1rem', lineHeight: '1.5' }}>
              {confirmDialog.message}
            </div>
            <div className="modal-footer" style={{ borderTop: '1px solid var(--border-color)', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
              {confirmDialog.onCancel && (
                <button
                  className="btn-secondary"
                  style={{ padding: '0.4rem 1.25rem', fontSize: '0.8rem' }}
                  onClick={() => {
                    confirmDialog.onCancel();
                    setConfirmDialog(null);
                  }}
                >
                  取消
                </button>
              )}
              <button
                className="btn-primary"
                style={{ padding: '0.4rem 1.25rem', fontSize: '0.8rem' }}
                onClick={() => {
                  confirmDialog.onConfirm();
                  setConfirmDialog(null);
                }}
              >
                确定
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Zoomed Spectrogram Modal */}
      {showZoomedSpectrogram && (
        <div 
          className="modal-overlay" 
          style={{ 
            zIndex: 10000, 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            backgroundColor: 'rgba(15, 23, 42, 0.75)', 
            backdropFilter: 'blur(4px)', 
            cursor: 'zoom-out' 
          }}
          onClick={() => setShowZoomedSpectrogram(false)}
        >
          <div 
            style={{ 
              maxWidth: '90vw', 
              maxHeight: '90vh', 
              width: '900px', 
              padding: '0', 
              background: 'transparent',
              display: 'flex',
              flexDirection: 'column',
              position: 'relative',
              overflow: 'hidden',
              animation: 'scaleIn 0.2s ease'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <img 
              src={spectrogramUrl} 
              alt="语谱图放大" 
              className="zoomed-spectrogram-image"
              style={{ 
                width: '100%', 
                height: 'auto', 
                borderRadius: '8px', 
                display: 'block',
                boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
              }} 
            />
            <button 
              style={{ 
                position: 'absolute', 
                top: '1rem', 
                right: '1rem', 
                width: '2rem', 
                height: '2rem', 
                borderRadius: '50%', 
                background: 'rgba(15, 23, 42, 0.6)', 
                backdropFilter: 'blur(4px)',
                border: 'none',
                color: '#ffffff',
                fontSize: '1.25rem',
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center', 
                cursor: 'pointer',
                transition: 'background-color 0.2s'
              }}
              onClick={() => setShowZoomedSpectrogram(false)}
            >
              ×
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
