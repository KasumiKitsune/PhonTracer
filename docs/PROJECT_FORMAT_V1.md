# `.teproj` 工程格式 1.0

状态：当前稳定格式

格式版本：`1.0`

参考 Schema：[project.schema.json](project.schema.json)

## 1. 容器结构

`.teproj` 是 ZIP 容器，根目录必须包含 `project.json`。当前写入器可能同时包含：

```text
project.json
audio/
  <工程内音频资源>
data/
  <F0 或共振峰 NPZ 缓存>
```

ZIP 成员路径统一使用 `/`。资源引用必须是 `audio/` 或 `data/` 下的相对路径；不接受归档外部绝对路径、`..`、符号链接和跨平台非法文件名。

当前读取安全上限：

- ZIP 成员数：10,000；
- 单个成员解压后大小：2 GiB；
- 全部成员解压后总大小：20 GiB；
- `project.json`：20 MiB。

这些上限是防御性约束，不代表推荐的工程体积。

## 2. 顶层对象

| 字段 | 类型 | 当前写入 | 说明 |
|---|---|---:|---|
| `version` | 字符串 | 必填 | 工程格式版本，当前为 `1.0`；历史缺失时按 `1.0` 兼容读取 |
| `software_version` | 字符串 | 必填 | 写入该工程的软件版本，不用于替代格式版本 |
| `report_format_version` | 字符串 | 必填 | 方法报告结构版本，当前为 `1.0` |
| `save_time` | 字符串 | 必填 | 本地保存时间，ISO 8601 风格 |
| `active_speaker_id` | 字符串或空 | 必填 | 当前活动发音人 ID |
| `export_numbering_rule` | 字符串 | 必填 | 导出编号规则，默认 `continuous` |
| `trim_silence` | 布尔值 | 必填 | 导出或处理时的静音裁剪设置 |
| `speakers` | 对象 | 必填 | 以发音人 ID 为键的事实源；至少一个发音人 |
| `custom_script_runs` | 数组 | 必填 | 已归档的脚本运行记录 |

读取器允许未知字段，以便旧数据恢复和向前兼容；新增字段不得改变既有字段语义。

## 3. 发音人对象

每个 `speakers.<speaker_id>` 当前包含：

- `id`、`name`：稳定 ID 与显示名；
- `last_params`：该发音人的最近分析参数；
- `tab_mode`：如“多条独立音频”等入口模式；
- `long_audio_path`：长音频资源路径或空；
- `pending_batch_paths`：批量音频资源路径数组；
- `current_macro_segments`、`manual_segments`：分段状态；
- `last_selected_iid`：最近选择条目；
- `items`：以条目 ID 为键的条目事实源。

对象键和内部 `id` 在新工程中应一致。导入适配器可以修复部分旧工程 ID 和字表结构，但不能依赖该行为生成不规范的新工程。

## 4. 条目对象

条目字段随来源和分析模式变化。常见字段包括：

| 类别 | 字段示例 | 说明 |
|---|---|---|
| 身份/显示 | `id`、`word`、`group` | 条目 ID、词项和分组 |
| 音频 | `path` | 必须指向 `audio/` 下的归档资源 |
| 分段 | `start`、`end`、`raw_start`、`raw_end`、`chars_bounds`、`inner_splits` | 时间以秒表示；`raw_*` 保留裁剪前边界（存在时），字符边界应与内部分割一致 |
| 分析缓存 | `pitch_data_file`、`formant_data_file` | 指向 `data/` 下的 NPZ；F0 至少含 `xs/freqs`，共振峰至少含 `xs/f1/f2` |
| 审核状态 | `is_excluded`、`review_status`、`metadata_source` | 人工剔除、复核与元数据来源 |
| 高级字表 | `item_note`、`item_tags`、`item_aliases`、`item_meta` | 词项备注、标签、别名和自定义科研字段 |
| 组元数据 | `group_note`、`group_tags` | 分组备注与标签 |
| 参数 | 分析参数及局部覆盖字段 | 条目局部参数不得污染其他条目 |

兼容读取时可能遇到 `note/item_note`、`tags/item_tags`、`aliases/item_aliases`、`meta/item_meta`。当前业务字段以后者为准；导出器不得因为别名转换丢失用户数据。

### `groups / words / chars` 的关系

`groups`、`words`、`chars` 是 PhonTracer TextGrid 互操作中的标准三层：分组层、词项层和字符/音节层。它们不是 `project.json` 中另一套并行条目事实源。工程内以 `speakers.<id>.items` 保存条目及其 `group`、词项标签和边界，导出 TextGrid 时再生成上述三层；重新导入时也必须归一化回条目结构，不能让 TextGrid 层和工程条目长期各自漂移。

- `start/end` 与 `raw_start/raw_end` 的单位均为秒；
- `chars_bounds` 是按字符/音节顺序排列的 `[开始, 结束]` 对；
- `inner_splits` 应等于除最后一段外各 `chars_bounds` 的结束时间；
- 边界应为有限数值、开始不晚于结束，并落在对应音频范围内。运行时适配器会规范化部分旧数据。

## 5. 资源一致性

- 每个被引用资源必须真实存在；
- 音频会在导入校验阶段尝试读取，旧版 WAV 的特定头字段错误可被兼容修复；
- NPZ 缓存必须包含所需数组键；
- 保存后会清理不再被状态引用的工作区资源；
- 分享工程前应假设容器包含录音和可识别元数据，并按 [PRIVACY.md](../PRIVACY.md) 脱敏。

## 6. 兼容与迁移规则

1. `version` 表示文件格式，`software_version` 表示写入软件；两者不能混用。
2. 当前只声明支持格式 `1.0`；未知版本必须明确拒绝，不能静默猜测。
3. 历史缺少 `version` 的工程按 `1.0` 读取，这是既有兼容行为，不建议新写入器省略该字段。
4. 导入应在临时目录完成成员校验、资源校验与状态适配，成功后再原子交换工作区；失败时恢复旧工作区。
5. Overlay 导入应复制并重映射资源，避免覆盖已有发音人与音频。
6. 新增必填字段前必须提供缺省值或迁移器，并增加旧工程、当前工程和失败回滚测试。

## 7. Schema 的定位

`project.schema.json` 用于编辑器提示、文档和基础结构检查。运行时安全仍由 `modules/project_adaptor.py` 执行，包括 ZIP 路径、大小、实际资源和音频/NPZ 内容校验。通过 Schema 不等于工程可安全导入。
