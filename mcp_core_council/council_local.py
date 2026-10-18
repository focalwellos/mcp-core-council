#!/usr/bin/env python3
"""Local runner of the reference council (MCP-core docs/REFERENCE-COUNCIL.md, "Local runner").

An MCP stdio server the caller's client launches. Its one tool, `council_run`, asks the
remote MCP-core for a plan (`council_plan`), runs every seat as its own CLI process on the
caller's subscription, and reports metadata only (`council_report`). The question, the
context and the answers stay on this machine: no remote call carries them.

Standard library only, no fcntl: runs on macOS, Linux and Windows.

Environment: MCP_REMOTE_URL and MCP_AUTH_TOKEN (both optional: access.py falls back to the
saved access file and the public instance), MCP_COUNCIL_LOCAL_CONFIG (optional path that
overrides $XDG_CONFIG_HOME/mcp-core/council-local.json).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from . import __version__, access
except ImportError:  # executed as a plain script
    import access  # type: ignore
    __version__ = "0.0.0"

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "council-local"

QUOTA_RE = re.compile(r"quota|rate limit|usage limit|RESOURCE_EXHAUSTED|429", re.IGNORECASE)
STRONG_HINTS = ("opus", "sonnet", "pro", "sol", "high")
FAST_HINTS = ("haiku", "flash", "mini", "luna", "low")

DEFAULT_CONTEXT_MAX_CHARS = 40000
DEFAULT_ANSWER_MAX_CHARS = 4000
DEFAULT_TIMEOUT = 300
DEFAULT_PARALLEL = 3
LABEL_MAX = 64          # the remote refuses any string longer than this

# claude's system prompt: the seat answers as text and nothing else.
CLAUDE_SYSTEM = ("You are one independent seat of a review council. You have no tools. "
                 "Answer the user's message as plain text only.")

TOOL = {
    "name": "council_run",
    "description": ("Run a reference council on this machine: plan from MCP-core, run each seat as a local "
                    "CLI on your own subscription, report metadata only. The question, the context and the "
                    "answers never leave this machine. Results are advice (verified: false)."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "Kind of council, e.g. critique, verify, red-team, alternatives"},
            "question": {"type": "string", "description": "The question, any size"},
            "context": {"type": "string", "description": "Context for the seats, any size; cut per role"},
            "seats": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Number of seats (default: the kind's roles)"},
        },
        "required": ["kind", "question"],
        "additionalProperties": False,
    },
}


# --------------------------------------------------------------------------- config

def config_path() -> str:
    override = os.environ.get("MCP_COUNCIL_LOCAL_CONFIG", "").strip()
    if override:
        return override
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mcp-core", "council-local.json")


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or config_path()
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    seats = config.get("seats")
    if not isinstance(seats, list) or not seats:
        raise ValueError("%s: 'seats' must be a non-empty list" % path)
    for seat in seats:
        if not isinstance(seat, dict) or not all(isinstance(seat.get(k), str) and seat.get(k) for k in ("label", "cli", "model")):
            raise ValueError("%s: every seat needs string 'label', 'cli' and 'model'" % path)
    config.setdefault("max_parallel", DEFAULT_PARALLEL)
    config.setdefault("timeout_seconds", DEFAULT_TIMEOUT)
    return config


# --------------------------------------------------------------------------- remote

class RemoteError(Exception):
    pass


_rpc_ids = iter(range(1, 1 << 62))
_rpc_lock = threading.Lock()


def call_remote(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """tools/call on MCP-core. Arguments are labels and numbers only, never content."""
    url = access.remote_url()
    with _rpc_lock:
        rpc_id = next(_rpc_ids)
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode("utf-8")
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    token = access.auth_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        if error.code == 401:
            raise RemoteError("%s: no valid access token (missing, expired or revoked). Request one with "
                              "`mcp-core-council request`, then `mcp-core-council login <token>`" % tool)
        raise RemoteError("%s: HTTP %s %s" % (tool, error.code, error.read().decode("utf-8", "replace")[:300]))
    except URLError as error:
        raise RemoteError("%s: remote unavailable: %s" % (tool, error.reason))
    message = _parse_rpc_body(raw)
    if "error" in message:
        raise RemoteError("%s: %s" % (tool, (message["error"] or {}).get("message", message["error"])))
    result = message.get("result") or {}
    if result.get("isError"):
        raise RemoteError("%s: %s" % (tool, _result_text(result)[:500]))
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    text = _result_text(result)
    try:
        value = json.loads(text)
    except ValueError:
        raise RemoteError("%s: unexpected result %r" % (tool, text[:200]))
    if not isinstance(value, dict):
        raise RemoteError("%s: unexpected result %r" % (tool, text[:200]))
    return value


def _parse_rpc_body(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    for line in raw.splitlines():                       # an SSE reply: take the first data line
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RemoteError("unexpected remote reply %r" % raw[:200])


def _result_text(result: Dict[str, Any]) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))


# --------------------------------------------------------------------------- prompts

def cut_context(context: str, max_chars: int) -> str:
    if len(context) <= max_chars:
        return context
    return context[:max_chars] + "\n[context cut at %d chars]" % max_chars


def build_prompt(question: str, context: str, role_prompt: str, context_max_chars: int, answer_max_chars: int) -> str:
    """Shared block first (context, then question), the role last: seats share a cacheable prefix."""
    parts = []
    if context:
        parts.append("Context:\n" + cut_context(context, context_max_chars))
    parts.append("Question:\n" + question)
    shared = "\n\n".join(parts)
    # The role's template may name the placeholders; the content already sits in the shared block.
    role = (role_prompt or "").replace("{{context}}", "the context above").replace("{{question}}", "the question above")
    tail = ("Answer in at most %d characters. End with a line 'CLAIMS:' followed by one-line claims."
            % answer_max_chars)
    return shared + "\n\n---\n\n" + (role.strip() + "\n\n" if role.strip() else "") + tail


def extract_claims(answer: str) -> List[str]:
    """The one-line claims after the last 'CLAIMS:' line; what a follow-up round passes on."""
    lines = (answer or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("CLAIMS:"):
            start = i
    if start is None:
        return []
    claims = []
    first = lines[start].strip()[len("CLAIMS:"):].strip()
    if first:
        claims.append(first)
    claims += [line.strip() for line in lines[start + 1:] if line.strip()]
    return claims


# --------------------------------------------------------------------------- seat choice

def _tier_of(label: str, tier: str) -> bool:
    hints = STRONG_HINTS if tier == "strong" else FAST_HINTS if tier == "fast" else ()
    low = label.lower()
    return any(h in low for h in hints)


def pick_seat(suggested: Optional[str], configured: List[Dict[str, Any]], tier: Optional[str] = None) -> Dict[str, Any]:
    """Exact label, else the first configured seat whose tier fits, else the first configured."""
    suggested = suggested or ""
    for seat in configured:
        if seat["label"] == suggested:
            return seat
    want = suggested if suggested in ("strong", "fast") else (tier or "")
    for seat in configured:
        if want and (_tier_of(seat["label"], want) or _tier_of(seat["model"], want)):
            return seat
    return configured[0]


# --------------------------------------------------------------------------- executors

def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sum(*values: Any) -> Optional[int]:
    nums = [_int(v) for v in values]
    nums = [n for n in nums if n is not None]
    return sum(nums) if nums else None


def _tokens(input_tokens=None, output_tokens=None, cached_tokens=None) -> Dict[str, Optional[int]]:
    """One meaning for every CLI, as OpenTelemetry and prices.cost use it: input_tokens counts ALL
    input, cache reads and cache writes included; cached_tokens counts cache reads only;
    output_tokens counts all output, reasoning included."""
    return {"input_tokens": _int(input_tokens), "output_tokens": _int(output_tokens), "cached_tokens": _int(cached_tokens)}


def _run(argv: List[str], prompt: str, cwd: str, timeout: float):
    """Run one CLI. Returns (returncode, stdout, stderr) or raises subprocess.TimeoutExpired."""
    run = subprocess.run(argv, input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                         timeout=timeout, text=True, encoding="utf-8", errors="replace")
    return run.returncode, run.stdout or "", run.stderr or ""


def exec_claude(binary: str, model: str, prompt: str, cwd: str, timeout: float, scratch: str) -> Dict[str, Any]:
    argv = [binary, "-p", "--model", model, "--tools", "", "--system-prompt", CLAUDE_SYSTEM,
            "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
            "--exclude-dynamic-system-prompt-sections", "--output-format", "json", "--no-session-persistence"]
    code, out, err = _run(argv, prompt, cwd, timeout)
    answer, printed_model, tokens, is_error = "", "", _tokens(), False
    data = None
    try:
        data = json.loads(out.strip()) if out.strip() else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        answer = data.get("result") if isinstance(data.get("result"), str) else ""
        is_error = bool(data.get("is_error"))
        usage = data.get("usage") or {}
        # Anthropic's input_tokens leaves out cache reads and cache writes: add both back.
        # Its output_tokens already include thinking.
        tokens = _tokens(_sum(usage.get("input_tokens"), usage.get("cache_creation_input_tokens"),
                              usage.get("cache_read_input_tokens")),
                         usage.get("output_tokens"), usage.get("cache_read_input_tokens"))
        model_usage = data.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            printed_model = max(model_usage, key=lambda k: (model_usage[k] or {}).get("outputTokens", 0)
                                if isinstance(model_usage[k], dict) else 0)
    return {"code": 1 if is_error and code == 0 else code, "answer": answer, "model": printed_model,
            "tokens": tokens, "output": out + "\n" + err}


def _find_usage(value: Any, found: List[Dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if any(k in value for k in ("input_tokens", "output_tokens", "cached_input_tokens")):
            found.append(value)
        for v in value.values():
            _find_usage(v, found)
    elif isinstance(value, list):
        for v in value:
            _find_usage(v, found)


def _find_model(value: Any) -> str:
    if isinstance(value, dict):
        if isinstance(value.get("model"), str) and value["model"]:
            return value["model"]
        for v in value.values():
            m = _find_model(v)
            if m:
                return m
    elif isinstance(value, list):
        for v in value:
            m = _find_model(v)
            if m:
                return m
    return ""


def exec_codex(binary: str, model: str, prompt: str, cwd: str, timeout: float, scratch: str) -> Dict[str, Any]:
    last = os.path.join(scratch, "last-message.txt")
    argv = [binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-C", cwd, "-m", model,
            "--json", "-o", last, "-"]
    code, out, err = _run(argv, prompt, cwd, timeout)
    usages: List[Dict[str, Any]] = []
    printed_model = ""
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        _find_usage(event, usages)
        printed_model = printed_model or _find_model(event)
    tokens = _tokens()
    if usages:
        u = usages[-1]                                  # the last report is the turn's total
        # Codex (0.159.2, turn.completed) already counts cached input inside input_tokens. Its
        # output_tokens are taken as given: OpenAI usage counts reasoning inside output, and the
        # usage store reads codex logs the same way (total = input + output).
        tokens = _tokens(u.get("input_tokens"), u.get("output_tokens"), u.get("cached_input_tokens"))
    answer = ""
    try:
        with open(last, encoding="utf-8", errors="replace") as f:
            answer = f.read()
    except OSError:
        pass
    return {"code": code, "answer": answer, "model": printed_model, "tokens": tokens, "output": out + "\n" + err}


def agy_tokens(usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """agy's usage in the one meaning of _tokens.

    Measured on agy 1.2.16 (stream-json result event): total_tokens = input_tokens +
    output_tokens (+ thinking_tokens), so its input_tokens is taken to EXCLUDE cache reads and
    its output_tokens to exclude thinking: both are added back. TO CONFIRM against a real run
    with cache_read_tokens > 0; if that run shows input_tokens already includes the reads, drop
    the addition here. Nothing else depends on it."""
    return _tokens(_sum(usage.get("input_tokens"), usage.get("cache_read_tokens")),
                   _sum(usage.get("output_tokens"), usage.get("thinking_tokens")),
                   usage.get("cache_read_tokens"))


def exec_agy(binary: str, model: str, prompt: str, cwd: str, timeout: float, scratch: str) -> Dict[str, Any]:
    # As runners/agy/relay-agy on the host: plan mode with agy's own sandbox. The prompt goes on
    # stdin as one stream-json user message.
    argv = [binary, "--input-format", "stream-json", "--output-format", "stream-json",
            "--mode", "plan", "--sandbox", "--model", model]
    message = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
    code, out, err = _run(argv, message, cwd, timeout)
    usage: Dict[str, Any] = {}
    printed_model, response, text = "", None, []
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("usage:"):
            try:
                usage = json.loads(stripped[len("usage:"):].strip()) or {}
            except ValueError:
                pass
            continue
        if stripped.startswith("model:"):
            printed_model = stripped[len("model:"):].strip()
            continue
        try:
            event = json.loads(stripped)
        except ValueError:
            text.append(line)
            continue
        if not isinstance(event, dict):
            continue
        if event.get("event") == "init" and (event.get("init") or {}).get("model"):
            printed_model = printed_model or event["init"]["model"]
        elif event.get("event") == "result":
            result = event.get("result") or {}
            if isinstance(result.get("response"), str):
                response = result["response"]
            if isinstance(result.get("usage"), dict) and not usage:
                usage = result["usage"]
            if result.get("status") not in (None, "SUCCESS") and code == 0:
                code = 1
    answer = response if response is not None else "\n".join(text).strip()
    return {"code": code, "answer": answer, "model": printed_model, "tokens": agy_tokens(usage), "output": out + "\n" + err}


EXECUTORS = {"claude": exec_claude, "codex": exec_codex, "agy": exec_agy}


def run_seat(seat: Dict[str, Any], prompt: str, timeout: float) -> Dict[str, Any]:
    """Run one seat. Returns {status, answer, model, tokens}; never raises."""
    cli = seat["cli"]
    executor = EXECUTORS.get(cli)
    binary = shutil.which(seat.get("command") or cli)   # finds claude.cmd / codex.exe on Windows
    empty = {"answer": "", "model": seat["model"], "tokens": _tokens()}
    if executor is None or binary is None:
        reason = "unknown cli %r" % cli if executor is None else "%s not found on PATH" % cli
        return dict(empty, status="unavailable", error=reason)
    with tempfile.TemporaryDirectory(prefix="council-seat-") as cwd, \
            tempfile.TemporaryDirectory(prefix="council-out-") as scratch:
        try:
            result = executor(binary, seat["model"], prompt, cwd, timeout, scratch)
        except subprocess.TimeoutExpired:
            return dict(empty, status="unavailable", error="timeout after %ss" % timeout)
        except OSError as error:
            return dict(empty, status="unavailable", error=str(error)[:300])
    answer = (result.get("answer") or "").strip()
    out = {"answer": answer, "model": result.get("model") or seat["model"], "tokens": result["tokens"]}
    if result["code"] == 0 and answer:
        return dict(out, status="answered")
    if QUOTA_RE.search(result.get("output") or "") or QUOTA_RE.search(answer):
        return dict(out, status="no_quota", answer="")
    if result["code"] != 0:
        tail = (result.get("output") or "").strip()[-300:]
        return dict(out, status="unavailable", answer="", error="exit %s: %s" % (result["code"], tail))
    return dict(out, status="refused")


# --------------------------------------------------------------------------- council_run

def council_run(arguments: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    kind = arguments.get("kind")
    question = arguments.get("question")
    context = arguments.get("context") or ""
    if not isinstance(kind, str) or not kind:
        raise ValueError("'kind' is required")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("'question' is required")
    if not isinstance(context, str):
        raise ValueError("'context' must be a string")
    config = config or load_config()
    configured = config["seats"]

    plan_args: Dict[str, Any] = {"kind": kind, "models": [s["label"][:LABEL_MAX] for s in configured][:8]}
    if arguments.get("seats") is not None:
        plan_args["seats"] = int(arguments["seats"])
    plan = call_remote("council_plan", plan_args)
    planned = plan.get("seats") or []

    jobs = []
    for entry in planned:
        seat = pick_seat(entry.get("suggested_model"), configured, entry.get("tier"))
        prompt = build_prompt(question, context, entry.get("prompt") or "",
                              int(entry.get("context_max_chars") or DEFAULT_CONTEXT_MAX_CHARS),
                              int(entry.get("answer_max_chars") or DEFAULT_ANSWER_MAX_CHARS))
        jobs.append((entry, seat, prompt))

    timeout = float(config.get("timeout_seconds") or DEFAULT_TIMEOUT)
    workers = max(1, int(config.get("max_parallel") or DEFAULT_PARALLEL))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda job: run_seat(job[1], job[2], timeout), jobs))

    seats_out, report_seats = [], []
    for (entry, seat, _), outcome in zip(jobs, outcomes):
        # The length bound is a target the prompt states, not a cut: the answer comes back whole
        # and over_bound says whether the seat kept to it.
        bound = int(entry.get("answer_max_chars") or DEFAULT_ANSWER_MAX_CHARS)
        seats_out.append({"seat_id": entry.get("seat_id"), "role": entry.get("role"), "label": seat["label"],
                          "model": outcome["model"], "status": outcome["status"], "answer": outcome["answer"],
                          "over_bound": len(outcome["answer"]) > bound, "tokens": outcome["tokens"]})
        if outcome.get("error"):
            seats_out[-1]["error"] = outcome["error"]
        # Metadata only: never the answer, the question or the context.
        report_seats.append(dict({"seat_id": str(entry.get("seat_id")), "model": str(outcome["model"])[:LABEL_MAX],
                                  "status": outcome["status"], "answer_chars": len(outcome["answer"])},
                                 **outcome["tokens"]))
    result: Dict[str, Any] = {"round_id": plan.get("round_id"), "kind": plan.get("kind", kind),
                              "template_version": plan.get("template_version"),
                              "template_sha256": plan.get("template_sha256"), "seats": seats_out}
    try:
        result["report"] = call_remote("council_report", {"round_id": plan.get("round_id"), "seats": report_seats})
    except RemoteError as error:
        result["report"] = None
        result["report_error"] = str(error)
    return result


# --------------------------------------------------------------------------- stdio MCP

def _tool_result(value: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
              "structuredContent": value}
    if is_error:
        result["isError"] = True
    return result


def handle(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = message.get("method")
    rpc_id = message.get("id")
    if rpc_id is None:
        return None                                     # a notification
    if method == "initialize":
        result = {"protocolVersion": (message.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "council_run":
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            result = _tool_result(council_run(params.get("arguments") or {}))
        except (ValueError, OSError, RemoteError) as error:
            result = _tool_result({"error": str(error)}, is_error=True)
    else:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def main() -> None:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            response = handle(json.loads(line))
        except Exception as error:  # never let one bad line end the server
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)[:500]}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
