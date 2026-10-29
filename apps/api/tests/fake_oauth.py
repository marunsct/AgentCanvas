"""A fake authorization server and a fake MCP server in one ASGI app, for the OAuth connection tests (G-7).

`https://mcp.test/mcp` is the protected resource (401 with `resource_metadata` until a known token is sent) and
`https://as.test` the authorization server. Nothing is checked loosely: the token endpoint verifies PKCE, redirect
URI, client and resource, and refresh tokens rotate and can be used once, like a careful real server.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

MCP, AS = "https://mcp.test/mcp", "https://as.test"


class FakeWorld:
    def __init__(self) -> None:
        # behaviour switches
        self.cimd = False
        self.dcr = True
        self.iss_param = False
        self.s256 = True
        self.expires_in: int | None = 3600
        self.issuer_in_metadata = AS
        self.prm_resource = MCP
        self.prm_missing = False
        self.metadata_http = False
        # state
        self.codes: dict[str, dict[str, Any]] = {}
        self.access: dict[str, str] = {}  # access token -> who
        self.refresh: dict[str, str] = {}
        self.revoked: list[str] = []
        self.registrations: list[dict[str, Any]] = []
        self.refreshes = 0
        self.calls: list[tuple[str, str]] = []
        self.n = 0

    # --- what the browser does ------------------------------------------------------------------------------------

    def authorize(self, url: str, who: str = "alice") -> dict[str, str]:
        """The authorization server's side of the redirect: checks the request, returns the query it sends back."""
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{AS}/authorize", url
        q = dict(parse_qsl(parts.query))
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256", q
        assert q["resource"] == MCP and q["redirect_uri"].endswith("/oauth/callback"), q
        code = "code_" + secrets.token_hex(8)
        self.codes[code] = {"challenge": q["code_challenge"], "client_id": q["client_id"],
                            "redirect_uri": q["redirect_uri"], "resource": q["resource"], "who": who}  # fmt: skip
        back = {"code": code, "state": q["state"]}
        if self.iss_param:
            back["iss"] = AS
        return back

    def expire(self, token: str) -> None:
        """The server forgets an access token (it expired or was revoked there)."""
        self.access.pop(token, None)

    def _issue(self, who: str) -> dict[str, Any]:
        self.n += 1
        at, rt = f"at_{self.n}_{secrets.token_hex(4)}", f"rt_{self.n}_{secrets.token_hex(4)}"
        self.access[at], self.refresh[rt] = who, who
        out: dict[str, Any] = {"access_token": at, "token_type": "Bearer", "refresh_token": rt, "scope": "read"}
        if self.expires_in is not None:
            out["expires_in"] = self.expires_in
        return out

    # --- the server ---------------------------------------------------------------------------------------------------

    def client(self, public_only: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self), timeout=5)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert scope["type"] == "http"
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        host, path, method = headers.get("host", ""), scope["path"], scope["method"]
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        self.calls.append((method, f"{host}{path}"))
        status, extra, out = self.route(host, method, path, headers, body)
        data = json.dumps(out).encode()
        out_headers = [(b"content-type", b"application/json"), *[(k.encode(), v.encode()) for k, v in extra.items()]]
        await send({"type": "http.response.start", "status": status, "headers": out_headers})
        await send({"type": "http.response.body", "body": data})

    def route(self, host: str, method: str, path: str, headers: dict[str, str], body: bytes):
        if host == "mcp.test":
            if path == "/.well-known/oauth-protected-resource/mcp":
                if self.prm_missing:
                    return 404, {}, {"error": "not_found"}
                return (
                    200,
                    {},
                    {"resource": self.prm_resource, "authorization_servers": [AS], "scopes_supported": ["read"]},
                )
            if path == "/mcp":
                token = headers.get("authorization", "").removeprefix("Bearer ")
                if token in self.access:
                    return 200, {}, {"ok": True, "who": self.access[token]}
                challenge = 'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp"'
                return 401, {"www-authenticate": challenge}, {"error": "unauthorized"}
        if host == "as.test":
            if path == "/.well-known/oauth-authorization-server":
                meta: dict[str, Any] = {
                    "issuer": self.issuer_in_metadata, "authorization_endpoint": f"{AS}/authorize",
                    "token_endpoint": f"{AS}/token", "revocation_endpoint": f"{AS}/revoke",
                    "code_challenge_methods_supported": ["S256"] if self.s256 else ["plain"],
                    "client_id_metadata_document_supported": self.cimd,
                    "authorization_response_iss_parameter_supported": self.iss_param,
                }  # fmt: skip
                if self.dcr:
                    meta["registration_endpoint"] = f"{AS}/register"
                if self.metadata_http:
                    meta["token_endpoint"] = "http://as.test/token"
                return 200, {}, meta
            if path == "/register" and method == "POST":
                reg = json.loads(body)
                self.registrations.append(reg)
                return 201, {}, {"client_id": f"dcr_{len(self.registrations)}", "redirect_uris": reg["redirect_uris"]}
            if path == "/token" and method == "POST":
                return self.token(dict(parse_qsl(body.decode())))
            if path == "/revoke" and method == "POST":
                self.revoked.append(dict(parse_qsl(body.decode()))["token"])
                return 200, {}, {}
        return 404, {}, {"error": "not_found", "path": f"{host}{path}"}

    def token(self, f: dict[str, str]):
        if f.get("grant_type") == "authorization_code":
            c = self.codes.pop(f.get("code", ""), None)  # a code works once
            if c is None:
                return 400, {}, {"error": "invalid_grant", "error_description": "unknown or used code"}
            verifier = f.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if digest != c["challenge"]:
                return 400, {}, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
            if f.get("client_id") != c["client_id"] or f.get("redirect_uri") != c["redirect_uri"]:
                return 400, {}, {"error": "invalid_grant", "error_description": "client or redirect mismatch"}
            if f.get("resource") != c["resource"]:
                return 400, {}, {"error": "invalid_target"}
            return 200, {}, self._issue(c["who"])
        if f.get("grant_type") == "refresh_token":
            who = self.refresh.pop(f.get("refresh_token", ""), None)  # rotation: each refresh token works once
            if who is None:
                return 400, {}, {"error": "invalid_grant", "error_description": "refresh token reused or revoked"}
            self.refreshes += 1
            return 200, {}, self._issue(who)
        return 400, {}, {"error": "unsupported_grant_type"}


def now() -> float:
    return time.time()
