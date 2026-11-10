# -*- coding: utf-8 -*-
"""
PhonTracer CLI Application Subsystem.
Modular headless command processing framework for phonetic analysis.
"""
from __future__ import annotations

from modules.cli_app.io_utils import (
    COMMON_AUDIO_EXTENSIONS,
    AcousticChartCLIAdapter,
    LoggerOut,
    _calc_preview_f0,
    _has_cjk_in_args,
    _natural_sort_key,
    dumps_utf8,
)
from modules.cli_app.help_catalog import (
    CLIHelpMixin,
    get_cli_help_text,
)
from modules.cli_app.session_core_mixin import CLISessionCoreMixin
from modules.cli_app.speaker_ingest_mixin import CLISpeakerIngestMixin
from modules.cli_app.analysis_mixin import CLIAnalysisMixin
from modules.cli_app.export_mixin import CLIExportMixin
from modules.cli_app.project_mixin import CLIProjectMixin
from modules.cli_app.toolkit_script_mixin import CLIToolkitScriptMixin

__all__ = [
    "COMMON_AUDIO_EXTENSIONS",
    "dumps_utf8",
    "_natural_sort_key",
    "LoggerOut",
    "AcousticChartCLIAdapter",
    "_calc_preview_f0",
    "_has_cjk_in_args",
    "get_cli_help_text",
    "CLIHelpMixin",
    "CLISessionCoreMixin",
    "CLISpeakerIngestMixin",
    "CLIAnalysisMixin",
    "CLIExportMixin",
    "CLIProjectMixin",
    "CLIToolkitScriptMixin",
]
