# PhonTracer 代理维护指南

> 适用范围：本文件所在目录及其全部子目录。
>
> 目的：为后续维护 PhonTracer 主程序、Toolkit、PhonTracerCLI、PhonRec、工程格式和发布链的代理提供统一、可执行的事实边界。
> 最近核对日期：2026-06-21。版本号、分支、Release 和测试数量会变化，执行任务时必须以当前仓库和远端状态重新核对。

## 1. 最高优先级规则

### 1.1 语言与沟通

- 所有对话、执行更新、计划、审阅结论、提交说明、生成文档和新增注释统一使用中文。
- 技术标识符、命令、文件名、API 字段和必要英文术语可以保留原文，但解释必须用中文。
- 表述应专业、清晰、直接。先报告结论，再说明依据和验证结果。
- 需要调用工具时，先用一句中文说明正在核对什么；长任务要提供简短阶段更新。

### 1.2 先看真实状态，不凭历史印象行动

每次任务开始至少执行：

```powershell
git status --short
git branch --show-current
git log -1 --oneline
git diff --check
```

然后按任务范围读取真实入口文件。不要仅根据 README、旧 Release、旧提交摘要或本文件中的历史快照判断当前实现。

- 版本事实源优先查 `modules/version.py`，但发布前还必须核对 PhonRec、安装器和 tag。
- 工程格式优先查 `modules/project_adaptor.py` 与 `modules/project_manager.py`。
- PhonRec 运行模式优先查 `PhonRec/frontend/src/runtimeClient.js` 与 `EngineGate.jsx`。
- 发布状态优先查 `.github/workflows/`、远端 `main`、tag、workflow `headSha` 和 Release 资产。
- 本文件记录的是长期规则和当前架构，不保证每个版本号、测试数量或资产名永久不变。

### 1.3 保护用户和其他代理的工作

- 工作区可能同时存在用户或其他代理的未提交修改。任何编辑前先看 `git status --short` 和相关文件 diff。
- 不覆盖、不还原、不格式化与当前任务无关的改动。
- 不使用 `git reset --hard`、`git checkout -- <file>`、强制清理或递归删除来“恢复干净状态”。
- 发现冲突时优先绕开；无法绕开再向用户说明，不要自行丢弃他人工作。
- 新文件和正式文档要检查白名单式 `.gitignore`，确保文件不是“本地存在但 Git 看不见”。

### 1.4 不扩大授权范围

- “审阅、查看、诊断、列计划”不等于允许修改代码、提交、推送、移动 tag 或更新 Release。
- “修复、实现、优化”通常允许在任务范围内改代码并验证，但不自动包含推送和发布。
- 用户说“提交到 GitHub”“同步到 GitHub”“运行 GitHub Actions 打包”时，交付范围才扩展为：验证、提交、推送、workflow、tag/Release 核对。
- 用户明确说“不要分支，直接到 main”时，直接在 `main` 完成，不另建临时分支或 PR。
- 外部写操作必须严格匹配用户要求；只做报告时不得顺便修改 GitHub Issue、PR、Release 或远端分支。

### 1.5 完工阶段的产品原则

- 当前项目已接近功能完成，默认优先提高稳定性、可交付性、兼容性、文档闭环和可维护性，不主动堆新功能。
- 用户要求“找 Bug”时，只处理真实影响体验或数据安全的问题，不为了产出而硬造问题。
- 用户要求“提高完成度”时，区分发布前必做、维护期应做和可选增强，不把愿望清单包装成缺陷。
- 完整收尾项目见 `SOFTWARE_COMPLETION_CHECKLIST.md`。不要一次性机械实现全部 279 项，应按用户批准的优先级逐批推进。
- 用户不需要独立版本日志文件。发布说明放在 GitHub Release 页面和发布验收记录中，不要主动新增或扩写版本日志流程。

## 2. 项目定位与明确边界

PhonTracer 是一套面向语音学标注、声学分析、人工复核和研究归档的桌面工具。它不是单一 GUI，而是四个相互关联的入口：

| 组件 | 入口 | 主要职责 | 当前平台边界 |
| --- | --- | --- | --- |
| PhonTracer 主程序 | `main.py`、`modules/app.py` | 导入、F0/共振峰分析、声谱图复核、工程保存恢复、导出 | Windows x64/ARM64、macOS ARM64 |
| Toolkit | `toolkit.py` | 音频合并/拆分、字表、TextGrid 转换、工程工具、脚本、报告 | Windows x64/ARM64、macOS ARM64 |
| PhonTracerCLI | `cli.py` | 批处理、工程操作、分析导出、Agent 控制面 | 当前只随 Windows 套件发布 |
| PhonRec | `PhonRec/frontend`、`PhonRec/backend` | 多发音人录音、质量检查、独立/完整模式、工程互通 | Windows x64/ARM64、macOS Apple Silicon |

以下是明确边界，不应被后续代理误判为未完成：

