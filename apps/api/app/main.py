"""Dependency-free local DareLoop demo server. Replace adapters incrementally for production."""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
import html
from pathlib import Path
from http.cookies import SimpleCookie

if __package__:
    from .config import ROOT, load_local_env
    from .persistence.repository import MemoryRepository, PostgresRepository
    from .services.sessions import SessionService
    from .services.common import DomainError
    from .integrations.twitch import TwitchClient
    from .persistence.accounts import MemoryAccountStore, PostgresAccountStore
    from .services.accounts import AccountService
else:
    # Use the same package imports for direct-script and module startup.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from apps.api.app.config import ROOT, load_local_env
    from apps.api.app.persistence.repository import MemoryRepository, PostgresRepository
    from apps.api.app.services.sessions import SessionService
    from apps.api.app.services.common import DomainError
    from apps.api.app.integrations.twitch import TwitchClient
    from apps.api.app.persistence.accounts import MemoryAccountStore, PostgresAccountStore
    from apps.api.app.services.accounts import AccountService
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

WEB = ROOT / "apps" / "web"

load_local_env()
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:8000/api/v1/auth/twitch/callback").strip()
DEMO = os.getenv("DEMO_MODE", "true").lower() == "true"


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
AUTH = None
CHAT = None

def build_auth(game):
    keys = os.getenv("TOKEN_ENCRYPTION_KEYS", "").strip()
    if not keys or not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        if not DEMO:
            raise RuntimeError("Live mode requires Twitch credentials and TOKEN_ENCRYPTION_KEYS.")
        return None
    try:
        if __package__:
            from .security.tokens import TokenCipher
        else:
            from apps.api.app.security.tokens import TokenCipher
        store = PostgresAccountStore(game.repository) if game.repository.storage == "postgresql" else MemoryAccountStore()
        return AccountService(store, TokenCipher(keys), TwitchClient(TWITCH_CLIENT_ID,TWITCH_CLIENT_SECRET,TWITCH_REDIRECT_URI))
    except ImportError:
        raise RuntimeError("Install apps/api/requirements.txt before enabling Twitch authentication.") from None

def chat_status():
    return CHAT.snapshot() if CHAT else {"state":"demo" if GAME.demo else "disabled"}

def with_chat(snapshot):
    snapshot["chat"] = chat_status()
    return snapshot

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
        return AUTH.authenticate(self.cookie_value("dareloop_session")) if AUTH else None
    def actor_id(self):
        if GAME.demo:
            return None  # Local simulation must work during a Twitch outage.
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
        try:
            return self._get()
        except DomainError as error:
            return self.error_json(error)
    def _get(self):
        path = urlparse(self.path).path
        if path == "/api/v1/auth/twitch/start":
            if AUTH is None:
                return self.send_page(503, "Twitch configuration needed", "Configure Twitch credentials and TOKEN_ENCRYPTION_KEYS, then restart. The local demo works without sign-in.")
            callback_origin = urlparse(TWITCH_REDIRECT_URI)
            if self.headers.get("Host", "") != callback_origin.netloc:
                return self.redirect(callback_origin.scheme + "://" + callback_origin.netloc + "/api/v1/auth/twitch/start")
            state = AUTH.begin()
            query = urlencode({"client_id": TWITCH_CLIENT_ID, "redirect_uri": TWITCH_REDIRECT_URI,
                               "response_type": "code", "scope": "user:read:chat", "state": state})
            secure = "; Secure" if TWITCH_REDIRECT_URI.startswith("https:") else ""
            return self.redirect("https://id.twitch.tv/oauth2/authorize?" + query, f"dareloop_oauth_state={state}; HttpOnly; SameSite=Lax; Path=/api/v1/auth/twitch; Max-Age=600{secure}")
        if path == "/api/v1/auth/twitch/callback":
            params = parse_qs(urlparse(self.path).query)
            if params.get("error"):
                return self.send_page(400, "Twitch connection cancelled", "Twitch did not authorize DareLoop. You can try connecting again.")
            if AUTH is None:
                return self.send_page(503, "Twitch unavailable", "Twitch authentication is not configured on this server.")
            try:
                session_id = AUTH.complete(params.get("state", [""])[0], self.cookie_value("dareloop_oauth_state"),
                                           params.get("code", [""])[0], self.cookie_value("dareloop_session"))
                secure = "; Secure" if TWITCH_REDIRECT_URI.startswith("https:") else ""
                return self.redirect("/", f"dareloop_session={session_id}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400{secure}")
            except DomainError as error:
                return self.send_page(error.status, "Twitch sign-in could not finish", "Return to DareLoop and reconnect. If this persists, check the server configuration and Twitch connection.")
        if path == "/api/v1/me":
            user = self.session_user()
            try:
                can_create = GAME.capabilities(user["id"] if user else None)
            except DomainError as error:
                return self.error_json(error)
            return self.send_json(200, {"data": {"connected": bool(user), "user": user, "demo_mode": DEMO, "can_create_session": can_create, "auth_ready": AUTH is not None, "auth_storage": AUTH.store.storage if AUTH else "disabled", "reconnect_required": bool(self.cookie_value("dareloop_session")) and not user}, "error": None})
        if path == "/api/v1/state":
            try:
                return self.send_json(200, {"data": with_chat(GAME.snapshot(self.actor_id())), "error": None, "request_id": str(uuid.uuid4())})
            except DomainError as error:
                return self.error_json(error)
        if path == "/api/v1/health": return self.send_json(200, {"ok": True, "demo_mode": DEMO, "storage": GAME.repository.storage})
        if path in ("/overlay/demo", "/overlay/preview"): path = "/overlay.html"
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
                pending = AUTH.disconnect(self.cookie_value("dareloop_session")) if AUTH else False
                return self._clear_cookie(pending)
            data = self.read_json()
            result = GAME.execute(path, data, self.actor_id())
            return self.send_json(200, {"data": with_chat(result), "error": None, "request_id": str(uuid.uuid4())})
        except DomainError as error:
            return self.error_json(error)
        except (ValueError, UnicodeDecodeError):
            return self.error_json(DomainError("invalid_json", 400))
    def _clear_cookie(self, pending=False):
        raw=json.dumps({"data":{"connected":False,"revocation_pending":pending},"error":None}).encode(); self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Set-Cookie","dareloop_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0" + ("; Secure" if TWITCH_REDIRECT_URI.startswith("https:") else "")); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return True

if __name__ == "__main__":
    GAME = build_game()
    AUTH = build_auth(GAME)
    account_stop = AUTH.start_worker() if AUTH else None
    if not DEMO and os.getenv("TWITCH_EVENTSUB_ENABLED","true").lower()=="true":
        try:
            from apps.api.app.integrations.eventsub import EventSubWorker
            CHAT = EventSubWorker(GAME,AUTH).start()
        except ImportError:
            raise RuntimeError("Install apps/api/requirements.txt for EventSub support.") from None
    host=os.getenv("HOST","127.0.0.1"); port=int(os.getenv("PORT","8000"))
    print(f"DareLoop Live demo at http://{host}:{port} (DEMO_MODE={str(DEMO).lower()})")
    try:
        ThreadingHTTPServer((host,port),Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if CHAT:
            CHAT.stop()
        if account_stop:
            account_stop.set()
