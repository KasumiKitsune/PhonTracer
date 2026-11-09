# -*- coding: utf-8 -*-
"""
Thin facade for modules.project_manager.
All implementations live in modules.storage.{workspace_lock, project_repository, archive_security, schema_migrator}.
"""

import os
import shutil

from .dsp.sound_loader import load_compatible_sound
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
    validate_project_archive_members,
    safe_extract_zip,
    resolve_workspace_path,
)
from .storage.schema_migrator import (
    CURRENT_SCHEMA_VERSION,
    ENGINE_PROTOCOL_VERSION,
    SUPPORTED_PROJECT_VERSIONS,
    ProjectSchemaAdaptor,
    validate_project_version,
    read_project_metadata_from_archive,
    prune_unreferenced_resources,
    validate_project_resources,
    normalize_independent_item_boundaries,
    _iter_state_resource_refs,
)
from .storage.workspace_lock import (
    is_pid_alive,
    _is_pid_alive,
    WorkspaceLockMixin,
)
from .storage.project_repository import (
    to_json_serializable,
    RUNTIME_EXCLUDED_KEYS,
    ACOUSTIC_PARAM_KEYS,
    migrate_removed_f0_engine,
    ProjectManager,
    ProjectRepository,
)

__all__ = [
    "os",
    "shutil",
    "load_compatible_sound",
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
    "validate_project_version",
    "validate_project_archive_members",
    "read_project_metadata_from_archive",
    "safe_extract_zip",
    "resolve_workspace_path",
    "prune_unreferenced_resources",
    "validate_project_resources",
    "normalize_independent_item_boundaries",
    "_iter_state_resource_refs",
    "is_pid_alive",
    "_is_pid_alive",
    "WorkspaceLockMixin",
    "to_json_serializable",
    "RUNTIME_EXCLUDED_KEYS",
    "ACOUSTIC_PARAM_KEYS",
    "migrate_removed_f0_engine",
    "ProjectManager",
    "ProjectRepository",
]
