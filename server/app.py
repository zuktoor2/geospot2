import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = Path(os.environ.get("GEOSPOT_BASE_DIR", "/home/codexops/geospot2"))
PUBLIC_DIR = Path(os.environ.get("GEOSPOT_PUBLIC_DIR", BASE_DIR / "current" / "public"))
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "leaderboard.sqlite3"
SECRET_PATH = DATA_DIR / "leaderboard_secret"
ALLOWED_ORIGIN = os.environ.get("GEOSPOT_ALLOWED_ORIGIN", "https://wecreativeforge.com")
DIFFICULTIES = {"easy", "medium", "hard", "expert"}
MAX_SCORE = 5500
TOKEN_MAX_AGE_SECONDS = 2 * 60 * 60
TOKEN_MIN_AGE_SECONDS = 10
PLAYER_PATTERN = re.compile(r"^[\w .'-]{1,24}$", re.UNICODE)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_rate_lock = threading.Lock()
_rate_events = defaultdict(deque)


def database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="ascii")
        SECRET_PATH.chmod(0o600)
    with database() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT NOT NULL,
                score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 5500),
                difficulty TEXT NOT NULL CHECK(difficulty IN ('easy','medium','hard','expert')),
                played_at INTEGER NOT NULL,
                nonce TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS leaderboard_rank ON leaderboard(score DESC, played_at ASC)")


def client_key():
    address = request.access_route[0] if request.access_route else request.remote_addr or "unknown"
    return hashlib.sha256(address.encode("utf-8", "replace")).hexdigest()


def rate_limited(bucket, limit, window_seconds):
    now = time.time()
    key = (bucket, client_key())
    with _rate_lock:
        events = _rate_events[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            return True
        events.append(now)
        return False


def valid_origin():
    return request.headers.get("Origin") == ALLOWED_ORIGIN


def clean_player(value):
    player = " ".join(str(value or "").strip().split())
    return player if PLAYER_PATTERN.fullmatch(player) else None


def token_secret():
    return SECRET_PATH.read_bytes().strip()


def encode_token(payload):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(token_secret(), body, hashlib.sha256).digest()
    return ".".join(
        base64.urlsafe_b64encode(part).decode("ascii").rstrip("=") for part in (body, signature)
    )


def decode_token(token):
    try:
        encoded_body, encoded_signature = token.split(".", 1)
        body = base64.urlsafe_b64decode(encoded_body + "=" * (-len(encoded_body) % 4))
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected = hmac.new(token_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(body)
        age = int(time.time()) - int(payload.get("issued_at", 0))
        if age < TOKEN_MIN_AGE_SECONDS or age > TOKEN_MAX_AGE_SECONDS:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.post("/api/session")
def create_session():
    if not valid_origin():
        return jsonify(error="origin_not_allowed"), 403
    if not request.is_json:
        return jsonify(error="json_required"), 415
    if rate_limited("session", 30, 3600):
        return jsonify(error="rate_limited"), 429
    payload = request.get_json(silent=True) or {}
    player = clean_player(payload.get("player"))
    difficulty = payload.get("difficulty")
    if not player or difficulty not in DIFFICULTIES:
        return jsonify(error="invalid_game"), 400
    token = encode_token(
        {"player": player, "difficulty": difficulty, "issued_at": int(time.time()), "nonce": secrets.token_urlsafe(18)}
    )
    return jsonify(token=token)


@app.get("/api/leaderboard")
def get_leaderboard():
    with database() as connection:
        rows = connection.execute(
            "SELECT player, score, difficulty, played_at FROM leaderboard ORDER BY score DESC, played_at ASC LIMIT 50"
        ).fetchall()
    return jsonify(
        scores=[
            {"player": row["player"], "score": row["score"], "difficulty": row["difficulty"], "playedAt": row["played_at"]}
            for row in rows
        ]
    )


@app.post("/api/leaderboard")
def submit_score():
    if not valid_origin():
        return jsonify(error="origin_not_allowed"), 403
    if not request.is_json:
        return jsonify(error="json_required"), 415
    if rate_limited("score", 12, 3600):
        return jsonify(error="rate_limited"), 429
    payload = request.get_json(silent=True) or {}
    player = clean_player(payload.get("player"))
    difficulty = payload.get("difficulty")
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= MAX_SCORE:
        return jsonify(error="invalid_score"), 400
    token_payload = decode_token(str(payload.get("token") or ""))
    if not player or difficulty not in DIFFICULTIES or not token_payload:
        return jsonify(error="invalid_submission"), 400
    if token_payload.get("player") != player or token_payload.get("difficulty") != difficulty:
        return jsonify(error="token_mismatch"), 400
    played_at = int(time.time() * 1000)
    try:
        with database() as connection:
            connection.execute(
                "INSERT INTO leaderboard(player, score, difficulty, played_at, nonce) VALUES (?, ?, ?, ?, ?)",
                (player, score, difficulty, played_at, token_payload["nonce"]),
            )
    except sqlite3.IntegrityError:
        return jsonify(error="duplicate_submission"), 409
    return jsonify(saved=True), 201


@app.get("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.get("/<path:path>")
def public_file(path):
    return send_from_directory(PUBLIC_DIR, path)


initialize()