- macOS 当前只承诺 Apple Silicon / ARM64；不要擅自扩大到 Intel Mac。
- CLI 二进制当前只随 Windows 套件发布；不要在文档中暗示已有 macOS CLI。
- PhonRec 独立模式不提供语谱图和完整 F0/共振峰分析。
- macOS 不承诺 Windows 式系统回环录音。
- Toolkit 的窗口级外部拖放已主动禁用，以规避 Tk/windnd/GIL 崩溃；不要随手恢复。
- 自动分析、F0 范围估计、共振峰参数建议和录音质量提示都是人工复核辅助，不是自动质量认证。
- 自动保存是恢复机制；正式归档、迁移和分享必须显式导出 `.teproj`。
- 手动检查更新是可接受的正式策略；没有用户需求时不必强推自动更新。

## 3. 当前仓库结构

### 3.1 根入口与打包

- `main.py`：主程序启动、启动参数保留、Splash、延迟加载。
- `toolkit.py`：Toolkit 主入口；文件较大，修改时尽量做局部、可验证的补丁。
- `cli.py`：CLI 主入口和命令实现；支持交互式与单指令执行。
- `ToneExtractor_Suite.spec`：主程序、Toolkit、Windows CLI、PhonTracerAnalysisEngine 的 PyInstaller 组合打包定义。
- `installer.iss`：Windows Inno Setup 安装器、开始菜单、文件关联和主程序发现注册表信息。
- `requirements.txt`：主套件 Python 依赖。
- `requirements-phonrec-engine.txt`：PhonRec 分析引擎额外依赖。
- `README.md`：项目概览与下载入口。
- `PRIVACY.md`：本地数据、联网行为、删除与隐私边界。
- `SUPPORT.md`：公开支持渠道、复现信息和敏感数据提醒。
- `SECURITY.md`：私下漏洞报告方式与安全边界。
- `assets/manual/manual.md`：正式 Markdown 用户手册。
- `assets/manual/manual.html`：HTML 手册；修改时要核对是否与 Markdown 同步。
- `PAPER_USAGE_GUIDE.md`：论文使用和写作边界。
- `SOFTWARE_COMPLETION_CHECKLIST.md`：项目完工收尾清单。
- `docs/ARCHITECTURE.md`：四个入口、共享后端与 PhonRec 双运行时架构。
- `docs/PROJECT_FORMAT_V1.md`、`docs/project.schema.json`：`.teproj` 1.0 合同与机器可读结构。
- `docs/THREAT_MODEL.md`：脚本、工程和本机引擎威胁模型。
- `docs/RELEASE_CHECKLIST.md`、`docs/RELEASE_TEMPLATE.md`：发布操作单与 Release 正文模板。
- `scripts/check_release_version.py`：应用版本、README 示例、tag 与引擎协议发布前检查。

### 3.2 主程序模块

- `modules/app.py`：主 GUI 状态、导入分析、参数应用、后台任务与界面协调。
- `modules/project_manager.py`：GUI/CLI 共用的工程持久化中心。
- `modules/project_adaptor.py`：`.teproj` 安全读取、版本验证、旧工程适配、资源验证和边界归一化。
- `modules/project_tree.py`：项目树、条目状态、编辑和拖放排序。
- `modules/spectrogram_panel.py`：声谱图、边界交互、播放、橡皮擦/剔除点。
- `modules/audio_core.py`：声学计算和共享音频边界逻辑。
- `modules/data_utils.py`：导入导出、字表匹配、TextGrid、Excel 等数据工具。
- `modules/acoustic_exporter.py`：声学表格和图表导出。
- `modules/report_generator.py`：研究报告与参数偏离说明。
- `modules/wordlist_v2.py`：普通文本、结构化文本、CSV、PTWL 的标准化字表模型。
- `modules/textgrid_converter.py`：Toolkit/CLI 共用的 TextGrid 检查、预览与转换后端。
- `modules/script_api.py`、`script_runner.py`、`script_prompt.py`、`script_manager.py`：受控脚本 API、执行、提示词和脚本库。
- `modules/project_patch.py`：脚本对工程数据的受控 patch，不允许直接覆盖源工程。
- `modules/version.py`：主程序显示版本事实源。

### 3.3 PhonRec

- `PhonRec/frontend/src/App.jsx`：主录音界面和录音状态协调。
- `PhonRec/frontend/src/EngineGate.jsx`：启动时引擎检测、重试和进入独立模式。
- `PhonRec/frontend/src/runtimeClient.js`：完整模式/独立模式能力图与数据操作统一接口。
- `PhonRec/frontend/src/engineApi.js`：本地引擎请求与 Bearer token。
- `PhonRec/frontend/src/audioUtils.js`：重采样和 WAV 生成。
- `PhonRec/frontend/src/vadEngine.js`：前端轻量 VAD 状态。
- `PhonRec/frontend/src/SettingsModal.jsx`：设备、质量、显示、路径和快捷键设置。
- `PhonRec/frontend/src-tauri/src/main.rs`：Tauri 原生运行时、独立工作区、录音设备、回环录音、设置持久化、工程导入导出、引擎启动发现。
- `PhonRec/backend/main.py`：完整模式 Python/FastAPI 分析引擎。
- `PhonRec/backend/test_backend.py`：引擎认证、事务保存、工作区、质量、字表、工程互通和路径安全测试。

## 4. 单一事实源与禁止复制的逻辑

