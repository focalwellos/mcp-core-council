"""Seats of the council client: discovery from the CLIs on this machine, and sync.

A seat is one CLI account and one model. MCP-core serves a catalog (`council_seats`): model
families, tier rules, and every CLI the council knows, including ones the client cannot run
yet. Each CLI lists the models of its own account; `sync` turns those lists into seats marked
`"auto": true` in council-local.json and leaves every seat written by hand as it is. Models
come and go with each CLI release, so `council_run` syncs once a day (`sync_hours`).

Nothing here sends a prompt, a context or an answer anywhere: only `council_seats` is called.
Standard library only: runs on macOS, Linux and Windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

LABEL_MAX = 64
LIST_TIMEOUT = 60
DEFAULT_SYNC_HOURS = 24
BUILT_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "council-seats.json")
ACCOUNT_KEYS = ("config_dir", "command")
CODEX_MODEL_RE = re.compile(r'^[ \t]*model[ \t]*=[ \t]*"([^"]+)"', re.MULTILINE)


# --------------------------------------------------------------------------- catalog

def valid_catalog(value: Any) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("families"), list)
            and isinstance(value.get("tiers"), list) and isinstance(value.get("clis"), dict))


def built_in_catalog() -> Dict[str, Any]:
    with open(BUILT_IN, encoding="utf-8") as f:
        return json.load(f)


def cache_path(config_path: str) -> str:
    return os.path.join(os.path.dirname(config_path) or ".", "council-seats-cache.json")


def read_cache(config_path: str) -> Dict[str, Any]:
    try:
        with open(cache_path(config_path), encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_catalog(config_path: str) -> Dict[str, Any]:
    """The catalog of the last sync, else the one this package ships."""
    catalog = read_cache(config_path).get("catalog")
    return catalog if valid_catalog(catalog) else built_in_catalog()


def fetch_catalog(call_remote: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                  config_path: str) -> Tuple[Dict[str, Any], str]:
    """The server's catalog, else the cached one, else the built-in one; and where it came from.
    A server older than 0.7.0 has no council_seats tool: the client still syncs from its own."""
    try:
        catalog = call_remote("council_seats", {})
        if valid_catalog(catalog):
            return catalog, "server"
    except Exception:
        pass
    catalog = read_cache(config_path).get("catalog")
    if valid_catalog(catalog):
        return catalog, "cache"
    return built_in_catalog(), "built-in"


def bare(model: str) -> str:
    return re.split(r"[:/]", model)[-1].strip().lower()


def _first_match(rules: List[Any], model: str, key: str) -> Optional[str]:
    for rule in rules:
        try:
            if isinstance(rule, dict) and re.search(str(rule.get("match") or "$^"), model):
                return str(rule.get(key) or "") or None
        except re.error:            # a bad rule in a served catalog must not stop a round
            continue
    return None


def family_of(seat: Dict[str, Any], catalog: Dict[str, Any]) -> str:
    """The model's vendor family; the CLI's family when no rule knows the model."""
    found = _first_match(catalog.get("families") or [], bare(seat.get("model") or ""), "family")
    cli = (catalog.get("clis") or {}).get(seat.get("cli")) or {}
    return found or str(cli.get("family") or seat.get("cli") or "unknown")


def tier_of(seat: Dict[str, Any], catalog: Dict[str, Any]) -> str:
    found = _first_match(catalog.get("tiers") or [], bare(seat.get("model") or ""), "tier")
    return found or str(catalog.get("default_tier") or "strong")


def plan_labels(seats: List[Dict[str, Any]], catalog: Dict[str, Any], limit: int) -> List[str]:
    """At most `limit` labels for council_plan, spread over families and both tiers: each pass
    takes the next seat of every (tier, family), so a long list of one vendor's models cannot
    crowd out the others. Within a group config order holds, so hand-written seats go first."""
    groups: Dict[Tuple[str, str], List[str]] = {}
    families: List[str] = []
    for seat in seats:
        fam = family_of(seat, catalog)
        if fam not in families:
            families.append(fam)
        groups.setdefault((tier_of(seat, catalog), fam), []).append(seat["label"][:LABEL_MAX])
    out: List[str] = []
    depth = max((len(g) for g in groups.values()), default=0)
    for i in range(depth):
        for tier in ("strong", "fast"):
            for fam in families:
                group = groups.get((tier, fam)) or []
                if i < len(group) and group[i] not in out and len(out) < limit:
                    out.append(group[i])
    return out


# --------------------------------------------------------------------------- discovery

def account_of(seat: Dict[str, Any]) -> Dict[str, str]:
    return {k: seat[k] for k in ACCOUNT_KEYS if seat.get(k)}


def account_tag(cli: str, account: Dict[str, str]) -> str:
    """The label prefix of an account: `claude` for the default one, else the name of its
    login directory or command, e.g. ~/.claude-claude-2 -> claude-2, ~/.codex-work -> codex-work."""
    where = account.get("config_dir") or account.get("command")
    if not where:
        return cli
    name = os.path.basename(os.path.normpath(os.path.expanduser(where))).lstrip(".")
    if name.startswith("%s-%s-" % (cli, cli)):
        name = name[len(cli) + 1:]
    if name.endswith((".cmd", ".exe")):
        name = name[:-4]
    return name if name.startswith(cli) else "%s-%s" % (cli, name)


def accounts(config: Dict[str, Any], catalog: Dict[str, Any], runnable: Tuple[str, ...],
             which: Callable[[str], Optional[str]]) -> List[Tuple[str, Dict[str, str]]]:
    """Accounts to list models for: every account a seat already uses, and the default account
    of a runnable CLI that has no seat yet but is on PATH. A CLI with seats never gains its
    default account by itself: it may be an account the caller keeps out of the council."""
    seats = config.get("seats") or []
    out: List[Tuple[str, Dict[str, str]]] = []
    for cli, info in (catalog.get("clis") or {}).items():
        if not isinstance(info, dict) or info.get("status") != "supported" or cli not in runnable:
            continue
        found: List[Dict[str, str]] = []
        for seat in seats:
            if isinstance(seat, dict) and seat.get("cli") == cli and account_of(seat) not in found:
                found.append(account_of(seat))
        if not found and which(cli):
            found = [{}]
        out += [(cli, account) for account in found]
    return out


def codex_home(account: Dict[str, str]) -> str:
    return os.path.expanduser(account.get("config_dir") or os.environ.get("CODEX_HOME", "").strip()
                              or os.path.join("~", ".codex"))


def codex_config_model(home: str) -> Optional[str]:
    """The model codex runs by default: the top-level `model` of its config.toml."""
    try:
        with open(os.path.join(home, "config.toml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    top = re.split(r"^[ \t]*\[", text, maxsplit=1, flags=re.MULTILINE)[0]    # before the first [table]
    match = CODEX_MODEL_RE.search(top)
    return match.group(1) if match else None


def list_models(cli: str, account: Dict[str, str], catalog: Dict[str, Any],
                which: Callable[[str], Optional[str]]) -> Tuple[List[str], Optional[str]]:
    """The models one account offers, best first as its CLI orders them, and why none if empty."""
    binary = which(account.get("command") or cli)
    if binary is None:
        return [], "%s not found on PATH" % (account.get("command") or cli)
    spec = ((catalog.get("clis") or {}).get(cli) or {}).get("discover") or {}
    if spec.get("aliases"):
        return [str(a) for a in spec["aliases"]], None
    if spec.get("models_cache"):
        home = codex_home(account)
        try:
            with open(os.path.join(home, str(spec["models_cache"])), encoding="utf-8") as f:
                listed = json.load(f).get("models") or []
            shown = [m for m in listed if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
            shown.sort(key=lambda m: m.get("priority") if isinstance(m.get("priority"), int) else 1 << 30)
            if shown:
                return [str(m["slug"]) for m in shown], None
        except (OSError, ValueError, AttributeError):
            pass
        model = codex_config_model(home)          # no cache yet: codex has not run on this account
        return ([model], None) if model else ([], "no models cache and no `model` in %s" % home)
    if spec.get("list_command"):
        try:
            run = subprocess.run([binary] + [str(a) for a in spec["list_command"]], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=LIST_TIMEOUT,
                                 text=True, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as error:
            return [], str(error)[:200]
        ids = [line.split("\t", 1)[0].strip() for line in (run.stdout or "").splitlines() if "\t" in line]
        if run.returncode != 0 or not ids:
            return [], "`%s %s` listed no model; run %s once and log in" % (cli, " ".join(spec["list_command"]), cli)
        return ids, None
    return [], "the catalog gives no way to list %s models" % cli


def discover(config: Dict[str, Any], catalog: Dict[str, Any], runnable: Tuple[str, ...],
             which: Callable[[str], Optional[str]] = shutil.which) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Auto seats for every model the accounts offer that no hand-written seat covers, and notes."""
    manual = [s for s in config.get("seats") or [] if isinstance(s, dict) and not s.get("auto")]
    covered = {(s.get("cli"), json.dumps(account_of(s), sort_keys=True), s.get("model")) for s in manual}
    labels = {s.get("label") for s in manual}
    exclude = [re.compile(p) for p in config.get("exclude") or [] if isinstance(p, str)]
    skip = re.compile(str(catalog.get("skip") or "$^"))
    seats: List[Dict[str, Any]] = []
    notes: List[str] = []
    for cli, account in accounts(config, catalog, runnable, which):
        models, error = list_models(cli, account, catalog, which)
        tag = account_tag(cli, account)
        if error:
            # A list that failed (offline, logged out for now) keeps the account's seats of the last sync.
            notes.append("%s: %s; its auto seats are kept" % (tag, error))
            kept = [s for s in config.get("seats") or [] if isinstance(s, dict) and s.get("auto")
                    and s.get("cli") == cli and account_of(s) == account and s.get("label") not in labels]
            labels.update(s["label"] for s in kept)
            seats += kept
            continue
        for model in models:
            label = ("%s:%s" % (tag, model))[:LABEL_MAX]
            key = (cli, json.dumps(account, sort_keys=True), model)
            if skip.search(model) or key in covered or label in labels or any(p.search(label) for p in exclude):
                continue
            labels.add(label)
            seat = dict({"label": label, "cli": cli, "model": model}, **account)
            seat.update({"family": family_of(seat, catalog), "tier": tier_of(seat, catalog), "auto": True})
            seats.append(seat)
    return seats, notes


