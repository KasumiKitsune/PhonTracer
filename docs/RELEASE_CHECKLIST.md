# 发布检查清单

本清单用于正式发布，不等同于今晚能完成的代码审计。未满足的签名、公证、硬件和许可证项目必须明确记录，不得用“已有构建”替代。

## 1. 版本与范围

- [ ] 确定发布版本 `X.Y.Z`、目标 tag `vX.Y.Z` 与功能冻结范围。
- [ ] 运行 `python scripts/check_release_version.py --expected vX.Y.Z`。
- [ ] 核对 `modules/version.py`、PhonRec `package.json`、`Cargo.toml`、`tauri.conf.json`、分析引擎与 `installer.iss`。
- [ ] 确认没有需要加入本次发布的未提交文件。
- [ ] 确认许可证、第三方声明、隐私与安全文档符合实际发布边界。

## 2. 自动化门禁

在仓库根目录使用 Python 3.12：

```powershell
python -m pytest -q tests PhonRec/backend/test_backend.py
python -m compileall -q main.py cli.py toolkit.py modules tests PhonRec/backend
python scripts/check_release_version.py --expected vX.Y.Z
python cli.py --version
python cli.py -h
python cli.py status
```

在 `PhonRec/frontend`：

```powershell
npm ci
npm run lint
npm test -- --run
npm run build
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo test --manifest-path src-tauri/Cargo.toml
```

- [ ] PR/main 常规 CI 全绿。
- [ ] 版本一致性检查全绿。
- [ ] Python、PhonRec 引擎、前端和 Rust 的完整测试范围全部执行，没有只跑默认发现目录。
- [ ] `git diff --check` 通过。

## 3. 手工端到端验收

- [ ] 主程序：新建工程、普通/高级字表、长音频切分、独立音频、F0、共振峰、人工修正、导出。
- [ ] Toolkit：打开工程、TextGrid 检查/预览/转换、图表/表格、脚本成功/拒绝/超时、受控工程变更。
- [ ] CLI：帮助、版本、状态、导入、分析、导出、TextGrid 和脚本命令；错误参数退出码为 `2`。
- [ ] PhonRec 完整模式：引擎探测、普通/高级字表、录音提交、语谱图和完整质量分析、`.teproj` 往返。
- [ ] PhonRec 独立模式：启动降级、录音、轻量质量检查、`.teproj` 往返、WAV 文件夹导出。
- [ ] 兼容性：旧工程、损坏工程、超限工程、缺失资源、导入失败回滚。
- [ ] 数据恢复：自动保存、异常退出、重新启动和显式工程导出。

## 4. 平台与安装资产

- [ ] Windows x64 安装、卸载、升级和文件关联验收。
- [ ] Windows ARM64 安装、卸载、升级和文件关联验收。
- [ ] macOS Apple Silicon 安装、首次启动、麦克风权限与卸载验收。
- [ ] PhonRec Windows x64、Windows ARM64、macOS Apple Silicon 分别验收。
- [ ] 主套件便携 ZIP 中实际包含 `PhonTracer`、`Toolkit`、CLI 与分析引擎；不声称包含独立 PhonRec。
- [ ] PhonRec 安装包与安装后体积通过工作流阈值，且不包含禁带 Python/科学计算运行时。
- [ ] 核对各资产文件名与 README 下载矩阵一致。

## 5. 签名、完整性与来源

- [ ] Windows 可执行文件和安装包使用有效 Authenticode 证书签名。
- [ ] macOS 应用完成 Developer ID 签名、Hardened Runtime 与 Apple 公证。
- [ ] 对每个发布资产生成 SHA-256 清单并随 Release 发布。
- [ ] 从最终 Release 下载资产，重新计算哈希并抽样安装，而不是只检查构建目录。

如本次无法签名或公证，Release 必须明确写出该事实和可能出现的系统警告。

## 6. 发布与发布后

- [ ] 使用 [RELEASE_TEMPLATE.md](RELEASE_TEMPLATE.md) 编写 Release body，列出资产矩阵、边界、升级与校验方法。
- [ ] tag 指向预期提交，`HEAD`、`origin/main` 与 tag 关系已核对。
- [ ] 五条打包工作流均对应同一 `headSha` 且成功。
- [ ] Release 包含全部预期资产，没有旧版同名残留。
- [ ] 重新检查公开 README、隐私、安全、支持和工程格式链接。
- [ ] 发布后记录已知限制和下一版本延期项。
