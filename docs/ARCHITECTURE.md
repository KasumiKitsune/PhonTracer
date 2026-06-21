# PhonTracer 架构与事实源

本文面向维护者，说明主程序、Toolkit、CLI、PhonRec 与共享后端之间的边界。文件路径和能力以 2026-06-21 的仓库状态为准。

## 组件关系

```text
PhonTracer GUI ─┐
Toolkit ────────┼─> modules/ 共享 Python 后端 ─> .teproj / TextGrid / 图表与表格
CLI ────────────┘

PhonRec React 前端 ─> Tauri/Rust 本地能力
        ├─ 独立模式：录音、轻量质量检查、字表/工程、WAV 文件夹导出
        └─ 完整模式：127.0.0.1 随机端口 ─> PhonTracer Python 分析引擎
```

## 主入口与职责

| 入口 | 文件 | 主要职责 |
|---|---|---|
| 主程序 | `main.py`、`modules/app.py` | GUI 编排、发音人/条目状态、分析与人工复核 |
| Toolkit | `toolkit.py` | 工程工具、图表/表格、批处理和自定义脚本工作流 |
| CLI | `cli.py` | 交互式与单次命令、批处理、工程和脚本管理 |
| PhonRec 前端 | `PhonRec/frontend/src/` | 录音任务、质量反馈、模式降级与用户交互 |
| PhonRec 原生层 | `PhonRec/frontend/src-tauri/src/main.rs` | 设备录音、本地设置、工作区、工程导入导出、分析引擎生命周期 |
| PhonRec 分析引擎 | `PhonRec/backend/main.py` | 完整模式的声学分析、工程适配与字表解析 HTTP 接口 |

## 共享事实源

新增能力应优先落在以下共享层，避免 GUI、Toolkit、CLI 和 PhonRec 各自实现一套。

| 领域 | 事实源 | 主要消费者 |
|---|---|---|
| 软件版本 | `modules/version.py` | 主程序、Toolkit、CLI、发布检查 |
| 工程保存/导入 | `modules/project_manager.py` | 主程序及共享工程工作流 |
| 工程安全与兼容 | `modules/project_adaptor.py` | 主程序、Toolkit 数据处理、PhonRec 引擎 |
| 高级字表 | `modules/wordlist_v2.py` | GUI、CLI、PhonRec 引擎 |
| TextGrid 转换 | `modules/textgrid_converter.py` | Toolkit 与 CLI |
| 音频静音裁剪 | `modules/audio_core.py` | GUI、Toolkit、CLI 相关流程 |
| 脚本 API/执行 | `modules/script_api.py`、`modules/script_runner.py` | Toolkit 与 CLI |
| 受控工程变更 | `modules/project_patch.py` | 数据处理脚本与 Toolkit |

## 状态与持久化

- 当前工程内存事实源是 `speaker_manager.speakers[*].items`；界面树只是视图，不应成为持久化事实源；
- `ProjectManager.save_to_workspace()` 先写临时 JSON，再原子替换 `project.json`；导入使用临时工作区、校验、交换与回滚；
- 工程资源必须位于归档中的 `audio/` 或 `data/`，不能引用归档外部绝对路径；
- `note/item_note`、`tags/item_tags`、`aliases/item_aliases`、`meta/item_meta` 是兼容读取中可能出现的别名，写入与业务代码应使用当前字段；
- 普通字表和 `.ptwl` 最终都应归一化成同一条目元数据结构。

工程格式详情见 [PROJECT_FORMAT_V1.md](PROJECT_FORMAT_V1.md)。

## PhonRec 双运行时

PhonRec 前端必须通过运行时能力而不是界面猜测决定可用功能：

| 能力 | 完整模式 | 独立模式 |
|---|---:|---:|
| `.teproj` 导入导出 | 是 | 是 |
| 高级 `.ptwl` 字表 | 是 | 是 |
| 语谱图 | 是 | 否 |
| 完整声学质量分析 | 是 | 否 |
| 轻量质量检查 | 是 | 是 |
| 按层级导出 WAV 文件夹 | 否 | 是 |

完整模式由 Tauri 启动分析引擎并传入工作区、回环端口和随机令牌。健康检查用于启动探测；业务接口必须验证 Bearer 令牌。独立模式仍需保持工程和录音可恢复，不能因引擎不可用而丢失状态。

## 自定义脚本边界

脚本以 `def run(ctx):` 为入口，获取只读数据快照，并通过 `FigureResult`、`TableResult` 或 `ProjectPatchResult` 返回结果。工程修改由受控操作清单统一验证和写回。当前执行器依靠 AST 检查、内置函数白名单、导入白名单和输出 API 限制，但仍与宿主同进程；详细模型见 [THREAT_MODEL.md](THREAT_MODEL.md)。

## 修改时的最低核对

1. 改工程字段：同步工程规范、Schema、读写兼容和 GUI/CLI/PhonRec 测试。
2. 改共享算法：检查全部消费者，不在入口层复制实现。
3. 改 PhonRec 能力：分别验证完整模式、独立模式和启动降级。
4. 改版本：运行 `python scripts/check_release_version.py --expected vX.Y.Z`。
5. 发布前：执行 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) 中的全量门禁。
