"""`mcp-core-council`: the one command a caller installs.

    mcp-core-council                 the MCP stdio server (what Claude Code / Codex launch)
    mcp-core-council request ...     ask for an access token; it arrives by email
    mcp-core-council login TOKEN     check the token from the email and save it
    mcp-core-council init            write council-local.json from the CLIs found on PATH

Standard library only: runs on macOS, Linux and Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, access, council_local

INSTALL = "uvx --from git+https://github.com/focalwellos/mcp-core-council mcp-core-council"

# Seats `init` writes for each CLI found. claude takes model aliases, so they never go stale.
# codex and agy model ids depend on the account: codex's comes from its config.toml, agy's
# from `agy models`. When neither answers, `init` prints the line for the caller to fill in.
CLAUDE_SEATS = [{"label": "claude:sonnet", "cli": "claude", "model": "sonnet"},
                {"label": "claude:haiku", "cli": "claude", "model": "haiku"}]
MANUAL_CLIS = {"codex": '{"label": "codex:<model>", "cli": "codex", "model": "<model>"}',
               "agy": '{"label": "agy:<model>", "cli": "agy", "model": "<model>"}  (see `agy models`)'}
CODEX_MODEL_RE = re.compile(r'^[ \t]*model[ \t]*=[ \t]*"([^"]+)"', re.MULTILINE)
AGY_MODELS_TIMEOUT = 60


def cmd_request(args: argparse.Namespace) -> int:
    reply = access.request_access(args.name, args.email, args.note or "", args.url)
    if reply["status"] == "sent":
        print("Token sent to %s. Check your inbox (and spam), then run:" % reply["email"])
    else:
        print("Request received. Once approved, the token is mailed to %s. Then run:" % reply["email"])
    print("  mcp-core-council login <token>")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    saved = access.login(args.token, args.url)
    print("Token accepted by %s and saved in %s." % (saved["url"], access.access_path()))
    print("Next: mcp-core-council init")
    return 0


def codex_model() -> Optional[str]:
    """The model codex runs by default: the top-level `model` of its config.toml."""
    home = os.environ.get("CODEX_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".codex")
    try:
        with open(os.path.join(home, "config.toml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    top = re.split(r"^[ \t]*\[", text, maxsplit=1, flags=re.MULTILINE)[0]    # before the first [table]
    match = CODEX_MODEL_RE.search(top)
    return match.group(1) if match else None


def agy_model(binary: str) -> Optional[str]:
    """The first Gemini model `agy models` lists (lines are `id<TAB>name`). None when the command
    fails, which most often means agy is not logged in yet. agy also lists other vendors' models;
    they are skipped so the seat adds a family the claude seats do not cover."""
    try:
        run = subprocess.run([binary, "models"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=AGY_MODELS_TIMEOUT, text=True,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if run.returncode != 0:
        return None
    ids = [line.split("\t", 1)[0].strip() for line in (run.stdout or "").splitlines() if "\t" in line]
    return next((i for i in ids if i.startswith("gemini")), None)


def detect_seats(found: Dict[str, Optional[str]]) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """Seats for the CLIs found, and for each CLI whose model could not be found, why."""
    seats: List[Dict[str, str]] = list(CLAUDE_SEATS) if found.get("claude") else []
    missing: Dict[str, str] = {}
    if found.get("codex"):
        model = codex_model()
        if model:
            seats.append({"label": ("codex:" + model)[:council_local.LABEL_MAX], "cli": "codex", "model": model})
        else:
            missing["codex"] = "no `model` in codex's config.toml"
    if found.get("agy"):
        model = agy_model(found["agy"])
        if model:
            seats.append({"label": ("agy:" + model)[:council_local.LABEL_MAX], "cli": "agy", "model": model})
        else:
            missing["agy"] = "`agy models` listed no Gemini model; run `agy` once and log in"
    return seats, missing


def default_config(seats: List[Dict[str, str]]) -> Dict[str, Any]:
    return {"seats": seats, "max_parallel": 2, "timeout_seconds": 300}


def cmd_init(args: argparse.Namespace) -> int:
    path = council_local.config_path()
    found = {cli: shutil.which(cli) for cli in ("claude", "codex", "agy")}
    for cli, where in found.items():
        print("%-7s %s" % (cli, where or "not found"))
    seats, missing = detect_seats(found)
    if os.path.exists(path) and not args.force:
        print("\n%s already exists; left as is (--force rewrites it)." % path)
        try:
            with open(path, encoding="utf-8") as f:
                have = {s.get("label") for s in json.load(f).get("seats") or [] if isinstance(s, dict)}
        except (OSError, ValueError, AttributeError):
            have = set()
        new = [s for s in seats if s["label"] not in have]
        if new:
            print("Seats found on this machine that it lacks (add them to \"seats\", or run init --force):")
            for seat in new:
                print("  " + json.dumps(seat))
    else:
        if not seats:
            print("\nNo claude, codex or agy seat found: add your seats to %s by hand." % path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_config(seats), f, indent=2)
        print("\nWrote %s with %d seat(s): %s" % (path, len(seats), ", ".join(s["label"] for s in seats) or "none"))
    for cli, reason in missing.items():
        print("No %s seat: %s. To add one by hand, put this line in \"seats\":" % (cli, reason))
        print("  " + MANUAL_CLIS[cli])
    print("\nRegister with Claude Code (once, for every project):")
    print("  claude mcp add council-local -s user -- " + INSTALL)
    if not access.auth_token():
        print("\nNo access token yet: run `mcp-core-council request --name ... --email ...`,")
        print("then `mcp-core-council login <token>` with the token from the email.")
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "serve":
        council_local.main()
        return
    parser = argparse.ArgumentParser(prog="mcp-core-council", description="MCP-core council client %s" % __version__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="ask for an access token")
    request.add_argument("--name", required=True)
    request.add_argument("--email", required=True)
    request.add_argument("--note", default="", help="what you will use it for (optional)")
    request.add_argument("--url", default=None, help="server URL (default: the public instance)")
    request.set_defaults(run=cmd_request)
    login = sub.add_parser("login", help="check the token from the email and save it")
    login.add_argument("token")
    login.add_argument("--url", default=None, help="server URL (default: the public instance)")
    login.set_defaults(run=cmd_login)
    init = sub.add_parser("init", help="write council-local.json from the CLIs on PATH")
    init.add_argument("--force", action="store_true", help="rewrite an existing council-local.json")
    init.set_defaults(run=cmd_init)
    sub.add_parser("serve", help="the MCP stdio server (the default)")
    args = parser.parse_args(argv)
    try:
        code = args.run(args)
    except access.AccessError as error:
        print("error: %s" % error, file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
