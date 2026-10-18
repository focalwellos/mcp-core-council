"""Access to MCP-core from the caller's machine: where the server is and which token to send.

A token comes from MCP_AUTH_TOKEN, else from the access file this module writes after
`mcp-core-council request` and `mcp-core-council status`:
$XDG_CONFIG_HOME/mcp-core/council-access.json (MCP_COUNCIL_ACCESS_FILE overrides). The
file holds the server URL, the request id and its claim code while the request is pending,
then the token. Only the name, the email and the note of a request ever reach the server.

Standard library only: runs on macOS, Linux and Windows.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_REMOTE_URL = "https://35-253-26-216.sslip.io/mcp"


class AccessError(Exception):
    pass


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mcp-core")


def access_path() -> str:
    return os.environ.get("MCP_COUNCIL_ACCESS_FILE", "").strip() or os.path.join(config_dir(), "council-access.json")


def load_access() -> Dict[str, Any]:
    try:
        with open(access_path(), encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_access(value: Dict[str, Any]) -> None:
    """Replace the file whole; owner-only where the OS has modes (the token is a secret)."""
    path = access_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remote_url() -> str:
    """MCP_REMOTE_URL, else the server the saved access came from, else the public instance."""
    return (os.environ.get("MCP_REMOTE_URL", "").strip() or str(load_access().get("url") or "").strip()
            or DEFAULT_REMOTE_URL)


def auth_token() -> str:
    return os.environ.get("MCP_AUTH_TOKEN", "").strip() or str(load_access().get("token") or "").strip()


def access_base(url: str) -> str:
    """https://host/mcp -> https://host/access"""
    url = url.rstrip("/")
    if url.endswith("/mcp"):
        url = url[:-4]
    return url + "/access"


def _post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw).get("error") or raw
        except (ValueError, AttributeError):
            detail = raw
        raise AccessError("HTTP %s: %s" % (error.code, str(detail)[:300]))
    except URLError as error:
        raise AccessError("server unavailable: %s" % error.reason)
    try:
        value = json.loads(raw)
    except ValueError:
        raise AccessError("unexpected reply %r" % raw[:200])
    if not isinstance(value, dict):
        raise AccessError("unexpected reply %r" % raw[:200])
    return value


def request_access(name: str, email: str, note: str = "", url: Optional[str] = None) -> Dict[str, Any]:
    """Send a request and keep its claim code; the token comes later, through check_status."""
    url = url or remote_url()
    reply = _post(access_base(url) + "/request", {"name": name, "email": email, "note": note})
    if not reply.get("request_id") or not reply.get("claim_code"):
        raise AccessError("unexpected reply: %s" % json.dumps(reply)[:200])
    save_access({"url": url, "status": "pending", "request_id": reply["request_id"],
                 "claim_code": reply["claim_code"], "email": email})
    return reply


def check_status() -> Dict[str, Any]:
    """Ask the server about the saved request. An approved request returns its token once: it is
    saved here and the claim code dropped. Returns the access record as saved."""
    saved = load_access()
    if saved.get("token") and not saved.get("claim_code"):
        return saved
    if not saved.get("request_id") or not saved.get("claim_code"):
        raise AccessError("no request saved in %s: run `mcp-core-council request` first" % access_path())
    url = str(saved.get("url") or remote_url())
    reply = _post(access_base(url) + "/claim", {"request_id": saved["request_id"], "claim_code": saved["claim_code"]})
    status = reply.get("status")
    if status == "approved" and reply.get("token"):
        saved = {"url": url, "status": "active", "request_id": saved["request_id"], "label": reply.get("label"),
                 "token": reply["token"], "expires_at": reply.get("expires_at"), "email": saved.get("email")}
        save_access(saved)
        return saved
    if status in ("pending", "denied", "expired", "claimed"):
        saved["status"] = status
        save_access(saved)
        return saved
    raise AccessError("unexpected reply: %s" % json.dumps(reply)[:200])
