"""Authenticated gateway. The separate worker owns execution and its event log."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager,asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field


class Settings:
    def __init__(self) -> None:
        self.data = Path(os.environ["WEB_AI_DATA_DIR"]).expanduser().resolve()
        self.uploads = self.data / "uploads"
        self.db = self.data / "gateway.sqlite3"
        self.secret = os.environ["WEB_AI_SESSION_SECRET"].encode()
        self.worker_token = os.environ["WEB_AI_WORKER_TOKEN"]
        if len(self.secret) < 32 or len(self.worker_token) < 32:
            raise RuntimeError("Session secret and worker token must be at least 32 characters")
        self.worker_url = os.getenv("WEB_AI_WORKER_URL", "http://127.0.0.1:18731").rstrip("/")
        parsed = urlparse(self.worker_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if parsed.scheme != "http" or not loopback or parsed.username or parsed.password:
            raise RuntimeError("Worker URL must be a loopback HTTP address")
        roots = [p for p in os.environ["WEB_AI_ALLOWED_ROOTS"].split(os.pathsep) if p]
        if not roots or any(not Path(p).is_absolute() or ".." in Path(p).parts or "\x00" in p for p in roots):
            raise RuntimeError("Allowed directories must be absolute and cannot contain traversal")
        # The execution identity performs the authoritative filesystem checks.
        self.roots = [Path(os.path.normpath(p)) for p in roots]
        self.secure_cookie = os.getenv("WEB_AI_HTTPS", "0") == "1"


os.umask(0o077)
settings = Settings()
settings.data.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(settings.data, 0o700)
settings.uploads.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(settings.uploads, 0o700)
CODER_MODEL=os.getenv("WEB_AI_CODER_MODEL", "")
REVIEWER_MODEL=os.getenv("WEB_AI_REVIEWER_MODEL", "")
from backend import system_metrics

@asynccontextmanager
async def app_lifespan(app):
    system_metrics.start()
    try:
        yield
    finally:
        system_metrics.stop()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,lifespan=app_lifespan)
session_locks: dict[str, asyncio.Lock] = {}


@contextmanager
def db():
    conn = sqlite3.connect(settings.db, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        with conn:
            yield conn
    finally:
        conn.close()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, _ = encoded.split("$")
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def initialize() -> None:
    with db() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
            cwd TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, reasoning_effort TEXT,
            permission_mode TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(user_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS auto_title_eligible (
            session_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_settings (
            session_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL, model TEXT, reasoning_effort TEXT,
            PRIMARY KEY (session_id, request_id)
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
            path TEXT NOT NULL, size INTEGER NOT NULL, content_type TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        """)
        from backend.transfers import SCHEMA as TRANSFER_SCHEMA
        conn.executescript(TRANSFER_SCHEMA)
        from backend.project_defaults import SCHEMA as DEFAULTS_SCHEMA
        conn.executescript(DEFAULTS_SCHEMA)
        from backend.attention import SCHEMA as ATTENTION_SCHEMA
        conn.executescript(ATTENTION_SCHEMA)
        from backend.project_preferences import SCHEMA as PREFERENCES_SCHEMA
        conn.executescript(PREFERENCES_SCHEMA)
        migrations = {
            'conversations': {'archived_at':'INTEGER NOT NULL DEFAULT 0', 'collaboration_enabled': 'INTEGER NOT NULL DEFAULT 0',
                'reviewer_effort': "TEXT NOT NULL DEFAULT 'high'",
                'coder_effort': "TEXT NOT NULL DEFAULT 'high'", 'pinned_at': 'INTEGER NOT NULL DEFAULT 0'},
            'request_settings': {'collaboration': 'TEXT'}
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + definition)
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password = os.getenv("WEB_AI_ADMIN_PASSWORD")
            if not password or len(password) < 12:
                raise RuntimeError("Set WEB_AI_ADMIN_PASSWORD (at least 12 characters) for first initialization")
            conn.execute("INSERT INTO users VALUES (?,?,?,1)", (uuid.uuid4().hex, os.getenv("WEB_AI_ADMIN_USERNAME", "admin"), hash_password(password)))


initialize()
for private_file in (settings.db, Path(str(settings.db) + "-wal"), Path(str(settings.db) + "-shm")):
    if private_file.exists():
        os.chmod(private_file, 0o600)


def token_hash(token: str) -> str:
    return hmac.new(settings.secret, token.encode(), hashlib.sha256).hexdigest()


def csrf(token: str) -> str:
    return hmac.new(settings.secret, ("csrf:" + token).encode(), hashlib.sha256).hexdigest()


def current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get("web_ai_session", "")
    with db() as conn:
        row = conn.execute("""SELECT users.* FROM users JOIN auth_sessions ON users.id=auth_sessions.user_id
             WHERE auth_sessions.token_hash=? AND auth_sessions.expires_at>?""",
             (token_hash(token), int(time.time()))).fetchone() if token else None
    if not row:
        raise HTTPException(401, "请先登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf(token)):
            raise HTTPException(403, "请求校验失败，请刷新页面")
    return dict(row)


def require_project_admin(user: dict[str, Any]) -> None:
    if not user["is_admin"]:
        raise HTTPException(403, "\u666e\u901a\u7528\u6237\u6682\u672a\u914d\u7f6e\u9879\u76ee\u76ee\u5f55\uff0c\u4ec5\u7ba1\u7406\u5458\u53ef\u4f7f\u7528")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"username": user["username"], "is_admin": bool(user["is_admin"])}


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)