后续开发必须优先复用以下事实源，不要再在 GUI、Toolkit、CLI、PhonRec 各写一套：

| 领域 | 单一事实源 | 维护要求 |
| --- | --- | --- |
| 工程持久化 | `ProjectManager` | GUI/CLI 的保存、导入、自动保存走同一中心 |
| 工程兼容/安全 | `modules/project_adaptor.py` | ZIP 安全、版本、旧工程、资源、边界在此统一 |
| 字表规范化 | `modules/wordlist_v2.py` | TXT、结构化文本、CSV、PTWL 不重复解析 |
| TextGrid 转换 | `modules/textgrid_converter.py` | Toolkit 与 CLI 共享 inspect/preview/convert |
| 静音裁剪 | `modules/audio_core.py::trim_bounds_by_amplitude` | 不在入口层复制时间轴计算 |
| 脚本合同 | `modules/script_api.py`、`script_runner.py` | 统一 `run(ctx)`、结果和安全检查 |
| 主程序版本 | `modules/version.py` | 发布前再同步核对其他清单位置 |
| PhonRec 模式能力 | `runtimeClient.js` | UI 只依据 capability，不散落模式判断 |

如果确实需要改变事实源，必须同时：

1. 搜索所有调用方。
2. 更新 GUI、Toolkit、CLI、PhonRec 的相关适配。
3. 补回归测试。
4. 验证旧工程和原模式没有受到串扰。

## 5. `.teproj` 工程与数据安全合同

### 5.1 当前格式

- `.teproj` 本质是 ZIP 归档。
- 当前工程格式版本为 `1.0`，代码约束位于 `SUPPORTED_PROJECT_VERSIONS`。
- 主体数据在 `project.json`，资源位于 `audio/`、`data/` 等受控目录。
- 真实项目条目优先来自 `speakers[*].items`，不能把顶层 `groups` 当唯一词表来源。
- `ProjectManager` 保存时记录软件版本和报告格式版本；这三种版本含义不同，不能混用。

### 5.2 必须保留的兼容字段

高级字表和工程往返时必须同时保留并正确合并：

- `note` / `item_note`
- `tags` / `item_tags`
- `aliases` / `item_aliases`
- `meta` / `item_meta`
- 组级名称、备注、标签和自定义元数据
- `start`、`end`、`raw_start`、`raw_end`
- `chars_bounds`、`inner_splits`
- 条目级参数覆盖、分析模式、人工删除/复核状态

合并条目时不能只按裸 `item_id`。重复 ID 可能跨发音人或分组出现；应结合 speaker、group、label、occurrence slot 等上下文映射。

### 5.3 导入安全要求

任何 ZIP/`.teproj` 导入都必须继续防护：

- 路径穿越和绝对路径。
- 符号链接。
- Windows 保留文件名和非法字符。
- 大小写折叠冲突。
- 重复归档成员。
- 成员数量、单成员大小、总解压大小和 `project.json` 大小上限。
- 导入资源缺失、路径指向受控工作区之外。

禁止用未经验证的 `extractall()` 绕过 `validate_project_archive_members()` 和安全解压逻辑。

### 5.4 保存与恢复要求

- 工程导入必须使用 staging + 校验 + 原子切换；失败时保留当前工作区。
- 覆盖/叠加导入都要在替换前完成资源验证。
- `save_autosave_snapshot()` 才是“立即自动保存”的正确原语；仅调用 `save_to_workspace()` 不包含备份语义。
- 显式导出前先将当前状态同步到工作区，再生成归档。
- 老 WAV 兼容继续通过 `repair_wav_header()` 与 `load_compatible_sound()`。
- 独立音频条目恢复继续通过 `normalize_independent_item_boundaries()` 补齐有效边界。
- 旧工程兼容目标是保住用户数据和恢复链，不要轻易把解决方案写成“重录”或“手工重建”。

### 5.5 工程相关修改的最低回归

至少运行：

```powershell
& 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q tests/test_project_adaptor.py PhonRec/backend/test_backend.py
```

如果涉及高级字表、TextGrid 或脚本归档，还应追加对应测试文件，并做一次真实 `.teproj` round-trip。

## 6. 主程序声学分析与界面状态合同

### 6.1 参数作用域

- 发音人默认参数存于 speaker `last_params`。
- 条目局部参数优先于发音人默认；界面、重算、导出和报告必须遵守同一优先级。
- 报告中的“发音人基准参数”按最终纳入分析条目的多数值计算，不使用导出时界面最后停留值。
- 修改全局参数时，不要静默覆盖已有条目局部参数。

### 6.2 F0 推荐范围

- GUI 与 CLI 的 F0 范围建议应用都应走 pitch-only refresh。
- 应用推荐范围时只更新音高相关元数据与曲线。
- 必须保留人工编辑的 `start/end/raw_start/raw_end/inner_splits/chars_bounds`。
- 不得因为修改 pitch floor/ceiling 而重新运行分割边界。

### 6.3 F3

- `show_f3` 只是显示/导出开关，不是另起一套 F3 计算链。
- F3 数据由既有共振峰分析产生；关闭时隐藏显示和导出，不应破坏已有数据。
- 修改时同时检查主 GUI、声谱图、CLI、导出和脚本快照。

