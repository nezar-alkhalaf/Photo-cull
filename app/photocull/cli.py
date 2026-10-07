"""Command line: python -m photocull init|cull|edit|split|all|doctor"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config
from .logsetup import setup_logging
from .paths import CoverageLayout, SafetyError, preflight

NOT_YET = {
    "doctor": "Stage 6",
}
HELP = {
    "init": "create the output folders inside a coverage folder",
    "cull": "step 1: pick the best frame of each burst",
    "edit": "step 2: exposure fix + Photoshop style",
    "split": "step 3: split photos into Instagram post folders",
    "all": "run steps 1, 2 and 3 in order",
    "gui": "open the PhotoCull window",
    "doctor": "check that this PC is set up correctly",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="photocull", description="PhotoCull command line.")
    p.add_argument("--version", action="version", version=f"photocull {__version__}")
    p.add_argument("--config", help="path to an alternate config.toml")
    sub = p.add_subparsers(dest="command", metavar="command")
    for name, text in HELP.items():
        sp = sub.add_parser(name, help=text, description=text)
        if name not in ("doctor", "gui"):
            sp.add_argument("folder", help="main coverage folder (contains Full_Coverage)")
        if name in ("cull", "edit", "split"):
            sp.add_argument("--input", help="override the input folder for this step")
        if name == "cull":
            sp.add_argument("--mode", choices=["portrait", "action"], default="portrait",
                            help="portrait/class (eyes matter) or action/match (sharpness matters)")
            sp.add_argument("--existing", choices=["ask", "clear"], default="ask",
                            help="what to do if Selected/Alternates already have photos")
        if name == "all":
            sp.add_argument("--mode", choices=["portrait", "action"], default="portrait")
            sp.add_argument("--style", help="style .psd (file name in Styles/ or a full path)")
            sp.add_argument("--action", help='optional action after the style: "Set name/Action name"')
            exp = sp.add_mutually_exclusive_group()
            exp.add_argument("--exposure", dest="exposure", action="store_const", const=True)
            exp.add_argument("--no-exposure", dest="exposure", action="store_const", const=False)
            sp.add_argument("--max-per-post", type=int, help="max photos per post")
            sp.add_argument("--replace", action="store_true",
                            help="replace earlier picks / edits / posts instead of stopping")
        if name == "edit":
            sp.add_argument("--style", help="style .psd (file name in Styles/ or a full path)")
            sp.add_argument("--action", help='optional action after the style: "Set name/Action name"')
            exp = sp.add_mutually_exclusive_group()
            exp.add_argument("--exposure", dest="exposure", action="store_const", const=True,
                             help="fix exposure before Photoshop (good for dark match photos)")
            exp.add_argument("--no-exposure", dest="exposure", action="store_const", const=False,
                             help="send photos to Photoshop as shot (good for studio)")
            sp.add_argument("--preview", type=int, default=0, metavar="N",
                            help="edit only N photos, then stop so you can check them")
            sp.add_argument("--existing", choices=["ask", "reprocess"], default="ask",
                            help="what to do if photos were edited before with other settings")
        if name == "split":
            sp.add_argument("--max-per-post", type=int, help="max photos per post (default from config)")
            sp.add_argument("--existing", choices=["ask", "clear", "new"], default="ask",
                            help="what to do if Posts/ already has Post_N folders")
    return p


def _utf8_output() -> None:
    """Arabic folder names must not crash print() when output is redirected (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "init":
        try:
            layout = CoverageLayout.for_main(args.folder)
            problems = preflight(layout)
            if problems:
                print("Cannot continue:\n  " + "\n  ".join(problems), file=sys.stderr)
                return 1
            layout.ensure()
        except SafetyError as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 1
        setup_logging(layout.log, console=True).info("Initialised %s", layout.main)
        return 0

    if args.command == "gui":
        from .gui.app import main as gui_main
        return gui_main(cfg)
    if args.command == "split":
        return _cmd_split(args, cfg)
    if args.command == "all":
        return _cmd_all(args, cfg)
    if args.command == "cull":
        return _cmd_cull(args, cfg)
    if args.command == "edit":
        return _cmd_edit(args, cfg)

    print(f"'{args.command}' is not implemented yet (planned for {NOT_YET[args.command]}).",
          file=sys.stderr)
    return 3


def _print_progress(done: int, total: int, name: str) -> None:
    if name and (done % 25 == 0 or done == total - 1):
        print(f"  {done + 1}/{total} {name}", flush=True)


def _cmd_cull(args, cfg) -> int:
    from .cull import run_cull

    layout = CoverageLayout.for_main(args.folder)
    if not layout.main.is_dir():
        print(f"Main folder does not exist: {layout.main}", file=sys.stderr)
        return 1
    setup_logging(layout.log, console=False)
    res = run_cull(layout, cfg, args.mode, args.input, args.existing,
                   progress_cb=_print_progress, log_cb=lambda m: print(m))
    print(res.message, file=sys.stderr if not res.ok else sys.stdout)
    if res.data.get("needs_decision"):
        print("Re-run with --existing clear to replace them with the new picks.",
              file=sys.stderr)
    elif res.ok:
        print(f"Report: {layout.report}")
    return 0 if res.ok else 1


def _cmd_edit(args, cfg) -> int:
    from .edit import parse_action, run_edit
    from .styles import list_styles

    layout = CoverageLayout.for_main(args.folder)
    if not layout.main.is_dir():
        print(f"Main folder does not exist: {layout.main}", file=sys.stderr)
        return 1
    if not (args.style or cfg.photoshop.default_style_file):
        names = [p.name for p in list_styles(cfg)]
        print("Choose a style with --style. In the Styles folder: "
              + (", ".join(names) if names else "(no .psd files yet)"), file=sys.stderr)
        return 1
    try:
        action = parse_action(args.action or cfg.photoshop.default_action)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    setup_logging(layout.log, console=False)
    print("Connecting to Photoshop (can take 30 s if it is closed)...", flush=True)
    res = run_edit(layout, cfg, args.style, action, args.input,
                   args.exposure, args.preview, args.existing,
                   progress_cb=_print_progress, log_cb=lambda m: print(m))
    print(res.message, file=sys.stdout if res.ok or res.data.get("preview_done") else sys.stderr)
    if res.data.get("needs_decision"):
        print("Re-run with --existing reprocess to edit them again with the new settings.",
              file=sys.stderr)
    return 0 if res.ok or res.data.get("preview_done") else 1


def _cmd_split(args, cfg) -> int:
    from .split import run_split

    layout = CoverageLayout.for_main(args.folder)
    setup_logging(layout.log, console=False)
    res = run_split(layout, args.max_per_post or cfg.split.max_per_post,
                    args.input, args.existing, log_cb=lambda m: print(m))
    print(res.message, file=sys.stderr if not res.ok else sys.stdout)
    if res.data.get("needs_decision"):
        print("Re-run with --existing clear (delete old Post_N folders) "
              "or --existing new (add new numbered folders).", file=sys.stderr)
    return 0 if res.ok else 1


def _cmd_all(args, cfg) -> int:
    from .edit import parse_action
    from .pipeline import RunParams, run_all
    from .styles import list_styles

    layout = CoverageLayout.for_main(args.folder)
    if not layout.main.is_dir():
        print(f"Main folder does not exist: {layout.main}", file=sys.stderr)
        return 1
    if not (args.style or cfg.photoshop.default_style_file):
        names = [p.name for p in list_styles(cfg)]
        print("Choose a style with --style. In the Styles folder: "
              + (", ".join(names) if names else "(no .psd files yet)"), file=sys.stderr)
        return 1
    try:
        action = parse_action(args.action or cfg.photoshop.default_action)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    setup_logging(layout.log, console=False)
    params = RunParams(args.mode, args.style, action, args.exposure, 0,
                       args.max_per_post or cfg.split.max_per_post)
    replace = (lambda step, res, choices: choices[0]) if args.replace else None
    res = run_all(layout, cfg, params, _print_progress, lambda m: print(m),
                  decide=replace, confirm_preview=lambda r: True,
                  on_step=lambda s, what, r: print(f"== {s}: {what}", flush=True))
    print(res.message, file=sys.stdout if res.ok else sys.stderr)
    if res.data.get("needs_decision") or res.data.get("declined"):
        print("Re-run with --replace to replace the earlier results.", file=sys.stderr)
    return 0 if res.ok else 1