# Temporary per-process throttle; production should also rate-limit at the reverse proxy.
login_attempts: dict[str, list[float]] = {}


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response) -> dict[str, Any]:
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "请求来源不匹配")
    host = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [t for t in login_attempts.get(host, []) if now - t < 300]
    if len(attempts) >= 10:
        raise HTTPException(429, "登录尝试过多，请稍后重试")
    attempts.append(now)
    login_attempts[host] = attempts
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "账号或密码错误")
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO auth_sessions VALUES (?,?,?)", (token_hash(token), row["id"], int(now) + 30 * 86400))
    login_attempts.pop(host, None)
    response.set_cookie("web_ai_session", token, max_age=30 * 86400, httponly=True,
                        secure=settings.secure_cookie, samesite="strict", path="/")
    return {"user": public_user(dict(row)), "csrf_token": csrf(token)}


@app.get("/api/auth/me")
def me(request: Request, user: dict = Depends(current_user)) -> dict:
    return {"user": public_user(user), "csrf_token": csrf(request.cookies["web_ai_session"])}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash(request.cookies["web_ai_session"]),))
    response.delete_cookie("web_ai_session", path="/")
    return {"ok": True}


class NewUser(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=1024)


@app.post("/api/users", status_code=201)
def add_user(body: NewUser, user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "仅管理员可以创建用户")
    try:
        with db() as conn:
            conn.execute("INSERT INTO users VALUES (?,?,?,0)", (uuid.uuid4().hex, body.username, hash_password(body.password)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "账号已存在")
    return {"ok": True, "username": body.username}


def safe_dir(path: str) -> Path:
    # Lexical gate only: the isolated worker performs the authoritative
    # filesystem/symlink check under its own OS identity.
    try:
        candidate = Path(path)
        if not candidate.is_absolute() or ".." in candidate.parts or "\x00" in path:
            raise ValueError()
        result = Path(os.path.normpath(str(candidate)))
    except (ValueError, OSError, RuntimeError, TypeError):
        raise HTTPException(400, "\u76ee\u5f55\u8def\u5f84\u65e0\u6548")
    if not any(result == root or root in result.parents for root in settings.roots):
        raise HTTPException(403, "\u76ee\u5f55\u4e0d\u5728\u6388\u6743\u8303\u56f4\u5185")
    return result


@app.get("/api/directories")
async def directories(path: str | None = None, user: dict = Depends(current_user)) -> dict:
    require_project_admin(user)
    params = {}
    if path is not None:
        params["path"] = str(safe_dir(path))
    return await worker("GET", "/directories", params=params)


class NewDirectory(BaseModel):
    parent: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=200)


@app.post('/api/directories', status_code=201)
async def create_directory(body: NewDirectory, user: dict = Depends(current_user)) -> dict:
    require_project_admin(user)
    return await worker('POST', '/directories', json={'parent': str(safe_dir(body.parent)), 'name': body.name})


async def worker(method: str, path: str, **kwargs: Any) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            result = await client.request(method, settings.worker_url + path,
                headers={"Authorization": "Bearer " + settings.worker_token}, **kwargs)
        if not result.is_success:
            try:
                detail = result.json().get("detail") or result.json().get("error") or "执行服务返回错误"
            except ValueError:
                detail = "执行服务返回错误"
            raise HTTPException(result.status_code, detail)
        return result.json()
    except httpx.RequestError:
        raise HTTPException(503, "执行服务未连接或不可用")


@app.get('/api/diagnostics')
async def diagnostics(session_id: str | None = None, download: bool = False, user: dict = Depends(current_user)):
    require_project_admin(user)
    if session_id is not None:
        conversation(session_id,user)
    from backend.diagnostics import bundle
    service={};raw=None;available=True
    try:
        service=await worker('GET','/diagnostics')
        if session_id is not None:
            raw=await worker('GET',f'/sessions/{session_id}/status')
            raw['resources']=await worker('GET',f'/sessions/{session_id}/resources')
    except HTTPException as exc:
        if exc.status_code!=503:raise
        available=False
    data=bundle(service,raw,available)
    headers={'Cache-Control':'no-store'}
    if download:
        headers['Content-Disposition']='attachment; filename="workbench-diagnostics.json"'
    return Response(content=json.dumps(data,ensure_ascii=False),media_type='application/json',headers=headers)


@app.get("/api/quota")
async def quota(user: dict = Depends(current_user)) -> dict:
    require_project_admin(user)
    return await worker("GET", "/quota")


@app.post("/api/quota/refresh")
async def refresh_quota(user: dict = Depends(current_user)) -> dict:
    require_project_admin(user)
    return await worker("POST", "/quota/refresh")