def merged(config: Dict[str, Any], auto: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The config with its auto seats replaced: hand-written seats first, in their order."""
    manual = [s for s in config.get("seats") or [] if not (isinstance(s, dict) and s.get("auto"))]
    return dict(config, seats=manual + auto)


# --------------------------------------------------------------------------- sync

def now() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def due(config: Dict[str, Any], config_path: str) -> bool:
    if config.get("auto_sync") is False:
        return False
    try:
        hours = float(config["sync_hours"] if config.get("sync_hours") is not None else DEFAULT_SYNC_HOURS)
        last = datetime.fromisoformat(str(read_cache(config_path).get("synced_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return (now() - last).total_seconds() >= hours * 3600


def sync(config_path: str, call_remote: Callable[[str, Dict[str, Any]], Dict[str, Any]],
         runnable: Tuple[str, ...], which: Callable[[str], Optional[str]] = shutil.which) -> Dict[str, Any]:
    """Rewrite the auto seats of council-local.json from the catalog and the CLIs' model lists.
    Every other key and every hand-written seat stays as written."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    catalog, source = fetch_catalog(call_remote, config_path)
    auto, notes = discover(config, catalog, runnable, which)
    before = [s.get("label") for s in config.get("seats") or [] if isinstance(s, dict) and s.get("auto")]
    after = [s["label"] for s in auto]
    new = merged(config, auto)
    if new != config:
        write_json(config_path, new)
    stamp = now().isoformat(timespec="seconds").replace("+00:00", "Z")
    write_json(cache_path(config_path), {"synced_at": stamp, "catalog_source": source, "catalog": catalog})
    return {"synced_at": stamp, "catalog_source": source, "catalog_version": catalog.get("version"),
            "added": [l for l in after if l not in before], "removed": [l for l in before if l not in after],
            "auto_seats": len(auto), "notes": notes}


def sync_if_due(config_path: str, call_remote: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                runnable: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """sync() once `sync_hours` have passed. Never raises: a failed sync leaves the seats as they were."""
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict) or not due(config, config_path):
            return None
        return sync(config_path, call_remote, runnable)
    except Exception as error:
        return {"error": str(error)[:300]}