### 6.4 橡皮擦与边界交互

- 离开条目、切换模式、关闭程序或保存前先 flush 当前橡皮擦会话。
- 主线程负责轻量状态提交和持久化调度；后台线程只做纯数据计算，不直接操作 Tk 控件。
- `SpectrogramPanel` 对 `chars_bounds`、line objects 和拖拽索引长度不一致必须保持越界守卫。
- 不要为了即时视觉反馈在鼠标移动事件中执行重型保存。

### 6.5 Tk 异步常见陷阱

Python 会在 `except` 结束后清理异常变量，因此以下模式不安全：

```python
except Exception as e:
    root.after(0, lambda: show_error(str(e)))
```

正确做法是先物化字符串，再通过默认参数绑定：

```python
except Exception as e:
    error_message = str(e)
    root.after(0, lambda msg=error_message: show_error(msg))
```

后台线程不得直接调用 Tk 控件；使用 `root.after(...)` 回到主线程。

## 7. TextGrid 与字表合同

### 7.1 标准输出

- PhonTracer 标准 TextGrid 输出为 `groups / words / chars`。
- 如果输入已有可靠 `chars` 边界，下游分析和导出应保留，不重新猜测。
- 单文件转换可以指定输出文件；批量转换写入目录，使用 `_converted` 和防冲突命名，不能覆盖原始输入。

### 7.2 共享转换器

统一使用：

- `inspect_textgrid()`
- `preview_textgrid_conversion()`
- `convert_textgrid()`

Toolkit 和 CLI 的 tier 映射、相邻配对、辅助字表和 override 语义必须一致。

### 7.3 字表格式

- 普通文本支持分组标题、BOM、常用分隔符和斜杠音节边界。
- 结构化文本、CSV、PTWL 都应归一化到 `wordlist_v2` 模型。
- 当前高级字表 schema 为 `phontracer.wordlist.v2`。
- 未知 CSV 列应按既有规则进入自定义元数据，不应被静默丢弃。
- 普通/高级字表的字段兼容修改必须同时验证主程序、Toolkit、CLI 和 PhonRec。

## 8. PhonRec 核心合同

### 8.1 两种运行模式

`runtimeClient.js` 是能力事实源：

- 完整模式：`projectArchive`、`advancedWordlist`、`spectrogram`、`fullQuality`、`lightQuality` 可用；不提供独立 WAV 文件夹导出。
- 独立模式：`projectArchive`、`advancedWordlist`、`lightQuality`、WAV 文件夹导出可用；`spectrogram`、`fullQuality` 不可用。

界面新增功能时先决定它属于哪种 capability，不要用散落的 `isStandalone` 分支复制业务逻辑。

### 8.2 启动闸门与本地引擎

- `EngineGate.jsx` 负责启动检测、重试、进入独立模式和退出。
- 独立模式选择只在本次运行有效，不应永久污染完整模式设置。
- 后端使用随机 `127.0.0.1` 端口。
- `/api/health` 可不带 token；其他 API 必须携带 Bearer token。
- 引擎版本和协议版本是不同概念；协议必须完全匹配。
- token、临时握手信息不得写入普通日志或工程。

### 8.3 录音提交状态机

录音链最重要的不变量：

1. 开始录制时捕获 active speaker/item 目标。
2. 录制和提交期间阻止导航、删除、导出等会改变目标的操作。
3. 保存时拒绝 stale speaker/item。
4. retry/reject 走 non-commit path，不覆盖已接受录音。
5. 只有保存成功后才标记已录制并前进到下一条。
6. 保存失败时保留旧录音和当前目标，允许重试。

任何录音 UI 重构都必须用这六条逐项验收，不能只看按钮状态。

### 8.4 完整模式与独立模式隔离

- 完整模式工程目录继续使用 `folder_path`。
- 独立模式 WAV 导出使用单独的 `wav_export_path`。
- 不得复用一个持久化设置导致模式间互相覆盖。
- 修改设置时先搜索共享字段名、文件选择器状态和旧设置迁移。
- 独立模式和完整模式的 `.teproj`、字表和工作区规则要保持 round-trip 等价。

### 8.5 路径清洗

必须区分两类清洗：

- 存储资源清洗：防冲突、跨平台安全、可稳定引用。
- 展示/导出目录清洗：尽量人类可读，同时合法。

不要拿 collision-proof 的内部资源名直接当用户可见导出目录，也不要拿只为可读设计的名字作为唯一存储键。

### 8.6 音频链

- 浏览器录音读取真实 `AudioContext.sampleRate`。
- 发送完整分析引擎前按既有链路重采样为 16 kHz PCM WAV。
- 测试夹具必须生成真实单声道 16-bit PCM WAV，不要用文本占位文件伪装 `.wav`。
- 独立模式只接受和保存符合既有校验的 WAV；修改格式支持时同步 Rust、前端和 Python 后端。

### 8.7 PhonRec 修改的验证顺序

在 `PhonRec/frontend` 执行：

```powershell
npm ci
npm run lint
npm test -- --run
npm run build
```

在仓库根目录执行：

