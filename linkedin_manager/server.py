#!/usr/bin/env python3
"""Jarvis LinkedIn Manager: a local drafting server.

Standard library only. Run it with:

    python3 linkedin_manager/server.py            # http://localhost:8000
    python3 linkedin_manager/server.py --port 8080 --open

Drafts live in linkedin_manager/data/posts.json and photos in
linkedin_manager/media/. Both folders are git-ignored, so private photos never
leave your machine.
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
MEDIA_DIR = ROOT / "media"
POSTS_FILE = DATA_DIR / "posts.json"
SEED_FILE = ROOT / "seed_posts.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_JSON_BYTES = 1 * 1024 * 1024
STATUSES = ("draft", "ready", "posted")
FIELDS = {
    "title": str,
    "body": str,
    "hashtags": str,
    "status": str,
    "notes": str,
    "media": list,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BadRequest(Exception):
    pass


def apply_fields(post, payload):
    """Copy validated, editable fields from payload onto post."""
    if not isinstance(payload, dict):
        raise BadRequest("Expected a JSON object")
    for key, typ in FIELDS.items():
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, typ):
            raise BadRequest(f"'{key}' must be a {typ.__name__}")
        if key == "status" and value not in STATUSES:
            raise BadRequest(f"'status' must be one of {', '.join(STATUSES)}")
        if key == "media":
            value = [Path(v).name for v in value if isinstance(v, str) and v]
        post[key] = value
    if post.get("status") == "posted":
        post["posted_at"] = post.get("posted_at") or now_iso()
    else:
        post["posted_at"] = None
    post["updated_at"] = now_iso()
    return post


def new_post(payload=None):
    stamp = now_iso()
    post = {
        "id": uuid.uuid4().hex[:12],
        "title": "Untitled post",
        "body": "",
        "hashtags": "",
        "status": "draft",
        "notes": "",
        "media": [],
        "created_at": stamp,
        "updated_at": stamp,
        "posted_at": None,
    }
    return apply_fields(post, payload or {})


class PostStore:
    def __init__(self):
        self._lock = threading.Lock()
        DATA_DIR.mkdir(exist_ok=True)
        MEDIA_DIR.mkdir(exist_ok=True)
        if not POSTS_FILE.exists():
            seed = []
            if SEED_FILE.exists():
                seed = [new_post(p) for p in json.loads(SEED_FILE.read_text("utf-8"))]
            self._write(seed)

    def _read(self):
        return json.loads(POSTS_FILE.read_text("utf-8"))

    def _write(self, posts):
        tmp = POSTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(posts, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, POSTS_FILE)

    def list(self):
        with self._lock:
            return self._read()

    def create(self, payload):
        with self._lock:
            posts = self._read()
            post = new_post(payload)
            posts.append(post)
            self._write(posts)
            return post

    def update(self, post_id, payload):
        with self._lock:
            posts = self._read()
            for post in posts:
                if post["id"] == post_id:
                    apply_fields(post, payload)
                    self._write(posts)
                    return post
            return None

    def delete(self, post_id):
        with self._lock:
            posts = self._read()
            kept = [p for p in posts if p["id"] != post_id]
            if len(kept) == len(posts):
                return False
            self._write(kept)
            return True


def list_media():
    items = []
    for path in sorted(MEDIA_DIR.iterdir(), key=lambda p: p.stat().st_mtime):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            items.append({"name": path.name, "size": path.stat().st_size})
    return items


def safe_media_name(raw):
    name = Path(raw or "").name
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in IMAGE_EXTS:
        raise BadRequest(f"Unsupported image type '{ext or '?'}'")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "photo"
    candidate = f"{stem}{ext}"
    counter = 2
    while (MEDIA_DIR / candidate).exists():
        candidate = f"{stem}-{counter}{ext}"
        counter += 1
    return candidate


store = None  # set in main()


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisLinkedIn/1.0"

    # ---------- helpers ----------
    def log_message(self, fmt, *args):
        sys.stderr.write("  %s %s\n" % (self.command, fmt % args))

    def _send_json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, message):
        self._send_json(code, {"error": message})

    def _read_body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise BadRequest(f"Request too large (limit {limit // (1024 * 1024)} MB)")
        return self.rfile.read(length) if length else b""

    def _read_json(self):
        raw = self._read_body(MAX_JSON_BYTES)
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise BadRequest(f"Invalid JSON: {exc}")

    def _serve_file(self, base, rel):
        base = base.resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return self._error(404, "Not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        parsed = urlparse(self.path)
        return unquote(parsed.path), parse_qs(parsed.query)

    def _post_id(self, path):
        match = re.fullmatch(r"/api/posts/([A-Za-z0-9]+)", path)
        return match.group(1) if match else None

    def _guard(self, fn):
        try:
            fn()
        except BadRequest as exc:
            self._error(400, str(exc))
        except Exception as exc:  # keep the server alive on unexpected errors
            self._error(500, f"Server error: {exc}")

    # ---------- verbs ----------
    def do_GET(self):
        def run():
            path, _ = self._route()
            if path == "/api/posts":
                return self._send_json(200, store.list())
            if path == "/api/media":
                return self._send_json(200, list_media())
            if path.startswith("/media/"):
                return self._serve_file(MEDIA_DIR, path[len("/media/"):])
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            return self._serve_file(STATIC_DIR, rel)

        self._guard(run)

    def do_POST(self):
        def run():
            path, query = self._route()
            if path == "/api/posts":
                return self._send_json(201, store.create(self._read_json()))
            if path == "/api/media":
                name = safe_media_name((query.get("name") or [""])[0])
                data = self._read_body(MAX_UPLOAD_BYTES)
                if not data:
                    raise BadRequest("Empty upload")
                (MEDIA_DIR / name).write_bytes(data)
                return self._send_json(201, {"name": name, "size": len(data)})
            return self._error(404, "Not found")

        self._guard(run)

    def do_PUT(self):
        def run():
            path, _ = self._route()
            post_id = self._post_id(path)
            if not post_id:
                return self._error(404, "Not found")
            post = store.update(post_id, self._read_json())
            if post is None:
                return self._error(404, "Post not found")
            return self._send_json(200, post)

        self._guard(run)

    def do_DELETE(self):
        def run():
            path, _ = self._route()
            post_id = self._post_id(path)
            if not post_id or not store.delete(post_id):
                return self._error(404, "Post not found")
            self.send_response(204)
            self.end_headers()

        self._guard(run)


def main():
    global store
    parser = argparse.ArgumentParser(description="Jarvis LinkedIn Manager")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default 8000)")
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    args = parser.parse_args()

    store = PostStore()
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        sys.exit(f"Could not start on port {args.port}: {exc}\nTry another port: --port 8080")

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"Jarvis LinkedIn Manager running at {url}")
    print(f"  drafts: {POSTS_FILE}")
    print(f"  photos: {MEDIA_DIR}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
