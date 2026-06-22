import { useState, useEffect, useRef } from 'react';

// --- Local Icons ---
const NetworkIcon = ({ active }) => (
  <svg style={{ width: '18px', height: '18px', color: active ? 'var(--color-success)' : 'var(--color-warning)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12.55a11 11 0 0 1 14.08 0" />
    <path d="M1.42 9a16 16 0 0 1 21.16 0" />
    <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
    <line x1="12" y1="20" x2="12.01" y2="20" strokeWidth="3" />
  </svg>
);

const WarningIcon = () => (
  <svg style={{ width: '20px', height: '20px', color: '#f59e0b' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

const InfoAlertIcon = () => (
  <svg style={{ width: '20px', height: '20px', color: '#3b82f6' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);

const CheckAlertIcon = () => (
  <svg style={{ width: '20px', height: '20px', color: '#10b981' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

// --- Header icons to make content lively ---
const EnvironmentIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
  </svg>
);

const HardwareIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
  </svg>
);

const UserIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

const NavigationIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="3 11 22 2 13 21 11 13 3 11" />
  </svg>
);

const QualityIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
  </svg>
);

const ArchiveIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="21 8 21 21 3 21 3 8" />
    <rect x="1" y="3" width="22" height="5" />
    <line x1="10" y1="12" x2="14" y2="12" />
  </svg>
);

// --- OS Badges & Status Icons ---
const WindowsIcon = () => (
  <svg style={{ width: '12px', height: '12px', verticalAlign: 'middle', marginRight: '3px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 5.5L10 4.5V11.5H3V5.5ZM3 12.5H10V19.5L3 18.5V12.5ZM11.5 4.3L21 3V11.5H11.5V4.3ZM11.5 12.5H21V20L11.5 18.7V12.5Z"/>
  </svg>
);

const MacIcon = () => (
  <svg style={{ width: '12px', height: '12px', verticalAlign: 'middle', marginRight: '3px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2C6.48 2 2 6.48 2 12C2 17.52 6.48 22 12 22C17.52 22 22 17.52 22 12C22 6.48 17.52 2V2ZM12 20C7.59 20 4 16.41 4 12C4 7.59 7.59 4 12 4C16.41 4 20 7.59 20 12C20 16.41 16.41 20 12 20Z"/>
  </svg>
);

const SparklesIcon = () => (
  <svg style={{ width: '12px', height: '12px', verticalAlign: 'middle', marginRight: '3px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </svg>
);

const LayersIcon = () => (
  <svg style={{ width: '12px', height: '12px', verticalAlign: 'middle', marginRight: '3px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 2 7 12 12 22 7 12 2" />
    <polyline points="2 17 12 22 22 17" />
    <polyline points="2 12 12 17 22 12" />
  </svg>
);

// --- Table Icons ---
const TableCheckIcon = () => (
  <span className="table-status-badge badge-yes" title="正常支持">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
    <span>正常支持</span>
  </span>
);

const TableCrossIcon = () => (
  <span className="table-status-badge badge-no" title="不支持">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
    <span>不支持</span>
  </span>
);

const TableSpecialCheckIcon = ({ text }) => (
  <span className="table-status-badge badge-special" title={text}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
    <span>{text}</span>
  </span>
);

// --- Content Decorator Icons ---
const HelpIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

const SettingsIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

const LayoutIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
    <line x1="9" y1="3" x2="9" y2="21" />
    <line x1="9" y1="9" x2="21" y2="9" />
    <line x1="9" y1="15" x2="21" y2="15" />
  </svg>
);

const MicIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
  </svg>
);

const VolumeIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
  </svg>
);

const FileTextIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
    <polyline points="10 9 9 9 8 9" />
  </svg>
);

const AlertMiniIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-danger, #ef4444)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const PlayIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="5 3 19 12 5 21 5 3" />
  </svg>
);

const BellIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0" />
  </svg>
);

const ShieldIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)', verticalAlign: 'middle', marginRight: '6px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);

export default function OperationGuide({ runtimeMode = 'engine' }) {
  const isStandalone = runtimeMode === 'standalone';
  const [activePart, setActivePart] = useState(0);
  const buttonRefs = useRef([]);

  useEffect(() => {
    const container = document.querySelector('.help-scroll-content');
    if (!container) return;

    const observerOptions = {
      root: container,
      rootMargin: '-20% 0px -55% 0px', // Triggers when section passes mid-visual viewport
      threshold: 0
    };

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          const index = parseInt(id.replace('part', '')) - 1;
          if (index >= 0 && index < 6) {
            setActivePart(index);
            // Auto scroll active button into bottom list center view
            const btn = buttonRefs.current[index];
            if (btn) {
              btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
            }
          }
        }
      });
    }, observerOptions);

    for (let i = 1; i <= 6; i++) {
      const el = document.getElementById(`part${i}`);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, []);

  const handleScrollTo = (e, id, index) => {
    e.preventDefault();
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    setActivePart(index);
    const btn = buttonRefs.current[index];
    if (btn) {
      btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
  };

  return (
    <div className="help-container">
      {/* 顶部引擎连接状态指示器 */}
      <div className={`connection-status-card ${isStandalone ? 'standalone' : 'engine-connected'}`}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="pulse-indicator-wrapper">
            <span className="pulse-dot"></span>
            <NetworkIcon active={!isStandalone} />
          </div>
          <div>
            <div style={{ fontSize: '0.9rem', fontWeight: 650, color: 'var(--text-primary)' }}>
              本地分析引擎状态：{isStandalone ? '未连接 (独立模式)' : '已连接 (完整模式)'}
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
              {isStandalone
                ? '提示：因为当前未检测到 PhonTracer 分析服务，处于独立模式，深度质检与语谱图不可用。'
                : '完整模式就绪：语谱图分析、基频/共振峰分析及完整声学质检均已可用。'}
            </div>
          </div>
        </div>
      </div>

      {/* 帮助指南 HTML 内容 - 放置于独立滚动容器内 */}
      <div className="help-scroll-content">
        <div className="help-markdown-content">
        <div className="help-intro-card">
          <h2 style={{ marginTop: 0, fontSize: '1.1rem', fontWeight: 650, color: 'var(--color-accent)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            欢迎阅读 PhonRec 详细操作与使用指引
          </h2>
          <p style={{ margin: 0, fontSize: '0.82rem', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
            这是一份专为希望高效完成录音任务的用户准备的系统性手册。无论你是需要录制大量词条的语音学研究人员，还是需要按清单交付音频素材的工作者，这份指南都会用平实、易懂的语言，详细解析软件的各项功能与内在逻辑。为了让你能够更直观地查阅信息，手册对原有内容进行了重新编排，去除了繁杂的章节划分，将核心功能浓缩为六大主要模块，并大量采用表格形式来梳理参数与操作逻辑。
          </p>
          <p style={{ margin: '0.5rem 0 0 0', fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)' }}>
            请跟随本手册的指引，逐步掌握 PhonRec 的使用方法。
          </p>
        </div>

        <hr className="help-divider" />

        {/* Part 1 */}
        <section id="part1" className="help-section">
          <h3><EnvironmentIcon /> 第一部分：软件定位与运行环境</h3>
          <p>在使用软件开展工作之前，我们需要先了解 PhonRec 的基本定位、安装要求以及它的两种核心运行模式。掌握这些基础信息，能够帮助你避免在后续安装 and 配置中走弯路。</p>

          <h4><HelpIcon /> 1. PhonRec 是什么？</h4>
          <p>PhonRec 是一款专门针对“多发音人、多词条列表”场景设计的轻量级桌面录音工具。它的核心工作流非常清晰：导入一份词表清单 &rarr; 建立多个发音人档案 &rarr; 软件将词条逐个展示在屏幕上 &rarr; 录音者逐条朗读并录制 &rarr; 软件实时检查录音质量并自动跳转 &rarr; 最终将录音和工程打包导出。</p>
          <p>需要特别说明的是，PhonRec 自身并没有内置那些庞大且复杂的声学分析引擎（例如 Python 环境、Praat 算法等）。它是 PhonTracer 语音分析套件的配套工具。当它需要生成专业的语谱图或进行深度的声音质量检测时，它会自动在你的电脑中呼叫并连接已经安装好的 PhonTracer 主程序。同时，PhonRec 是一款完全本地化的软件，所有的录音文件、工程状态 and 参数设置都只保存在你的个人电脑硬盘中，软件没有任何将数据上传至网络的代码，充分保障你的数据隐私。</p>

          <h4><WindowsIcon /> 2. 支持的操作系统与安装指南</h4>
          <p>目前软件主要支持 Windows 和 macOS 两大平台，但在硬件架构的支持上有所区别。请对照下表确认你的设备是否符合要求，并按照建议完成安装。</p>
          
          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '18%' }}>操作系统</th>
                <th style={{ width: '20%' }}>支持的硬件架构</th>
                <th style={{ width: '15%' }}>安装包格式</th>
                <th>安装与配置建议</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="tag-os windows"><WindowsIcon /> Windows</span></td>
                <td>常见的 x64 架构（Intel/AMD 处理器），以及 ARM64 架构</td>
                <td><code>.exe</code> 安装程序</td>
                <td>下载对应架构的安装包后双击运行，按默认提示安装至当前用户目录下。如果你希望体验完整功能，请务必提前安装兼容版本的 PhonTracer 主程序安装版。</td>
              </tr>
              <tr>
                <td><span className="tag-os macos"><MacIcon /> macOS</span></td>
                <td>仅支持 Apple Silicon 芯片（如 M1, M2, M3 等 ARM64 架构），不支持老款 Intel 芯片</td>
                <td><code>.dmg</code> 磁盘映像</td>
                <td>系统版本需在 macOS 12 或更高。双击打开 dmg 文件后，将 PhonRec 拖入“应用程序”文件夹。首次录音时，请务必在系统弹窗中允许麦克风权限。</td>
              </tr>
            </tbody>
          </table>

          <h4><SettingsIcon /> 3. 核心机制：完整模式与独立模式</h4>
          <p>每次启动 PhonRec时，软件都会进行一次环境侦测，尝试寻找本机的 PhonTracer 分析引擎。根据侦测结果，软件会进入两种不同的工作状态。理解这两种模式的区别，是顺畅使用的关键。</p>
          <ul>
            <li><span className="tag-mode full-mode"><SparklesIcon /> 完整模式</span>：软件成功找到了分析引擎。此时软件火力全开，不仅能录音，还能绘制专业的语谱图，并能对音频进行背景噪声、嘎裂声等深度的声学质量检测。</li>
            <li><span className="tag-mode standalone-mode"><LayersIcon /> 独立模式</span>：如果你的电脑没有安装 PhonTracer，或者引擎启动失败，你可以点击界面上的“进入独立软件模式”。请放心，这绝不是一个残缺的演示版，它拥有完整的工程管理和录音能力，唯一的遗憾是无法生成语谱图和进行复杂的声学质检。</li>
          </ul>
          <p>为了让你更直观地了解两种模式的差异，请参考下方的能力对照表：</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '22%' }}>功能模块</th>
                <th style={{ width: '15%' }}>完整模式</th>
                <th style={{ width: '15%' }}>独立模式</th>
                <th>补充说明</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>基础录音能力</strong></td>
                <td><TableCheckIcon /></td>
                <td><TableCheckIcon /></td>
                <td>均支持麦克风录音及本地工作区存储。</td>
              </tr>
              <tr>
                <td><strong>词表格式支持</strong></td>
                <td><TableCheckIcon /></td>
                <td><TableCheckIcon /></td>
                <td>均支持 TXT、CSV 及高级 PTWL 格式的导入。</td>
              </tr>
              <tr>
                <td><strong>工程导入与导出</strong></td>
                <td><TableCheckIcon /></td>
                <td><TableCheckIcon /></td>
                <td>均可顺畅导入与导出 <code>.teproj</code> 格式的打包工程文件。</td>
              </tr>
              <tr>
                <td><strong>基础质量检测</strong></td>
                <td><TableCheckIcon /></td>
                <td><TableCheckIcon /></td>
                <td>均支持检测声音是否过小，以及是否发生爆音（削波截断）。</td>
              </tr>
              <tr>
                <td><strong>深度声学检测</strong></td>
                <td><TableSpecialCheckIcon text="支持 (声学多参数)" /></td>
                <td><TableCrossIcon /></td>
                <td>独立模式无法检测背景噪声、有效语音占比、嘎裂声及直流偏移。</td>
              </tr>
              <tr>
                <td><strong>可视化图表</strong></td>
                <td><TableSpecialCheckIcon text="支持 (波形/语谱图)" /></td>
                <td><TableCrossIcon /></td>
                <td>独立模式仅显示波形图；完整模式可切换查看叠加了声学曲线的语谱图。</td>
              </tr>
              <tr>
                <td><strong>特殊导出与保存</strong></td>
                <td><TableSpecialCheckIcon text="支持 (文件夹工程)" /></td>
                <td><TableCrossIcon /></td>
                <td>独立模式虽不支持文件夹工程，但独占“批量导出 WAV”按钮功能。</td>
              </tr>
            </tbody>
          </table>

          <div className="alert-box alert-note">
            <div className="alert-icon"><InfoAlertIcon /></div>
            <div className="alert-text">
              <strong>注</strong>：独立模式下录制的所有数据都是规范且受保护的。如果你先在独立模式下完成了录音，未来安装好引擎并进入完整模式后，依然可以读取这些进度并进行深度分析。进入独立模式的决定只对当前这一次运行有效，若想切换回完整模式，需要完全退出软件并重新打开。
            </div>
          </div>
        </section>

        <hr className="help-divider" />

        {/* Part 2 */}
        <section id="part2" className="help-section">
          <h3><HardwareIcon /> 第二部分：界面布局与硬件接入</h3>
          <p>进入软件主界面后，合理的界面布局和正确的硬件配置是顺利开展工作的前提。本部分将带你熟悉软件的各个操作区域，并指导你如何正确接入麦克风及配置音频输入。</p>

          <h4><LayoutIcon /> 1. 宽屏界面区域导览</h4>
          <p>PhonRec 的桌面端主界面经过精心设计，主要分为四个逻辑区域。熟悉这些区域，能让你在操作时得心应手：</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '25%' }}>界面区域</th>
                <th>主要承担的功能与操作</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>左侧：发音人与字表区</strong></td>
                <td>这里是任务管理的调度中心。你可以在此导入外部词表文件；在独立模式下还支持直接粘贴文本作为普通词表。同时，这里用于添加、切换和删除发音人，并以直观的进度条展示当前发音人的录制完成百分比。当窗口较小时，此区域会折叠为左上角的抽屉菜单。</td>
              </tr>
              <tr>
                <td><strong>中央：核心录音控制区</strong></td>
                <td>你的视线焦点区域。屏幕正中央以大字号显示当前需要朗读的词条，下方可配置显示拼音或备注。这里配备了醒目的圆形录音启停按钮、录音状态指示文字（如“准备就绪”、“处理中”），以及上一条、下一条、丢弃录音和播放进度条等控制组件。底部还设有导入工程和保存工程的快捷入口。</td>
              </tr>
              <tr>
                <td><strong>中右侧：词表清单浏览区</strong></td>
                <td>这是一个垂直的列表，展示了所有的词条。你可以通过顶部的下拉菜单筛选特定分组。列表会用不同的状态图标标明每个词条是“未录制”、“已录制”还是“录音中”。如果某条录音质量存在瑕疵，这里也会挂上“偏小”、“截断”等简要的质量警告角标。你可以直接点击列表中的任意词条进行快速跳转。</td>
              </tr>
              <tr>
                <td><strong>右侧：检测与设置面板区</strong></td>
                <td>软件的仪表盘和配置中心。最上方是实时音量条，一说话就会跳动。下方是录音完成后的详细质量检测报告卡片，包含评分等级和各个子项的检测结果指示灯。这里还提供了完整模式下的波形/语谱图切换开关、录音模式的快捷切换下拉框、进入全局“设置中心”的齿轮按钮，以及位于最底部的“清空工作区”高危操作按钮。</td>
              </tr>
            </tbody>
          </table>

          <h4><MicIcon /> 2. 麦克风权限的获取与重置</h4>
          <p>没有麦克风权限，软件就变成了“聋子”。在首次请求使用麦克风时，系统会弹出权限请求对话框，请务必选择“允许”。</p>
          <p>如果因为误操作拒绝了权限，界面的实时音量条将毫无反应。此时：</p>
          <ul>
            <li>软件界面会提供“重新请求权限”或“打开系统设置”的按钮。</li>
            <li><strong>在 <span className="tag-os windows"><WindowsIcon /> Windows</span> 系统中</strong>：如果权限被系统级永久拒绝，软件可能会提示你需要重启应用并重置权限记录。确认后，PhonRec 会自动退出、清理内部的权限记忆并重新启动，以期再次触发系统授权。</li>
            <li><strong>在 <span className="tag-os macos"><MacIcon /> macOS</span> 系统中</strong>：你需要手动打开苹果的“系统设置 &rarr; 隐私与安全性 &rarr; 麦克风”，在列表中找到 PhonRec 并打开旁边的开关，修改后建议完全退出并重新启动软件。</li>
          </ul>

          <h4><VolumeIcon /> 3. 输入设备的选择与系统声音回环</h4>
          <p>确保权限无误后，你需要前往“设置 &rarr; 设备与系统 &rarr; 麦克风输入源”中选择正确的收音设备。</p>
          <ul>
            <li><strong>物理麦克风</strong>：下拉列表中会枚举出你电脑上连接的所有麦克风设备。如果你的设备是刚刚插上的，请点击旁边的“刷新硬件设备”按钮让其显示。如果之前保存的麦克风被拔出了，软件启动时会自动安全回退到“系统默认麦克风”。</li>
            <li><strong><span className="tag-os windows"><WindowsIcon /> Windows</span> 系统声音回环（特殊功能）</strong>：如果你使用的是 <span className="tag-os windows"><WindowsIcon /> Windows</span> 系统，你会在列表中看到以“系统声音回环”开头的设备（例如“系统声音回环(Realtek Audio)”）。这个选项并非录制外界的声音，而是录制<strong>电脑内部正在播放的声音</strong>（如网页视频的声音、音乐播放器的声音）。使用回环录音不需要物理麦克风的权限，且无论电脑输出的是多声道还是立体声，软件在录制后都会自动混合为标准的单声道音频。受限于系统底层架构，<span className="tag-os macos"><MacIcon /> macOS</span> 版本不提供此回环录制功能。</li>
          </ul>

          <h4><BellIcon /> 4. 待机音频输入监听机制</h4>
          <p>在设置中有一个名为“待机音频输入监听”的开关。</p>
          <p>当其开启时，即使你尚未点击录音按钮，软件也会在后台保持音频输入流的开启状态。这样做的好处是，你可以随时通过右侧的音量条观察环境噪音，并且在真正点击录音时，设备能以最快速度响应，省去了唤醒麦克风的延迟。</p>
          <p>当其关闭时，软件在平时会彻底释放音频设备，只有在你确切按下录音按钮的那一刻，才会去请求并打开麦克风。</p>
        </section>

        <hr className="help-divider" />

        {/* Part 3 */}
        <section id="part3" className="help-section">
          <h3><UserIcon /> 第三部分：词表体系与发音人调度</h3>
          <p>录音任务的核心在于“录什么”和“谁来录”。PhonRec 提供了灵活的词表导入机制 and 多发音人管理系统。本部分将详细讲解如何准备符合规范的数据文件，以及管理发音人时的注意事项。</p>

          <h4><FileTextIcon /> 1. 支持的词表格式及编写规范</h4>
          <p>无论处于哪种模式，软件均支持三种格式的词表文件。你可以通过主界面的导入按钮，或直接将文件拖拽至软件界面来完成导入。</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '22%' }}>文件格式</th>
                <th style={{ width: '25%' }}>适用场景与特点</th>
                <th>编写建议与内部规范</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>普通 TXT 文本</strong></td>
                <td>最轻量、最快捷的格式，适合简单的单词列表。在 <span className="tag-mode standalone-mode"><LayersIcon /> 独立模式</span> 的左侧栏还支持直接复制文本并“粘贴普通字表”。</td>
                <td>支持 UTF-8 编码，空行自动忽略。分组标题可以使用 <code>【分组名】</code>、<code>[分组名]</code> 或 <code>#分组名</code> 的形式。词条可以直接一行一个，也可以用空格、逗号或顿号隔开同一行的多个词。如果没有写任何分组标题，所有的词条都会自动归入一个名为“未分组”的默认组中。如果词条内部包含斜杠（如 <code>北/京</code>），软件会将其保留为发音边界提示，而不会将一行拆断。</td>
              </tr>
              <tr>
                <td><strong>CSV 逗号分隔表格</strong></td>
                <td>适合需要附加拼音、备注、标签等额外信息的中等复杂度任务。可通过 Excel 等表格软件编辑后另存为 CSV。</td>
                <td>
                  <strong>推荐的中文表头</strong>：<code>组名</code>, <code>组备注</code>, <code>组标签</code>, <code>词项</code>, <code>词项备注</code>, <code>标签</code>, <code>别名</code>, <code>复核状态</code>, <code>拼音</code>, <code>声调</code>, <code>韵母</code>, <code>实验条件</code>。<br />
                  <strong>核心规则</strong>：必须包含 <code>词项</code> 这一列；每一行代表一个词条；标签或别名内部如果有多个值，请用分号或逗号隔开；如果写了软件不认识的表头列（如“测试环境”），数据不会丢失，而是存入后台的 meta 数据中随工程保留。如果 CSV 存在引号未闭合等严重的格式错误，<span className="tag-mode standalone-mode"><LayersIcon /> 独立模式</span> 将拒绝导入。
                </td>
              </tr>
              <tr>
                <td><strong>高级 PTWL 字表</strong></td>
                <td>PhonTracer 专用的高级 JSON 格式字表，通常由其他程序或系统生成。</td>
                <td>文件内部采用 <code>phontracer.wordlist.v2</code> 规范。能够最完整、最严谨地保留组级别与词条级别的全部元数据（ID、注释、标签、别名等），并能在导入导出中完美往返。</td>
              </tr>
            </tbody>
          </table>

          <div className="alert-box alert-warning">
            <div className="alert-icon"><WarningIcon /></div>
            <div className="alert-text">
              <strong>导入词表的破坏性后果（极其重要）</strong>：<br />
              在操作导入词表之前，请务必牢记一条铁律：<strong>导入新字表不是追加操作，而是全盘替换操作。</strong><br />
              当你确认导入一份新的词表后，软件会执行以下清理工作：
              <ol style={{ margin: '0.3rem 0 0 0', paddingLeft: '1.25rem' }}>
                <li>删除并替换当前工作区内的全部分组和词条。</li>
                <li><strong>彻底清除所有发音人现有的录音记录和进度。</strong></li>
                <li>清理底层工作区中那些失去引用的本地音频文件。</li>
                <li>将当前焦点重置回新词表的第一条。</li>
              </ol>
              因此，如果你当前的工程中已经有了不想丢失的录音成果，在更换词表之前，<strong>必须先点击底部保存按钮，导出一份 <code>.teproj</code> 归档工程进行备份</strong>。如果在录音或保存文件尚未处理完毕的瞬间尝试更换词表，软件会强制禁止该操作。
            </div>
          </div>

          <h4><UserIcon /> 3. 发音人的全生命周期管理</h4>
          <p>一份词表可以供多位不同的发音人使用，每个发音人拥有自己独立平行的录音进度，互不干扰。</p>
          <ul>
            <li><strong>添加发音人</strong>：点击左侧的发音人列表面板中的“+ 添加”按钮，在输入框中键入姓名或编号，按下回车键或点击输入框外部空白处即可完成添加。新建立的发音人会自动成为当前处于激活状态的录音者。</li>
            <li><strong>切换发音人</strong>：只需在列表中直接点击对应的姓名。软件会自动封存当前发音人的进度状态，并丝滑地加载目标发音人的录制进度。为防止数据串音写入，在“录音进行中”或“音频落盘处理中”的短暂时间内，切换发音人的操作会被禁用。</li>
            <li><strong>删除发音人（危险操作）</strong>：点击发音人姓名右侧的垃圾桶图标，并在弹窗中确认。该操作会不可逆地移除该发音人的档案及其名下的全部录音记录。如有保留需要，同样请先备份 <code>.teproj</code> 工程。</li>
            <li><strong>理解进度统计</strong>：左侧面板显示的进度百分比，其分母始终是“当前字表全部分组的词项总数”，分子是“当前发音人拥有有效录音文件的词项数”。即使你在右侧列表中通过下拉菜单筛选了只显示某个特定分组的词条，这也不会改变总进度的计算基准。</li>
          </ul>
        </section>

        <hr className="help-divider" />

        {/* Part 4 */}
        <section id="part4" className="help-section">
          <h3><NavigationIcon /> 第四部分：录音实战与导航操控</h3>
          <p>准备工作就绪后，便进入了高频的录音阶段。PhonRec 提供了三种截然不同的录音驱动模式，以适应不同用户的工作习惯。</p>

          <h4><SettingsIcon /> 1. 三大录音模式对比解析</h4>
          <p>请在“设置 &rarr; 录音与导航 &rarr; 录音模式”中选择最适合你的操作方式：</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '18%' }}>录音模式</th>
                <th style={{ width: '22%' }}>适用人群与场景</th>
                <th>详细操作流程与内在逻辑</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>点击开关</strong><br /><small>(手动模式)</small></td>
                <td>适合需要精确控制录音时机、习惯传统操作的用户。</td>
                <td>
                  1. 点击圆形的录音按钮（或按键盘快捷键）下达开始指令。<br />
                  2. 观察界面状态变为“录音中”后，开口发音。<br />
                  3. 读完后，再次点击按钮（或快捷键）下达停止指令。<br />
                  4. 软件进入“处理中”状态，通过质量校验后自动保存并前进到下一条。
                </td>
              </tr>
              <tr>
                <td><strong>按住录制</strong><br /><small>(对讲机模式)</small></td>
                <td>适合不想频繁点击两次按钮、希望操作更具连贯性的用户。</td>
                <td>
                  1. 使用鼠标左键或触摸屏<strong>按住</strong>录音按钮不放。<br />
                  2. 保持按压状态并进行发音朗读。<br />
                  3. 读完后松开鼠标，或将鼠标指针移出按钮范围，录音即刻停止。<br />
                  <em>注：此模式强烈依赖鼠标或触摸事件的连续性，当前不支持通过长按键盘空格键来实现对讲机语义。如果设备还未就绪你就松开了手，软件会判定操作无效并取消录音。</em>
                </td>
              </tr>
              <tr>
                <td><strong>智能 VAD</strong><br /><small>(自动跳转模式)</small></td>
                <td>适合追求极致效率，希望完全解放双手，在安静环境中连续朗读的高级用户。</td>
                <td>
                  1. 点击一次按钮启动流程。<br />
                  2. 界面提示“静音中”，软件开始监听。<br />
                  3. 当检测到稳定的人声，状态变为“说话中”。<br />
                  4. 读完词条后保持安静，软件检测到静音达到阈值，自动停止当前录音。<br />
                  5. 质量校验通过后自动跳到下一条，稍作短暂节拍停顿后，自动重启监听流程准备录制下一个词。<br />
                  <em>注：VAD 仅负责启停判断，无法鉴别你读的内容对不对。</em>
                </td>
              </tr>
            </tbody>
          </table>

          <h4><BellIcon /> 2. VAD 模式的保护机制与灵敏度调节</h4>
          <p>VAD（语音活动检测）虽然智能，但面对复杂的环境声容易发生误判。因此软件内置了多重保护机制：</p>
          <ul>
            <li><strong>超时丢弃</strong>：如果启动监听后约 8 秒钟依然没有检测到有效语音，本次录音会被主动丢弃。</li>
            <li><strong>强制掐断</strong>：单次自动录音的最长持续时间被限制在约 15 秒，超时将被强制停止，防止背景长音持续占用流。</li>
            <li><strong>熔断机制</strong>：如果连续三次没录到有效语音，或者连续三次录制的音频被判定为质量不合格（需重录），自动流程会立刻暂停，并提示用户检查硬件设备或改为手动模式。前两次失败时，系统会自动停留在当前词条进行重试。</li>
          </ul>
          <p>如果你发现 VAD 经常误触发或迟迟不结束，可以在设置中调整其灵敏度档位：</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '22%' }}>VAD 预设档位</th>
                <th style={{ width: '22%' }}>语音确认耗时</th>
                <th style={{ width: '22%' }}>尾随静音判定</th>
                <th>适用环境与表现特点</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>稳健型</strong></td>
                <td>约 350 毫秒</td>
                <td>约 1000 毫秒</td>
                <td>适合偶有环境杂音的房间。它需要听很久才确信你在说话，录完后也需要你保持长达 1 秒的安静才会结束。宁可动作慢一点，也绝不被关门声等突发杂音误导。</td>
              </tr>
              <tr>
                <td><strong>标准型</strong></td>
                <td>约 220 毫秒</td>
                <td>约 700 毫秒</td>
                <td>适合一般的室内录音环境。响应速度与防干扰能力的折中方案。</td>
              </tr>
              <tr>
                <td><strong>灵敏型</strong></td>
                <td>约 150 毫秒</td>
                <td>约 450 毫秒</td>
                <td>适合极其安静的专业录音棚。反应极快，停顿不到半秒便立刻断句跳转，节奏非常干脆利落。</td>
              </tr>
            </tbody>
          </table>

          <h4><PlayIcon /> 3. 词条导航、回放与可视化图表</h4>
          <p>在录音过程中，熟练的导航与复查功能必不可少。</p>
          <ul>
            <li><strong>常规跳转与直接选择</strong>：除了使用上一条、下一条按钮，你还可以直接在右侧的长列表中点击任意条目进行远距离跳转。如果在录音进行中直接点击其他词条，软件会立刻取消尚未提交的当前录音片段（不作保存），直接切换到新词条并准备就绪。</li>
            <li><strong>随机排序功能</strong>：开启主界面上的“随机排序录制”开关后，当前视图列表中的词条顺序会被随机打乱。这仅仅是界面显示层面的乱序，用于防止发音人产生机械的肌肉记忆。它绝不会改变底层词表源文件以及最终导出的 <code>.teproj</code> 工程中的正式规范顺序。</li>
            <li><strong>音频回放与波形裁切</strong>：对于已经录制完成的词条，点击播放按钮即可加载收听，并在时间轴上拖拽进度。静止状态下展示的波形图并非原始音频的生硬铺陈，软件会根据能量估算，智能裁切掉首尾漫长的无声空白，并在有效语音两端各保留约 150 毫秒的余量，以最大化地看清波形细节。但这仅针对屏幕绘图，实际落盘的 WAV 文件是完整未损的。</li>
            <li><strong>语谱图分析（仅限完整模式）</strong>：完整模式下可将图表切换为“语谱图”。每一次录音完成后，分析引擎会渲染色彩丰富的频谱图像，并尝试在图上叠加绘制出 F0（基频/音高曲线）、F1 和 F2（共振峰轨迹）。这极大地辅助了研究人员进行人工判断。即便某次音频的共振峰计算失败，引擎也会尽量保留并展示基础的语谱底图，而不会导致录音保存失败。</li>
          </ul>

          <h4><NavigationIcon /> 4. 高效操作的快捷键体系</h4>
          <p>在“设置 &rarr; 录音与导航 &rarr; 快捷键预设”中，提供了多套映射方案，让你摆脱鼠标的束缚（注意：当输入框获取焦点或弹窗出现时，快捷键会自动屏蔽以防误触）。</p>

          <table className="help-table">
            <thead>
              <tr>
                <th>操作指令</th>
                <th>标准预设（最常用）</th>
                <th>左手预设</th>
                <th>右手预设</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>开始 / 停止录音</strong></td>
                <td><kbd className="keyboard-key">Space</kbd> (空格键)</td>
                <td><kbd className="keyboard-key">Space</kbd> (空格键)</td>
                <td><kbd className="keyboard-key">Enter</kbd> (回车/回车)</td>
              </tr>
              <tr>
                <td><strong>播放当前录音</strong></td>
                <td><kbd className="keyboard-key">R</kbd> 键</td>
                <td><kbd className="keyboard-key">S</kbd> 键</td>
                <td><kbd className="keyboard-key">K</kbd> 键</td>
              </tr>
              <tr>
                <td><strong>上一条</strong></td>
                <td><kbd className="keyboard-key">&larr;</kbd> (左方向键)</td>
                <td><kbd className="keyboard-key">A</kbd> 键</td>
                <td><kbd className="keyboard-key">J</kbd> 键</td>
              </tr>
              <tr>
                <td><strong>下一条</strong></td>
                <td><kbd className="keyboard-key">&rarr;</kbd> (右方向键)</td>
                <td><kbd className="keyboard-key">D</kbd> 键</td>
                <td><kbd className="keyboard-key">L</kbd> 键</td>
              </tr>
            </tbody>
          </table>
        </section>

        <hr className="help-divider" />

        {/* Part 5 */}
        <section id="part5" className="help-section">
          <h3><QualityIcon /> 第五部分：质量检测与声学参数解析</h3>
          <p>PhonRec 内置了一套严密的自动质量检测系统。它就像一位不知疲倦的质检员，在你每次停止录音的瞬间给出评估。你需要深入了解这些判定逻辑，才能理解软件为什么会拒绝你的某些音频。</p>

          <h4><ShieldIcon /> 1. 录音提交的硬性条件与判定等级</h4>
          <p>当你按下停止录音后，音频并不会毫无门槛地直接存入工作区。它必须同时满足以下条件才会正式“提交”：目标发音人和词条依旧存在、生成的 WAV 文件解析正常、底层落盘写入成功，<strong>以及最关键的一点：启用的质量检测规则没有下达“需重录”的指令。</strong></p>
          <p>质检员会给出以下三种评估级别：</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '18%' }}>质检结论</th>
                <th style={{ width: '18%' }}>界面表现</th>
                <th>针对当前录音的处理结果</th>
                <th>针对工程进度的影响</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>良好 (Accept)</strong></td>
                <td><span className="badge badge-success">绿色指示</span>，满分 100 分。</td>
                <td><strong>正常保存</strong>音频文件并更新记录。</td>
                <td>标记该词条为已完成，自动前进到下一条。</td>
              </tr>
              <tr>
                <td><strong>建议复核 (Review)</strong></td>
                <td><span className="badge badge-warning">黄色警告牌</span>，扣减对应分数（每项扣 8 分）。</td>
                <td><strong>依然正常保存</strong>，将瑕疵项记入元数据摘要。</td>
                <td>标记该词条为已完成，自动前进到下一条，留待日后人工试听确认。</td>
              </tr>
              <tr>
                <td><strong>需重录 (Retry)</strong></td>
                <td><span className="badge badge-danger">红色报错卡片</span>，固定分数较低。</td>
                <td><strong>拒绝保存并丢弃本次录制的音频切片</strong>。<em>如果原本词条已有合格录音，不会被覆盖。</em></td>
                <td>进度停止在当前词条不往前走，等待用户重新录制。</td>
              </tr>
            </tbody>
          </table>

          <p>如果你对某个已经录好的词条不满意，直接重新点击录音即可。新录音如果通过了质检，就会自动覆盖旧录音；如果新录音不幸触发了“需重录”，它将被抛弃，而你原本那条合格的旧录音依然安然无恙地保留着。你也可以点击垃圾桶图标直接手动“丢弃录音”，彻底清空该词条的数据。</p>

          <h4><SettingsIcon /> 2. 各项检测规则参数深度拆解</h4>
          <p>在“设置 &rarr; 质量检测”面板中，你可以通过总开关一键开启或关闭检测，或者针对每一项具体规则单独设置“宽松”、“标准”或“严格”档位。以下是各项规则的详细侦测逻辑及内部阈值说明。</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '22%' }}>质检规则名称</th>
                <th style={{ width: '22%' }}>所属模式</th>
                <th>检测对象与内部逻辑解析</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>有效音量</strong></td>
                <td>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'flex-start' }}>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                    <span className="tag-mode standalone-mode"><LayersIcon /> 独立</span>
                  </div>
                </td>
                <td>
                  检测声音是否微弱如蚊蝇，或响亮至失控。程序分析的是剥离了首尾静音后的有效语音段平均能量。<br />
                  &bull; <strong>严格档</strong>：音量必须在 -30 dBFS 至 -9 dBFS 之间，超出即判“需重录”。<br />
                  &bull; <strong>标准档</strong>：容忍下限放宽至 -35 dBFS，上限放宽至 -6 dBFS。<br />
                  &bull; <strong>宽松档</strong>：容忍下限低至 -40 dBFS，上限高达 -3 dBFS。
                </td>
              </tr>
              <tr>
                <td><strong>音频截断 (削波)</strong></td>
                <td>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'flex-start' }}>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                    <span className="tag-mode standalone-mode"><LayersIcon /> 独立</span>
                  </div>
                </td>
                <td>
                  当声音能量达到数字音频系统的满幅天花板时，波形顶部会被强行“切平”，造成无可挽回的失真。<br />
                  &bull; <strong>严格档</strong>：检测到任何微小的削波都会警报，若满幅采样占比达 0.1% 直接判“需重录”。<br />
                  &bull; <strong>标准档</strong>：满幅占比超 0.01% 提示复核，达到 0.3% 时判“需重录”。<br />
                  &bull; <strong>宽松档</strong>：满幅占比超 0.3% 才提示复核，达到 1% 时才强制要求重录。
                </td>
              </tr>
              <tr>
                <td><strong>有效语音</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>仅</span>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                  </div>
                </td>
                <td>
                  评估录音片段里你真正发声的时间占比。<br />
                  &bull; <strong>严格档</strong>：整段音频中至少要有 300 毫秒是真正的人声，且有效人声占比不得低于整段音频的 15%，否则判定为“语音不足 / 录音过短”。<br />
                  &bull; <strong>宽松档</strong>：门槛极大地降低，仅需 100 毫秒声音，占比 4% 即可放行。
                </td>
              </tr>
              <tr>
                <td><strong>背景噪声</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>仅</span>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                  </div>
                </td>
                <td>
                  评估录音环境有多吵。系统会计算信噪比（SNR），即目标人声能量相对于底层背景持续底噪的倍数。<br />
                  &bull; <strong>严格档</strong>：信噪比低于 10 dB（声音不够突出）就会直接触发“需重录”。10-16 dB 之间给“建议复核”。<br />
                  &bull; <strong>宽松档</strong>：哪怕信噪比跌到 3 dB 才会被强制退回重录，容忍度极高。
                </td>
              </tr>
              <tr>
                <td><strong>可能的嘎裂声</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>仅</span>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                  </div>
                </td>
                <td>针对发音学中的低频不规则发声现象。系统使用短时自相关算法，寻找频率在 45-85 赫兹的低频异常帧。这仅仅作为一种基于统计的线索提示，只会给出“建议复核”，<strong>绝不会</strong>因此单项直接判决录音失败。</td>
              </tr>
              <tr>
                <td><strong>直流偏移</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>仅</span>
                    <span className="tag-mode full-mode"><SparklesIcon /> 完整</span>
                  </div>
                </td>
                <td>检测麦克风或声卡硬件导致的波形中心线偏离 0 刻度的现象。同样只触发“建议复核”。绝对值偏移量在标准档下超过 0.03 就会亮起提示灯。</td>
              </tr>
            </tbody>
          </table>

          <div className="alert-box alert-important">
            <div className="alert-icon"><CheckAlertIcon /></div>
            <div className="alert-text">
              <strong>重要提示</strong>：界面上显示的 100 分制质量分数，仅仅是为了让用户能在扫视时快速获得一个直观印象。由于它的计算规则是阶梯式扣分（出现一条重录项直接扣 45 分，出现瑕疵复核项每条扣 8 分，扣到 0 分为止），因此它并不是一个平滑连续的线性数值。请研究人员在撰写论文或实验报告时，不要将此界面分数直接作为声学质量变量进行回归分析，最终的质量定性仍需依赖人工听辨。
            </div>
          </div>
        </section>

        <hr className="help-divider" />

        {/* Part 6 */}
        <section id="part6" className="help-section">
          <h3><ArchiveIcon /> 第六部分：数据归档、导出与应急排错</h3>
          <p>所有录音工作的最终目的都是为了安全交付数据。本部分将深入讲解 PhonRec 的数据留存机制、导出格式规范，以及在遇到常见问题时的应对策略。</p>

          <h4><ArchiveIcon /> 1. 自动保存与手工导出工程</h4>
          <p>PhonRec 的后台设有一个强制运行的“自动保存”机制。你的任何实质性操作（增加发音人、录完一个词、丢弃一条录音等），都会被通过临时文件和原子替换技术，实时地写入电脑本地受控工作区（位于系统深度隐藏文件夹内）。这能有效防止软件崩溃导致的数据丢失。但这仅仅是程序的防灾缓冲地带，<strong>绝不可将其作为长期的备份手段。</strong></p>
          <p>为了实现跨设备迁移、任务交付和长期归档，你必须点击主界面底部的“保存工程”按钮。</p>
          <ul>
            <li><strong><code>.teproj</code> 工程归档文件</strong>：这是软件最核心的标准化产物。保存后会生成一个拓展名为 <code>.teproj</code> 的压缩包，里面包含了描述工程全貌的 <code>project.json</code> 索引文件，以及 <code>audio/</code> 目录下所有发音人的规范 WAV 录音文件。这个包是高度安全的，独立模式在读取导入它时，有着极其严苛的安全上限防护（最多解压 10,000 个文件、单文件不能超过 2 GiB、解压总容量受限为 20 GiB，并会拦截各种越界、非法的恶意目录路径）。</li>
            <li><strong>如何导入 <code>.teproj</code>？</strong> 点击底部的导入工程按钮，选择目标文件后软件会进行校验。导入成功后，系统会给出一份包括“合并发音人数”、“缺失字表数”等详情的导入体检报告。需要强调的是，<strong>导入工程的过程是用压缩包内的状态全面替换掉你当前界面上的工作区</strong>，而不是合并追加。如果导入过程因包体损坏而失败，系统会自动中止，你的原工作区会安然无恙地保留。</li>
          </ul>

          <h4><FileTextIcon /> 2. 文件夹工程与 WAV 批量导出</h4>
          <p>针对特殊需求，两种模式各提供了一种差异化的产物导出方式：</p>
          <ul>
            <li>
              <strong>完整模式专属：暴露模式的外部文件夹工程</strong><br />
              在设置中心，完整模式用户可以将保存格式改为“外部工程文件夹目录”。执行保存时，软件会在你指定的一个<strong>空文件夹</strong>中，直接建立未压缩 of 目录树。典型结构如下，包含极其详尽的日志汇总表：
              <pre className="help-code-block">
{`指定的目标空目录/
├─ .phonrec-project.json (识别标记文件)
├─ project.json (核心结构索引)
├─ wordlist/wordlist.ptwl (高级词表备份)
├─ logs/recordings.csv (记录了音频路径、录制时间、设备来源及质量摘要的日志表)
└─ audio/
   └─ 发音人目录/
      └─ 分组编号_词项名称__词项ID.wav`}
              </pre>
            </li>
            <li>
              <strong>独立模式专属：批量导出 WAV 目录</strong><br />
              由于独立模式无法生成带有复杂声学分析日志的文件夹工程，它在界面底部提供了一个直观的“导出 WAV”按钮。点击后，软件会在你指定的路径下，自动新建一个按照日期时间命名的总文件夹，并像整理文件柜一样，严格按照发音人、分组、词项层级把所有合格的录音抽取出来。<br />
              如果导出结果提示有“跳过条数”，那代表某些词条你还没有录制，或者其底层音频文件损坏无法读取，程序并不会自作主张地给你生成一个无声的空文件来凑数。
            </li>
          </ul>

          <h4><AlertMiniIcon /> 3. 悬崖勒马：不可逆危险操作清单</h4>
          <p>由于 PhonRec 强调数据的确定性，部分操作具有极强的破坏效果。在执行下表所列操作前，强烈建议先执行一次导出 <code>.teproj</code> 备份。</p>

          <table className="help-table help-table-danger">
            <thead>
              <tr>
                <th style={{ width: '25%' }}>危险操作动作</th>
                <th style={{ width: '40%' }}>将引发的不可逆后果</th>
                <th>防范建议与补救原则</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>导入或更换新的字表</strong></td>
                <td>清除所有发音人当前的录音文件记录，工程进度被全部重置覆盖。</td>
                <td>操作前先导出当前 <code>.teproj</code>。</td>
              </tr>
              <tr>
                <td><strong>导入外部 <code>.teproj</code></strong></td>
                <td>外部工程状态直接替换掉屏幕上当前所有进度。</td>
                <td>若当前有未保存的进度，请先将其存为另一个工程文件。</td>
              </tr>
              <tr>
                <td><strong>右侧设置面板点击“清空工作区”</strong></td>
                <td>彻底销毁当前工作区内的全部发音人、字表、各类参数及所有的音频资产。</td>
                <td>此为最彻底的项目清算操作，切勿在没有备份的情况下点击。</td>
              </tr>
              <tr>
                <td><strong>删除特定发音人</strong></td>
                <td>仅销毁该发音人名下的所有进度与录音文件。</td>
                <td>确认该发音人数据确实不再需要，或已剥离备份。</td>
              </tr>
            </tbody>
          </table>

          <h4><HelpIcon /> 4. 常见疑难杂症自救指南 (Q&A)</h4>
          <p>遇到软件罢工时，请对照下表寻找病因。</p>

          <table className="help-table">
            <thead>
              <tr>
                <th style={{ width: '30%' }}>出现的故障症状</th>
                <th>可能的原因排查与解决对策</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="qa-badge q-badge">Q</span><strong>启动停滞：提示分析引擎启动失败或需要安装 PhonTracer</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.2rem' }}>
                    <span className="qa-badge a-badge">A</span>
                    <div style={{ flex: 1 }}>
                      1. 确认已正确安装了 PhonTracer 主套件，且版本号匹配。<br />
                      2. Windows 下如果只是解压了主程序便携版，注册表将缺乏路径指引。请使用安装版。<br />
                      3. 暂时需要赶进度时，果断点击“进入独立软件模式”先进行录音。
                    </div>
                  </div>
                </td>
              </tr>
              <tr>
                <td><span className="qa-badge q-badge">Q</span><strong>麦克风静默：不论怎么说话，音量条就是不动</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.2rem' }}>
                    <span className="qa-badge a-badge">A</span>
                    <div style={{ flex: 1 }}>
                      1. 检查“设备与系统”设置，是否错选了设备，尝试点击“刷新硬件设备”。<br />
                      2. 权限阻拦：点击重新请求权限，或直接前往系统的隐私设置页面手动放行麦克风。<br />
                      3. 硬件独占：检查是否有视频会议软件等正在后台死死霸占着麦克风资源，将其关闭。
                    </div>
                  </div>
                </td>
              </tr>
              <tr>
                <td><span className="qa-badge q-badge">Q</span><strong>回环失效：Windows 选择系统声音回环后录不到声音</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.2rem' }}>
                    <span className="qa-badge a-badge">A</span>
                    <div style={{ flex: 1 }}>
                      1. 确认该回环选项所对应的扬声器，是不是当前电脑正在出声的那个扬声器。<br />
                      2. 操作时序建议：先让电脑把视频或音乐播放出声，看到 PhonRec 音量条跳动后，再按下录音按钮。
                    </div>
                  </div>
                </td>
              </tr>
              <tr>
                <td><span className="qa-badge q-badge">Q</span><strong>VAD 失控：一直不开始录音，或者迟迟不自动停止</strong></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.2rem' }}>
                    <span className="qa-badge a-badge">A</span>
                    <div style={{ flex: 1 }}>
                      1. 迟迟不启动：可能是麦克风增益太小或你发音太轻，去设置里将档位调至“灵敏型”。<br />
                      2. 迟迟不结束：说明房间里存在持续的嗡嗡底噪，VAD 误以为你还在拖长音。调至“稳健型”或关闭环境干扰源。<br />
                      3. 如果连续三次被 VAD 机制阻断，不要硬刚，先切回“手动点击”模式录两条排查设备情况。
                    </div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <div className="help-footer-card">
          <p style={{ margin: 0, textAlign: 'center', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            掌握了以上六个部分的知识，你已经具备了驾驭 PhonRec 应对各种繁重录音任务的能力。<br />
            希望这套工具能成为你科研探索与日常工作中的得力助手！
          </p>
        </div>
        </div>
      </div>

      {/* 底部贴底悬浮快速跳转栏（窄屏模式下由 CSS 激活） */}
      <div className="help-bottom-nav">
        <span className="help-bottom-nav-title">快速跳转：</span>
        <div className="help-bottom-nav-links">
          {[
            { id: 'part1', text: '定位环境' },
            { id: 'part2', text: '界面硬件' },
            { id: 'part3', text: '字表发音' },
            { id: 'part4', text: '录音操控' },
            { id: 'part5', text: '质检参数' },
            { id: 'part6', text: '数据应急' }
          ].map((item, index) => (
            <a
              key={item.id}
              ref={(el) => (buttonRefs.current[index] = el)}
              href={`#${item.id}`}
              className={`help-bottom-nav-btn ${activePart === index ? 'active' : ''}`}
              onClick={(e) => handleScrollTo(e, item.id, index)}
            >
              {item.text}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
