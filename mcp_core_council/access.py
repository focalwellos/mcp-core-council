"""Access to MCP-core from the caller's machine: where the server is and which token to send.

A token comes from MCP_AUTH_TOKEN, else from the access file `mcp-core-council login` writes:
$XDG_CONFIG_HOME/mcp-core/council-access.json (MCP_COUNCIL_ACCESS_FILE overrides), holding
the server URL and the token. `mcp-core-council request` sends a name, an email and a note;
the server mails the token to that email.

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
    """Send a request. The token never comes back here: the server mails it to `email`."""
    url = url or remote_url()
    reply = _post(access_base(url) + "/request", {"name": name, "email": email, "note": note})
    if reply.get("status") not in ("sent", "pending"):
        raise AccessError("unexpected reply: %s" % json.dumps(reply)[:200])
    return reply


def login(token: str, url: Optional[str] = None) -> Dict[str, Any]:
    """Check the token against the server, then save it for the runner."""
    token = token.strip()
    url = url or remote_url()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8")
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + token}
    try:
        with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=30) as response:
            response.read()
    except HTTPError as error:
        if error.code == 401:
            raise AccessError("the server does not accept this token (mistyped, expired or revoked)")
        raise AccessError("HTTP %s from %s" % (error.code, url))
    except URLError as error:
        raise AccessError("server unavailable: %s" % error.reason)
    saved = {"url": url, "token": token}
    save_access(saved)
    return saved