```powershell
cargo fmt --manifest-path PhonRec/frontend/src-tauri/Cargo.toml -- --check
cargo test --manifest-path PhonRec/frontend/src-tauri/Cargo.toml
cargo check --manifest-path PhonRec/frontend/src-tauri/Cargo.toml --target aarch64-pc-windows-msvc
& 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q PhonRec/backend/test_backend.py tests
git diff --check
```

注意：Tauri 的 `generate_context!()` 需要存在前端 `dist`，所以 CI/本地完整验证通常先 `npm run build`，再运行依赖 Tauri context 的 Rust 测试或打包。

## 9. Toolkit 维护规则

### 9.1 产品语义

- 用户可见名称统一为 `Toolkit`，入口为 `toolkit.py`，产物为 `Toolkit.exe` / `Toolkit.app`。
- 不要恢复旧的 AudioToolkit 命名，也不要引用已不存在的旧 UI 文件作为事实源。
- Toolkit 不只是音频工具，还包含字表、TextGrid、工程、报告和脚本工作流。

### 9.2 稳定性

- 窗口级 windnd 外部拖放已禁用；按钮文件选择是稳定路径。
- 列表内部排序、Delete 删除等受控交互可以保留。
- 长任务应提供阶段进度；后台线程不得直接操作 Tk。
- 图表和报告后台导出优先使用无窗口 Matplotlib 后端，避免 Tk/Agg 线程冲突。
- 用户取消后清理临时文件；部分成功结果要给出清单，不得静默假装全部成功。

### 9.3 不要重复后端

- 音频静音裁剪复用 `trim_bounds_by_amplitude()`。
- TextGrid 复用 `modules/textgrid_converter.py`。
- 字表复用 `modules/wordlist_v2.py`。
- 工程读取复用安全 metadata/adapter 路径。
- 报告复用 `modules/report_generator.py`。
- 自定义脚本复用 `script_api` / `script_runner` / `project_patch`。

## 10. CLI 维护规则

### 10.1 CLI 的角色

- CLI 是批处理和 Agent 的正式控制面，不是临时调试脚本。
- 操作真实项目数据时，如已有 CLI 指令，优先通过 CLI 而不是让 Agent 直接改 `project.json` 或 ZIP。
- CLI 与 GUI 共用 `ProjectManager` 和本地工作区；并发修改可能互相覆盖，操作前必须确认没有 GUI 正在写入。
- 单指令模式和交互模式应调用同一命令语义。

### 10.2 输出和启动

- 机器可读结果保持 JSON；不要把调试打印混入标准输出。
- 错误参数应返回非零退出码。
- Windows 打包入口必须保留 `multiprocessing.freeze_support()`。
- 必须继续忽略/正确处理 PyInstaller 多进程内部参数，不能把它们当用户命令。
- `python cli.py -h` 是当前完整帮助入口；新增命令时同步中英文帮助与 `agent_guide`。

### 10.3 持久化

- `project_export` 是正式可迁移归档。
- `project_save` 只是同步当前本地工作区。
- `autosave now` 必须走 `save_autosave_snapshot()`。
- destructive 命令要清楚说明副作用；非交互自动化中不要靠模糊默认值覆盖用户数据。

### 10.4 CLI 最小 smoke

```powershell
$py = 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe'
& $py cli.py -h
& $py cli.py status
& $py cli.py --bad-arg
```

预期：帮助和状态成功；非法参数返回结构化错误和非零退出码。涉及具体命令时再补真实 subprocess 测试，不能只在进程内调用方法。

## 11. 受控脚本与 AI 协作

### 11.1 稳定合同

- 自定义脚本统一入口为 `def run(ctx):`。
- 数据通过 `ScriptContext` 的只读快照和受控 helper 提供。
- 图表结果使用 `FigureResult` 等既有结果模型。
- 脚本需要修改工程时返回受控 patch，由 `project_patch.py` 验证和应用；不得直接写 ZIP 或源工程。
- 执行记录可归档进 `.teproj`，包括源码、指纹、日志和结果信息。

### 11.2 安全定位

- 这是受限科研执行层，不是任意系统控制台。
- 保持 import、builtins、文件系统和系统调用限制。
- 外部/AI 生成脚本默认先审阅，不自动执行。
- 安全检查不能被“为了兼容某个脚本”轻易绕过。
- 如果新增允许库，必须同时评估文件、网络、子进程、反射和资源耗尽能力。
- 超时、取消和子进程回收必须继续有效。

### 11.3 产品方向

- 主程序已覆盖大量标准图表，不要通过堆内置模板重复 GUI 已有能力。
- 脚本系统价值在于受控探索、AI 协作、特殊图表和可复现归档。
- 提示词和生成图表的标题、坐标轴、图例、日志默认使用中文。

## 12. 测试策略

### 12.1 Python 环境

本机稳定验证解释器：

```text
C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe
```

不要因为当前代理自带 Python 缺少 Parselmouth、CustomTkinter 等依赖，就错误判断项目无法测试。

### 12.2 目标测试映射

