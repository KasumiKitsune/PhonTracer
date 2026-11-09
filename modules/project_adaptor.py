# -*- coding: utf-8 -*-
"""
Thin facade for modules.project_adaptor.
All implementations live in modules.storage.{archive_security, schema_migrator} and modules.dsp.sound_loader.
"""

from .dsp.sound_loader import (
    repair_wav_header,
    load_compatible_sound,
)
from .storage.archive_security import (
    ArchiveSecurityError,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_MEMBER_COUNT,
    MAX_SINGLE_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_SINGLE_MEMBER_BYTES,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES,
    MAX_ARCHIVE_EXPANSION_RATIO,
    MAX_PROJECT_JSON_BYTES,
    safe_resource_token,
    normalize_archive_member_name,
    validate_project_archive_members,
    safe_extract_zip,
    resolve_workspace_path,
)
from .storage.schema_migrator import (
    CURRENT_SCHEMA_VERSION,
    ENGINE_PROTOCOL_VERSION,
    SUPPORTED_PROJECT_VERSIONS,
    ProjectSchemaAdaptor,
    to_list,
    to_dict,
    validate_project_version,
    read_project_metadata_from_archive,
    normalize_independent_item_boundaries,
    _iter_state_resource_refs,
    validate_project_resources,
    adapt_project_state,
    prune_unreferenced_resources,
)

__all__ = [
    "ArchiveSecurityError",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_ARCHIVE_MEMBER_COUNT",
    "MAX_SINGLE_MEMBER_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ARCHIVE_MEMBER_BYTES",
    "MAX_ARCHIVE_SINGLE_MEMBER_BYTES",
    "MAX_ARCHIVE_TOTAL_BYTES",
    "MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ARCHIVE_EXPANSION_RATIO",
    "MAX_PROJECT_JSON_BYTES",
    "CURRENT_SCHEMA_VERSION",
    "ENGINE_PROTOCOL_VERSION",
    "SUPPORTED_PROJECT_VERSIONS",
    "ProjectSchemaAdaptor",
    "to_list",
    "to_dict",
    "safe_resource_token",
    "normalize_archive_member_name",
    "validate_project_version",
    "validate_project_archive_members",
    "read_project_metadata_from_archive",
    "safe_extract_zip",
    "resolve_workspace_path",
    "repair_wav_header",
    "load_compatible_sound",
    "normalize_independent_item_boundaries",
    "_iter_state_resource_refs",
    "validate_project_resources",
    "adapt_project_state",
    "prune_unreferenced_resources",
]
