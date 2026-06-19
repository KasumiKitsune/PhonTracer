import { useState, useEffect, useRef } from 'react';
import { save } from '@tauri-apps/plugin-dialog';
import { writeFile } from '@tauri-apps/plugin-fs';
import { apiFetch } from './engineApi.js';
import { bufferToWav, resampleAudio } from './audioUtils.js';

// --- Inline SVG Icons ---
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

const MenuIcon = () => (
  <svg style={{ width: '20px', height: '20px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

const CustomSelect = ({ value, onChange, options, style }) => {
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
        <div className="custom-select-dropdown">
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

export default function App() {
  // --- State Variables ---
  const [, setConnectionStatus] = useState(true);
  const [showMicGuidance, setShowMicGuidance] = useState(false);
  const [micBlocked, setMicBlocked] = useState(false);
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
  const [randomizeOrder, setRandomizeOrder] = useState(false);
  
  // Shuffled items computed state
  const [displayedItems, setDisplayedItems] = useState([]);
  const [isSmallScreen, setIsSmallScreen] = useState(window.innerWidth <= 768);
  
  const [visualizerTab, setVisualizerTab] = useState('waveform');
  
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
  
  const [wordlistInfo, setWordlistInfo] = useState({ title: '无字表', count: 0 });

  // --- Refs ---
  const audioContextRef = useRef(null);
  const streamRef = useRef(null);
  const processorNodeRef = useRef(null);
  const audioChunksRef = useRef([]);
  const canvasRef = useRef(null);
  const fileInputRef = useRef(null);
  const projectInputRef = useRef(null);
  
  const vadThreshold = 0.025;
  const silenceDurationLimit = 850;
  const lastSpeechTimeRef = useRef(0);
  const speechDetectedRef = useRef(false);

  const isRecordingRef = useRef(isRecording);
  isRecordingRef.current = isRecording;

  const qualityChecksEnabledRef = useRef(qualityChecksEnabled);
  qualityChecksEnabledRef.current = qualityChecksEnabled;

  const recordingModeRef = useRef(recordingMode);
  recordingModeRef.current = recordingMode;

  const speakersRef = useRef(speakers);
  speakersRef.current = speakers;

  const activeSpeakerIdRef = useRef(activeSpeakerId);
  activeSpeakerIdRef.current = activeSpeakerId;

  const groupsRef = useRef(groups);
  groupsRef.current = groups;

  const displayedItemsRef = useRef(displayedItems);
  displayedItemsRef.current = displayedItems;

  const activeItemIndexRef = useRef(activeItemIndex);
  activeItemIndexRef.current = activeItemIndex;

  const stopRecordingRef = useRef(null);

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

  // --- Background Microphone Helpers ---
  const ensureMicStream = async () => {
    if (streamRef.current && audioContextRef.current && audioContextRef.current.state !== 'closed') {
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
    } catch (e) {
      showOverlay = true;
    }

    if (showOverlay) {
      setShowMicGuidance(true);
    }
    
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      // 让 WebView 使用设备的真实采样率，停止录音时再统一重采样到 16 kHz。
      audioContextRef.current = new AudioContext();
      
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        } 
      });
      streamRef.current = stream;
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
          
          if (recordingModeRef.current === 'vad') {
            if (rms > vadThreshold) {
              if (!speechDetectedRef.current) {
                speechDetectedRef.current = true;
                setVadSpeaking(true);
              }
              lastSpeechTimeRef.current = Date.now();
            } else {
              if (speechDetectedRef.current) {
                const silentDuration = Date.now() - lastSpeechTimeRef.current;
                if (silentDuration > silenceDurationLimit) {
                  if (stopRecordingRef.current) {
                    stopRecordingRef.current(true);
                  }
                }
              }
            }
          }
        }
      };
      
      source.connect(processor);
      processor.connect(audioContextRef.current.destination);
      
      return stream;
    } catch (err) {
      console.error(err);
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setMicBlocked(true);
        setShowMicGuidance(true);
      } else {
        setShowMicGuidance(false);
      }
      throw err;
    }
  };

  const closeMicStream = () => {
    if (processorNodeRef.current) {
      processorNodeRef.current.disconnect();
      processorNodeRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      if (audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close();
      }
      audioContextRef.current = null;
    }
    setLiveVolume(0);
    setVadLevel(0);
    setVadSpeaking(false);
  };

  // --- Load State on Mount ---
  useEffect(() => {
    fetchProjectState();
    
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch('/project/state');
        if (res.ok) setConnectionStatus(true);
      } catch {
        setConnectionStatus(false);
      }
    }, 5000);
    
    return () => clearInterval(interval);
  }, []);

  // Background mic stream lifecycle based on qualityChecksEnabled
  useEffect(() => {
    if (qualityChecksEnabled) {
      ensureMicStream().catch(err => {
        console.error('Failed to initialize live volume stream:', err);
      });
    } else {
      if (!isRecordingRef.current) {
        closeMicStream();
      }
    }
  }, [qualityChecksEnabled]);

  // Clean up mic stream on unmount
  useEffect(() => {
    return () => closeMicStream();
  }, []);

  // Sync keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'SELECT') {
        return;
      }
      
      if (e.code === 'Space') {
        e.preventDefault();
        if (recordingMode === 'click' || recordingMode === 'vad') {
          if (isRecordingRef.current) {
            stopRecording();
          } else {
            startRecording();
          }
        }
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        navigateItem(-1);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        navigateItem(1);
      } else if (e.code === 'KeyR' && !isRecordingRef.current) {
        e.preventDefault();
        playRecordedAudio();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [displayedItems, activeItemIndex, speakers, activeSpeakerId, recordingMode]);

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

  // Load static waveform / spectrogram on active item change
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
      analyzeAudio(activeSpeakerId, activeItem.id);
      drawStaticWaveformFromUrl();
    } else {
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
    }
  }, [activeItemIndex, activeSpeakerId, displayedItems]);

  // Auto-scroll word list to center active item
  useEffect(() => {
    const activeItem = getActiveItem();
    if (activeItem) {
      const el = document.getElementById(`word-item-${activeItem.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      }
    }
  }, [activeItemIndex, displayedItems]);

  // --- API Wrappers ---
  const fetchProjectState = async () => {
    try {
      const res = await apiFetch('/project/state');
      if (!res.ok) throw new Error('API Error');
      const data = await res.json();
      
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

  const saveProjectState = async (updatedSpeakers, updatedGroups = groupsRef.current) => {
    const state = {
      version: "1.0",
      software_version: "PhonRec-1.0.0",
      save_time: new Date().toISOString(),
      active_speaker_id: activeSpeakerIdRef.current,
      speakers: updatedSpeakers,
      groups: updatedGroups
    };
    
    try {
      await apiFetch('/project/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state)
      });
    } catch (err) {
      console.error('Failed to save project state:', err);
    }
  };

  const getActiveItem = () => {
    if (displayedItems.length === 0) return null;
    return displayedItems[activeItemIndex] || null;
  };

  const getCompletedCount = () => {
    if (!activeSpeakerId || !speakers[activeSpeakerId]) return 0;
    const items = speakers[activeSpeakerId].items || {};
    return Object.keys(items).length;
  };

  const getTotalCount = () => {
    return groups.reduce((acc, g) => acc + g.items.length, 0);
  };

  // --- Speaker Controls (Inline) ---
  const handleInlineSpeakerSubmit = () => {
    const name = newSpeakerName.trim();
    if (!name) {
      setIsAddingSpeaker(false);
      return;
    }
    const id = 'spk_' + Math.random().toString(36).substr(2, 9);
    
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
    saveProjectState(updated);
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
    if (activeSpeakerId === id) {
      const keys = Object.keys(updated);
      setActiveSpeakerId(keys.length > 0 ? keys[0] : '');
    }
    saveProjectState(updated);
  };

  // --- Upload Files ---
  const triggerWordlistUpload = () => fileInputRef.current.click();
  const triggerProjectUpload = () => projectInputRef.current.click();

  const uploadWordlistFile = async (file) => {
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
      
      saveProjectState(speakers, data.groups);
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
      
      // Clear/Reset all workspace UI states immediately
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      setSpeakers({});
      setActiveSpeakerId('');
      setGroups([]);
      setWordlistInfo({ title: '导入中...', count: 0 });
      setDisplayedItems([]);
      setActiveGroupIndex('all');
      setActiveItemIndex(0);

      const res = await apiFetch('/project/import', {
        method: 'POST',
        body: formData
      });
      if (!res.ok) throw new Error('导入工程失败');
      const data = await res.json();
      
      const state = data.state;
      
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
      await customAlert('工程导入成功！');
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

  const handleProjectExport = async () => {
    try {
      const res = await apiFetch('/project/export');
      if (!res.ok) throw new Error('导出工程失败');
      const destination = await save({
        defaultPath: 'PhonRec_Project.teproj',
        filters: [{ name: 'PhonTracer 工程', extensions: ['teproj'] }],
      });
      if (!destination) return;
      await writeFile(destination, new Uint8Array(await res.arrayBuffer()));
    } catch (error) {
      console.error(error);
      await customAlert(`导出工程失败：${error.message || error}`);
    }
  };

  const handleProjectClear = async () => {
    const ok = await customConfirm('确定清空当前工作区，开始全新的录制吗？');
    if (!ok) return;
    try {
      await apiFetch('/project/clear', { method: 'POST' });
      setSpeakers({});
      setActiveSpeakerId('');
      setGroups([]);
      setActiveGroupIndex('all');
      setActiveItemIndex(0);
      setWordlistInfo({ title: '无字表', count: 0 });
      setSpectrogramUrl('');
      setQualityResults(null);
      clearCanvas();
      await customAlert('工作区已清空');
    } catch (err) {
      console.error(err);
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
    if (!activeSpeakerId) {
      await customAlert('请先添加并选择一个发音人！');
      return;
    }
    const activeItem = getActiveItem();
    if (!activeItem) {
      await customAlert('请导入字表以开始录音！');
      return;
    }
    
    try {
      setIsRecording(true);
      setSpectrogramUrl('');
      setQualityResults(null);
      
      audioChunksRef.current = [];
      speechDetectedRef.current = false;
      lastSpeechTimeRef.current = Date.now();
      
      // Ensure continuous mic stream is active
      await ensureMicStream();
      
    } catch (err) {
      console.error(err);
      setIsRecording(false);
      await customAlert('麦克风访问失败，请确认权限并重试！');
    }
  };

  const stopRecording = (shouldAutoAdvance = false) => {
    if (!isRecordingRef.current) return;
    
    setIsRecording(false);
    setIsProcessing(true);
    setVadLevel(0);
    setVadSpeaking(false);
    
    // If live quality checks are disabled, shut down the microphone stream immediately.
    // Otherwise, keep it active in the background for the live volume meter.
    if (!qualityChecksEnabledRef.current) {
      closeMicStream();
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
    const resampledBuffer = resampleAudio(floatBuffer, sourceSampleRate, 16000);
    const wavBlob = bufferToWav(resampledBuffer, 16000);
    uploadAudio(wavBlob, shouldAutoAdvance);
  };

  const uploadAudio = async (blob, shouldAutoAdvance) => {
    const activeItem = displayedItemsRef.current[activeItemIndexRef.current];
    const spkId = activeSpeakerIdRef.current;
    if (!activeItem || !spkId) return;
    
    const formData = new FormData();
    formData.append('file', blob, `${spkId}_${activeItem.id}.wav`);
    formData.append('speaker_id', spkId);
    formData.append('word_id', activeItem.id);
    
    try {
      const res = await apiFetch('/audio/save', {
        method: 'POST',
        body: formData
      });
      if (!res.ok) throw new Error('保存音频失败');
      const data = await res.json();
      
      const updatedSpeakers = { ...speakersRef.current };
      if (!updatedSpeakers[spkId].items) {
        updatedSpeakers[spkId].items = {};
      }
      
      // Cache the quality results directly inside the speaker structure
      updatedSpeakers[spkId].items[activeItem.id] = {
        path: data.path,
        label: activeItem.label,
        note: activeItem.note,
        tags: activeItem.tags,
        aliases: activeItem.aliases || [],
        meta: activeItem.meta || {},
        metadata_source: activeItem.metadata_source || '录音软件',
        quality: data.quality
      };
      
      setSpeakers(updatedSpeakers);
      await saveProjectState(updatedSpeakers);
      
      // Update local view states instantly
      if (data.spectrogram) setSpectrogramUrl(data.spectrogram);
      if (data.quality) setQualityResults(data.quality);
      
      if (shouldAutoAdvance) {
        setTimeout(() => navigateItem(1), 500);
      }
    } catch (err) {
      console.error(err);
      alert('上传音频失败: ' + err.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const analyzeAudio = async (speakerId, wordId) => {
    const formData = new FormData();
    formData.append('speaker_id', speakerId);
    formData.append('word_id', wordId);
    
    try {
      const res = await apiFetch('/audio/analyze', {
        method: 'POST',
        body: formData
      });
      if (!res.ok) throw new Error('分析失败');
      const data = await res.json();
      
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
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) return;
    
    const recordMeta = speakers[activeSpeakerId]?.items?.[activeItem.id];
    if (!recordMeta) return;
    
    try {
      const res = await apiFetch(`/audio/file?speaker_id=${encodeURIComponent(activeSpeakerId)}&word_id=${encodeURIComponent(activeItem.id)}&t=${Date.now()}`);
      if (!res.ok) throw new Error('读取录音失败');
      const audioUrl = URL.createObjectURL(await res.blob());
      const audio = new Audio(audioUrl);
      const releaseUrl = () => URL.revokeObjectURL(audioUrl);
      audio.addEventListener('ended', releaseUrl, { once: true });
      audio.addEventListener('error', releaseUrl, { once: true });
      await audio.play();
    } catch (error) {
      console.error('播放失败:', error);
    }
  };

  const discardRecordedAudio = async () => {
    const activeItem = getActiveItem();
    if (!activeItem || !activeSpeakerId) return;
    
    const recordMeta = speakers[activeSpeakerId]?.items?.[activeItem.id];
    if (!recordMeta) return;
    
    const ok = await customConfirm('确定要丢弃当前词条的录音吗？');
    if (!ok) return;
    
    const updatedSpeakers = { ...speakers };
    delete updatedSpeakers[activeSpeakerId].items[activeItem.id];
    setSpeakers(updatedSpeakers);
    saveProjectState(updatedSpeakers);
    
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
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  };

  const drawStaticWaveform = (floatBuffer) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, width, height);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = '#10b981';
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
      const res = await apiFetch(`/audio/file?speaker_id=${encodeURIComponent(activeSpeakerId)}&word_id=${encodeURIComponent(activeItem.id)}`);
      if (!res.ok) return;
      const arrayBuffer = await res.arrayBuffer();
      
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

  return (
    <div 
      className="app-container"
      onDragOver={handleDragOver}
    >
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
                <h3>🎤 麦克风访问被拒绝</h3>
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
                <h3>🎤 请求麦克风使用权限</h3>
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
            支持字表文件 (.ptwl / .txt / .csv) 或工程归档 (.teproj)
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
              <button className="btn-primary" onClick={triggerWordlistUpload}>
                <ImportIcon /> 导入字表
              </button>
              <input 
                type="file" 
                ref={fileInputRef} 
                style={{ display: 'none' }} 
                accept=".ptwl,.txt,.csv" 
                onChange={handleWordlistUpload} 
              />
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
            <div className="info-card">
              <div className="panel-title" style={{ fontSize: '0.8rem', marginBottom: '0.25rem', textTransform: 'none' }}>
                <BookIcon /> 字段显示设置
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>列表角标:</span>
                  <CustomSelect 
                    value={badgeMetaKey} 
                    onChange={setBadgeMetaKey}
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
                    onChange={setPrimaryMetaKey}
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
                    onClick={() => {
                      setActiveSpeakerId(spk.id);
                      saveProjectState(speakers);
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
                  onChange={(e) => setCharFontSize(Number(e.target.value))}
                  className="font-size-slider"
                />
                <span style={{ fontSize: '1.05rem', fontWeight: 700 }}>A</span>
              </div>
              <div className="char-display-container">
                <span className="char-display" style={{ fontSize: `${charFontSize}px`, transition: 'font-size 0.1s ease' }}>{activeItem.label}</span>
                {primaryMetaKey !== 'none' && (
                  <span className="char-pinyin">
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
                disabled={!activeItem || !speakers[activeSpeakerId]?.items?.[activeItem.id]}
                title="播放录音 (R键)"
              >
                <PlayIcon />
              </button>

              {/* Previous arrow */}
              <button 
                className="nav-arrow prev-btn" 
                onClick={() => navigateItem(-1)} 
                disabled={activeItemIndex === 0}
                title="上一个 (左方向键)"
              >
                <ChevronLeft />
              </button>

              {/* Record Button */}
              <div className="record-btn-wrapper">
                <div className={`record-ring ${isRecording ? 'active' : ''}`}></div>
                <button 
                  className={`btn-record ${isRecording ? 'recording' : ''} ${isProcessing ? 'processing' : ''}`}
                  onMouseDown={recordingMode === 'hold' ? startRecording : null}
                  onMouseUp={recordingMode === 'hold' ? () => stopRecording(false) : null}
                  onMouseLeave={recordingMode === 'hold' && isRecording ? () => stopRecording(false) : null}
                  onTouchStart={recordingMode === 'hold' ? startRecording : null}
                  onTouchEnd={recordingMode === 'hold' ? () => stopRecording(false) : null}
                  onClick={recordingMode !== 'hold' ? (isRecording ? () => stopRecording(false) : startRecording) : null}
                  disabled={isProcessing || !activeSpeakerId || !activeItem}
                  title={recordingMode === 'hold' ? '按住录音，松开停止' : '点击录音，再次点击停止 (空格键)'}
                >
                  <div className="record-core"></div>
                </button>
              </div>

              {/* Next arrow */}
              <button 
                className="nav-arrow next-btn" 
                onClick={() => navigateItem(1)}
                disabled={activeItemIndex === displayedItems.length - 1}
                title="下一个 (右方向键)"
              >
                <ChevronRight />
              </button>
              
              {/* Discard button */}
              <button 
                className="nav-arrow discard-btn" 
                onClick={discardRecordedAudio} 
                disabled={!activeItem || !speakers[activeSpeakerId]?.items?.[activeItem.id]}
                title="丢弃录音"
              >
                <TrashIcon />
              </button>
            </div>

            {/* Keyboard hints at the bottom of controls card */}
            <div style={{ display: 'flex', flexWrap: 'wrap', width: '100%', justifyContent: 'center', alignItems: 'center', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)', borderTop: '1px solid var(--border-color)', paddingTop: '0.75rem', marginTop: '0.5rem' }}>
              <span><KeyboardIcon /> [空格] 录音/停止</span>
              <span>[← / →] 切换字表词条</span>
              {activeItem && speakers[activeSpeakerId]?.items?.[activeItem.id] && (
                <span style={{ color: 'var(--color-accent)', cursor: 'pointer', fontWeight: 'bold' }} onClick={playRecordedAudio}>
                  [R] 播放录音
                </span>
              )}
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
                const isRecorded = !!recordMeta;
                
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
          <div className="panel-header">
            <span className="panel-title">
              <CheckIcon /> 检测与配置
            </span>
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
                    onChange={(e) => setQualityChecksEnabled(e.target.checked)}
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
              
              <div className="quality-grid">
                <div className="quality-item">
                  <span>音量检测</span>
                  <div className="quality-indicator">
                    <span className={`indicator-led ${
                      !qualityChecksEnabled || !qualityResults ? '' : 
                      (qualityResults.volume.status === 'normal' ? 'green' : 'orange')
                    }`}></span>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.volume.label}
                    </span>
                  </div>
                </div>
                <div className="quality-item">
                  <span>嘎裂声</span>
                  <div className="quality-indicator">
                    <span className={`indicator-led ${
                      !qualityChecksEnabled || !qualityResults ? '' : 
                      (qualityResults.creak.abnormal ? 'red' : 'green')
                    }`}></span>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.creak.label}
                    </span>
                  </div>
                </div>
                <div className="quality-item">
                  <span>音频截断</span>
                  <div className="quality-indicator">
                    <span className={`indicator-led ${
                      !qualityChecksEnabled || !qualityResults ? '' : 
                      (qualityResults.clipping.abnormal ? 'red' : 'green')
                    }`}></span>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {!qualityChecksEnabled || !qualityResults ? '未检测' : qualityResults.clipping.label}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Visualizer panel */}
            <div className="info-card visualizer-card" style={{ padding: 0 }}>
              <div className="visualizer-tabs">
                <button 
                  className={`tab-btn ${visualizerTab === 'waveform' ? 'active' : ''}`}
                  onClick={() => setVisualizerTab('waveform')}
                >
                  波形图
                </button>
                <button 
                  className={`tab-btn ${visualizerTab === 'spectrogram' ? 'active' : ''}`}
                  onClick={() => setVisualizerTab('spectrogram')}
                >
                  语谱图
                </button>
              </div>
              
              <div className="visualizer-viewport">
                <canvas 
                  ref={canvasRef} 
                  className="visualizer-canvas"
                  style={{ display: visualizerTab === 'waveform' ? 'block' : 'none' }}
                  width={300}
                  height={150}
                />
                
                {visualizerTab === 'spectrogram' && (
                  spectrogramUrl ? (
                    <img src={spectrogramUrl} alt="语谱图" className="visualizer-image" />
                  ) : (
                    <div className="visualizer-placeholder">
                      语谱图将在录制结束后生成
                    </div>
                  )
                )}
              </div>
            </div>

            {/* Recording settings */}
            <div className="info-card" style={{ padding: '0.75rem' }}>
              <div className="panel-title" style={{ fontSize: '0.8rem', marginBottom: '0.5rem', textTransform: 'none' }}>
                <MicIcon active={isRecording} /> 录音模式设置
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>模式:</span>
                  <CustomSelect 
                    value={recordingMode} 
                    onChange={setRecordingMode}
                    options={[
                      { value: 'hold', label: '按住 (对讲机)' },
                      { value: 'click', label: '点击开关' },
                      { value: 'vad', label: '智能 VAD 跳转' }
                    ]}
                    style={{ minWidth: '160px' }}
                  />
                </div>
              </div>
            </div>

            {/* Project actions */}
            <div className="project-actions" style={{ borderTop: 'none', background: 'transparent', padding: 0 }}>
              <div className="switch-container" style={{ marginBottom: '0.4rem' }}>
                <span>随机排序录制</span>
                <label className="switch">
                  <input 
                    type="checkbox" 
                    checked={randomizeOrder}
                    onChange={(e) => setRandomizeOrder(e.target.checked)}
                  />
                  <span className="slider"></span>
                </label>
              </div>
              
              <div className="project-action-row">
                <button className="btn-secondary" style={{ fontSize: '0.8rem' }} onClick={triggerProjectUpload}>
                  <ImportIcon /> 导入
                </button>
                <input 
                  type="file" 
                  ref={projectInputRef} 
                  style={{ display: 'none' }} 
                  accept=".teproj" 
                  onChange={handleProjectUpload} 
                />
                <button className="btn-primary" style={{ fontSize: '0.8rem' }} onClick={handleProjectExport}>
                  <ExportIcon /> 保存
                </button>
              </div>
              <button className="btn-secondary" style={{ fontSize: '0.8rem', color: 'var(--color-danger)', borderColor: 'var(--border-color)', marginTop: '0.2rem' }} onClick={handleProjectClear}>
                <SweepIcon /> 清空工作区
              </button>
            </div>
          </div>
        </section>

      </main>

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
    </div>
  );
}