@app.get("/api/providers")
async def providers(user: dict = Depends(current_user)) -> dict:
    try:
        capability = await worker("GET", "/capabilities")
        configured = bool(capability.get("available") and capability.get("logged_in"))
        return {"providers": [
            {"id": "claude", "name": "Claude Code", "configured": configured,
             "reason": ("仅管理员可使用当前 Claude 账户" if not user["is_admin"] else None),
             **capability, "collaboration_models":{"coder":CODER_MODEL or None,"reviewer":REVIEWER_MODEL or None}},
            {"id": "codex", "name": "Codex", "configured": False, "reason": "尚未接入"},
            {"id": "deepseek", "name": "DeepSeek", "configured": False, "reason": "尚未接入"},
        ]}
    except HTTPException:
        return {"providers": [{"id": "claude", "name": "Claude Code", "configured": False,
                               "reason": "执行服务未连接或能力探测不可用"},
                              {"id": "codex", "name": "Codex", "configured": False},
                              {"id": "deepseek", "name": "DeepSeek", "configured": False}]}


async def validate_model_selection(model: str | None, effort: str | None) -> None:
    """Validate candidate metadata, without claiming actual account entitlement."""
    if model is None and effort is None:
        return
    if model is not None and (model == "default" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model)):
        raise HTTPException(400, "\u6a21\u578b\u683c\u5f0f\u65e0\u6548")
    if effort is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", effort):
        raise HTTPException(400, "\u601d\u8003\u5f3a\u5ea6\u683c\u5f0f\u65e0\u6548")
    capability = await worker("GET", "/capabilities")
    models = capability.get("models")
    if not capability.get("model_list_available") or not isinstance(models, list):
        raise HTTPException(503, "\u65e0\u6cd5\u83b7\u53d6\u6a21\u578b\u5019\u9009\u5217\u8868")
    selected_value = model if model is not None else "default"
    selected = next((item for item in models if isinstance(item, dict) and item.get("value") == selected_value), None)
    if not selected or selected.get("selectable") is not True:
        raise HTTPException(400, "\u8be5\u6a21\u578b\u4e0d\u5728\u53ef\u9009\u5019\u9009\u5217\u8868\u4e2d")
    if effort is not None:
        levels = selected.get("supportedEffortLevels")
        if not isinstance(levels, list) or effort not in levels:
            raise HTTPException(400, "\u5f53\u524d\u6a21\u578b\u7684\u601d\u8003\u5f3a\u5ea6\u4e0d\u53ef\u7528\u6216\u672a\u77e5")


async def collaboration_snapshot(enabled: bool, reviewer: str, coder: str) -> dict | None:
    if not enabled:
        return None
    capability = await worker("GET", "/capabilities")
    models = capability.get("models")
    if not capability.get("model_list_available") or not isinstance(models, list):
        raise HTTPException(503, "\u6682\u65e0\u6a21\u578b\u5217\u8868\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5")
    if not CODER_MODEL or not REVIEWER_MODEL:raise HTTPException(400,'Collaboration role models are not configured')
    for target, effort in [(REVIEWER_MODEL, reviewer), (CODER_MODEL, coder)]:
        matches = [item for item in models if isinstance(item, dict) and item.get('selectable') is True
                   and (item.get('resolvedModel') or item.get('value')) == target]
        if not any(effort in (item.get('supportedEffortLevels') or []) for item in matches):
            raise HTTPException(400, target + " \u6216\u6240\u9009\u63a8\u7406\u5f3a\u5ea6\u5f53\u524d\u4e0d\u53ef\u7528")
    return dict(version=1, coder_model=CODER_MODEL, reviewer_model=REVIEWER_MODEL,
                reviewer_effort=reviewer, coder_effort=coder)


Effort = Literal['low', 'medium', 'high', 'xhigh', 'max']


class NewConversation(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=200)
    cwd: str
    provider: str = "claude"
    model: str | None = None
    reasoning_effort: str | None = None
    permission_mode: str = "dontAsk"
    collaboration_enabled: bool = False
    reviewer_effort: Effort = 'high'
    coder_effort: Effort = 'high'


def conversation(session_id: str, user: dict) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (session_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "会话不存在")
    return dict(row)


