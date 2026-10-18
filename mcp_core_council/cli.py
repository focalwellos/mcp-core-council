"""`mcp-core-council`: the one command a caller installs.

    mcp-core-council                 the MCP stdio server (what Claude Code / Codex launch)
    mcp-core-council request ...     ask the server owner for an access token
    mcp-core-council status          check the request; once approved, save the token
    mcp-core-council init            write council-local.json from the CLIs found on PATH

Standard library only: runs on macOS, Linux and Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

from . import __version__, access, council_local

INSTALL = "uvx --from git+https://github.com/focalwellos/mcp-core-council mcp-core-council"

# Seats `init` writes for each CLI found. claude takes model aliases, so they never go stale.
# codex and agy model ids depend on the account: `init` names the CLI and leaves the seat to
# the caller (`agy models` lists what a Google account can use).
CLAUDE_SEATS = [{"label": "claude:sonnet", "cli": "claude", "model": "sonnet"},
                {"label": "claude:haiku", "cli": "claude", "model": "haiku"}]
MANUAL_CLIS = {"codex": '{"label": "codex:<model>", "cli": "codex", "model": "<model>"}',
               "agy": '{"label": "agy:<model>", "cli": "agy", "model": "<model>"}  (see `agy models`)'}


def cmd_request(args: argparse.Namespace) -> int:
    saved = access.load_access()
    if saved.get("status") in ("pending", "active") and not args.force:
        print("A request is already %s (id %s). Run `mcp-core-council status`, or add --force to "
              "send a new one (it replaces the saved token)." % (saved["status"], saved.get("request_id")))
        return 1
    reply = access.request_access(args.name, args.email, args.note or "", args.url)
    print("Request sent (id %s). The owner reviews it by hand." % reply["request_id"])
    print("Check it later with: mcp-core-council status")
    print("Saved in %s; keep that file private." % access.access_path())
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    saved = access.check_status()
    status = saved.get("status")
    if status == "active":
        print("Access active: %s, expires %s." % (saved.get("label"), saved.get("expires_at") or "never"))
        print("Token saved in %s; the council uses it on its own." % access.access_path())
        return 0
    messages = {"pending": "Still pending: the owner has not reviewed it yet.",
                "denied": "The request was declined.",
                "expired": "The request expired before review. Send a new one with --force.",
                "claimed": "This request's token was already collected. Ask the owner for a new one."}
    print(messages.get(str(status), "Status: %s" % status))
    return 0 if status == "pending" else 1


def default_config(found: Dict[str, Optional[str]]) -> Dict[str, Any]:
    seats: List[Dict[str, str]] = list(CLAUDE_SEATS) if found.get("claude") else []
    return {"seats": seats, "max_parallel": 2, "timeout_seconds": 300}


def cmd_init(args: argparse.Namespace) -> int:
    path = council_local.config_path()
    found = {cli: shutil.which(cli) for cli in ("claude", "codex", "agy")}
    for cli, where in found.items():
        print("%-7s %s" % (cli, where or "not found"))
    if os.path.exists(path) and not args.force:
        print("\n%s already exists; left as is (--force rewrites it)." % path)
    else:
        config = default_config(found)
        if not config["seats"]:
            print("\nNo claude CLI on PATH: add your seats to %s by hand." % path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        print("\nWrote %s with %d seat(s)." % (path, len(config["seats"])))
    manual = [cli for cli in MANUAL_CLIS if found.get(cli)]
    if manual:
        print("To add a seat for %s, put one line per model in \"seats\":" % ", ".join(manual))
        for cli in manual:
            print("  " + MANUAL_CLIS[cli])
    print("\nRegister with Claude Code (once, for every project):")
    print("  claude mcp add council-local -s user -- " + INSTALL)
    if not access.auth_token():
        print("\nNo access token yet: run `mcp-core-council request --name ... --email ...`.")
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
    request.add_argument("--force", action="store_true", help="send a new request even if one is pending")
    request.set_defaults(run=cmd_request)
    sub.add_parser("status", help="check the request and save the token once approved").set_defaults(run=cmd_status)
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