| 修改范围 | 最低测试 |
| --- | --- |
| 工程导入、旧项目、路径安全 | `tests/test_project_adaptor.py`、`PhonRec/backend/test_backend.py` |
| TextGrid | `test_textgrid_converter.py`、`test_cli_textgrid_converter.py`、`test_textgrid_chain.py` |
| 字表 | `test_wordlist_structured_text.py`、`test_toolkit_wordlist_sources.py`、PhonRec 字表测试 |
| 脚本/工程 patch | `test_data_process_scripts.py` |
| 音频裁剪 | `test_audio_trim_bounds.py` |
| PhonRec 质量 | `test_phonrec_quality.py`、后端测试、前端质量设置测试 |
| PhonRec UI/runtime | 前端 lint + Vitest + build + Rust test |
| Toolkit 工程提取 | `test_toolkit_project_extract.py` |
| CLI | 目标 pytest + 真实 `python cli.py ...` subprocess smoke |
| 版本/发布表面 | `tests/test_release_surface.py` + `scripts/check_release_version.py` |

### 12.3 Python 全量验证

```powershell
$py = 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe'
& $py -m pytest -q tests PhonRec/backend/test_backend.py
& $py -m compileall -q main.py cli.py toolkit.py modules tests PhonRec/backend
& $py scripts/check_release_version.py
& $py cli.py --version
git diff --check
```

不要把本文件写下的历史测试数量当恒定目标。应关注命令是否收集到预期目录、是否全部通过、是否新增合理回归。

### 12.4 测试强度原则

- 先跑目标测试，确认修改方向。
- 再按影响面跑全量测试。
- 修改持久化、录音提交、共享字表或跨模式能力时，必须做端到端 round-trip。
- 修改打包代码时，源码测试通过不等于打包版正确；至少做构建或对应 GitHub Actions 验证。
- Windows 本机不能替代 macOS 真实打包验证；跨平台结论必须以目标 runner/真机为证据。

## 13. GitHub Actions、打包与发布

### 13.1 当前发布工作流

日常门禁：

- `.github/workflows/ci.yml`：PR 与 `main` 推送触发；覆盖 Python/后端完整测试、compileall、版本检查、CLI smoke、PhonRec lint/test/build、Rust fmt/clippy/test 和 Windows ARM64 `cargo check`。

发布打包：

- `.github/workflows/package-windows.yml`
- `.github/workflows/package-windows-arm64.yml`
- `.github/workflows/package-macos.yml`
- `.github/workflows/package-phonrec-windows.yml`
- `.github/workflows/package-phonrec-macos.yml`

它们支持手动触发和 `v*` tag。具体 runner、action 版本和产物路径每次发布前重新读取，不能只照抄本文件。

五条打包工作流均调用 `scripts/check_release_version.py`。GitHub tag 触发时，脚本会从 `GITHUB_REF_TYPE/GITHUB_REF_NAME` 取得预期版本并阻止 tag 与代码版本不一致的发布；手动触发时只检查仓库内八处版本事实源的一致性。

### 13.2 主套件资产

主套件通常包含：

- Windows x64 安装包和 ZIP。
- Windows ARM64 安装包和 ZIP。
- macOS ARM64 DMG。

PyInstaller 套件必须包含主程序、Toolkit、Windows CLI（仅 Windows）和 PhonTracerAnalysisEngine。PhonRec Tauri 安装包是独立发布资产，不在主套件 ZIP 中假装成 `PhonRec.exe`。

### 13.3 PhonRec 资产

- Windows x64 NSIS 安装包。
- Windows ARM64 NSIS 安装包。
- macOS ARM64 DMG。

PhonRec workflow 的稳定顺序：

1. `npm ci`
2. `npm run lint`
3. `npm test`
4. `npm run build`
5. `cargo test`
6. Tauri 打包
7. 体积和禁带内容检查

macOS 检查 DMG 内容时应挂载最终 DMG，再检查其中 `.app`；不要依赖可能被 Tauri 清理的临时 `bundle/macos/PhonRec.app`。

Windows 检查 Electron 禁带依赖时，直接文本匹配 `package-lock.json` 比默认 `ConvertFrom-Json` 更稳；npm lockfile 可能含 PowerShell JSON 解析不友好的键。

### 13.4 发布完整流程

当用户明确要求发布时，依次完成：

1. 核对工作区范围和用户改动。
2. 运行与变更匹配的目标测试和全量测试。
3. `git diff --check`。
4. 提交明确范围的文件。
5. 推送指定分支；用户要求直接 `main` 时不加 PR 流程。
6. 若覆盖现有版本，确认 workflow 仍支持 `overwrite_files: true`，再按用户要求移动 tag。
7. 比对：

```powershell
git rev-parse HEAD
git rev-parse origin/main
git rev-parse refs/tags/<版本标签>
```

8. 对每条 workflow 分别核对 run id、`headSha`、status、conclusion。
9. 核对 Release 资产名称、架构、大小、更新时间和下载入口。
10. 所有目标平台完成前，不得宣称发布完成。

三个/五个平台完成时间通常不同，要分别等待，不要把 staggered completion 误判成失败。

### 13.5 发布前版本核对

至少检查：

- `modules/version.py`
- `PhonRec/frontend/package.json`
- `PhonRec/frontend/src-tauri/Cargo.toml`
- `PhonRec/frontend/src-tauri/tauri.conf.json`
- `PhonRec/backend/main.py` 的 `ENGINE_VERSION` / `PROTOCOL_VERSION`
- `installer.iss`
- README badge 和资产示例
- tag 和 Release 名称

