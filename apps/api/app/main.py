"""Dependency-free local DareLoop demo server. Replace adapters incrementally for production."""
from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
import secrets
import html
from pathlib import Path
from http.cookies import SimpleCookie

if __package__:
    from .config import ROOT, load_local_env
    from .persistence.repository import MemoryRepository, PostgresRepository
    from .services.sessions import SessionService
    from .services.common import DomainError
    from .integrations.twitch import exchange_profile
else:
    # Use the same package imports for direct-script and module startup.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from apps.api.app.config import ROOT, load_local_env
    from apps.api.app.persistence.repository import MemoryRepository, PostgresRepository
    from apps.api.app.services.sessions import SessionService
    from apps.api.app.services.common import DomainError
    from apps.api.app.integrations.twitch import exchange_profile
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

WEB = ROOT / "apps" / "web"

load_local_env()
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:8000/api/v1/auth/twitch/callback").strip()
DEMO = os.getenv("DEMO_MODE", "true").lower() == "true"
OAUTH_STATES = {}
WEB_SESSIONS = {}

def build_game():
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn and not DEMO:
        raise RuntimeError("DATABASE_URL is required with DEMO_MODE=false. Configure PostgreSQL and apply migrations first.")
    try:
        repository = PostgresRepository(dsn, workspace=os.getenv("DATABASE_WORKSPACE", "default"), demo=DEMO) if dsn else MemoryRepository()
        return SessionService(demo=DEMO, repository=repository)
    except ImportError:
        raise RuntimeError("Install apps/api/requirements.txt in the project virtual environment.") from None
    except DomainError:
        raise RuntimeError("Database unavailable. Check connectivity and apply migrations before starting.") from None

# Construct storage only at server startup, never as an import side effect.
GAME = None

