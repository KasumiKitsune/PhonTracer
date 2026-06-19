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

PhonRec 启动时会验证 PhonTracer 的引擎协议版本。未安装主程序、版本不兼容或引擎启动失败时，客户端会显示依赖提示页，不会进入录音界面。