首选命令：

```powershell
python scripts/check_release_version.py --expected vX.Y.Z
```

工程格式版本、应用版本、报告格式版本和引擎协议版本不能因为数字相似而一起随意升级。

## 14. 文档维护规则

### 14.1 正式文档边界

- README：概览、能力、下载、快速开始、平台边界。
- `assets/manual/manual.md`：正式详细手册。
- `assets/manual/manual.html`：用户可阅读 HTML 版，修改时核对同步。
- `PAPER_USAGE_GUIDE.md`：论文写作、人工复核和不过度宣称。
- `SOFTWARE_COMPLETION_CHECKLIST.md`：产品收尾与发布成熟度。
- `AGENTS.md`：代理维护规则，不面向普通用户。
- `PRIVACY.md`、`SUPPORT.md`、`SECURITY.md`：普通用户的数据、支持与安全入口。
- `docs/PROJECT_FORMAT_V1.md` 与 `docs/project.schema.json`：工程合同；改字段时必须同步。
- `docs/ARCHITECTURE.md` 与 `docs/THREAT_MODEL.md`：维护边界；改运行时或脚本能力时必须同步。
- `docs/RELEASE_CHECKLIST.md` 与 `docs/RELEASE_TEMPLATE.md`：正式发布前逐项执行，不代表项目已完成签名或真机验收。

### 14.2 默认交付方式

- 用户要求“独立 Markdown 草稿”“交给另一个 AI”“不要改正式文档”时，只新增独立草稿，不改 README/正式手册。
- 用户只要求审阅时，不顺手重写文档。
- 用户要求更新正式文档时，先从真实代码核对功能、平台和文件名，避免过度承诺。
- 看不到真实 DOCX/HTML/界面格式时直接说明，不根据截图猜测结构。
- 同目录有并行 AI 产物时先列文件和 Git 状态，避免覆盖。

### 14.3 白名单式 `.gitignore`

本仓库默认忽略所有文件，再精确放行。新增正式文件后执行：

```powershell
git check-ignore -v <文件路径>
git status --short
git ls-files <文件路径>
```

注意：`git check-ignore -v` 会显示匹配规则；最终以 `git status` 和 `git ls-files` 判断是否可跟踪。若要放行，添加精确 `!路径`，不要粗暴放开整个临时目录。

### 14.4 不过度宣称

文档中避免使用以下未经验证的口径：

- “完全自动”“绝对准确”“无需人工复核”。
- “全平台支持”而不列架构。
- “独立模式具有完整分析能力”。
- “沙箱绝对安全”。
- “主套件自带 PhonRec”，除非打包结构真的改变。
- “发布完成”，但没有核对最终 workflow 和资产。

## 15. Git 与工作区约定

### 15.1 分支与提交

- 默认分支是 `main`，远端为 `origin`；当前远端仓库是 `KasumiKitsune/PhonTracer`，但执行前仍应 `git remote -v` 复核。
- 不根据旧记忆假设仓库只剩某几条分支；以 `git branch -a` 为准。
- 用户明确要求直推 `main` 时按要求执行。
- 未经要求，不主动创建 PR、移动 tag 或删除远端分支。
- 提交应按任务范围精确暂存，不使用 `git add .` 把并行改动一起带走。

### 15.2 混合工作树

如果工作区已有修改：

1. 先区分当前任务、用户改动、其他代理改动。
2. 只编辑当前任务必要区域。
3. 提交时显式列路径。
4. 验证当前补丁没有破坏未提交文件。
5. 无法安全分离时停下说明，不要重置。

### 15.3 文件编辑

- 小而明确的文本改动使用补丁方式。
- 不用临时脚本重写整个大文件，除非是可审计的机械变换且已备份/验证。
- 格式化命令只针对任务范围；Rust `cargo fmt` 可能改动整个文件，执行前看工作树。
- 所有新增文件内容使用中文。

## 16. 按任务类型执行

### 16.1 “看看、审阅、诊断”

1. 读取真实代码和当前状态。
2. 给出带文件/函数依据的结论。
3. 区分已确认问题、风险和建议。
4. 不修改代码，除非用户随后明确要求。

### 16.2 “帮我修复/实现”

1. 把用户批准的方案视为实施合同。
2. 做最小完整修改，不扩大产品边界。
3. 补针对性测试。
4. 跑影响面对应的全量验证。
5. 汇报改了什么、验证了什么、仍有什么平台限制。

### 16.3 “全方位审阅 PhonRec”

默认同时覆盖：

- 普通字表和高级字表。
- `.teproj` 导入导出和主程序 round-trip。
- 录音目标绑定、retry/reject/save/advance。
- 完整模式和独立模式串扰。
- 工作区和设置路径。
- macOS ARM64、Windows x64、Windows ARM64。
- Rust、前端、Python 后端和发布打包。

不要只修前端显示或单个 API 就称“链路打通”。

### 16.4 “让 CLI 也支持”

默认要求：

- 复用共享后端。
- CLI 帮助和 `agent_guide` 同步。
- 结构化输出和非零错误码合理。
- 真实 subprocess smoke。
- 不顺手改无关 GUI 流程。

