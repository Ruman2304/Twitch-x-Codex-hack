"""Dependency-free local DareLoop demo server. Replace adapters incrementally for production."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import uuid
import copy
import secrets
import html
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
LOCK = threading.RLock()

def load_local_env():
    """Load simple KEY=value entries without overriding real process environment."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)

load_local_env()
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:8000/api/v1/auth/twitch/callback").strip()
DEMO = os.getenv("DEMO_MODE", "true").lower() == "true"
OAUTH_STATES = {}
WEB_SESSIONS = {}

def challenge(title, instructions, duration, points, category="Discovery"):
    return {"id": str(uuid.uuid4()), "title": title, "instructions": instructions,
            "duration_sec": duration, "points": points, "category": category,
            "safety_notes": "Stay in a permitted area. Do not film people without consent.",
            "approval_status": "pending", "source": "curated"}

STATE = {
    "session": {"id": "demo-session", "title": "Campus Field Notes", "context": "Campus", "difficulty": "Easy", "status": "live", "version": 1},
    "challenges": [
        challenge("Spot three tree species", "Find three different tree species in a public area. No need to leave the permitted grounds.", 120, 30, "Discovery"),
        challenge("Find a public art detail", "Notice one detail in a public art installation without interrupting anyone.", 90, 25, "Observation"),
        challenge("Architecture texture hunt", "Find an interesting building texture from a public path. Capture it only if no people are in frame.", 100, 25, "Photography"),
    ],
    "round": None, "attempt": None, "votes": {}, "scores": {}, "activity": [], "ended": False,
}
INITIAL_STATE = copy.deepcopy(STATE)
VIEWERS = {"viewer-1": "Maple", "viewer-2": "Orbit", "viewer-3": "Pixel"}
SAFE_PACK = [
    ("Find a quiet color palette", "Notice three colors in the environment from a permitted public path. Do not enter closed areas.", 90, 20, "Observation"),
    ("Spot a wayfinding symbol", "Find a public sign or wayfinding symbol and describe its shape to chat. Do not record people or private information.", 90, 20, "Discovery"),
    ("Skyline shape study", "From a permitted spot, find a distinctive roofline or silhouette. No climbing or leaving public paths.", 120, 25, "Observation"),
]

def event(message):
    STATE["activity"].insert(0, {"id": str(uuid.uuid4()), "at": int(time.time()), "message": message})
    STATE["activity"] = STATE["activity"][:12]

def snapshot():
    with LOCK:
        now = int(time.time())
        round_ = STATE["round"]
        attempt = STATE["attempt"]
        if round_ and round_["state"] == "voting" and now >= round_["closes_at"]:
            _close_round()
            round_ = STATE["round"]
        tallies = {str(i): 0 for i in range(1, 4)}
        if round_:
            for vote in STATE["votes"].values():
                tallies[str(vote["option"])] += 1
        if attempt and attempt["status"] == "running":
            attempt["remaining_sec"] = max(0, attempt["ends_at"] - now)
            if attempt["remaining_sec"] == 0:
                attempt["status"] = "expired"
                event("Challenge timer expired")
                STATE["session"]["version"] += 1
        return {"demo_mode": DEMO, "session": STATE["session"], "challenges": STATE["challenges"],
                "round": round_, "tallies": tallies, "attempt": attempt,
                "scores": sorted([{"name": n, "points": p} for n, p in STATE["scores"].items()], key=lambda x: (-x["points"], x["name"])),
                "activity": STATE["activity"], "viewers": VIEWERS}

def _close_round():
    r = STATE["round"]
    if not r or r["state"] != "voting":
        return
    tallies = {i: sum(1 for v in STATE["votes"].values() if v["option"] == i) for i in (1, 2, 3)}
    winner = max(r["options"], key=lambda x: (tallies[x["number"]], -x["number"]))
    r["state"] = "resolved"
    r["winner"] = winner
    r["tallies"] = {str(k): v for k, v in tallies.items()}
    event(f"Vote closed · {winner['title']} wins")
    STATE["session"]["version"] += 1