class Handler(BaseHTTPRequestHandler):
    server_version = "DareLoopDemo/0.1"
    def log_message(self, fmt, *args):
        # OAuth callback URLs contain short-lived authorization codes. Never log queries.
        print("[http]", self.command, urlparse(self.path).path)
    def send_json(self, code, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def send_page(self, code, title, message):
        raw = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
               f"<title>{html.escape(title)} · DareLoop</title><body style='font:16px system-ui;max-width:620px;margin:12vh auto;padding:24px;background:#080b14;color:#f8fafc'>"
               f"<h1>{html.escape(title)}</h1><p style='color:#a6b1c7'>{html.escape(message)}</p><a style='color:#c4b5fd' href='/'>Return to DareLoop</a></body>").encode()
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def redirect(self, location, cookie=None):
        self.send_response(302); self.send_header("Location", location); self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0"); self.end_headers()
    def cookie_value(self, name):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        return cookies[name].value if name in cookies else ""
    def session_user(self):
        token = self.cookie_value("dareloop_session")
        entry = WEB_SESSIONS.get(token)
        if entry and entry["expires_at"] > time.time():
            return entry["user"]
        if token:
            WEB_SESSIONS.pop(token, None)
        return None
    def actor_id(self):
        user = self.session_user()
        return user["id"] if user else None
    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 100_000:
            raise DomainError("request_too_large", 413)
        if length and self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise DomainError("application_json_required", 415)
        return json.loads(self.rfile.read(length) or b"{}")
    def error_json(self, error):
        return self.send_json(error.status, {"data": None, "error": error.code, "request_id": str(uuid.uuid4())})
    def origin_allowed(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        expected_scheme = "https" if TWITCH_REDIRECT_URI.startswith("https:") else "http"
        return origin == expected_scheme + "://" + self.headers.get("Host", "")
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/v1/auth/twitch/start":
            if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
                return self.send_page(503, "Twitch credentials missing", "Add TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET to the project .env file, then restart the server.")
            callback_origin = urlparse(TWITCH_REDIRECT_URI)
            if self.headers.get("Host", "") != callback_origin.netloc:
                return self.redirect(callback_origin.scheme + "://" + callback_origin.netloc + "/api/v1/auth/twitch/start")
            state = secrets.token_urlsafe(32)
            OAUTH_STATES[state] = time.time() + 600
            query = urlencode({"client_id": TWITCH_CLIENT_ID, "redirect_uri": TWITCH_REDIRECT_URI,
                               "response_type": "code", "scope": "user:read:chat", "state": state})
            secure = "; Secure" if TWITCH_REDIRECT_URI.startswith("https:") else ""
            return self.redirect("https://id.twitch.tv/oauth2/authorize?" + query, f"dareloop_oauth_state={state}; HttpOnly; SameSite=Lax; Path=/api/v1/auth/twitch; Max-Age=600{secure}")
        if path == "/api/v1/auth/twitch/callback":
            params = parse_qs(urlparse(self.path).query)
            if params.get("error"):
                return self.send_page(400, "Twitch connection cancelled", "Twitch did not authorize DareLoop. You can try connecting again.")
            state = params.get("state", [""])[0]
            bound_state = self.cookie_value("dareloop_oauth_state")
            if not bound_state or not secrets.compare_digest(state.encode(), bound_state.encode()):
                return self.send_page(400, "Sign-in expired", "Sign-in must finish in the browser that started it.")
            expires = OAUTH_STATES.pop(state, None)
            if not expires or expires < time.time():
                return self.send_page(400, "Sign-in expired", "The one-time sign-in state is missing or expired. Return to DareLoop and try again.")
            code = params.get("code", [""])[0]
            if not code or not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
                return self.send_page(400, "Twitch sign-in failed", "The callback was missing an authorization code or server credentials.")
            try:
                user, token_data = exchange_profile(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_REDIRECT_URI, code)
                access_token = token_data["access_token"]
                session_id = secrets.token_urlsafe(32)
                WEB_SESSIONS[session_id] = {"user": user, "access_token": access_token, "refresh_token": token_data.get("refresh_token"), "expires_at": time.time() + int(token_data.get("expires_in", 0))}
                secure = "; Secure" if TWITCH_REDIRECT_URI.startswith("https:") else ""
                return self.redirect("/", f"dareloop_session={session_id}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400{secure}")
            except (HTTPError, URLError, TimeoutError, KeyError, IndexError, ValueError):
                return self.send_page(502, "Twitch connection failed", "The server could not finish OAuth with Twitch. Check the Client ID, secret, and exact redirect URL, then try again.")
        if path == "/api/v1/me":
            user = self.session_user()
            try:
                can_create = GAME.capabilities(user["id"] if user else None)
            except DomainError as error:
                return self.error_json(error)
            return self.send_json(200, {"data": {"connected": bool(user), "user": user, "demo_mode": DEMO, "can_create_session": can_create}, "error": None})
        if path == "/api/v1/state":
            try:
                return self.send_json(200, {"data": GAME.snapshot(self.actor_id()), "error": None, "request_id": str(uuid.uuid4())})
            except DomainError as error:
                return self.error_json(error)
        if path == "/api/v1/health": return self.send_json(200, {"ok": True, "demo_mode": DEMO, "storage": GAME.repository.storage})
        if path == "/overlay/demo": path = "/overlay.html"
        if path == "/": path = "/index.html"
        safe = (WEB / path.lstrip("/")).resolve()
        if not safe.is_relative_to(WEB.resolve()) or not safe.is_file(): return self.send_error(404)
        body = safe.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(safe.name)[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self.origin_allowed():
                raise DomainError("origin_not_allowed", 403)
            if path == "/api/v1/auth/twitch/disconnect":
                token = self.cookie_value("dareloop_session")
                if token:
                    WEB_SESSIONS.pop(token, None)
                return self._clear_cookie()
            data = self.read_json()
            result = GAME.execute(path, data, self.actor_id())
            return self.send_json(200, {"data": result, "error": None, "request_id": str(uuid.uuid4())})
        except DomainError as error:
            return self.error_json(error)
        except (ValueError, UnicodeDecodeError):
            return self.error_json(DomainError("invalid_json", 400))
    def _clear_cookie(self):
        raw=json.dumps({"data":{"connected":False},"error":None}).encode(); self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Set-Cookie","dareloop_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return True

if __name__ == "__main__":
    GAME = build_game()
    host=os.getenv("HOST","127.0.0.1"); port=int(os.getenv("PORT","8000"))
    print(f"DareLoop Live demo at http://{host}:{port} (DEMO_MODE={str(DEMO).lower()})")
    ThreadingHTTPServer((host,port),Handler).serve_forever()
