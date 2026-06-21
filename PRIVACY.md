# 隐私与本地数据说明

最后核对日期：2026-06-21

PhonTracer、Toolkit、PhonTracer CLI 与 PhonRec 面向本地语音研究工作流设计。根据当前仓库代码，软件不会主动上传录音、工程、字表、分析结果或自定义脚本，也未集成遥测、广告或第三方用户行为分析服务。

## 会处理的数据

- 用户主动导入或录制的音频；
- 普通字表、高级字表（`.ptwl`）、TextGrid 与 `.teproj` 工程；
- 发音人名称、分组、备注、标签、自定义科研字段和人工复核状态；
- F0、共振峰、切分边界、质量检查结果和导出文件；
- 本地设置、自动保存副本、自定义脚本及脚本执行记录。

上述内容可能包含个人信息或可识别的声音特征。录制、处理或共享他人数据前，请由数据管理者自行取得适用的知情同意并遵守所在机构与地区的规则。

## 本地存储位置

主程序、Toolkit 和 CLI 共用以下用户目录：

- Windows：`%USERPROFILE%\.phon_tracer\`
- macOS：`~/.phon_tracer/`

其中通常包含：

- `workspace/`：当前工作区及音频、分析缓存；
- `auto_save_backup.teproj`：自动恢复副本；
- `config.json`：主程序设置；
- `scripts/`：用户保存的自定义脚本。

当前主程序与 Toolkit 尚未建立统一的持久化日志目录；部分诊断只输出到启动它们的控制台。CLI 诊断同样以终端输出为主。提交问题时请复制错误文字或终端输出，但先移除用户名、绝对路径和研究数据。后续若加入日志文件，必须在本说明和支持指南中补充位置、保留期与清理方式。

手动更新检查的本地状态保存在 `~/.phontracer_settings.json`。

PhonRec 使用独立的应用数据目录：

- Windows：`%LOCALAPPDATA%\KasumiKitsune\PhonRec\`
- macOS：`~/Library/Application Support/com.kasumikitsune.phonrec/`

该目录包含 `workspace/` 与 `settings.json`。软件升级时可能从旧安装位置迁移已有 PhonRec 工作区。

## 网络与本机通信

- 主程序“检查更新”功能仅在用户手动触发时访问 GitHub Releases；
- PhonRec 完整模式会在本机 `127.0.0.1` 的随机端口启动 PhonTracer 分析引擎；除健康检查外的接口要求会话令牌；
- 开发模式可能使用 `localhost:5173`；
- 当前代码没有把研究数据发送到远程服务的路径。

系统防火墙、代理、浏览器组件、操作系统或用户自行安装的脚本不属于上述代码边界。

## 删除、迁移与备份

- 删除本地工作区前，请先导出需要保留的 `.teproj`；
- 卸载程序不一定删除用户数据目录；如需彻底清除，请退出所有组件后手动删除上述目录；
- `.teproj` 是 ZIP 容器，可能包含原始或切分后的音频、元数据与分析缓存。分享前应按研究伦理要求检查并脱敏；
- GitHub Issue 是公开区域，不要上传含敏感录音、真实姓名、令牌或未脱敏工程的附件。

## 联系与更新

隐私问题请通过 [GitHub Issues](https://github.com/KasumiKitsune/PhonTracer/issues) 提交不含敏感数据的说明。安全问题请遵循 [SECURITY.md](SECURITY.md)。本说明描述的是当前代码状态；新增联网功能或数据处理方式时应同步修订。
