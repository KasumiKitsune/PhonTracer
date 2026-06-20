# PhonRec 桌面端

PhonRec 使用 React、Vite 与 Tauri v2 构建。安装包不携带 Python 或科学计算库，运行录音分析功能时会调用已安装的 PhonTracer 分析引擎。

## 开发

```powershell
npm ci
npm run tauri:dev
```

调试版可通过 `PHONTRACER_ENGINE_PATH` 指定 `PhonTracerAnalysisEngine` 的绝对路径；正式版只从 PhonTracer 安装信息中发现引擎。

## 验证与打包

```powershell
npm run lint
npm test
npm run tauri:build
```

Windows 使用 NSIS 安装器，支持 x64 与 ARM64。macOS 使用 DMG，仅支持 Apple Silicon，最低系统版本为 macOS 12。

PhonRec 启动时会验证 PhonTracer 的引擎协议版本。未安装主程序、版本不兼容或引擎启动失败时，可在依赖提示页选择“进入独立软件模式”。该选择只在本次运行中有效。

独立模式支持麦克风与 Windows 系统回环录音、播放、VAD、波形、本地自动保存、音量与削波检测，以及按“发音人/分组/词项”批量导出 WAV。独立模式只接受 TXT 普通字表或粘贴文本，不支持 `.teproj`、CSV、PTWL、高级字表、语谱图及完整质量分析。独立模式与完整模式共用受控工作区；以后安装或修复 PhonTracer 后，完整模式可继续读取已有字表和录音。
