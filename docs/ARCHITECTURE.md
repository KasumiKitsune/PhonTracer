# PhonTracer 架构与共享事实源

本文面向维护者与二次开发者，说明主程序、Toolkit、CLI 与共享后端子包之间的职责边界及持久化数据契约。

## 组件关系

```text
PhonTracer GUI (main.py / modules/gui/) ────────┐
Toolkit (toolkit.py / modules/toolkit_ui/) ─────┼─> modules/ 共享后端子包 ─> .teproj / TextGrid / Collection / 图表与报告
PhonTracerCLI (cli.py / modules/cli_app/) ──────┘
```

## 主入口与职责

| 入口 | 顶层文件与界面包 | 主要职责 |
|---|---|---|
| 主程序 (`PhonTracer`) | `main.py`、`modules/app.py`、`modules/gui/` | 桌面主界面编排、多发音人/条目状态管理、声谱图交互复核、参数隔离与科学图表导出 |
| 独立工具箱 (`Toolkit`) | `toolkit.py`、`modules/toolkit_ui/` | 音频合并与自适应切分、TextGrid 转换、高级字表（`.ptwl`）编辑、工程预览与方法报告生成、自定义脚本沙箱面板 |
| 命令行工作台 (`PhonTracerCLI`) | `cli.py`、`modules/cli_app/` | 交互式会话、单次命令与 `--script-file` 批处理、异常自动修复、`export_bundle` 一站式交付，全程输出标准 JSON |

## 统一工程数据契约 (`.teproj` 1.0)

`PhonTracer` 的持久化工程为 ZIP 归档（格式版本 `1.0`），核心由根目录 `project.json` 与 `audio/`、`data/` 资源目录构成：

1. **顶层统一字表与分组结构（`groups`）**：
   - 顶层 `groups` 数组保存分组名、组备注、组标签及词项级元数据（对应 `.ptwl` 高级字表结构）；
   - 即使词条尚未关联录音或分组为空，保存时均完整保留，避免多发音人对照采录或分批分析时丢失字表结构。

2. **发音人与条目事实源（`speakers` 与 `items`）**：
   - 运行时与持久化的核心条目事实源由 `speakers[spk_id].items` 统一维护；
   - 每位发音人包含独立 `id`、`name`、`last_params`、`tab_mode`（`多条独立音频` / `单条长音频`）与分组顺序 `project_groups`；
   - 长音频模式下，整段基频缓存在发音人级共享文件 `data/{spk_id}_long_pitch.npz` 中只存一份，条目仅保存局部窗口切片缓存，避免工程体积膨胀。

3. **单条参数按需覆盖（`param_overrides`）**：
   - 单条目的声学参数仅在与所属发音人 `last_params` 不同时将差异键写入 `item["param_overrides"]`，不每条冗余复制默认参数，同时保证局部重算不污染其他词条。

4. **运行态临时字段剥离**：
   - 写入磁盘时统一剔除运行态对象与临时预览缓存（如 `snd`、`pitch`、`formant`、`preview_f0`、`preview_formants`、`warnings`、`has_empty_data`、`_dirty_*`），保持归档轻量可移植。

5. **时间边界与手工精修保护**：
   - 条目同时维护宏观切片边界 `macro_start`/`macro_end`、静音裁剪前边界 `raw_start`/`raw_end` 与当前有效边界 `start`/`end`；
   - 多音节词条维护逐字区间 `chars_bounds` 与内部分界 `inner_splits`，手工调整边界或擦除散点后置位 `is_manual_edited = True`，在模式切换与工程保存中受保护。

完整容器限制、字段约定与兼容规则见 [`.teproj` 工程格式 1.0](PROJECT_FORMAT_V1.md) 与 [project.schema.json](project.schema.json)。

## 共享事实源与模块化子包

新增或修改核心能力时应落在共享后端子包中，避免在 GUI、Toolkit 与 CLI 各自复制逻辑（顶层同名 `.py` 文件作为向后兼容门面保留导出）：

