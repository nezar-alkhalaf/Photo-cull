"""Command line: python -m photocull init|cull|edit|split|all|doctor"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config
from .logsetup import setup_logging
from .paths import CoverageLayout, SafetyError, preflight

NOT_YET = {
    "cull": "Stage 3",
    "edit": "Stage 4",
    "all": "Stage 5",
    "doctor": "Stage 6",
}
HELP = {
    "init": "create the output folders inside a coverage folder",
    "cull": "step 1: pick the best frame of each burst",
    "edit": "step 2: exposure fix + Photoshop style",
    "split": "step 3: split photos into Instagram post folders",
    "all": "run steps 1, 2 and 3 in order",
    "doctor": "check that this PC is set up correctly",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="photocull", description="PhotoCull command line.")
    p.add_argument("--version", action="version", version=f"photocull {__version__}")
    p.add_argument("--config", help="path to an alternate config.toml")
    sub = p.add_subparsers(dest="command", metavar="command")
    for name, text in HELP.items():
        sp = sub.add_parser(name, help=text, description=text)
        if name != "doctor":
            sp.add_argument("folder", help="main coverage folder (contains Full_Coverage)")
        if name in ("cull", "edit", "split"):
            sp.add_argument("--input", help="override the input folder for this step")
        if name == "split":
            sp.add_argument("--max-per-post", type=int, help="max photos per post (default from config)")
            sp.add_argument("--existing", choices=["ask", "clear", "new"], default="ask",
                            help="what to do if Posts/ already has Post_N folders")
    return p


def main(argv: list[str] | None = None) -> int:
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

    if args.command == "split":
        return _cmd_split(args, cfg)

    print(f"'{args.command}' is not implemented yet (planned for {NOT_YET[args.command]}).",
          file=sys.stderr)
    return 3


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