@app.get('/api/session-statuses')
async def session_statuses(focus: str | None = None, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        ids=[row[0] for row in conn.execute('SELECT id FROM conversations WHERE user_id=?',(user['id'],))]
    if focus is not None:
        conversation(focus,user)
    data=await worker('POST','/session-statuses',json={'ids':ids,'focus':focus}) if ids else {'sessions':{}}
    from backend.attention import enrich
    import sys
    return await enrich(sys.modules[__name__],user,data)

class ReadCursor(BaseModel):
    seq: int = Field(ge=0)

@app.post('/api/sessions/{session_id}/read')
async def mark_session_read(session_id: str, body: ReadCursor, user: dict = Depends(current_user)):
    conversation(session_id,user)
    from backend.attention import mark
    import sys
    return await mark(sys.modules[__name__],user,session_id,body.seq)

@app.post('/api/sessions/{session_id}/approvals/{request_id}')
async def answer_approval(session_id: str,request_id: str,payload: dict,user: dict = Depends(current_user)) -> dict:
    conversation(session_id,user)
    if not user['is_admin']:raise HTTPException(403,'\u4ec5\u7ba1\u7406\u5458\u53ef\u56de\u590d\u5b98\u65b9\u5ba1\u6279')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',request_id):raise HTTPException(400,'Invalid approval ID')
    return await worker('POST',f'/tasks/{session_id}/approvals/{request_id}',json=payload)

class ProjectPreferencePatch(BaseModel):
    name: str | None = Field(default=None,max_length=100)
    collapsed: bool | None = None

@app.get('/api/project-preferences')
async def get_project_preferences(user: dict = Depends(current_user)):
    from backend.project_preferences import list_for
    with db() as conn:return {'projects':list_for(conn,user['id'])}

@app.patch('/api/sessions/{session_id}/project-preferences')
async def patch_project_preferences(session_id: str, body: ProjectPreferencePatch, user: dict = Depends(current_user)):
    session=conversation(session_id,user)
    values=body.model_dump(exclude_unset=True)
    if not values or any(v is None for v in values.values()):raise HTTPException(422,'Invalid project preference')
    if 'name' in values:
        values['name']=values['name'].strip()
        if re.search(r'[\x00-\x1f\x7f]',values['name']):raise HTTPException(422,'Project name contains control characters')
    from backend.project_preferences import save
    with db() as conn:result=save(conn,user['id'],session['cwd'],values)
    return {'cwd':session['cwd'],'project':result}

class ProjectDefaults(BaseModel):
    model: str | None = None
    reasoning_effort: str | None = None
    collaboration_enabled: bool = False
    reviewer_effort: Effort = 'high'
    coder_effort: Effort = 'high'

@app.get('/api/project-defaults')
async def get_project_defaults(cwd: str, user: dict = Depends(current_user)):
    require_project_admin(user)
    canonical=str(safe_dir(cwd))
    from backend.project_defaults import get
    import sys
    return {'defaults':get(sys.modules[__name__],user,canonical),'cwd':canonical}

@app.put('/api/sessions/{session_id}/project-defaults')
async def save_project_defaults(session_id: str, body: ProjectDefaults, user: dict = Depends(current_user)):
    require_project_admin(user)
    session=conversation(session_id,user)
    from backend.project_defaults import put
    import sys
    return {'defaults':await put(sys.modules[__name__],user,session,body)}

@app.get("/api/sessions")
async def list_sessions(q: str = "", archived: bool = False, user: dict = Depends(current_user)) -> dict:
    with db() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM conversations WHERE user_id=? AND (archived_at > 0)=? ORDER BY (pinned_at > 0) DESC,pinned_at DESC,updated_at DESC,id", (user["id"],int(archived))).fetchall()]
    if not q.strip():
        return {"sessions": rows, "search_complete": True}
    q = q.strip()[:200]
    matches = []
    complete = True
    for row in rows:
        if q.casefold() in row["title"].casefold():
            matches.append(row)
            continue
        try:
            result = await worker("GET", f"/sessions/{row['id']}/search", params={"q": q})
            if result.get("matched"):
                matches.append(row)
        except HTTPException:
            complete = False
    return {"sessions": matches, "search_complete": complete}


class ArchiveConversation(BaseModel):
    archived: bool

@app.post('/api/sessions/{session_id}/archive')
async def archive_conversation(session_id: str, body: ArchiveConversation, user: dict = Depends(current_user)):
    async with session_locks.setdefault(session_id,asyncio.Lock()):
        conversation(session_id,user)
        if body.archived:
            state=await worker('GET',f'/sessions/{session_id}/status')
            if state['status'] in ('running','starting','stopping','unknown','queued') or state.get('queue') or state.get('approvals'):
                raise HTTPException(409,'\u8bf7\u7b49\u5f85\u4efb\u52a1\u7ed3\u675f\u5e76\u5904\u7406\u5f85\u9001\u8fbe\u6d88\u606f\u540e\u518d\u5f52\u6863')
        with db() as conn:conn.execute('UPDATE conversations SET archived_at=? WHERE id=? AND user_id=?',(int(time.time()) if body.archived else 0,session_id,user['id']))
        return {'session':conversation(session_id,user)}

@app.get('/api/sessions/{session_id}/project-view')
async def project_view(session_id: str, kind: Literal['tree','text','diff','changes'] = 'tree', path: str = '', user: dict = Depends(current_user)):
    require_project_admin(user);conversation(session_id,user)
    return await worker('GET',f'/sessions/{session_id}/project-{kind}',params={'path':path})


@app.post("/api/sessions", status_code=201)
async def create_session(body: NewConversation, user: dict = Depends(current_user)) -> dict:
    require_project_admin(user)
    if body.provider != "claude":
        raise HTTPException(400, "该服务商尚未配置")
    if body.collaboration_enabled:
        await collaboration_snapshot(True, body.reviewer_effort, body.coder_effort)
    else:
        await validate_model_selection(body.model, body.reasoning_effort)
    if Path(body.cwd).is_absolute():
        cwd = str(safe_dir(body.cwd))
    else:
        project = await worker("POST", "/directories/project", json={"name": body.cwd})
        cwd = str(safe_dir(project.get("path", "")))
    sid = uuid.uuid4().hex
    now = int(time.time())
    # Register before committing gateway metadata; disconnected workers must
    # not produce a session that looks usable. Clean up an orphan if the DB
    # commit fails after successful registration.
    await worker("POST", f"/sessions/{sid}/register", json={"cwd": cwd})
    try:
        with db() as conn:
            conn.execute("INSERT INTO conversations (id,user_id,title,cwd,provider,model,reasoning_effort,"
                         "permission_mode,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, user["id"], body.title.strip(), cwd,
                body.provider, body.model, body.reasoning_effort, body.permission_mode, now, now))
            conn.execute("UPDATE conversations SET collaboration_enabled=?,reviewer_effort=?,coder_effort=? WHERE id=?",
                (int(body.collaboration_enabled), body.reviewer_effort, body.coder_effort, sid))
            if body.title.strip() == "\u65b0\u4f1a\u8bdd":
                conn.execute("INSERT INTO auto_title_eligible VALUES (?)", (sid,))
    except Exception:
        try:
            await worker("DELETE", f"/sessions/{sid}")
        except HTTPException:
            pass
        raise
    return {"session": conversation(sid, user)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, user: dict = Depends(current_user)) -> dict:
    return {"session": conversation(session_id, user)}