| 领域 | 底层实现子包 / 模块 | 兼容门面文件 | 主要消费者 |
|---|---|---|---|
| 软件版本单一事实源 | `modules/version.py` | — | 主程序、Toolkit、CLI、安装包与发布校验脚本 |
| 工程仓储、锁与安全迁移 | `modules/storage/`（`project_repository`、`schema_migrator`、`archive_security`、`workspace_lock`） | `modules/project_manager.py`、`modules/project_adaptor.py` | 主程序、Toolkit、CLI |
| 声学信号处理与静音切分 | `modules/dsp/`（`sound_loader`、`pitch_tracker`、`formant_tracker`、`vad_splitter`） | `modules/audio_core.py` | 主程序、Toolkit、CLI |
| 高级字表与草稿容错解析 | `modules/wordlist/`、`modules/core/text_io.py` | `modules/wordlist_v2.py`、`modules/wordlist_editor.py` | 主程序、Toolkit、CLI |
| 科学图表引擎与导出服务 | `modules/chart_engine/`、`modules/export_service/` | `modules/acoustic_exporter.py`、`modules/data_utils.py` | 主程序、CLI、`export_bundle` |
| 异常筛查与研究方法报告 | `modules/anomaly_detection.py`、`modules/reporting/` | `modules/report_generator.py` | 主程序项目树、Toolkit 工程预览、CLI |
| 自定义脚本沙箱与内置库 | `modules/scripting/`（`sandbox_runner`、`builtin_scripts_catalog`、`context_api`） | `modules/script_runner.py`、`modules/script_api.py` | Toolkit 自定义脚本页签、CLI `run_script` |
| 受控工程补丁写回 | `modules/project_patch.py` | — | 数据处理类沙箱脚本、Toolkit 与 CLI |
| TextGrid 双向转换 | `modules/textgrid_converter.py` | — | 主程序、Toolkit、CLI |

## 状态与持久化原则

- 运行时内存事实源为 `speaker_manager.speakers[*].items`；界面树控件仅作视图投影，不充当持久化事实源；
- `ProjectManager.save_to_workspace()` 先写入 `.tmp` 临时文件并校验可序列化性，再原子替换 `project.json`；工程导入先在独立临时目录完成安全与资源校验，通过后再原子切换工作区，失败即回滚；
- 归档内所有音频与缓存路径必须位于 `audio/` 或 `data/` 相对路径下，严禁写入宿主机绝对路径；
- 历史别名字段 `item_note / item_tags / item_aliases / item_meta` 在持久化时统一归一化为 `note / tags / aliases / meta`；
- 普通/草稿文本字表（`.txt`）、表格字表（`.csv`）与高级字表（`.ptwl`）导入后统一转换为相同的分组词项内存模型。

## 自定义脚本沙箱边界

自定义脚本以 `def run(ctx):` 为入口，可选声明顶层 `PARAMS` 列表（由静态解析器提取并自动渲染界面参数控件或校验 CLI 入参）。脚本接收只读工程数据快照，允许导入白名单科学计算模块（`numpy`、`scipy`、`pandas`、`matplotlib`、受控 `parselmouth` 及安全标准库子集），并通过 `FigureResult`、`TableResult` 或 `ProjectPatchResult` 返回结果；任何针对工程数据的修改均须封装为受控补丁操作并通过 `modules/project_patch.py` 校验后写回。执行器通过 AST 语法静态审查（拦截双下划线属性、动态导入与文件/网络 I/O）及受限内置函数表保障执行边界。

## 修改时的最低核对清单

1. **变更工程字段**：同步更新 `docs/PROJECT_FORMAT_V1.md`、`docs/project.schema.json`、`modules/storage/schema_migrator.py` 及读写回滚测试。
2. **变更共享算法或导出口径**：核对 GUI、Toolkit、CLI 三个入口行为一致，不在界面层硬编码分叉逻辑。
3. **变更软件版本**：同步 `modules/version.py`、`installer.iss`、`README.md` 与 `CITATION.cff`，并运行 `python scripts/check_release_version.py --expected vX.Y.Z`。
4. **发布与提交门禁**：运行 `python -m compileall -q main.py cli.py toolkit.py modules tests` 与 `python -m pytest -q tests`。