### 16.5 “提交/同步/发布到 GitHub”

默认交付到远端验证闭环，不停在本地：

- 测试。
- 精确提交。
- 推送。
- workflow run 和 `headSha`。
- tag 指向。
- Release 资产。

用户只说“提交”但未说发布时，不自动移动 tag。

### 16.6 “横扫 Bug，但没有就不要硬加”

- 先检查数据丢失、崩溃、错误目标写入、模式串扰、长任务卡死和打包入口。
- 只报告可复现或证据充分的问题。
- 纯风格、理论风险、个人偏好不当作体验 Bug。
- 没有真实问题就明确说没有，不为交付数量硬改。

## 17. 已建立的易错点索引

### 17.1 工程和音频

- `.teproj` 可打开不等于条目可恢复；验收要看可用 items、资源和有效边界。
- 老 PCM WAV 头可能需要修复后才能被 Parselmouth 读取。
- 导入后 `chars_bounds` 和绘图 line objects 可能短暂不同步，交互代码必须守卫索引。
- 资源清理只能删除未引用的托管资源，不能根据文件名猜测。

### 17.2 时间轴

- Parselmouth `extract_part(...).xs()` 是相对截取片段的时间轴。
- 静音裁剪必须保留原始 `start` 恢复绝对边界；不能先改 start 再用新 start 推 end。

### 17.3 PhonRec

- retry/reject 绝不能覆盖已接受录音。
- 保存时必须校验开始录制时捕获的目标。
- `folder_path` 与 `wav_export_path` 不能复用。
- 存储路径清洗与展示名清洗不能混为一套。
- Tauri build 前缺少前端 dist 会导致 `generate_context!()` 失败。

### 17.4 Tk/Toolkit

- `except ... as e` 中的 `e` 不能直接被延迟 lambda 捕获。
- windnd 窗口拖放在 Tk/GIL 下曾造成崩溃，当前禁用是设计决定。
- 后台线程不直接碰 Tk 控件。

### 17.5 发布

- workflow 成功但 `headSha` 不是目标提交，不算有效发布。
- 移动 tag 后要重新核对所有平台资产更新时间。
- macOS PhonRec 体积检查应挂载最终 DMG。
- Windows npm lockfile 不宜直接用默认 `ConvertFrom-Json` 做禁包检查。

## 18. 每次交付的完成定义

### 18.1 代码修改

- 修改与用户要求一致，没有无关重构。
- 共享逻辑没有被复制。
- 旧工程、原模式和其他入口的影响已审计。
- 目标测试通过。
- 影响面全量测试通过。
- `git diff --check` 通过。
- `git status` 中没有意外文件。

### 18.2 文档修改

- 内容基于当前代码和产物，不基于旧宣传。
- 平台、架构、模式和限制写清楚。
- 新文件被 `.gitignore` 精确放行。
- 不覆盖并行草稿。
- 不使用用户明确不要的独立版本日志流程。

### 18.3 发布

- 本地 HEAD、远端目标分支、tag 和 workflow `headSha` 一致。
- 目标平台全部完成。
- 资产名称、架构和数量符合预期。
- Release 页面可指导用户选择文件。
- 失败或仍在运行的平台被明确报告，不能把部分成功写成全部完成。

## 19. 快速命令附录

### 19.1 仓库状态

```powershell
git status --short
git branch --show-current
git remote -v
git log -1 --oneline
git diff --check
```

### 19.2 Python

```powershell
$py = 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe'
& $py -m pytest -q tests PhonRec/backend/test_backend.py
& $py -m compileall -q main.py cli.py toolkit.py modules tests PhonRec/backend
```

### 19.3 PhonRec 前端

```powershell
Set-Location PhonRec/frontend
npm ci
npm run lint
npm test -- --run
npm run build
Set-Location ../..
```

### 19.4 PhonRec Rust

```powershell
cargo fmt --manifest-path PhonRec/frontend/src-tauri/Cargo.toml -- --check
cargo test --manifest-path PhonRec/frontend/src-tauri/Cargo.toml
cargo check --manifest-path PhonRec/frontend/src-tauri/Cargo.toml --target aarch64-pc-windows-msvc
```

### 19.5 CLI smoke

```powershell
$py = 'C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe'
& $py cli.py -h
& $py cli.py status
& $py cli.py --bad-arg
```

### 19.6 发布核对

```powershell
git fetch origin --tags
git rev-parse HEAD
git rev-parse origin/main
git rev-parse refs/tags/<版本标签>
gh run list --limit 20
gh release view <版本标签>
```

## 20. 最后的判断原则

维护这个项目时，优先问四个问题：

1. 这次改动会不会让旧工程、旧录音或人工边界丢失？
2. 这次改动会不会让 GUI、Toolkit、CLI、PhonRec 出现多套事实源？
3. 这次改动是否在 Windows x64、Windows ARM64、macOS ARM64 或完整/独立模式之间产生串扰？
4. 我是否已经用真实入口、真实子进程、真实归档或真实发布产物验证，而不只是单个函数看起来正确？

如果四个问题都能用证据回答，这次维护才算真正完成。