class Handler(BaseHTTPRequestHandler):
    server_version = "DareLoopDemo/0.1"
    def log_message(self, fmt, *args):
        print("[http] " + fmt % args)
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
    def session_user(self):
        cookies = self.headers.get("Cookie", "")
        token = next((part.split("=", 1)[1] for part in cookies.split(";") if part.strip().startswith("dareloop_session=")), "")
        entry = WEB_SESSIONS.get(token)
        if entry and entry["expires_at"] > time.time():
            return entry["user"]
        if token: WEB_SESSIONS.pop(token, None)
        return None
    def read_json(self):
        length = min(int(self.headers.get("Content-Length", "0")), 100_000)
        return json.loads(self.rfile.read(length) or b"{}")
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/v1/auth/twitch/start":
            if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
                return self.send_page(503, "Twitch credentials missing", "Add TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET to the project .env file, then restart the server.")
            state = secrets.token_urlsafe(32)
            OAUTH_STATES[state] = time.time() + 600
            query = urlencode({"client_id": TWITCH_CLIENT_ID, "redirect_uri": TWITCH_REDIRECT_URI,
                               "response_type": "code", "scope": "user:read:chat", "state": state})
            return self.redirect("https://id.twitch.tv/oauth2/authorize?" + query)
        if path == "/api/v1/auth/twitch/callback":
            params = parse_qs(urlparse(self.path).query)
            if params.get("error"):
                return self.send_page(400, "Twitch connection cancelled", "Twitch did not authorize DareLoop. You can try connecting again.")
            state = params.get("state", [""])[0]
            expires = OAUTH_STATES.pop(state, None)
            if not expires or expires < time.time():
                return self.send_page(400, "Sign-in expired", "The one-time sign-in state is missing or expired. Return to DareLoop and try again.")
            code = params.get("code", [""])[0]
            if not code or not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
                return self.send_page(400, "Twitch sign-in failed", "The callback was missing an authorization code or server credentials.")
            try:
                body = urlencode({"client_id": TWITCH_CLIENT_ID, "client_secret": TWITCH_CLIENT_SECRET,
                                  "code": code, "grant_type": "authorization_code", "redirect_uri": TWITCH_REDIRECT_URI}).encode()
                token_req = Request("https://id.twitch.tv/oauth2/token", data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}, method="POST")
                with urlopen(token_req, timeout=15) as response: token_data = json.loads(response.read())
                access_token = token_data["access_token"]
                validate_req = Request("https://id.twitch.tv/oauth2/validate", headers={"Authorization": f"OAuth {access_token}"})
                with urlopen(validate_req, timeout=15) as response: validation = json.loads(response.read())
                if validation.get("client_id") != TWITCH_CLIENT_ID or "user:read:chat" not in validation.get("scopes", []):
                    return self.send_page(403, "Twitch permission missing", "The token did not validate for this app or lacks user:read:chat. Disconnect and reconnect with the requested permission.")
                helix_req = Request("https://api.twitch.tv/helix/users", headers={"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {access_token}"})
                with urlopen(helix_req, timeout=15) as response: profile = json.loads(response.read()).get("data", [])
                if not profile: return self.send_page(502, "Twitch profile unavailable", "Twitch authorized the app, but did not return a channel profile.")
                user = {"id": profile[0]["id"], "login": profile[0]["login"], "display_name": profile[0]["display_name"], "profile_image_url": profile[0].get("profile_image_url", "")}
                session_id = secrets.token_urlsafe(32)
                WEB_SESSIONS[session_id] = {"user": user, "access_token": access_token, "refresh_token": token_data.get("refresh_token"), "expires_at": time.time() + int(token_data.get("expires_in", 0)) + 3600}
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "http") == "https" else ""
                return self.redirect("/", f"dareloop_session={session_id}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400{secure}")
            except (HTTPError, URLError, TimeoutError, KeyError, ValueError):
                return self.send_page(502, "Twitch connection failed", "The server could not finish OAuth with Twitch. Check the Client ID, secret, and exact redirect URL, then try again.")
        if path == "/api/v1/me":
            user = self.session_user()
            return self.send_json(200, {"data": {"connected": bool(user), "user": user}, "error": None})
        if path == "/api/v1/state": return self.send_json(200, {"data": snapshot(), "error": None, "request_id": str(uuid.uuid4())})
        if path == "/api/v1/health": return self.send_json(200, {"ok": True, "demo_mode": DEMO})
        if path == "/overlay/demo": path = "/overlay.html"
        if path == "/": path = "/index.html"
        safe = (WEB / path.lstrip("/")).resolve()
        if not safe.is_relative_to(WEB.resolve()) or not safe.is_file(): return self.send_error(404)
        body = safe.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(safe.name)[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/v1/auth/twitch/disconnect":
            token = next((part.split("=", 1)[1] for part in self.headers.get("Cookie", "").split(";") if part.strip().startswith("dareloop_session=")), "")
            if token: WEB_SESSIONS.pop(token, None)
            self._clear_cookie()
            return
        try:
            data = self.read_json()
            with LOCK:
                if path.startswith("/api/v1/challenges/") and path.endswith("/approval"):
                    cid = path.split("/")[-2]; row = next((c for c in STATE["challenges"] if c["id"] == cid), None)
                    if not row: return self.send_json(404, {"error": "challenge_not_found"})
                    approval = data.get("approval")
                    if approval not in ("approved", "rejected"): return self.send_json(400, {"error": "invalid_approval"})
                    row["approval_status"] = approval; event(f"{row['title']} {approval}"); STATE["session"]["version"] += 1
                elif path == "/api/v1/rounds":
                    if STATE["round"] and STATE["round"]["state"] == "voting": return self.send_json(409, {"error": "round_already_open"})
                    approved = [c for c in STATE["challenges"] if c["approval_status"] == "approved"]
                    if len(approved) < 2: return self.send_json(400, {"error": "approve_at_least_two_challenges"})
                    options = approved[:3]
                    STATE["round"] = {"id": str(uuid.uuid4()), "state": "voting", "options": [{"number": i+1, "challenge_id": c["id"], "title": c["title"]} for i,c in enumerate(options)], "closes_at": int(time.time())+60}
                    STATE["votes"] = {}; event("Voting opened for 60 seconds"); STATE["session"]["version"] += 1
                elif path == "/api/v1/demo/vote":
                    if not DEMO: return self.send_json(403, {"error": "demo_disabled"})
                    r=STATE["round"]
                    if not r or r["state"] != "voting" or int(time.time()) >= r["closes_at"]: return self.send_json(409, {"error": "voting_closed"})
                    viewer=data.get("viewer"); option=data.get("option")
                    if viewer not in VIEWERS or not isinstance(option,int) or option not in [x["number"] for x in r["options"]]: return self.send_json(400, {"error": "invalid_vote"})
                    first_vote = viewer not in STATE["votes"]
                    STATE["votes"][viewer]={"option":option,"updated_at":int(time.time())}
                    if first_vote: STATE["scores"][VIEWERS[viewer]]=STATE["scores"].get(VIEWERS[viewer],0)+1
                    event(f"{VIEWERS[viewer]} voted for option {option}"); STATE["session"]["version"] += 1
                elif path == "/api/v1/rounds/close":
                    r=STATE["round"]
                    if not r or r["state"] != "voting": return self.send_json(409, {"error": "no_active_round"})
                    _close_round()
                elif path == "/api/v1/attempt":
                    r=STATE["round"]
                    if not r or r.get("state") != "resolved": return self.send_json(409, {"error": "resolve_a_vote_first"})
                    c=next(c for c in STATE["challenges"] if c["id"]==r["winner"]["challenge_id"])
                    STATE["attempt"]={"id":str(uuid.uuid4()),"challenge_id":c["id"],"title":c["title"],"duration_sec":c["duration_sec"],"status":"running","ends_at":int(time.time())+c["duration_sec"]}
                    event(f"Challenge started · {c['title']}"); STATE["session"]["version"] += 1
                elif path == "/api/v1/attempt/action":
                    a=STATE["attempt"]; action=data.get("action")
                    if not a or action not in ("complete","skip"): return self.send_json(400, {"error": "invalid_action"})
                    if a["status"] not in ("running","expired"): return self.send_json(409, {"error": "attempt_already_closed"})
                    a["status"]="completed" if action=="complete" else "skipped"; event(f"Challenge {a['status']} by streamer"); STATE["session"]["version"] += 1
                elif path == "/api/v1/session/pause":
                    STATE["session"]["status"]="paused" if STATE["session"]["status"]=="live" else "live"; event(f"Session {STATE['session']['status']}"); STATE["session"]["version"] += 1
                elif path == "/api/v1/demo/reset":
                    if not DEMO: return self.send_json(403, {"error": "demo_disabled"})
                    STATE.clear(); STATE.update(copy.deepcopy(INITIAL_STATE))
                elif path == "/api/v1/challenges/curated":
                    idx = sum(1 for c in STATE["challenges"] if c["source"] == "curated") - len(INITIAL_STATE["challenges"])
                    if idx >= len(SAFE_PACK): return self.send_json(409, {"error": "curated_pack_exhausted"})
                    title, instructions, duration, points, category = SAFE_PACK[idx]
                    STATE["challenges"].append(challenge(title, instructions, duration, points, category))
                    event(f"Curated challenge added · {title}"); STATE["session"]["version"] += 1
                else: return self.send_json(404, {"error": "not_found"})
            return self.send_json(200, {"data": snapshot(), "error": None, "request_id": str(uuid.uuid4())})
        except (ValueError, json.JSONDecodeError): return self.send_json(400, {"error": "invalid_json"})
    def _clear_cookie(self):
        raw=json.dumps({"data":{"connected":False},"error":None}).encode(); self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Set-Cookie","dareloop_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return True

if __name__ == "__main__":
    host=os.getenv("HOST","127.0.0.1"); port=int(os.getenv("PORT","8000"))
    print(f"DareLoop Live demo at http://{host}:{port} (DEMO_MODE={str(DEMO).lower()})")
    ThreadingHTTPServer((host,port),Handler).serve_forever()