class EditConversation(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=100)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    collaboration_enabled: bool | None = None
    reviewer_effort: Effort | None = None
    coder_effort: Effort | None = None
    pinned: bool | None = None


@app.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, body: EditConversation, user: dict = Depends(current_user)) -> dict:
    fields = body.model_fields_set
    if not fields:
        raise HTTPException(400, "\u672a\u63d0\u4f9b\u9700\u8981\u4fee\u6539\u7684\u8bbe\u7f6e")
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        current = conversation(session_id, user)
        title = current["title"]
        if "title" in fields:
            if body.title is None or not body.title.strip():
                raise HTTPException(400, "\u6807\u9898\u4e0d\u80fd\u4e3a\u7a7a")
            title = body.title.strip()
        model = body.model if "model" in fields else current["model"]
        effort = body.reasoning_effort if "reasoning_effort" in fields else current["reasoning_effort"]
        if fields.intersection({"model", "reasoning_effort"}):
            require_project_admin(user)
            if (model, effort) != (current["model"], current["reasoning_effort"]):
                await validate_model_selection(model, effort)
        collaboration_fields = {'collaboration_enabled', 'reviewer_effort', 'coder_effort'}
        enabled, reviewer, coder = (current['collaboration_enabled'], current['reviewer_effort'], current['coder_effort'])
        if fields.intersection(collaboration_fields):
            require_project_admin(user)
            if any(getattr(body, key) is None for key in fields.intersection(collaboration_fields)):
                raise HTTPException(400, "Invalid collaboration setting")
            enabled = body.collaboration_enabled if 'collaboration_enabled' in fields else bool(enabled)
            reviewer = body.reviewer_effort if 'reviewer_effort' in fields else reviewer
            coder = body.coder_effort if 'coder_effort' in fields else coder
            await collaboration_snapshot(enabled, reviewer, coder)
        if 'pinned' in fields and body.pinned is None:
            raise HTTPException(400, "Invalid pin setting")
        pinned_at = current['pinned_at']
        if 'pinned' in fields:
            pinned_at = (pinned_at or time.time_ns()) if body.pinned else 0
        with db() as conn:
            if "title" in fields:
                conn.execute("DELETE FROM auto_title_eligible WHERE session_id=?", (session_id,))
            conn.execute("UPDATE conversations SET title=?,model=?,reasoning_effort=?,updated_at=? WHERE id=? AND user_id=?",
                (title, model, effort, int(time.time()), session_id, user["id"]))
            conn.execute("UPDATE conversations SET collaboration_enabled=?,reviewer_effort=?,coder_effort=?,pinned_at=? WHERE id=?",
                         (int(enabled), reviewer, coder, pinned_at, session_id))
        return {"session": conversation(session_id, user)}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(current_user)) -> dict:
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        conversation(session_id, user)
        result = await worker("DELETE", f"/sessions/{session_id}")
        with db() as conn:
            conn.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (session_id, user["id"]))
        return {"ok": True, "provider_history_retained": result.get("provider_history_retained", True)}


