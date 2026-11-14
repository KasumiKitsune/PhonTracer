# PhonTracer 架构与事实源

本文面向维护者，说明主程序、Toolkit、CLI 与共享后端之间的边界与数据契约。

## 组件关系

```text
PhonTracer GUI ─┐
Toolkit ────────┼─> modules/ 共享 Python 后端 ─> .teproj / TextGrid / Collection / 图表与表格
CLI ────────────┘
```

## 主入口与职责

| 入口 | 文件 | 主要职责 |
|---|---|---|
| 主程序 | `main.py`、`modules/app.py` | GUI 编排、发音人/条目状态、声学分析与人工精修复核 |
| Toolkit | `toolkit.py` | 工程辅助工具、音频拆合、高级字表编辑、批处理与受控自定义脚本工作流 |
| CLI | `cli.py` | 交互式与单次命令、脚本跑批、工程管理与一键导出打包，标准 JSON 协议通信 |

## 统一工程数据契约 (.teproj v1/v2)

`PhonTracer` 的持久化工程核心为 ZIP 归档中的 `project.json` 及关联音频/数据文件。数据契约遵循以下原则：

1. **顶层字表与结构（`groups`）**：
   - 顶层 `groups` 数组保留字表的分组名、组备注、组标签及词项元数据；
   - 即使词条尚未匹配音频或为空分组，保存时均予以完整保留，避免字表结构丢失。

2. **发音人与条目（`speakers` 与 `items`）**：
   - 事实源由 `speakers[spk_id].items` 统一管理；
   - 每个发音人包含 `id`, `name`, `tab_mode`（多条独立音频 / 单条长音频），以及分组顺序列表 `project_groups`；
   - 长音频模式下，全量基频缓存采用发音人级单一共享文件 `data/{spk_id}_long_pitch.npz`，避免各条目重复打包相同基频导致工程体积膨胀。

3. **按需覆盖参数（`param_overrides`）**：
   - 单条目的声学参数仅在用户发生个别微调时记录差异至 `item["param_overrides"]`，不再每条冗余复制全套默认参数。

4. **运行态临时字段排除**：
   - 保存至磁盘时，统一剥离临时运行态缓存字段（如 `preview_f0`, `preview_formants`, `warnings`, `has_empty_data`），保证归档轻量可移植。

5. **条目边界与切分契约**：
   - 词条音段包含宏观时间 `macro_start`/`macro_end` 与微观人工边界 `start`/`end`；
   - 多音节切分包含 `chars_bounds` 与 `inner_splits`，用户手动精修标记 `is_manual_edited` 在切换模式与保存时严格受到保护。

详细 JSON 校验规则见 [project.schema.json](project.schema.json)。

## 共享事实源

新增能力优先落在以下共享层，避免 GUI、Toolkit 和 CLI 各自实现一套。

| 领域 | 事实源 | 主要消费者 |
|---|---|---|
| 软件版本 | `modules/version.py` | 主程序、Toolkit、CLI、发布检查 |
| 工程保存/导入 | `modules/project_manager.py` | 主程序、CLI 及共享工程工作流 |
| 工程安全与兼容 | `modules/project_adaptor.py` | 主程序、Toolkit 数据处理、跨版本工程升级 |
| 高级字表与容错解析 | `modules/wordlist_v2.py`、`modules/core/text_io.py` | GUI、Toolkit、CLI、字表解析与导出 |
| TextGrid 转换 | `modules/textgrid_converter.py` | Toolkit 与 CLI |
| 音频核心算法 | `modules/audio_core.py` | GUI、Toolkit、CLI 相关流程 |
| 导出格式与样式 | `modules/data_utils.py`、`modules/export_service/` | GUI、CLI、报告生成器 |
| 脚本 API/执行 | `modules/script_api.py`、`modules/script_runner.py` | Toolkit 与 CLI |
| 受控工程变更 | `modules/project_patch.py` | 数据处理脚本与 Toolkit |

## 状态与持久化原则

- 当前工程内存事实源是 `speaker_manager.speakers[*].items`；界面树只是视图，不作为持久化事实源；
- `ProjectManager.save_to_workspace()` 先写临时 JSON，再原子替换 `project.json`；导入使用临时工作区、校验、交换与回滚；
- 工程资源必须位于归档中的 `audio/` 或 `data/`，禁止引用归档外部绝对路径；
- `note/item_note`、`tags/item_tags`、`aliases/item_aliases`、`meta/item_meta` 统一由字段标准化机制映射，写入使用规范字段；
- 普通/草稿字表（`.txt`）、表格字表（`.csv`）与高级字表（`.ptwl`/`.json`）在导入后均归一化为统一分组条目模型。

## 自定义脚本边界

脚本以 `def run(ctx):` 为入口，获取只读数据快照，并通过 `FigureResult`、`TableResult` 或 `ProjectPatchResult` 返回结果。工程修改由受控操作清单统一验证和写回。执行器依靠严格的 AST 语法检查（拦截私有属性和双下划线访问）、内置函数白名单与纯函数命名空间，确保沙箱安全。

## 修改时的最低核对

1. 改工程字段：同步工程规范、Schema（`project.schema.json`）、读写兼容和 GUI/CLI 测试。
2. 改共享算法：检查全部消费者，不在入口层复制实现。
3. 改版本：运行 `python scripts/check_release_version.py --expected vX.Y.Z`。
4. 发布前：执行全量测试门禁（`python -m pytest -q tests`）。
