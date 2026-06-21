# PhonTracer vX.Y.Z

> 发布日期：YYYY-MM-DD
>
> 对应提交：`<commit sha>`

## 本次发布

用 3—6 条可验证的短句说明新增能力、兼容性变化和重要修复。不要把实验性能力、未测试平台或静态界面描述成稳定支持。

## 下载矩阵

| 使用场景 | 平台/架构 | 资产文件 | 备注 |
|---|---|---|---|
| 完整主套件安装 | Windows x64 | `PhonTracer_Setup_Windows_x64.exe` | 主程序、Toolkit、CLI、分析引擎 |
| 完整主套件便携 | Windows x64 | `PhonTracer_Suite-Windows-x64.zip` | 不含独立 PhonRec |
| 完整主套件安装 | Windows ARM64 | `PhonTracer_Setup_Windows_ARM64.exe` | 原生 ARM64 |
| 完整主套件便携 | Windows ARM64 | `PhonTracer_Suite-Windows-ARM64.zip` | 不含独立 PhonRec |
| 完整主套件 | macOS Apple Silicon | `PhonTracer_Suite-macOS.dmg` | 主程序与 Toolkit |
| PhonRec | Windows x64 | `PhonRec_X.Y.Z_x64-setup.exe` | 独立录音工具 |
| PhonRec | Windows ARM64 | `PhonRec_X.Y.Z_arm64-setup.exe` | 原生 ARM64 |
| PhonRec | macOS Apple Silicon | `PhonRec_X.Y.Z_aarch64.dmg` | 最低 macOS 12 |

实际发布前必须用工作流产物核对 PhonRec 的精确大小写和 Tauri 后缀，并替换模板文件名。

## 升级与兼容

- `.teproj` 当前格式版本：`1.0`；
- 安装新版本前建议显式导出重要工程；
- 说明本版本是否修改设置、工作区、字表或工程字段；
- 说明已验证的旧工程版本和不能回退的变化。

## 已知边界

- 声学分析结果仍需人工复核；
- PhonRec 独立模式不提供语谱图和完整 F0/共振峰分析；
- 列出尚未完成的签名、公证、硬件或系统版本验收；
- 列出本版本确认存在但未修复的问题。

## 完整性校验

下载同页的 `SHA256SUMS.txt` 后执行：

```powershell
Get-FileHash -Algorithm SHA256 .\<资产文件>
```

将结果与清单中的对应值逐字符比较。若本次未提供哈希清单，必须在此明确说明，不能保留上述已提供的暗示。

## 文档与支持

- [README](https://github.com/KasumiKitsune/PhonTracer/blob/main/README.md)
- [隐私说明](https://github.com/KasumiKitsune/PhonTracer/blob/main/PRIVACY.md)
- [支持指南](https://github.com/KasumiKitsune/PhonTracer/blob/main/SUPPORT.md)
- [安全策略](https://github.com/KasumiKitsune/PhonTracer/blob/main/SECURITY.md)
- [工程格式](https://github.com/KasumiKitsune/PhonTracer/blob/main/docs/PROJECT_FORMAT_V1.md)

发现可复现问题请使用 Issue 模板；安全问题请私下报告。
