# `.teproj` 工程格式 1.0

状态：当前稳定格式

格式版本：`1.0`

参考 Schema：[project.schema.json](project.schema.json)

## 1. 容器结构

`.teproj` 是 ZIP 容器，根目录必须包含 `project.json`。当前写入器同时包含关联音频与分析缓存目录：

```text
project.json
audio/
  <工程内音频资源>
data/
  <F0 或共振峰 NPZ 缓存>
```

ZIP 成员路径统一使用 `/`。资源引用必须是 `audio/` 或 `data/` 下的相对路径；拒绝归档外部绝对路径、`..` 路径穿越、符号链接、重复路径（大小写折叠去重）及跨平台保留或非法文件名。

当前读取安全上限（见 `modules/storage/archive_security.py`）：

- ZIP 成员数：`100,000`；
- 单个成员解压后大小：`2 GiB`；
- 全部成员解压后总大小：`4 GiB`；
- `project.json` 大小：`20 MiB`。

以上上限为防御性安全边界，不代表推荐的日常工程体积。

## 2. 顶层对象

| 字段 | 类型 | 当前写入 | 说明 |
|---|---|---:|---|
| `version` | 字符串 | 必填 | 工程格式版本，当前固定为 `"1.0"`；历史缺失时按 `"1.0"` 兼容读取 |
| `software_version` | 字符串 | 必填 | 写入该工程的软件版本（如 `"1.0.0"`），不替代格式版本 |
| `report_format_version` | 字符串 | 必填 | 研究方法报告结构版本，当前为 `"1.0"` |
| `save_time` | 字符串 | 必填 | 保存时间戳（ISO 8601 风格，含本地时区偏移） |
| `active_speaker_id` | 字符串或 `null` | 必填 | 当前活动发音人 ID |
| `export_numbering_rule` | 字符串 | 必填 | 导出编号规则，默认 `"continuous"`（可选 `"per_group"`） |
| `trim_silence` | 布尔值 | 必填 | 导出或提取时的静音裁剪开关 |
| `speakers` | 对象 | 必填 | 以发音人 ID 为键的发音人事实源（至少包含 1 位发音人） |
| `custom_script_runs` | 数组 | 必填 | 已归档的自定义脚本运行记录 |
| `groups` | 数组 | 按需写入 | 顶层统一字表分组与词项元数据列表；旧工程缺失时由导入适配器合并重建 |
| `wordlist_version` / `wordlist_title` / `wordlist_note` / `wordlist_tags` | 字符串 / 数组 | 按需写入 | 高级字表（`.ptwl`）顶层元数据 |
| `scripts_library` / `quality_config` / `meta` | 对象 | 按需写入 | 工程随附脚本字典、质控配置与扩展元数据 |

读取器允许未知扩展字段，以保证向前兼容与历史数据恢复；新增字段不得改变既有字段语义。

## 3. 发音人对象

每个 `speakers.<speaker_id>` 当前包含：

- `id`、`name`：稳定发音人 ID 与显示名称（对象键与内部 `id` 在规范工程中一致）；
- `last_params`：该发音人的当前声学提取参数字典；
- `tab_mode`：导入工作流模式（如 `"多条独立音频"` 或 `"单条长音频"`）；
- `project_groups`：该发音人项目树的分组显示顺序列表；
- `long_audio_path`：长音频归档相对路径（`audio/...`）或 `null`；
- `long_pitch_data_file`：长音频模式下发音人级共享基频缓存（`data/<spk_id>_long_pitch.npz`），避免各条目重复存储整段基频；
- `pending_batch_paths`：批量导入音频资源相对路径数组；
- `current_macro_segments`、`manual_segments`：长音频宏观切分与手动分段状态；
- `last_selected_iid`：最近选中的条目 ID；
- `wordlist_doc`：发音人关联字表文档快照（可选）；
- `items`：以条目 ID 为键的条目事实源对象。

## 4. 条目对象

条目字段随导入来源与分析模式变化。规范保存后的主要字段包括：

