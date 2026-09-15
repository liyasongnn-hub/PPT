"""Durable local platform store for the PPT MVP.

The adapter deliberately uses SQLite so the product workflow is usable without
Docker. A later PostgreSQL/Redis implementation can keep the same API methods.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class PlatformStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outline TEXT NOT NULL DEFAULT '',
                    document_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, id);
                """
            )
            self._ensure_column("projects", "document_json", "TEXT NOT NULL DEFAULT '{}'")
            # In-process jobs cannot survive an API restart. Make that state
            # explicit so clients do not poll an interrupted job forever.
            self._conn.execute(
                """
                UPDATE jobs
                SET status='FAILED', error='服务重启导致任务中断，请重新生成', updated_at=?
                WHERE status IN ('QUEUED', 'RUNNING')
                """,
                (_now(),),
            )

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
        return f"pbkdf2$210000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        try:
            scheme, rounds, salt, expected = encoded.split("$", 3)
            if scheme != "pbkdf2":
                return False
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds))
            return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
        except (ValueError, TypeError):
            return False

    def create_user(self, email: str, password: str, display_name: str) -> dict[str, Any]:
        email = email.strip().lower()
        if len(email) > 160 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("邮箱格式不正确")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 位")
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(
                    "INSERT INTO users(email,password_hash,tenant_id,display_name,created_at) VALUES(?,?,?,?,?)",
                    (email, self.hash_password(password), f"tenant_{secrets.token_hex(6)}", display_name.strip() or email, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("邮箱已注册") from exc
        return self.get_user(int(cur.lastrowid))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT id,email,tenant_id,display_name,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not row or not self.verify_password(password, row["password_hash"]):
            return None
        return {k: row[k] for k in ("id", "email", "tenant_id", "display_name", "created_at")}

    def create_project(self, owner_id: int, tenant_id: str, title: str, prompt: str, config: dict[str, Any]) -> dict[str, Any]:
        project_id = secrets.token_urlsafe(12)
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO projects(id,tenant_id,owner_id,title,prompt,config_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (project_id, tenant_id, owner_id, title.strip() or "未命名演示文稿", prompt.strip(), json.dumps(config, ensure_ascii=False), "DRAFT", now, now),
            )
        return self.get_project(project_id, owner_id)

    def list_projects(self, owner_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects WHERE owner_id=? ORDER BY updated_at DESC", (owner_id,)).fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: str, owner_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id, owner_id)).fetchone()
        return self._project(row) if row else None

    def update_project(self, project_id: str, owner_id: int, **changes: Any) -> dict[str, Any] | None:
        allowed = {"title", "prompt", "config_json", "status", "outline", "document_json"}
        updates = [(key, value) for key, value in changes.items() if key in allowed and value is not None]
        if not updates:
            return self.get_project(project_id, owner_id)
        updates.append(("updated_at", _now()))
        clause = ",".join(f"{key}=?" for key, _ in updates)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE projects SET {clause} WHERE id=? AND owner_id=?", [value for _, value in updates] + [project_id, owner_id])
        return self.get_project(project_id, owner_id)

    @staticmethod
    def _project(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json") or "{}")
        item["document"] = json.loads(item.pop("document_json") or "{}")
        return item

    def create_job(self, project_id: str, owner_id: int, job_type: str) -> dict[str, Any]:
        job_id = secrets.token_urlsafe(12)
        now = _now()
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO jobs(id,project_id,owner_id,type,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (job_id, project_id, owner_id, job_type, "QUEUED", now, now))
        self.add_event(job_id, "job.queued", "decision", 0, {})
        return self.get_job(job_id, owner_id)

    def get_job(self, job_id: str, owner_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item

    def get_active_job(self, project_id: str, owner_id: int, job_type: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id FROM jobs
                WHERE project_id=? AND owner_id=? AND type=? AND status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, owner_id, job_type),
            ).fetchone()
        return self.get_job(row["id"], owner_id) if row else None

    def update_job(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "result_json", "error"}
        updates = [(key, value) for key, value in changes.items() if key in allowed]
        if not updates:
            return
        updates.append(("updated_at", _now()))
        clause = ",".join(f"{key}=?" for key, _ in updates)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {clause} WHERE id=?", [value for _, value in updates] + [job_id])

    def add_event(self, job_id: str, event_type: str, stage: str, progress: int, payload: dict[str, Any]) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT INTO job_events(job_id,event_type,stage,progress,payload_json,created_at) VALUES(?,?,?,?,?,?)", (job_id, event_type, stage, max(0, min(100, int(progress))), json.dumps(payload, ensure_ascii=False), _now()))
        return int(cur.lastrowid)

    def events_after(self, job_id: str, event_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id", (job_id, event_id)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(user: dict[str, Any], secret: str, token_type: str = "access", ttl_seconds: int = 3600) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": str(user["id"]), "tenant_id": user["tenant_id"], "type": token_type, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        _, payload, signature = token.split(".", 2)
        header, _, _ = token.split(".", 2)
        expected = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
