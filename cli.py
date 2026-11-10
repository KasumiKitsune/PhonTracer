# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Command-Line Interface and Batch Acoustic Analysis System.
Headless root entrypoint combining all modular Mixin subsystems.
"""
from __future__ import annotations

import cmd
import json
import multiprocessing
import os
import shlex
import sys
import warnings

# Force sys.stdin, sys.stdout, sys.stderr to use UTF-8 encoding to avoid encoding issues (especially on Windows)
if sys.stdin and hasattr(sys.stdin, 'reconfigure'):
    try:
        sys.stdin.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Mean of empty slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Degrees of freedom <= 0 for slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*All-NaN slice encountered.*")
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=UserWarning, message=".*Glyph.*missing from.*font.*")

# Modify sys.path if necessary
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Re-exports required by tests, external callers, and CLI subpackages
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
from modules.version import APP_NAME, __version__


class PhonTracerCLI(
    CLIHelpMixin,
    CLISessionCoreMixin,
    CLISpeakerIngestMixin,
    CLIAnalysisMixin,
    CLIExportMixin,
    CLIProjectMixin,
    CLIToolkitScriptMixin,
    cmd.Cmd,
):
    """
    PhonTracer Unified Command-Line Interface.
    Composed of 7 specialized headless mixin modules and standard library cmd.Cmd.
    """
    pass


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Clean UTF-8 BOM from every startup argument token
    raw_argv = [
        a.lstrip('\ufeff\ufffe\u200b') if isinstance(a, str) else a
        for a in raw_argv
    ]
    # Handle multiprocessing bootstrap correctly in frozen Windows builds.
    multiprocessing.freeze_support()

    shared_workspace = False
    custom_ws = None
    project_file = None
    script_file = None
    explicit_lang = None
    quiet_mode = False
    json_mode = False
    unknown_option = None

    cleaned_argv = []
    i = 0
    while i < len(raw_argv):
        arg = raw_argv[i]
        arg_clean = arg.strip() if isinstance(arg, str) else ""
        if not cleaned_argv:
            # Parse global startup options before the subcommand
            if arg_clean == "--shared-workspace":
                shared_workspace = True
                i += 1
                continue
            elif arg_clean == "--workspace" and i + 1 < len(raw_argv):
                custom_ws = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--workspace="):
                custom_ws = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean == "--project" and i + 1 < len(raw_argv):
                project_file = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--project="):
                project_file = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean in ("--script-file", "--batch-cmds") and i + 1 < len(raw_argv):
                script_file = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--script-file=") or arg_clean.startswith("--batch-cmds="):
                script_file = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean == "--lang" and i + 1 < len(raw_argv):
                explicit_lang = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip().lower()
                i += 2
                continue
            elif arg_clean.startswith("--lang="):
                explicit_lang = arg_clean.split("=", 1)[1].strip().lower()
                i += 1
                continue
            elif arg_clean in ("--quiet", "-q"):
                quiet_mode = True
                i += 1
                continue
            elif arg_clean == "--json":
                json_mode = True
                quiet_mode = True
                i += 1
                continue
            elif arg_clean in ("-h", "--help", "-v", "--version") or arg_clean.startswith("--multiprocessing-"):
                cleaned_argv.append(arg_clean)
                i += 1
                continue
            elif arg_clean.startswith("-"):
                unknown_option = arg_clean
                break

        cleaned_argv.append(arg)
        i += 1

    if unknown_option is not None:
        print(json.dumps({
            "success": False,
            "error": f"Unknown startup option: {unknown_option}",
            "hint": "Use --help for manual, or pass a CLI command directly (e.g. `PhonTracerCLI.exe status`)."
        }))
        return 2

    # Language resolution: explicit --lang zh|en, or auto-detect Chinese characters in raw_argv
    if explicit_lang in ("zh", "cn", "chinese", "中文"):
        resolved_lang = "zh"
    elif explicit_lang in ("en", "english", "英文"):
        resolved_lang = "en"
    elif _has_cjk_in_args(raw_argv):
        resolved_lang = "zh"
    else:
        resolved_lang = "en"

    argv = cleaned_argv

    # Handle --version before initializing workspace if no workspace/project work is needed
    if argv and argv[0].strip().lower() in ("-v", "--version", "version") and not script_file:
        print(f"{APP_NAME} CLI {__version__}")
        return 0

    if argv and argv[0].strip().lower().startswith("--multiprocessing-"):
        return 0

    is_stdin_tty = True
    try:
        is_stdin_tty = bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        is_stdin_tty = True

    is_non_interactive = bool(argv or script_file or not is_stdin_tty or quiet_mode or json_mode)

    cli = PhonTracerCLI(
        workspace_dir=custom_ws,
        shared_workspace=shared_workspace,
        project_path=project_file,
        lang=resolved_lang,
        quiet=(quiet_mode or is_non_interactive),
        json_mode=json_mode,
    )
    try:
        # 1. Batch script file execution mode (--script-file / --batch-cmds)
        if script_file is not None:
            if not os.path.exists(script_file):
                cli._emit(
                    False,
                    error=f"Script file not found: {script_file}",
                    failed_line=0,
                    failed_command="",
                )
                return 1
            try:
                with open(script_file, "r", encoding="utf-8-sig") as sf:
                    script_lines = sf.readlines()
            except Exception as e:
                cli._emit(
                    False,
                    error=f"Failed to read script file: {e}",
                    failed_line=0,
                    failed_command="",
                )
                return 1

            cli._defer_persistence = True
            cli._in_script_file = True
            for line_no, raw_line in enumerate(script_lines, start=1):
                cmd_line = PhonTracerCLI._clean_bom_and_whitespace(raw_line)
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                if cmd_line.lower() in ("exit", "quit", "eof"):
                    break
                cli._last_emit_success = True
                cli._last_emit_payload = None
                cli.onecmd(cmd_line)
                if cli._last_emit_success is False:
                    prev_err = ""
                    if isinstance(cli._last_emit_payload, dict):
                        prev_err = cli._last_emit_payload.get("error") or cli._last_emit_payload.get("message") or ""
                    cli._emit(
                        False,
                        f"Batch script aborted at line {line_no}: {cmd_line}",
                        error=prev_err or f"Command failed at line {line_no}: {cmd_line}",
                        failed_line=line_no,
                        failed_command=cmd_line,
                        script_file=script_file,
                    )
                    return 1
            return 0

        # 2. Single command or --help on command line
        if argv:
            first_arg = PhonTracerCLI._clean_bom_and_whitespace(argv[0]).lower()
            if first_arg in ("-h", "--help", "help", "?"):
                if len(argv) > 1:
                    cli.do_help(" ".join(argv[1:]))
                else:
                    cli.do_help("")
                return 0
            if first_arg in ("-v", "--version", "version"):
                print(f"{APP_NAME} CLI {__version__}")
                return 0
            if not first_arg.startswith("-"):
                if len(argv) == 1 and " " in argv[0]:
                    command_line = PhonTracerCLI._clean_bom_and_whitespace(argv[0])
                else:
                    cleaned_tokens = [PhonTracerCLI._clean_bom_and_whitespace(a) for a in argv]
                    command_line = shlex.join(cleaned_tokens)
                cli.onecmd(command_line)
                return 0
            print(json.dumps({
                "success": False,
                "error": f"Unknown startup option: {argv[0]}",
                "hint": "Use --help for manual, or pass a CLI command directly (e.g. `PhonTracerCLI.exe status`)."
            }))
            return 2

        # 3. Non-interactive stdin pipe mode (pure JSON stdout without banner/prompts)
        if not is_stdin_tty or quiet_mode or json_mode:
            cli._defer_persistence = True
            for raw_line in sys.stdin:
                cmd_line = PhonTracerCLI._clean_bom_and_whitespace(raw_line)
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                if cmd_line.lower() in ("exit", "quit", "eof"):
                    break
                stop = cli.onecmd(cmd_line)
                if stop:
                    break
            return 0

        # 4. Interactive TTY REPL mode
        cli.cmdloop()
        return 0
    finally:
        cli.cleanup_workspace()


if __name__ == '__main__':
    raise SystemExit(main())
