"""`mcp-core-council`: the one command a caller installs.

    mcp-core-council                 the MCP stdio server (what Claude Code / Codex launch)
    mcp-core-council request ...     ask for an access token; it arrives by email
    mcp-core-council login TOKEN     check the token from the email and save it
    mcp-core-council init            write council-local.json from the CLIs found on PATH
    mcp-core-council seats [--sync]  list the seats; --sync refreshes the auto ones now

Standard library only: runs on macOS, Linux and Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

from . import __version__, access, council_local, seats

INSTALL = "uvx --from git+https://github.com/focalwellos/mcp-core-council mcp-core-council"

# A seat the caller writes by hand, per CLI, for when its model list cannot be read.
MANUAL_CLIS = {"codex": '{"label": "codex:<model>", "cli": "codex", "model": "<model>"}',
               "agy": '{"label": "agy:<model>", "cli": "agy", "model": "<model>"}  (see `agy models`)'}


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


def default_config(found: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"seats": found, "max_parallel": 2, "timeout_seconds": 300}


def print_catalog(catalog: Dict[str, Any]) -> None:
    """The CLIs the council knows, whether each is on this machine, and how to get the others."""
    print("\nCLIs the council knows (catalog %s):" % catalog.get("version"))
    for cli, info in (catalog.get("clis") or {}).items():
        where = shutil.which(cli)
        if info.get("status") != "supported":
            state = "planned, no runner yet" + (" (installed)" if where else "")
        else:
            state = where or "not installed: %s" % info.get("install")
        print("  %-7s %-10s %s" % (cli, info.get("family", ""), state))


def warn_single_family(configured: List[Dict[str, Any]], catalog: Dict[str, Any]) -> None:
    """A council needs two vendors to be one; say so when the seats cover one."""
    families = sorted({seats.family_of(seat, catalog) for seat in configured})
    if configured and len(families) < 2:
        print("\nWarning: every seat is %s. Roles will all be answered by one vendor's models, so the council "
              "is not independent. Install and log in to a second CLI (claude, agy), then run "
              "`mcp-core-council seats --sync`." % families[0])


def cmd_init(args: argparse.Namespace) -> int:
    path = council_local.config_path()
    catalog = seats.built_in_catalog()
    runnable = tuple(council_local.EXECUTORS)
    for cli in runnable:
        print("%-7s %s" % (cli, shutil.which(cli) or "not found"))
    if os.path.exists(path) and not args.force:
        print("\n%s already exists; left as is (--force rewrites it)." % path)
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = {}
        new, _ = seats.discover(existing if isinstance(existing, dict) else {}, catalog, runnable)
        have = {s.get("label") for s in existing.get("seats") or [] if isinstance(s, dict)} if isinstance(existing, dict) else set()
        new = [s for s in new if s["label"] not in have]
        if new:
            print("Seats found on this machine that it lacks (`mcp-core-council seats --sync` adds them):")
            for seat in new:
                print("  " + seat["label"])
        missing = {}
    else:
        found, notes = seats.discover({}, catalog, runnable)
        missing = {note.split(":", 1)[0]: note.split(":", 1)[1].strip() for note in notes}
        if not found:
            print("\nNo claude, codex or agy seat found: add your seats to %s by hand." % path)
        seats.write_json(path, default_config(found))
        print("\nWrote %s with %d seat(s): %s" % (path, len(found), ", ".join(s["label"] for s in found) or "none"))
        warn_single_family(found, catalog)
    for cli, reason in missing.items():
        print("No %s seat: %s. To add one by hand, put this line in \"seats\":" % (cli, reason))
        print("  " + MANUAL_CLIS.get(cli, '{"label": "%s:<model>", "cli": "%s", "model": "<model>"}' % (cli, cli)))
    print_catalog(catalog)
    print("\nRegister with Claude Code (once, for every project):")
    print("  claude mcp add council-local -s user -- " + INSTALL)
    if not access.auth_token():
        print("\nNo access token yet: run `mcp-core-council request --name ... --email ...`,")
        print("then `mcp-core-council login <token>` with the token from the email.")
    return 0


def cmd_seats(args: argparse.Namespace) -> int:
    path = council_local.config_path()
    if args.sync:
        report = seats.sync(path, council_local.call_remote, tuple(council_local.EXECUTORS))
        print("Synced %s (catalog %s from %s): %d auto seat(s)." % (
            path, report["catalog_version"], report["catalog_source"], report["auto_seats"]))
        for label in report["added"]:
            print("  + " + label)
        for label in report["removed"]:
            print("  - " + label)
        for note in report["notes"]:
            print("  note: " + note)
    config = council_local.load_config(path)
    catalog = seats.cached_catalog(path)
    print("\n%-40s %-10s %-7s %s" % ("seat", "family", "tier", "source"))
    for seat in config["seats"]:
        print("%-40s %-10s %-7s %s" % (seat["label"], seats.family_of(seat, catalog), seats.tier_of(seat, catalog),
                                      "auto" if seat.get("auto") else "by hand"))
    print("\nSent to council_plan: " + ", ".join(
        seats.plan_labels(config["seats"], catalog, council_local.PLAN_MODELS_MAX)))
    warn_single_family(config["seats"], catalog)
    print_catalog(catalog)
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
    seat_list = sub.add_parser("seats", help="list the seats; --sync updates the auto seats now")
    seat_list.add_argument("--sync", action="store_true", help="refresh the auto seats from the catalog and the CLIs")
    seat_list.set_defaults(run=cmd_seats)
    sub.add_parser("serve", help="the MCP stdio server (the default)")
    args = parser.parse_args(argv)
    try:
        code = args.run(args)
    except (access.AccessError, OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
