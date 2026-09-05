"""Verify Savanna credentials before anything depends on them.

Connection problems here have a small number of causes and each has a different
fix, so this reports which one it hit rather than surfacing a raw stack trace.
The one that catches people out is a **suspended workspace**: free-tier
workspaces auto-suspend when idle, and the first connection after that fails or
hangs while the workspace wakes up — which looks exactly like bad credentials.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import Config, SavannaCredentials


@dataclass(slots=True)
class CheckResult:
    """A staged diagnosis. ``ok`` means every step passed.

    It was previously set by whoever reached the end of the function, so a run
    with a FAIL line still reported success and exited 0 — a verifier that can
    pass while showing a failure is worse than no verifier.
    """

    ok: bool = False
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    hint: str = ""

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.steps.append((name, passed, detail))

    def finish(self, hint: str = "") -> "CheckResult":
        self.ok = bool(self.steps) and all(passed for _, passed, _ in self.steps)
        if hint and self.ok:
            self.hint = hint
        return self

    def render(self) -> str:
        lines = [f"  [{'PASS' if ok else 'FAIL'}] {name}"
                 + (f": {detail}" if detail else "")
                 for name, ok, detail in self.steps]
        if self.hint:
            lines += ["", "  " + self.hint.replace("\n", "\n  ")]
        return "\n".join(lines)


def check_savanna(cfg: Config, timeout: int = 30) -> CheckResult:
    """Walk the connection from credentials to a live query, stopping at the first break."""
    result = CheckResult()
    creds: SavannaCredentials = Config.savanna_credentials()

    # 1. credentials present
    missing = creds.missing()
    result.add("credentials present", not missing,
               "all set" if not missing else f"missing {', '.join(missing)}")
    if missing:
        result.hint = (
            f"Set {', '.join(missing)} in capstone/.env (copy .env.example).\n"
            f"See docs/tigergraph-setup.md for where each value comes from.")
        return result

    host = creds.normalized_host
    parsed = urlparse(host)
    hostname = parsed.hostname or ""

    # 2. DNS
    try:
        socket.gethostbyname(hostname)
        result.add("hostname resolves", True, hostname)
    except socket.gaierror as exc:
        result.add("hostname resolves", False, f"{hostname}: {exc}")
        result.hint = (
            "TG_HOST does not resolve. Copy it from the Savanna console:\n"
            "workspace -> Connect -> 'Connect from API'. It looks like\n"
            "https://abcd1234.i.tgcloud.io — no trailing path, no port.")
        return result

    # 3. TLS port reachable. A suspended workspace usually fails right here.
    port = int(creds.rest_port or 443)
    started = time.perf_counter()
    try:
        with socket.create_connection((hostname, port), timeout=timeout):
            pass
        result.add(f"port {port} reachable", True, f"{(time.perf_counter()-started)*1000:.0f}ms")
    except (socket.timeout, OSError) as exc:
        result.add(f"port {port} reachable", False, str(exc))
        result.hint = (
            "Could not open a connection. The usual cause is a **suspended\n"
            "workspace** — free-tier workspaces auto-suspend when idle. Open the\n"
            "Savanna console and check the workspace shows 'Active'; resume it\n"
            "and wait for it to come up, then run this again.")
        return result

    # 4. credential type — reported before use, because the failure it causes
    #    is opaque ("User authentication failed") and the fix depends entirely
    #    on which of Savanna's several credentials was pasted in.
    kind = creds.auth_kind
    described = {"jwt": "JWT bearer token (eyJ...) — passed as jwtToken",
                 "secret": "database secret — passed as gsqlSecret",
                 "password": "username/password"}.get(kind, "none")
    result.add("credential type", kind != "none", described)

    try:
        from pyTigerGraph import TigerGraphConnection  # noqa: F401
    except ImportError:
        result.add("pyTigerGraph importable", False, "not installed")
        result.hint = "pip install -r requirements.txt"
        return result

    try:
        from .savanna import build_connection
        conn = build_connection(creds)
        result.add("authenticated", True, f"via {kind}")
    except Exception as exc:                      # pyTigerGraph raises broadly
        message = str(exc)
        result.add("authenticated", False, message[:200])
        if kind == "jwt":
            result.hint = (
                "The credential is a JWT and TigerGraph rejected it. JWTs expire —\n"
                "typically within hours — so the usual causes are an expired token\n"
                "or one minted for a different workspace.\n\n"
                "For anything longer than a quick test, use a **database secret**\n"
                "instead: it does not expire, and pyTigerGraph exchanges it for a\n"
                "fresh token on every connection. Create one in the workspace's\n"
                "GSQL editor:\n"
                f"    USE GRAPH {creds.graph}\n"
                "    CREATE SECRET s1\n"
                "then put that ~32-character value in TG_SECRET.")
            return result
        if "Endpoint is not found" in message or "404" in message:
            result.hint = (
                f"Authenticated against the host but graph '{creds.graph}' was not\n"
                f"found. A database secret is scoped to a graph, so the graph must\n"
                f"exist first. In Savanna's GSQL editor run:\n"
                f"    CREATE GRAPH {creds.graph} ()\n"
                f"then create the secret and put it in TG_SECRET.")
        else:
            result.hint = (
                "TG_SECRET was rejected. Two things are easy to mix up:\n"
                "  - a Savanna **API key** authenticates the management API and\n"
                "    will NOT work here;\n"
                "  - a **database secret** is what pyTigerGraph wants.\n"
                "Create one in the workspace Admin Portal -> My Profile -> '+'\n"
                "beside Secrets, or in the GSQL editor with: CREATE SECRET s1")
        return result

    # 5. round-trip a query
    try:
        version = conn.getVer()
        result.add("query round-trip", True, f"TigerGraph {version}")
    except Exception as exc:
        result.add("query round-trip", False, str(exc)[:200])
        result.hint = "Authenticated, but the server would not answer. Retry once the workspace is fully up."
        return result

    result.finish()
    result.hint = (
        "Ready. Next: paysentry provision --store savanna --profile small"
        + ("\n\nNote: you are authenticating with a JWT, which expires. If a later\n"
           "command suddenly fails to authenticate, that is why — a database\n"
           "secret (CREATE SECRET s1) avoids it." if creds.is_jwt else ""))
    return result
