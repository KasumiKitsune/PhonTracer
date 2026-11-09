# -*- coding: utf-8 -*-
"""
PhonTracer 高级字表 (v2) 兼容门面模块

内部实现已拆分并迁移至 `modules.wordlist` 子包：
- 数据模型、校验与持久化：`modules.wordlist.schema_and_models`
- 多格式解析与导出：`modules.wordlist.parsers_and_exporters`
"""
from __future__ import annotations

from .core.text_io import parse_wordlist
from .wordlist.parsers_and_exporters import (
    ADVANCED_WORDLIST_AGENT_PROMPT, _apply_structured_group_attrs,
    _parse_structured_attrs, _strip_markdown_code_fence, _structured_item_from_attrs,
    build_ai_conversion_prompt, build_document_from_csv_text, build_document_from_messy_text,
    build_document_from_structured_text, build_document_from_v1_text, document_to_csv_text,
    document_to_v1_text, export_document_to_csv_text, export_document_to_v1_text,
    extract_json_payload, load_any_wordlist_file, load_wordlist_file,
    load_wordlist_from_csv_file, looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text, parse_ai_wordlist_response, parse_any_wordlist_text,
)
from .wordlist.schema_and_models import (
    AI_GENERATED_STATUS, AI_REVIEW_STATUS, CORE_META_FIELDS, DEFAULT_GROUP_NAME,
    DEFAULT_REVIEW_STATUS, META_PINYIN, META_RHYME, META_TONE, REVIEWED_STATUS,
    STRUCTURED_KV_PIPE_RE, TITLE_DIRECTIVE_RE, WORDLIST_EXTENSION, WORDLIST_FORMAT,
    WORDLIST_SCHEMA, WORDLIST_SCHEMA_VERSION, _as_text, _clean_text, _normalize_group,
    _normalize_item, _normalize_meta, _normalize_meta_dict, _read_text_file_with_fallback,
    _split_text_list, _split_tokens, apply_record_metadata,
    build_wordlist_document_from_items, create_empty_wordlist_document,
    describe_wordlist_item, find_First_matching_item, find_first_matching_item,
    find_item_by_id, flatten_wordlist_document, get_document_flat_words,
    get_document_groups_for_runtime, is_wordlist_v2_data, iter_document_items,
    load_wordlist_document, mark_ai_fields_reviewed, merge_wordlist_documents,
    metadata_from_record, normalize_wordlist_document, save_wordlist_document,
    save_wordlist_file, summarize_wordlist_document, validate_wordlist_document,
)

__all__ = [
    "WORDLIST_SCHEMA", "WORDLIST_SCHEMA_VERSION", "WORDLIST_FORMAT", "WORDLIST_EXTENSION",
    "DEFAULT_GROUP_NAME", "DEFAULT_REVIEW_STATUS", "AI_REVIEW_STATUS", "AI_GENERATED_STATUS",
    "REVIEWED_STATUS", "META_PINYIN", "META_TONE", "META_RHYME", "CORE_META_FIELDS",
    "TITLE_DIRECTIVE_RE", "STRUCTURED_KV_PIPE_RE", "ADVANCED_WORDLIST_AGENT_PROMPT",
    "parse_wordlist", "create_empty_wordlist_document", "normalize_wordlist_document",
    "is_wordlist_v2_data", "merge_wordlist_documents", "build_wordlist_document_from_items",
    "flatten_wordlist_document", "metadata_from_record", "apply_record_metadata",
    "load_wordlist_document", "save_wordlist_file", "save_wordlist_document",
    "summarize_wordlist_document", "validate_wordlist_document", "mark_ai_fields_reviewed",
    "get_document_groups_for_runtime", "get_document_flat_words", "iter_document_items",
    "find_first_matching_item", "find_First_matching_item", "find_item_by_id", "describe_wordlist_item",
    "looks_like_structured_wordlist_text", "looks_like_csv_wordlist_text",
    "build_document_from_structured_text", "build_document_from_csv_text",
    "build_document_from_v1_text", "build_document_from_messy_text", "parse_any_wordlist_text",
    "document_to_v1_text", "export_document_to_v1_text", "document_to_csv_text",
    "export_document_to_csv_text", "load_wordlist_file", "load_any_wordlist_file",
    "load_wordlist_from_csv_file", "build_ai_conversion_prompt", "extract_json_payload",
    "parse_ai_wordlist_response",
]