@app.get("/api/sessions/{session_id}/status")
async def status(session_id: str, user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    return await worker("GET", f"/sessions/{session_id}/status")


@app.get("/api/sessions/{session_id}/requests/{request_id}")
async def request_receipt(session_id: str, request_id: str, user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise HTTPException(400, 'Invalid request ID')
    return await worker("GET", f"/sessions/{session_id}/requests/{request_id}")


@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str, after: int = Query(0, ge=0), before: int | None = Query(None, ge=1),
                 limit: int = Query(100, ge=1, le=500), user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    params = {"after": after, "limit": limit}
    if before is not None:
        params["before"] = before
    result = await worker("GET", f"/sessions/{session_id}/events", params=params)
    return history_attachments(result, session_id, user)


def history_attachments(result: dict, session_id: str, user: dict) -> dict:
    with db() as conn:
        for event in result.get("events", []):
            data = event.get("data")
            if event.get("type") != "user" or not isinstance(data, dict):
                continue
            attachments = []
            raw_paths = data.get("attachments", [])
            for raw in raw_paths if isinstance(raw_paths, list) else []:
                if not isinstance(raw, str) or "\x00" in raw:
                    continue
                path = Path(raw)
                parts = path.parts
                if (not path.is_absolute() or ".." in parts or len(parts) < 4
                    or parts[-3] != session_id
                    or not re.fullmatch(r"[0-9a-f]{32}", parts[-2])
                    or not re.fullmatch(r"file(?:\.[a-z0-9]{1,15})?", parts[-1])):
                    continue
                row = conn.execute("SELECT id,name,size,content_type FROM uploads WHERE id=? AND user_id=?",
                                   (parts[-2], user["id"])).fetchone()
                if row:
                    attachments.append(dict(row))
            data["attachments"] = attachments
    return result


@app.get("/api/sessions/{session_id}/history")
async def history(session_id: str, before: int | None = Query(None, ge=1, le=9223372036854775807),
                  limit: int = Query(100, ge=1, le=100), user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    params = {"limit": limit}
    if before is not None:
        params["before"] = before
    result = await worker("GET", f"/sessions/{session_id}/history", params=params)
    return history_attachments(result, session_id, user)


@app.get("/api/sessions/{session_id}/updates")
async def history_updates(session_id: str, after: int = Query(0, ge=0, le=9223372036854775807),
                          limit: int = Query(2000, ge=1, le=2000), user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    result = await worker("GET", f"/sessions/{session_id}/updates", params={"after": after, "limit": limit})
    return history_attachments(result, session_id, user)


@app.get("/api/sessions/{session_id}/history-detail")
async def history_detail(session_id: str, seq: int = Query(..., ge=1, le=9223372036854775807),
                         block: int = Query(..., ge=0), user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    return await worker("GET", f"/sessions/{session_id}/history-detail", params={"seq": seq, "block": block})


class Turn(BaseModel):
    request_id: str = Field(pattern=r"^[a-fA-F0-9-]{32,36}$")
    prompt: str = Field(default="", max_length=200_000)
    attachments: list[str] = Field(default_factory=list, max_length=20)


async def transfer_attachment(session_id: str, row: dict) -> None:
    # The runner cannot read the gateway's private upload directory. Hold an
    # O_NOFOLLOW fd while sending only this authorized upload over loopback.
    fd, length = open_secure_file(settings.uploads, Path(row["path"]))
    suffix = Path(row["name"]).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,15}", suffix):
        suffix = ""
    async def content():
        remaining = length
        while remaining:
            chunk = await asyncio.to_thread(os.read, fd, min(65536, remaining))
            if not chunk:
                raise OSError("upload changed during transfer")
            remaining -= len(chunk)
            yield chunk
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.post(
                settings.worker_url + f"/sessions/{session_id}/attachments/{row['id']}",
                headers={"Authorization": "Bearer " + settings.worker_token,
                         "Content-Type": "application/octet-stream", "Content-Length": str(length),
                         "X-File-Suffix": suffix}, content=content())
        if not response.is_success:
            try:
                detail = response.json().get("detail") or response.json().get("error") or "\u9644\u4ef6\u4f20\u8f93\u5931\u8d25"
            except ValueError:
                detail = "\u9644\u4ef6\u4f20\u8f93\u5931\u8d25"
            raise HTTPException(response.status_code, detail)
    except (httpx.RequestError, OSError):
        raise HTTPException(503, "\u9644\u4ef6\u4f20\u8f93\u4e2d\u65ad\uff0c\u53ef\u91cd\u8bd5")
    finally:
        os.close(fd)


@app.post("/api/sessions/{session_id}/turns")
async def start_turn(session_id: str, body: Turn, user: dict = Depends(current_user)) -> dict:
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        session = conversation(session_id, user)
        if session.get('archived_at'):
            raise HTTPException(409,'\u8bf7\u5148\u6062\u590d\u5f52\u6863\u4f1a\u8bdd\u518d\u53d1\u9001')
        if not user["is_admin"]:
            raise HTTPException(403, "当前 Claude 账户仅供管理员使用；普通用户暂未配置独立凭据")
        safe_dir(session["cwd"])
        if not body.prompt.strip() and not body.attachments:
            raise HTTPException(400, "请输入消息或添加文件")
        # Persist preferences before any network I/O. A retry with the same
        # request ID must never silently switch to subsequently changed settings.
        with db() as conn:
            saved = conn.execute("SELECT 1 FROM request_settings WHERE session_id=? AND request_id=?",
                                 (session_id, body.request_id)).fetchone()
        snapshot = None
        if not saved:
            snapshot = await collaboration_snapshot(bool(session['collaboration_enabled']),
                                                    session['reviewer_effort'], session['coder_effort'])
        with db() as conn:
            conn.execute("INSERT OR IGNORE INTO request_settings (session_id,request_id,model,reasoning_effort,collaboration) VALUES (?,?,?,?,?)",
                (session_id, body.request_id, session["model"], session["reasoning_effort"],
                 json.dumps(snapshot, sort_keys=True) if snapshot else None))
            preferences = dict(conn.execute(
                "SELECT model,reasoning_effort,collaboration FROM request_settings WHERE session_id=? AND request_id=?",
                (session_id, body.request_id)).fetchone())
        await worker("POST", f"/sessions/{session_id}/register", json={"cwd": session["cwd"]})
        for file_id in body.attachments:
            with db() as conn:
                row = conn.execute("SELECT * FROM uploads WHERE id=? AND user_id=?", (file_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "\u9644\u4ef6\u4e0d\u5b58\u5728")
            await transfer_attachment(session_id, dict(row))
        task = {"session_id": session_id, "cwd": session["cwd"],
            "prompt": body.prompt, "request_id": body.request_id, "model": preferences["model"], "permission_mode": session["permission_mode"],
            "reasoning_effort": preferences["reasoning_effort"], "attachments": body.attachments}
        if preferences['collaboration']:
            task['collaboration'] = json.loads(preferences['collaboration'])
        result = await worker("POST", "/tasks", json=task)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM auto_title_eligible WHERE session_id=?", (session_id,)).fetchone():
                title = re.split(r"[\n\u3002\uff01\uff1f!?]", body.prompt.strip(), maxsplit=1)[0].strip() or body.prompt.strip()
                if not title and body.attachments:
                    row = conn.execute("SELECT name FROM uploads WHERE id=? AND user_id=?", (body.attachments[0], user["id"])).fetchone()
                    title = row[0] if row else ""
                if title:
                    conn.execute("UPDATE conversations SET title=? WHERE id=? AND user_id=?", (title[:60], session_id, user["id"]))
                    conn.execute("DELETE FROM auto_title_eligible WHERE session_id=?", (session_id,))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (int(time.time()), session_id))
            result["session_title"] = conn.execute("SELECT title FROM conversations WHERE id=? AND user_id=?", (session_id, user["id"])).fetchone()[0]
        return result


class ConfirmInterrupted(BaseModel):
    confirm: Literal[True]


@app.post("/api/sessions/{session_id}/confirm-interrupted")
async def confirm_interrupted(session_id: str, body: ConfirmInterrupted, user: dict = Depends(current_user)) -> dict:
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        conversation(session_id, user)
        if not user["is_admin"]:
            raise HTTPException(403, "\u4ec5\u7ba1\u7406\u5458\u53ef\u786e\u8ba4\u4efb\u52a1\u4e2d\u65ad")
        return await worker("POST", f"/tasks/{session_id}/confirm-interrupted", json={"confirm": True})


@app.post("/api/sessions/{session_id}/resume-queue")
async def resume_queue(session_id: str, user: dict = Depends(current_user)) -> dict:
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        conversation(session_id, user)
        if not user["is_admin"]:
            raise HTTPException(403, "仅管理员可使用当前 Claude 账户")
        return await worker("POST", f"/tasks/{session_id}/resume-queue")


@app.delete("/api/sessions/{session_id}/queue/{request_id}")
async def cancel_queue(session_id: str, request_id: str, user: dict = Depends(current_user)) -> dict:
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        conversation(session_id, user)
        if not user["is_admin"]:
            raise HTTPException(403, "仅管理员可使用当前 Claude 账户")
        if not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", request_id):
            raise HTTPException(400, "请求 ID 无效")
        return await worker("DELETE", f"/tasks/{session_id}/queue/{request_id}")


@app.post("/api/sessions/{session_id}/stop")
async def stop(session_id: str, user: dict = Depends(current_user)) -> dict:
    conversation(session_id, user)
    return await worker("POST", f"/tasks/{session_id}/stop")


@app.post("/api/uploads", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)) -> dict:
    import sys
    from backend.transfers import receive
    return await receive(sys.modules[__name__],request,file,user)


def open_secure_file(base: Path, requested: Path) -> tuple[int, int]:
    # Hold verified file descriptors through streaming. Resolve-then-open would
    # let a concurrent symlink replacement escape the allowed directory.
    if not base.is_absolute() or any(part in (".", "..") for part in base.parts):
        raise HTTPException(403, "\u76ee\u5f55\u65e0\u6548")
    if any(part == ".." for part in requested.parts):
        raise HTTPException(404, "\u6587\u4ef6\u4e0d\u5b58\u5728")
    try:
        relative = requested.relative_to(base) if requested.is_absolute() else requested
    except ValueError:
        raise HTTPException(403, "\u6587\u4ef6\u4e0d\u5728\u6388\u6743\u76ee\u5f55\u5185")
    parts = relative.parts
    if not parts or any(part in ("", ".", "..", "/") for part in parts):
        raise HTTPException(403, "\u6587\u4ef6\u8def\u5f84\u65e0\u6548")
    if not hasattr(os, "O_NOFOLLOW"):
        raise HTTPException(503, "\u5b89\u5168\u6587\u4ef6\u8bbf\u95ee\u4e0d\u53ef\u7528")
    directory_fd = None
    file_fd = None
    # O_PATH needs only directory traversal, while O_NOFOLLOW and O_DIRECTORY
    # still reject symlinks in every component. Linux supports the requested
    # least-privilege layout; other platforms fail closed with O_RDONLY.
    flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        directory_fd = os.open("/", flags)
        for component in (*base.parts[1:], *parts[:-1]):
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(403, "\u4e0d\u662f\u666e\u901a\u6587\u4ef6")
        if info.st_size > 512 * 1024 * 1024:
            raise HTTPException(413, "\u6587\u4ef6\u8d85\u8fc7 512 MiB \u9650\u5236")
        length = info.st_size
    except HTTPException:
        if file_fd is not None:
            os.close(file_fd)
        raise
    except (OSError, ValueError):
        if file_fd is not None:
            os.close(file_fd)
        raise HTTPException(404, "\u6587\u4ef6\u4e0d\u5b58\u5728\u6216\u65e0\u6cd5\u8bbf\u95ee")
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return file_fd, length


def secure_file_response(base: Path, requested: Path, name: str) -> StreamingResponse:
    file_fd, length = open_secure_file(base, requested)
    extension = Path(name).suffix.lower()
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                  ".webp": "image/webp", ".txt": "text/plain", ".md": "text/plain", ".py": "text/plain",
                  ".json": "text/plain", ".js": "text/plain", ".css": "text/plain", ".log": "text/plain",
                  ".html": "text/plain", ".htm": "text/plain", ".svg": "text/plain", ".xml": "text/plain",
                  ".yaml": "text/plain", ".yml": "text/plain", ".toml": "text/plain", ".ini": "text/plain",
                  ".cfg": "text/plain", ".sh": "text/plain", ".sql": "text/plain", ".csv": "text/plain",
                  ".ts": "text/plain", ".tsx": "text/plain", ".jsx": "text/plain"}.get(extension, "application/octet-stream")
    disposition = "inline" if media_type.startswith("image/") or media_type == "text/plain" else "attachment"
    def chunks():
        remaining = length
        try:
            while remaining:
                block = os.read(file_fd, min(65536, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block
        finally:
            os.close(file_fd)
    return StreamingResponse(chunks(), media_type=media_type,
        headers={"Content-Length": str(length), "Content-Disposition": disposition + "; filename*=utf-8\'\'" + quote(name, safe=""),
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                 "Content-Security-Policy": "default-src \'none\'; sandbox"})


@app.get("/api/files/{file_id}")
def get_file(file_id: str, user: dict = Depends(current_user)) -> StreamingResponse:
    with db() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id=? AND user_id=?", (file_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "\u6587\u4ef6\u4e0d\u5b58\u5728")
    return secure_file_response(settings.uploads, Path(row["path"]), row["name"])


@app.get("/api/project-file")
async def project_file(session_id: str, path: str, user: dict = Depends(current_user)) -> StreamingResponse:
    require_project_admin(user)
    session = conversation(session_id, user)
    base = safe_dir(session["cwd"])
    requested = Path(path)
    if ".." in requested.parts or "\x00" in path:
        raise HTTPException(404, "\u6587\u4ef6\u4e0d\u5b58\u5728")
    if requested.is_absolute() and requested != base and base not in requested.parents:
        raise HTTPException(403, "\u6587\u4ef6\u4e0d\u5728\u6388\u6743\u76ee\u5f55\u5185")
    # Worker applies authoritative cwd-bound no-symlink file opening using the
    # runner's file permissions; the gateway never reads runner credentials.
    await worker("POST", f"/sessions/{session_id}/register", json={"cwd": session["cwd"]})
    client = httpx.AsyncClient(timeout=60, trust_env=False)
    upstream = None
    try:
        req = client.build_request("GET", settings.worker_url + f"/sessions/{session_id}/project-file",
            params={"path": path}, headers={"Authorization": "Bearer " + settings.worker_token})
        upstream = await client.send(req, stream=True)
        if not upstream.is_success:
            status = upstream.status_code
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status, "\u6587\u4ef6\u4e0d\u5b58\u5728\u6216\u65e0\u6cd5\u8bbf\u95ee")
        try:
            length = int(upstream.headers.get("content-length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > 512 * 1024 * 1024:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(502, "\u6587\u4ef6\u5927\u5c0f\u65e0\u6548")
        name = Path(path).name or "file"
        ext = Path(name).suffix.lower()
        media_type = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".gif":"image/gif", ".webp":"image/webp"}.get(ext)
        if media_type is None:
            media_type = "text/plain" if ext in {".txt", ".md", ".py", ".json", ".js", ".css", ".log", ".html", ".htm", ".svg", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".sql", ".csv", ".ts", ".tsx", ".jsx"} else "application/octet-stream"
        disposition = "inline" if media_type.startswith("image/") or media_type == "text/plain" else "attachment"
        async def close_upstream():
            await upstream.aclose()
            await client.aclose()
        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await close_upstream()
        return StreamingResponse(body(), media_type=media_type, background=BackgroundTask(close_upstream), headers={
            "Content-Length": str(length), "Content-Disposition": disposition + "; filename*=utf-8\'\'" + quote(name, safe=""),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Content-Security-Policy": "default-src \'none\'; sandbox"})
    except httpx.RequestError:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(503, "\u6267\u884c\u670d\u52a1\u4e0d\u53ef\u7528")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class PublicStatic(StaticFiles):
    async def get_response(self, path: str, scope: dict) -> Response:
        if Path(path).suffix.lower() in {".html", ".htm"}:
            raise HTTPException(404, "Not found")
        return await super().get_response(path, scope)


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/static", PublicStatic(directory=FRONTEND), name="static")


@app.get("/")
def index(request: Request) -> Response:
    try:
        current_user(request)
    except HTTPException:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(FRONTEND / "index.html")


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(FRONTEND / "login.html")

from backend.remote_browser import router as remote_browser_router
app.include_router(remote_browser_router)



@app.get('/api/system-metrics')
def host_metrics(response: Response,user: dict = Depends(current_user)) -> dict:
    if not user['is_admin']:
        raise HTTPException(403,'\u4ec5\u7ba1\u7406\u5458\u53ef\u67e5\u770b\u670d\u52a1\u5668\u8d44\u6e90')
    response.headers['Cache-Control']='no-store'
    return system_metrics.snapshot()
