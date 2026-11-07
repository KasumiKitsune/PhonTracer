import { useState, useEffect, useRef, useMemo } from 'react';

// --- Local Icons ---

const WarningIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: '#f59e0b', flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

const InfoAlertIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: '#3b82f6', flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);

const CheckAlertIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: '#10b981', flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

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

export default function OperationGuide({ isOpen, onClose } = {}) {
  const [activePart, setActivePart] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const buttonRefs = useRef([]);

  // Section anchor IDs matching SettingsModal floating navigation
  const sections = [
    { id: 'part1', title: '定位与运行环境', icon: <EnvironmentIcon />, keywords: '定位 环境 架构 rust 引擎 phontracer 权限 macos windows 隐私 本地 声卡 枚举' },
    { id: 'part2', title: '界面与硬件接入', icon: <HardwareIcon />, keywords: '界面 布局 硬件 麦克风 回环 监听 设备 声卡 抽屉 电平 四栏 待机' },
    { id: 'part3', title: '字表与发音人', icon: <UserIcon />, keywords: '字表 词表 格式 csv txt xlsx json 表头 发音人 进度 替换 随机排序 word ipa pinyin group note 重命名' },
    { id: 'part4', title: '录音与导航操控', icon: <NavigationIcon />, keywords: '录音 模式 点击 按住 对讲机 vad 快捷键 space enter 方向键 波形图 语谱图 fft 共振峰 筛选 下一未录' },
    { id: 'part5', title: '质检与参数解析', icon: <QualityIcon />, keywords: '质量 质检 音量 截断 削波 嘎裂声 有效语音 噪声 snr 直流偏移 阈值 评分 重录 复核 dbfs 防喷罩 爆音' },
    { id: 'part6', title: '归档导出与应急', icon: <ArchiveIcon />, keywords: '导出 保存 工程 teproj wav 文件夹 phontracer 清空工作区 faq 排错 故障 textgrid' }
  ];

  // Search filtering state
  const cleanQuery = searchQuery.trim().toLowerCase();
  const isSearching = cleanQuery.length > 0;

  const matchedPartIds = useMemo(() => {
    if (!isSearching) return new Set(sections.map(s => s.id));
    const matched = new Set();
    sections.forEach(s => {
      if (s.title.toLowerCase().includes(cleanQuery) || s.keywords.toLowerCase().includes(cleanQuery)) {
        matched.add(s.id);
      }
    });
    // Check text inside sections if rendered
    return matched;
  }, [cleanQuery]);

  useEffect(() => {
    const container = document.querySelector('.help-scroll-content');
    if (!container) return;

    const observerOptions = {
      root: container,
      rootMargin: '-15% 0px -50% 0px',
      threshold: 0
    };

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          const index = sections.findIndex(s => s.id === id);
          if (index >= 0) {
            setActivePart(index);
            const btn = buttonRefs.current[index];
            if (btn) {
              btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
            }
          }
        }
      });
    }, observerOptions);

    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, [sections]);

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

  const isVisible = (id) => !isSearching || matchedPartIds.has(id);

  return (
    <div className="help-container">
      {/* 帮助指南内容主滚动区 */}
      <div className="help-scroll-content">
        <div className="help-markdown-content">

          {/* 顶栏手册导读与搜索卡片 */}
          <div className="help-intro-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem', marginBottom: '0.5rem' }}>
              <h2 style={{ margin: 0, fontSize: '1.08rem', fontWeight: 700, color: 'var(--color-accent)', display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                PhonRec 操作与田野录音指引
              </h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="badge badge-success">开箱即用</span>
                <span className="badge badge-warning">Rust 原生分析</span>
              </div>
            </div>
            <p style={{ margin: 0, fontSize: '0.8rem', lineHeight: '1.55', color: 'var(--text-secondary)' }}>
              面向语言学田野调查与发音实验的高效录音工作站。涵盖硬件配置、字表格式规范、录音与快捷键、六维音质阈值标准以及与 PhonTracer 联动归档。
            </p>

            {/* 关键词检索输入框 */}
            <div style={{ marginTop: '0.75rem', position: 'relative', width: '100%' }}>
              <input
                type="text"
                className="form-input"
                placeholder="搜索指南关键词（如：爆音、语谱图、快捷键、发音人、WAV、阈值）..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  width: '100%',
                  padding: '0.42rem 2rem 0.42rem 2rem',
                  fontSize: '0.78rem',
                  borderRadius: '6px',
                  boxSizing: 'border-box'
                }}
              />
              <span style={{ position: 'absolute', left: '0.65rem', top: '50%', transform: 'translateY(-50%)', opacity: 0.5, pointerEvents: 'none', fontSize: '0.82rem' }}>
                🔍
              </span>
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  style={{
                    position: 'absolute',
                    right: '0.6rem',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-muted)',
                    fontSize: '0.85rem',
                    padding: '0.2rem'
                  }}
                  title="清除搜索"
                >
                  ✕
                </button>
              )}
            </div>

            {isSearching && (
              <div style={{ marginTop: '0.45rem', fontSize: '0.72rem', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>找到 {matchedPartIds.size} 个匹配章节</span>
                <span style={{ cursor: 'pointer', color: 'var(--color-accent)' }} onClick={() => setSearchQuery('')}>清除检索条件</span>
              </div>
            )}
          </div>

          <hr className="help-divider" />

          {/* Part 1 */}
          <section id="part1" className="help-section" style={{ display: isVisible('part1') ? 'block' : 'none' }}>
            <h3><EnvironmentIcon /> 第一部分：定位与运行环境</h3>
            <p>PhonRec 专为语言学调查、实验室发音采集及万级词条录音设计，支持多发音人管理与流水线采集。</p>

            <h4>1. 软件定位与原生声学架构</h4>
            <ul>
              <li><strong>内置原生引擎</strong>：内置 Rust 高效时域与频域声学检测核心，并搭配 Canvas 语谱图实时渲染。无需配置 Python 环境或第三方依赖，纯本地运行，启动即可使用全量质检与语谱图。</li>
              <li><strong>PhonTracer 一键联动</strong>：录音完成后，点击主界面【在 PhonTracer 中打开】，无缝送入 PhonTracer 进行音段切分（Segmentation）、音标对齐与共振峰/基频精细分析。</li>
              <li><strong>本地安全与隐私</strong>：所有音频与元数据完全保存在本机硬盘工作区，绝无任何网络数据上传。</li>
            </ul>

            <h4>2. 操作系统支持与麦克风授权</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '20%' }}>操作系统</th>
                  <th style={{ width: '22%' }}>架构与格式</th>
                  <th>安装与权限建议</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><span className="tag-os windows"><WindowsIcon /> Windows</span></td>
                  <td>x64 / ARM64 (<code>.exe</code>)</td>
                  <td>下载安装程序运行即可。支持物理麦克风及独占的“系统声音回环录音”。</td>
                </tr>
                <tr>
                  <td><span className="tag-os macos"><MacIcon /> macOS</span></td>
                  <td>Apple Silicon (<code>.dmg</code>)</td>
                  <td>需 macOS 12+。首次录音请在系统弹窗允许麦克风权限；若误拒请至“系统设置 → 隐私与安全性 → 麦克风”放行。</td>
                </tr>
              </tbody>
            </table>

            <div className="alert-box alert-note">
              <div className="alert-icon"><InfoAlertIcon /></div>
              <div className="alert-text">
                <strong>麦克风检测提示</strong>：若对着话筒说话时右栏电平表毫无反应，请点击设置中的【重新请求并检测权限】，或点击【刷新硬件设备】重新枚举声卡。
              </div>
            </div>
          </section>

          {/* Part 2 */}
          <section id="part2" className="help-section" style={{ display: isVisible('part2') ? 'block' : 'none' }}>
            <h3><HardwareIcon /> 第二部分：界面与硬件接入</h3>
            <p>界面采用高效的四栏工作流布局，视线与操作逻辑高度聚焦。</p>

            <h4>1. 四栏工作区导览</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '26%' }}>界面区域</th>
                  <th>主要功能与现场操作</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>左栏：发音人与字表</strong></td>
                  <td>导入字表文件、添加与切换发音人、展示当前发音人的全局录制完成进度。窗口较窄时可由左上角菜单抽屉唤出。</td>
                </tr>
                <tr>
                  <td><strong>中栏：核心录音控制台</strong></td>
                  <td>大字号居中呈现当前词项、国际音标、拼音与备注；提供大尺寸录音启停键、波形试听进度条，以及底部的【导入工程】、【导出 WAV】、【在 PhonTracer 中打开】和【保存工程】。</td>
                </tr>
                <tr>
                  <td><strong>中右栏：词表清单面板</strong></td>
                  <td>垂直滚动浏览全表词条，支持发音组筛选下拉框；通过“未录/已录”状态与“优质/偏小/截断”标签清晰区分，点击任意词条即可快速定位。</td>
                </tr>
                <tr>
                  <td><strong>右栏：检测与图谱面板</strong></td>
                  <td>实时麦克风输入电平表、录后六维声学质检卡片、波形图与 512 点 FFT 语谱图切换视图，以及全局设置和工作区管理入口。</td>
                </tr>
              </tbody>
            </table>

            <h4>2. 音频输入源与系统声音回环</h4>
            <ul>
              <li><strong>物理麦克风</strong>：前往“设置 → 设备与系统”选择输入源，推荐使用 USB 声卡或专业心形指向电容麦。新插拔设备后点击【刷新硬件设备】即可同步。</li>
              <li><strong>系统声音回环录音 (Windows 专属)</strong>：在输入设备下拉框选择“系统声音回环”，可直接内录电脑当前播放的声音（如标准发音示范音频），免除外界杂音干扰，自动转为单声道 WAV。</li>
              <li><strong>待机音频输入监听</strong>：建议保持开启。后台预热音频通道，不仅消除点击录音时的硬件唤醒迟滞，还能随时通过电平表观察环境底噪。</li>
            </ul>
          </section>

          {/* Part 3 */}
          <section id="part3" className="help-section" style={{ display: isVisible('part3') ? 'block' : 'none' }}>
            <h3><UserIcon /> 第三部分：字表与发音人</h3>
            <p>字表规范决定了词条展示与声学标记的完整度。支持 Excel、CSV 与纯文本一键导入。</p>

            <h4>1. 支持的字表格式与推荐表头</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '22%' }}>格式</th>
                  <th style={{ width: '28%' }}>推荐结构与表头</th>
                  <th>解析规则与编写建议</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>CSV 表格字表</strong><br /><code>.csv</code></td>
                  <td>推荐表头：<br /><code>word, ipa, pinyin, group, note</code><br />亦兼容中文表头：<br /><code>词项, 音标, 拼音, 组名, 词项备注</code></td>
                  <td>必须包含 <code>word</code> 或 <code>词项</code> 列。若有 <code>ipa</code>、<code>pinyin</code> 会自动在大字下方展示；未知自定义列会保存在条目元数据中随工程导出。</td>
                </tr>
                <tr>
                  <td><strong>TXT 纯文本字表</strong><br /><code>.txt</code></td>
                  <td>一行一词；<br />使用 <code>【组名】</code>、<code>[组名]</code> 或 <code>#组名</code> 分组</td>
                  <td>轻量快捷格式，未加组名标记的词条归入“未分组”。词内斜杠（如 <code>北/京</code>）会被保留作为发音边界提示，不会拆分为两行。</td>
                </tr>
                <tr>
                  <td><strong>PTWL / JSON 高级字表</strong><br /><code>.ptwl / .json</code></td>
                  <td>PhonTracer 标准字表规范（含 <code>groups</code> 分组结构）</td>
                  <td>完整保留词条全局唯一 ID、多音节切分、实验条件标注与扩展字段。亦支持词条列表或分组映射格式。</td>
                </tr>
              </tbody>
            </table>

            <div className="alert-box alert-warning">
              <div className="alert-icon"><WarningIcon /></div>
              <div className="alert-text">
                <strong>导入新字表属于全盘替换操作</strong>：<br />
                导入新字表会清空当前工作区的所有词条，并重置各发音人的录音记录与本地音频缓存。在更换字表前，<strong>请务必先点击底部【保存工程】导出一份 <code>.teproj</code> 备份</strong>。
              </div>
            </div>

            <h4>2. 多发音人生命周期管理</h4>
            <ul>
              <li><strong>添加与切换</strong>：在左栏点击【+ 添加】输入发音人编号或姓名。点击发音人条目即可丝滑切换，各发音人录音文件与进度相互独立、互不干扰。</li>
              <li><strong>随机排序录制</strong>：开启主界面底部的【随机排序录制】，仅打乱界面展现顺序以防发音人产生机械疲劳，不会改变底层词表原始序号与导出工程顺序。</li>
            </ul>
          </section>

          {/* Part 4 */}
          <section id="part4" className="help-section" style={{ display: isVisible('part4') ? 'block' : 'none' }}>
            <h3><NavigationIcon /> 第四部分：录音与导航操控</h3>
            <p>提供适应不同田野节奏的三大录音模式，搭配单手键盘快捷键，大幅提升采集效率。</p>

            <h4>1. 三大录音模式对比</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '22%' }}>录音模式</th>
                  <th style={{ width: '28%' }}>适用场景</th>
                  <th>操作手感与自动化逻辑</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>点击开关模式</strong></td>
                  <td>常规实验与精细录音</td>
                  <td>按一次开始，朗读完毕按一次停止。录后质检合格自动落盘，最稳健可控。</td>
                </tr>
                <tr>
                  <td><strong>按住录制模式</strong><br />(对讲机)</td>
                  <td>快速单字高频连录</td>
                  <td>按住录音键朗读，松开立即停止。单手鼠标或触摸屏操作节奏极快。</td>
                </tr>
                <tr>
                  <td><strong>智能 VAD 模式</strong><br />(自动断句)</td>
                  <td>静音室解放双手连续读</td>
                  <td>点击一次开启监听，开口自动开录，读完停顿自动切断并检测跳转。设置中可选“稳健型”（尾随静音 1000ms）、“标准型”（700ms）、“灵敏型”（450ms）。连续 3 次无有效语音自动熔断暂停。</td>
                </tr>
              </tbody>
            </table>

            <h4>2. 键盘快捷键速查表</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th>操作动作</th>
                  <th>标准预设 (默认)</th>
                  <th>左手预设 (极速单手)</th>
                  <th>右手预设 (数字区)</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>录音 / 停止</strong></td>
                  <td><kbd className="keyboard-key">Space</kbd> (空格)</td>
                  <td><kbd className="keyboard-key">Space</kbd> (空格)</td>
                  <td><kbd className="keyboard-key">Enter</kbd> (回车)</td>
                </tr>
                <tr>
                  <td><strong>播放 / 试听</strong></td>
                  <td><kbd className="keyboard-key">R</kbd></td>
                  <td><kbd className="keyboard-key">S</kbd></td>
                  <td><kbd className="keyboard-key">K</kbd></td>
                </tr>
                <tr>
                  <td><strong>上一词条</strong></td>
                  <td><kbd className="keyboard-key">&larr;</kbd> 方向键</td>
                  <td><kbd className="keyboard-key">A</kbd></td>
                  <td><kbd className="keyboard-key">J</kbd></td>
                </tr>
                <tr>
                  <td><strong>下一词条</strong></td>
                  <td><kbd className="keyboard-key">&rarr;</kbd> 方向键</td>
                  <td><kbd className="keyboard-key">D</kbd></td>
                  <td><kbd className="keyboard-key">L</kbd></td>
                </tr>
              </tbody>
            </table>

            <h4>3. 波形图与 512 点 FFT 语谱图读图指南</h4>
            <ul>
              <li><strong>时域波形图</strong>：展示振幅包络，自动修剪首尾冗长静音段。若波形峰值被上下边框硬切变平，即表示存在爆音削顶（Clipping）。</li>
              <li><strong>512 点 FFT 语谱图</strong>：提供 0~8kHz 频带的时频能量分布图。可直观观察元音频带上的共振峰（Formants）能量浓度、嗓音纯净度及环境底噪横纹。</li>
              <li><em>说明：F0 音高基频轨迹曲线与精细音段边界切分，将在点击【在 PhonTracer 中打开】后由主分析引擎深度提取并高精度呈现在标注轨道中。</em></li>
            </ul>
          </section>

          {/* Part 5 */}
          <section id="part5" className="help-section" style={{ display: isVisible('part5') ? 'block' : 'none' }}>
            <h3><QualityIcon /> 第五部分：质检与参数解析</h3>
            <p>每次停止录音后，Rust 本地声学引擎在毫秒级内完成六维分析并给出清晰指示。</p>

            <h4>1. 质检判定等级与现场动作</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>结论</th>
                  <th style={{ width: '18%' }}>界面标记</th>
                  <th>音频文件处理</th>
                  <th>田野现场建议动作</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>良好 (Good)</strong></td>
                  <td><span className="badge badge-success">绿色优质</span></td>
                  <td>正常保存落盘，词条标为合格并前进。</td>
                  <td>录音完全达标，继续进行下一词条。</td>
                </tr>
                <tr>
                  <td><strong>建议复核 (Review)</strong></td>
                  <td><span className="badge badge-warning">黄色警告</span></td>
                  <td><strong>正常保存落盘</strong>，在元数据中记录异常项摘要。</td>
                  <td>存在轻度瑕疵（轻微截断或微弱底噪），不中断采集节奏，留待实验后统一试听复核。</td>
                </tr>
                <tr>
                  <td><strong>需重录 (Retry)</strong></td>
                  <td><span className="badge badge-danger">红色重录</span></td>
                  <td><strong>拒绝保存并丢弃本次音频</strong>（原已有合格录音保留不覆盖）。</td>
                  <td>存在致命缺陷（严重爆音失真、音量太微弱或发音中断），停留在当前条目，按提示调整设备后重新录制。</td>
                </tr>
              </tbody>
            </table>

            <h4>2. 六项检测指标与精准阈值表</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>检测项</th>
                  <th style={{ width: '22%' }}>分析原理</th>
                  <th style={{ width: '30%' }}>各档位阈值 (宽松 / 标准 / 严格)</th>
                  <th>异常原因与现场调音方案</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>有效音量</strong><br />(volume)</td>
                  <td>剥离首尾静音后，计算发声核心段的平均 RMS 分贝值。</td>
                  <td>
                    • 宽松：<code>-40 ~ -3 dBFS</code><br />
                    • 标准：<code>-34 ~ -6 dBFS</code><br />
                    • 严格：<code>-30 ~ -9 dBFS</code>
                  </td>
                  <td>过小多因离麦克风太远或声卡增益不足；过大则易接近饱和。现场微调声卡 Gain 旋钮或口麦距离（建议 15~20cm）。</td>
                </tr>
                <tr>
                  <td><strong>音频截断</strong><br />(clipping)</td>
                  <td>统计有效语音段满幅采样点（<code>|s| &ge; 0.99</code>）占比与连续削波平顶。</td>
                  <td>
                    • 宽松：<code>&le; 0.3%</code> (重录 &ge; 1.0%)<br />
                    • 标准：<code>&le; 0.01%</code> (重录 &ge; 0.2% 或连续平顶)<br />
                    • 严格：<code>0%</code> (重录 &ge; 0.1%)
                  </td>
                  <td>严重截断产生不可逆的破音失真。触发红灯时必须调小输入增益，并加装防喷罩防止送气塞音爆破。</td>
                </tr>
                <tr>
                  <td><strong>嘎裂声</strong><br />(creak)</td>
                  <td>汉宁窗加窗 NACF 归一化自相关分析脉冲抖动（Jitter &gt; 18% 或 Shimmer &gt; 25%）。</td>
                  <td>
                    • 宽松：<code>&le; 25%</code><br />
                    • 标准：<code>&le; 15%</code><br />
                    • 严格：<code>&le; 8%</code>
                  </td>
                  <td>仅提示复核，绝不强制重录。算法已针对成年男声低基频与汉语上声（三声）平稳曲折调做防误报优化，提示时人工听辨确认即可。</td>
                </tr>
                <tr>
                  <td><strong>有效语音</strong><br />(speech)</td>
                  <td><code>-45 dBFS</code> 绝对底噪下限门槛 + 有效语音时长与占比双重校验。</td>
                  <td>
                    • 宽松：<code>&ge; 4%</code> 且 <code>&ge; 100ms</code><br />
                    • 标准：<code>&ge; 8%</code> 且 <code>&ge; 140ms</code><br />
                    • 严格：<code>&ge; 15%</code> 且 <code>&ge; 300ms</code>
                  </td>
                  <td>杜绝纯静音空录被误判为优质，同时保护清擦音与短促单字；若触发重录，请提示发音人清晰完整朗读。</td>
                </tr>
                <tr>
                  <td><strong>背景噪声</strong><br />(noise)</td>
                  <td>信噪比（SNR）：有效人声平均能量相对于底层静音底噪的比值。</td>
                  <td>
                    • 宽松：<code>&ge; 8 dB</code> (重录 &lt; 3 dB)<br />
                    • 标准：<code>&ge; 14 dB</code> (重录 &lt; 6 dB)<br />
                    • 严格：<code>&ge; 18 dB</code> (重录 &lt; 10 dB)
                  </td>
                  <td>低信噪比影响后续声学切分。关闭空调、风扇或远离主机风道，让发音人发音更洪亮以提升信噪比。</td>
                </tr>
                <tr>
                  <td><strong>直流偏移</strong><br />(dc_offset)</td>
                  <td>波形时域均值偏离零基准轴的百分比绝对值。</td>
                  <td>
                    • 宽松：<code>&le; 5.0%</code><br />
                    • 标准：<code>&le; 3.0%</code><br />
                    • 严格：<code>&le; 1.5%</code>
                  </td>
                  <td>硬件电路接地不良或滤波故障所致。仅提示复核，现代 USB 声卡通常在 0.1% 以下。</td>
                </tr>
              </tbody>
            </table>

            <div className="alert-box alert-important">
              <div className="alert-icon"><CheckAlertIcon /></div>
              <div className="alert-text">
                <strong>评分机制说明</strong>：初始满分 100 分。出现“需重录”项扣 45 分，出现“建议复核”项每项扣 8 分。此分数主要用于田野采集时一目了然的整体印象，最终声学验收建议结合人工听辨与语谱图复核。
              </div>
            </div>
          </section>

          {/* Part 6 */}
          <section id="part6" className="help-section" style={{ display: isVisible('part6') ? 'block' : 'none' }}>
            <h3><ArchiveIcon /> 第六部分：归档导出与应急</h3>
            <p>规范的数据归档与清晰的故障应对策略，保障珍贵田野语音资产的安全。</p>

            <h4>1. 标准工程归档与 WAV 导出</h4>
            <ul>
              <li>
                <strong><code>.teproj</code> 工程归档文件</strong>：点击底部【保存工程】生成。标准 ZIP 压缩包格式，内部结构如下：
                <pre className="help-code-block">
{`.teproj 工程包/
├─ project.json   (工程状态、各词条录音时间与质检摘要索引)
├─ wordlist.yaml  (全量字表分词、音标及分组数据)
├─ meta.yaml      (发音人元数据、软硬件配置与采集时间)
└─ audio/
   └─ <发音人ID>/
      └─ 分组编号_词项名称.wav (标准 16-bit 44.1kHz 单声道音频)`}
                </pre>
              </li>
              <li><strong>外部文件夹工程模式</strong>：可在设置中将保存格式切换为文件夹模式，在指定目录下直接展开保存上述树状结构，方便外部脚本批量自动化处理。</li>
              <li><strong>【导出 WAV】</strong>：点击底部【导出 WAV】，可将所有发音人的全部合格音频按“发音人/分组/词项”目录分门别类提取导出。</li>
            </ul>

            <h4>2. 一键在 PhonTracer 中打开</h4>
            <p>
              点击界面底部的【在 PhonTracer 中打开】按钮，系统将自动拉起 PhonTracer 分析套件，直接载入当前工程、字表与已录音频。无需手动搬运文件，即可开展音段自动切分（Forced Alignment）、共振峰与基频轨迹精细提取及 TextGrid 标注。
            </p>

            <h4>3. 工作区安全与“清空工作区”真实说明</h4>
            <div className="alert-box alert-warning">
              <div className="alert-icon"><WarningIcon /></div>
              <div className="alert-text">
                <strong>“清空工作区”操作边界说明</strong>：<br />
                点击右栏下方的【清空工作区】仅会清理当前工作区中的发音人列表、字表词条及临时录音缓存。<strong>绝对不会修改或重置您的软件设置、主题外观、输入设备选择与质量检测规则</strong>。如当前录音未保存，清空前请务必先点击【保存工程】。
              </div>
            </div>

            <h4>4. 常见疑难排错速查 (FAQ)</h4>
            <table className="help-table">
              <thead>
                <tr>
                  <th style={{ width: '32%' }}>故障表现</th>
                  <th>可能原因排查与解决步骤</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><span className="qa-badge q-badge">Q</span><strong>麦克风静音：音量电平表毫无反应</strong></td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.3rem' }}>
                      <span className="qa-badge a-badge">A</span>
                      <div>
                        1. 检查操作系统麦克风隐私权限是否允许 PhonRec；<br />
                        2. 在设置中确认麦克风输入源未错选为空闲设备；<br />
                        3. 若为新插拔麦克风，点击【刷新硬件设备】重新枚举。
                      </div>
                    </div>
                  </td>
                </tr>
                <tr>
                  <td><span className="qa-badge q-badge">Q</span><strong>严重截断频发：录音频繁红灯重录</strong></td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.3rem' }}>
                      <span className="qa-badge a-badge">A</span>
                      <div>
                        1. 麦克风硬件输入增益过大或离嘴太近造成喷麦爆音；<br />
                        2. 调低声卡物理 Gain 旋钮，保持 15~20cm 距离，加装防风防喷罩；<br />
                        3. 在设置中将截断检测档位从“严格”暂调至“标准”。
                      </div>
                    </div>
                  </td>
                </tr>
                <tr>
                  <td><span className="qa-badge q-badge">Q</span><strong>VAD 模式迟迟不自动停止录音</strong></td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.3rem' }}>
                      <span className="qa-badge a-badge">A</span>
                      <div>
                        1. 环境存在持续风噪或空调嗡鸣，导致尾随静音判定未达标；<br />
                        2. 在设置中将 VAD 灵敏度调至“稳健型”；<br />
                        3. 若环境噪声不可控，建议改用“按住录制”手动对讲机模式。
                      </div>
                    </div>
                  </td>
                </tr>
                <tr>
                  <td><span className="qa-badge q-badge">Q</span><strong>误导入字表导致之前录音找不到</strong></td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.3rem' }}>
                      <span className="qa-badge a-badge">A</span>
                      <div>
                        导入新字表属于全盘重置。请直接点击底部的【导入工程】，选择之前备份导出的 <code>.teproj</code> 工程压缩包即可完整恢复全部进度。
                      </div>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </section>

          {/* 页脚结语卡片 */}
          <div className="help-footer-card">
            <p style={{ margin: 0, textAlign: 'center', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              PhonRec 语音采集与质量监控系统 &bull; 遵循严谨声学标准，守护田野原声资产
            </p>
          </div>

        </div>
      </div>

      {/* 底部悬浮快速跳转导航栏 */}
      <div className="help-bottom-nav">
        <span className="help-bottom-nav-title">快速跳转：</span>
        <div className="help-bottom-nav-links">
          {sections.map((item, index) => (
            <a
              key={item.id}
              ref={(el) => (buttonRefs.current[index] = el)}
              href={`#${item.id}`}
              className={`help-bottom-nav-btn ${activePart === index ? 'active' : ''}`}
              onClick={(e) => handleScrollTo(e, item.id, index)}
              style={{
                opacity: isVisible(item.id) ? 1 : 0.4
              }}
            >
              {item.title}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