| 类别 | 字段示例 | 说明 |
|---|---|---|
| 身份与显示 | `id`、`label`、`word`、`group` | 条目 ID、显示词项（以 `label` 为主，兼容 `word`）和所属分组名 |
| 音频引用 | `path`、`source_segment` | `path` 指向 `audio/` 下的归档音频；长音频切片导入后会记录原始片段来源 `source_segment` |
| 时间边界 | `start`、`end`、`macro_start`、`macro_end`、`raw_start`、`raw_end`、`chars_bounds`、`inner_splits` | 单位均为秒；`macro_*` 为宏观切片边界，`raw_*` 保留静音裁剪前边界，`chars_bounds` 为逐字/音节区间 |
| 分析缓存 | `pitch_data_file`、`formant_data_file` | 指向 `data/` 下的压缩 `.npz`；F0 必含 `xs`、`freqs`，共振峰必含 `xs`、`f1`、`f2`（提取 F3 时含 `f3`） |
| 复核与状态 | `is_manual_edited`、`is_excluded`、`exclusion_reason`、`review_status`、`split_warnings` | 人工精修保护标记、剔除状态与原因、复核状态及切分风险提示码 |
| 词项元数据 | `note`、`tags`、`aliases`、`meta`、`metadata_source` | 词项备注、标签数组、别名数组、自定义科研字段字典与元数据来源 |
| 单条参数隔离 | `param_overrides` | 仅记录与所属发音人 `last_params` 存在差异的声学参数键值，不冗余复制默认参数 |

**字段别名与运行态剥离约定：**
- 内存中历史或界面字段 `item_note`、`item_tags`、`item_aliases`、`item_meta` 在持久化时统一归一化为 `note`、`tags`、`aliases`、`meta`（读取器双向兼容，确保导出时不丢失元数据）。
- 保存到磁盘时自动剥离运行态临时缓存字段（如 `snd`、`pitch`、`formant`、`preview_f0`、`preview_formants`、`warnings`、`has_empty_data`、`_dirty_pitch`、`_dirty_formant`）。

### `groups / words / chars` 与 TextGrid 的关系

`groups`、`words`、`chars` 是 PhonTracer 导出标准 Praat `TextGrid` 时的三层标注结构（分组层、词项层、字/音节层），并非 `project.json` 内与 `items` 并行的第二套时间轴事实源。工程内以 `speakers.<id>.items` 保存各条目的分组归属与边界，导出 `TextGrid` 时动态生成三层区间；从外部导入 `TextGrid` 时同样归一化回条目结构。

- `chars_bounds` 是按字/音节顺序排列的 `[开始, 结束]` 二元数组，满足开始时间不晚于结束时间且落在条目有效音频范围内；
- `inner_splits` 长度为 `len(chars_bounds) - 1`：相邻音节边界紧邻时等于前字结束时间；存在字间静音间隙时位于相邻两字边界之间（交互拖拽时取中点）。

## 5. 资源一致性

- `project.json` 引用的每一个 `audio/` 与 `data/` 资源在归档内必须真实存在且路径不越界；
- 音频资源在导入校验阶段会实际解析头信息（兼容修复部分旧设备录音的非标准 WAV 头字段）；
- `.npz` 缓存文件在导入校验阶段会核查必需数组键（`xs/freqs` 或 `xs/f1/f2`）；
- 工作区保存后会自动清理不再被任何发音人或条目引用的孤立资源文件；
- `.teproj` 内包含原始录音副本与发音人信息，公开发布或共享前请按研究伦理规范检查脱敏。

## 6. 兼容与迁移规则

1. `version` 表示 `.teproj` 容器与 `project.json` 格式版本，`software_version` 表示写入该文件的软件版本；两者不得混用。
2. 当前读取器仅支持格式版本 `"1.0"`；遇到未知格式版本时必须显式报错拒绝，不得静默猜测。
3. 历史缺失 `version` 字段的旧工程按 `"1.0"` 兼容读取；新写入器必须显式写入 `"1.0"`。
4. 导入工程必须在独立临时工作区完成 ZIP 安全校验、资源完整性校验与状态适配，全部通过后再原子替换工作区；任一环节失败即回滚保留原工作区。
5. 叠加（Overlay）导入时须重命名并隔离复制音频与缓存资源，防止覆盖当前工作区已有发音人数据。
6. 未来扩展字段须提供默认值或迁移逻辑，并同步补充旧工程兼容、当前工程读写与异常回滚测试。

## 7. Schema 与运行时校验职责

`docs/project.schema.json` 用于编辑器结构提示、契约文档化及 JSON 结构单元测试。运行时归档安全与资源一致性仍由 `modules/project_adaptor.py` 门面及 `modules/storage/`（`archive_security.py`、`schema_migrator.py`、`project_repository.py`）执行；仅通过 JSON Schema 校验并不等同于归档文件可安全导入。
