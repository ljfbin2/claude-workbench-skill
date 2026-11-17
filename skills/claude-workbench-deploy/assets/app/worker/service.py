"""Authenticated loopback worker for Claude Code.

The worker owns the CLI children and its event journal, independent of the
HTTP gateway. It never exposes this listener on a non-loopback interface.
"""
from __future__ import annotations

from . import history as historyipc

import datetime as dt
from contextlib import contextmanager
import hmac
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import tempfile
import time
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from worker.recovery import provenance
from worker import files as fileipc
from worker import quota as quotaipc
from worker import live_quota


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def roots_from_env():
    value = os.environ.get("WEB_AI_WORKSPACE_ROOTS", "")
    return [Path(p).resolve(strict=True) for p in value.split(os.pathsep) if p]


def inside(path, roots):
    return any(path == root or root in path.parents for root in roots)


def diagnostic(lines):
    """Return bounded diagnostics, removing likely credentials and environment dumps."""
    text = "\n".join(str(line)[:1200] for line in lines[:20])
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[private key redacted]", text, flags=re.I | re.S)
    text = re.sub(r"https?://[^\s]+", "[url redacted]", text, flags=re.I)
    text = re.sub(r"\b(?:bearer|basic)\s+[^\s,;]+", "[authorization redacted]", text, flags=re.I)
    text = re.sub(r"\b(?:sk-ant|sk-proj|sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]+", "[secret redacted]", text, flags=re.I)
    text = re.sub('(?i)\\b(password|passwd|secret|token|api[-_ ]?key|authorization|credential)(\\s*(?:=|:|\\bis\\b)\\s*)[^\\n]*', '\\1\\2[redacted]', text)
    text = re.sub(r"(?i)\b[A-Z_]*(?:PASSWORD|TOKEN|SECRET|API_KEY|CREDENTIAL)[A-Z_]*\s+[^\s,;]+", "[credential redacted]", text)
    text = re.sub(r"\b[A-Za-z0-9_+/=-]{32,}\b", "[value redacted]", text)
    candidates = []
    for line in text.splitlines():
        line = re.sub(r"[\x00-\x1f\x7f]", " ", line).strip()
        if not line or line.startswith(("at ", "File ", "Traceback", "export ")):
            continue
        if re.match(r"^[A-Z_][A-Z0-9_]*=", line):
            continue
        candidates.append(line[:250])
        if len(candidates) == 20:
            break
    preferred = [line for line in candidates if re.search(r"error|fail|denied|invalid|limit|timeout|unauthor|not found|overload|refused|\b[45][0-9][0-9]\b", line, flags=re.I)]
    return " | ".join((preferred or candidates)[:3])[:600]


def signal_child_tree(proc, sig):
    """The worker starts each child in a fresh process group on Unix."""
    try:
        if os.name == "nt":
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        else:
            os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


STOP_GRACE_SECONDS = 8


def request_child_stop(proc):
    signal_child_tree(proc, signal.SIGTERM)
    def force_stop():
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        finally:
            signal_child_tree(proc, signal.SIGKILL)
    threading.Thread(target=force_stop, daemon=True).start()


class WorkerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Worker:
    def __init__(self, db_path, work_roots, upload_root, claude=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            if self.db_path.parent.stat().st_mode & 0o077:
                raise RuntimeError("worker database directory must be private (0700)")
            if self.db_path.exists():
                self.db_path.chmod(0o600)
        self.work_roots = [Path(p).resolve(strict=True) for p in work_roots]
        self.upload_root = Path(upload_root).resolve(strict=True) if upload_root else None
        isolated_home = os.environ.get("WEB_AI_CLAUDE_HOME")
        if isolated_home:
            home = Path(isolated_home).resolve(strict=True)
            self.environment = {key: os.environ[key] for key in ("LANG", "LC_ALL", "TZ") if key in os.environ}
            self.environment.update({
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "PATH": str(home / ".local" / "bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            })
        else:
            self.environment = os.environ.copy()
        self.claude = claude or os.environ.get("WEB_AI_CLAUDE_BIN") or shutil.which("claude")
        if os.name == "nt" and self.claude and not self.claude.lower().endswith((".exe", ".cmd", ".bat")):
            candidate = self.claude + ".cmd"
            if os.path.isfile(candidate):
                self.claude = candidate
        self.observation_generation = uuid.uuid4().hex
        self.observation_revision = 0
        self.children = {}
        self.mutex = threading.RLock()
        self._model_lock = threading.Lock()
        self._model_cache = None
        self._model_cache_until = 0
        self._model_cache_time = None
        self._auth_cache = None
        self._capability_probe_active = False
        self._quota_probe_active = False
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, claude_id TEXT, status TEXT NOT NULL,
              pid INTEGER, cwd TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
              type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS event_session_seq ON events(session_id,seq);
            CREATE INDEX IF NOT EXISTS user_request_receipt
              ON events(session_id,json_extract(data,'$.request_id')) WHERE type='user';
            CREATE TABLE IF NOT EXISTS turn_requests (
              session_id TEXT NOT NULL, request_id TEXT NOT NULL, body_hash TEXT NOT NULL,
              PRIMARY KEY(session_id,request_id)
            );
            CREATE TABLE IF NOT EXISTS stream_deliveries (
              session_id TEXT NOT NULL, request_id TEXT NOT NULL, wire_uuid TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id,request_id)
            );
            CREATE TABLE IF NOT EXISTS pending_turns (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
              request_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
              blocked TEXT, UNIQUE(session_id,request_id)
            );
            CREATE INDEX IF NOT EXISTS pending_session_seq ON pending_turns(session_id,seq);
            CREATE TABLE IF NOT EXISTS approvals (session_id TEXT NOT NULL, request_id TEXT NOT NULL, tool_name TEXT NOT NULL, input TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id,request_id));
            CREATE TABLE IF NOT EXISTS execution_scopes (session_id TEXT PRIMARY KEY, scope TEXT NOT NULL, identity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queue_control (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL);
            INSERT OR IGNORE INTO queue_control VALUES (1,0);
            """)
            from worker import attention,resource_usage
            attention.initialize(db)
            db.execute(resource_usage.SCHEMA)
            queue_columns = {row[1] for row in db.execute("PRAGMA table_info(pending_turns)")}
            if "quota_wait" not in queue_columns:
                db.execute("ALTER TABLE pending_turns ADD COLUMN quota_wait INTEGER NOT NULL DEFAULT 0")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "worker_provenance" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN worker_provenance TEXT")
            if 'input_paused' not in columns:
                db.execute('ALTER TABLE sessions ADD COLUMN input_paused INTEGER NOT NULL DEFAULT 0')
            db.execute("UPDATE stream_deliveries SET state='unknown' WHERE state IN ('staging','writing','delivered')")
            db.execute("UPDATE approvals SET state='cancelled' WHERE state IN ('pending','answering')")
            # Never assume orphaned subprocesses have stopped after worker failure.
            rows = db.execute("SELECT id,status,worker_provenance FROM sessions WHERE status IN ('running','stopping')").fetchall()
            current = provenance() if rows else None
            for sid, old_status, raw in rows:
                try:
                    previous = json.loads(raw or "null")
                except (TypeError, ValueError):
                    previous = None
                safe = old_status == 'stopping' and self.safe_restart(previous, current)
                status = 'interrupted' if safe else 'unknown'
                db.execute("UPDATE sessions SET status=?,pid=NULL,updated_at=? WHERE id=?", (status, now(), sid))
                db.execute("UPDATE sessions SET input_paused=1 WHERE id=?", (sid,))
                message = ("\u505c\u6b62\u5df2\u5b8c\u6210\uff1b\u4efb\u52a1\u53ef\u80fd\u5df2\u90e8\u5206\u4fee\u6539\u6587\u4ef6\uff0c\u961f\u5217\u5df2\u6682\u505c\u3002" if safe else "\u6267\u884c\u670d\u52a1\u4e2d\u65ad\uff0c\u4efb\u52a1\u72b6\u6001\u672a\u77e5\uff0c\u961f\u5217\u5df2\u6682\u505c\u3002")
                self._event(db, sid, "error", {"message": message})
                self._event(db, sid, "status", {"status": status})
            resume_quota_wait = bool(db.execute("SELECT 1 FROM pending_turns p JOIN sessions s ON s.id=p.session_id WHERE p.quota_wait=1 AND s.input_paused=0 AND p.blocked IS NULL AND s.status NOT IN ('running','stopping','unknown') LIMIT 1").fetchone())
        if resume_quota_wait:
            threading.Thread(target=self._dispatch_global, daemon=True).start()

    timestamp = staticmethod(now)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def safe_restart(previous, current):
        return bool(isinstance(previous, dict) and current and
                    all(previous.get(k) for k in ("unit", "invocation", "boot", "config")) and
                    all(previous.get(k) == current.get(k) for k in ("unit", "config")) and
                    previous.get("invocation") != current.get("invocation"))

    @staticmethod
    def valid_id(value):
        # IDs are opaque: accept either producer's exact lowercase form,
        # without normalizing one representation into the other.
        return isinstance(value, str) and re.fullmatch(
            r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            value,
        ) is not None

    @staticmethod
    def _event(db, sid, kind, data):
        cursor=db.execute("INSERT INTO events(session_id,type,data,created_at) VALUES (?,?,?,?)",
                   (sid, kind, json.dumps(data, ensure_ascii=False), now()))
        from worker.attention import record
        record(db,sid,kind,data,cursor.lastrowid)

    def append(self, sid, kind, data):
        with self.db() as db:
            self._event(db, sid, kind, data)
        if kind == "claude" and isinstance(data, dict) and (
                data.get("type") == "rate_limit_event" or "usage_report" in data):
            quotaipc.invalidate(self)

    def validate(self, request):
        sid = request.get("session_id")
        if not self.valid_id(sid):
            raise WorkerError("会话 ID 无效")
        try:
            cwd = Path(request["cwd"]).resolve(strict=True)
        except (KeyError, OSError, RuntimeError, TypeError):
            raise WorkerError("工作目录不存在")
        if not cwd.is_dir() or not inside(cwd, self.work_roots):
            raise WorkerError("工作目录不在授权范围", 403)
        text = request.get("prompt", "")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 1024 * 1024:
            raise WorkerError("消息格式无效或过长")
        attachments = request.get("attachments") or []
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise WorkerError("附件格式无效")
        files = []
        for item in attachments:
            try:
                files.append(fileipc.attachment(self, sid, item))
            except fileipc.FileIPCError as exc:
                raise WorkerError(str(exc), exc.status) from exc
        if not text.strip() and not files:
            raise WorkerError("请输入消息或添加附件")
        mode = request.get("permission_mode") or "dontAsk"
        # These modes are supplied directly to Claude. With no permission host,
        # prompts are denied; no mode pretends an interactive approval exists.
        if mode not in ("plan", "dontAsk", "acceptEdits", "bypassPermissions"):
            raise WorkerError("不支持此权限模式")
        model = request.get("model") or ""
        if not isinstance(model, str) or (model and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model)):
            raise WorkerError("模型参数无效")
        effort = request.get("reasoning_effort") or request.get("effort") or ""
        if effort and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", effort):
            raise WorkerError("推理强度参数无效")
        from worker.collaboration import profile
        try:
            collaboration = profile(request)
        except (ValueError, TypeError) as exc:
            raise WorkerError('Invalid collaboration configuration') from exc
        if collaboration:
            model, effort = collaboration['coder_model'], collaboration['coder_effort']
        return sid, cwd, text, files, mode, model, effort

    def start(self, request):
        sid = request.get("session_id")
        request_id = request.get("request_id")
        if not self.valid_id(sid) or not self.valid_id(request_id):
            raise WorkerError("A valid session and request ID are required")
        identity = provenance()
        encoded_identity = json.dumps(identity, sort_keys=True) if identity else None
        body_hash = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.mutex, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT body_hash FROM turn_requests WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
            if existing:
                if not hmac.compare_digest(existing[0], body_hash):
                    raise WorkerError("Request ID already belongs to different content", 409)
                queued = db.execute("SELECT 1 FROM pending_turns WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
                return {"accepted": True, "duplicate": True, "queued": bool(queued)}
            if getattr(self,'_account_login_active',False):
                raise WorkerError("Claude authorization in progress; complete or cancel it before sending",503)
            sid, cwd, text, files, mode, model, effort = self.validate(request)
            if not self.claude:
                raise WorkerError("Claude Code is not installed", 503)
            row = db.execute("SELECT claude_id,status,cwd FROM sessions WHERE id=?", (sid,)).fetchone()
            if row and row[1] == "unknown":
                raise WorkerError("任务状态未知，不能安全发送消息", 409)
            if row and row[2] != str(cwd):
                raise WorkerError("Cannot change the working directory of an existing session", 409)
            pending = db.execute("SELECT count(*) FROM pending_turns WHERE session_id=?", (sid,)).fetchone()[0]
            busy = bool(row and row[1] in ("running", "stopping", "unknown"))
            waiting = bool(pending)
            local_wait = db.execute("SELECT input_paused FROM sessions WHERE id=?",(sid,)).fetchone()
            local_wait = bool(local_wait and local_wait[0] and pending)
            if busy or waiting or local_wait or self._quota_probe_active:
                if self._quota_probe_active and not busy and not waiting:
                    db.execute("UPDATE queue_control SET paused=0 WHERE id=1")
                if not row:
                    db.execute("INSERT INTO sessions(id,claude_id,status,pid,cwd,updated_at) VALUES (?,?,?,?,?,?)", (sid, None, 'idle', None, str(cwd), now()))
                if pending >= 50:
                    raise WorkerError("排队消息已达上限（50 条）", 409)
                db.execute("INSERT INTO turn_requests VALUES (?,?,?)", (sid, request_id, body_hash))
                quota_wait = int(bool(self._quota_probe_active and not busy))
                db.execute("INSERT INTO pending_turns(session_id,request_id,payload,created_at,quota_wait) VALUES (?,?,?,?,?)",
                           (sid, request_id, json.dumps(request, ensure_ascii=False), now(), quota_wait))
                self._event(db, sid, "queue_changed", {"reason": "added"})
                child = self.children.get(sid)
                if child and child.get('stream') and not child.get('closing'):
                    child['wake'].set()
                return {"accepted": True, "queued": True}
            db.execute("UPDATE queue_control SET paused=0 WHERE id=1")
            db.execute('UPDATE sessions SET input_paused=0 WHERE id=?',(sid,))
            claude_id = row[0] if row else None
            if not claude_id:
                claude_id = str(uuid.uuid4())
            if row:
                db.execute("UPDATE sessions SET status='running',pid=NULL,updated_at=?,worker_provenance=? WHERE id=?", (now(), encoded_identity, sid))
            else:
                db.execute("INSERT INTO sessions(id,claude_id,status,pid,cwd,updated_at,worker_provenance) VALUES (?,?,?,?,?,?,?)", (sid, None, "running", None, str(cwd), now(), encoded_identity))
            db.execute("INSERT INTO turn_requests VALUES (?,?,?)", (sid, request_id, body_hash))
            from worker.streaming import message_uuid
            db.execute("INSERT INTO stream_deliveries VALUES (?,?,?,?,?)", (sid, request_id, message_uuid(sid, request_id), 'staging', now()))
            self._event(db, sid, "user", {"text": text, "attachments": files, "request_id": request_id})
            self._event(db, sid, "status", {"status": "running"})
            self.children[sid] = {"process": None, "stop": False, "request": request}
        thread = threading.Thread(target=self._run, args=(sid, cwd, text, files, mode, model, effort, claude_id, bool(row and row[0])), daemon=True)
        thread.start()
        return {"accepted": True}

    def _run(self, sid, cwd, text, files, mode, model, effort, claude_id, resume):
        from worker.streaming import run
        run(self, sid, cwd, text, files, mode, model, effort, claude_id, resume)

    def _dispatch_global(self):
        # Dispatch independently; no cross-session FIFO or fixed parallel limit.
        with self.db() as db:
            rows = db.execute("SELECT DISTINCT p.session_id FROM pending_turns p JOIN sessions s ON s.id=p.session_id WHERE s.input_paused=0 AND p.blocked IS NULL AND s.status NOT IN ('running','stopping','unknown')").fetchall()
        for (sid,) in rows:
            self._dispatch_next(sid, automatic=True)

    def _dispatch_next(self, sid, automatic=False):
        identity = provenance()
        encoded_identity = json.dumps(identity, sort_keys=True) if identity else None
        # Claim the oldest queued message atomically with the running state.
        # Failures or a stopped/unknown task leave later messages in the queue.
        with self.mutex:
            if self._quota_probe_active or getattr(self,'_account_login_active',False):
                if automatic:
                    return {"started": False}
                raise WorkerError("\u6267\u884c\u670d\u52a1\u6b63\u5fd9\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5", 503)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT claude_id,status,cwd,input_paused FROM sessions WHERE id=?", (sid,)).fetchone()
                if automatic and (not row or row[3]):
                    return {"started": False}
                if not row or row[1] in ("running", "stopping", "unknown"):
                    if automatic: return {"started": False}
                    raise WorkerError("\u672c\u4f1a\u8bdd\u6b63\u5728\u8fd0\u884c\u6216\u72b6\u6001\u672a\u77e5", 409)
                queued = db.execute("SELECT seq,payload FROM pending_turns WHERE session_id=? ORDER BY seq LIMIT 1", (sid,)).fetchone()
                if not queued:
                    return {"started": False}
                try:
                    request = json.loads(queued[1])
                    _, cwd, text, files, mode, model, effort = self.validate(request)
                    if str(cwd) != row[2]:
                        raise WorkerError("工作目录已变化")
                    if not self.claude:
                        raise WorkerError("Claude Code 未安装")
                except (WorkerError, ValueError, TypeError) as exc:
                    detail = str(exc)[:300]
                    db.execute('UPDATE sessions SET input_paused=1 WHERE id=?',(sid,))
                    db.execute("UPDATE pending_turns SET blocked=? WHERE seq=?", (detail, queued[0]))
                    self._event(db, sid, "queue_changed", {"reason": "blocked", "message": detail})
                    return {"started": False, "blocked": detail}
                claude_id = row[0] or str(uuid.uuid4())
                db.execute('UPDATE sessions SET input_paused=0 WHERE id=?',(sid,))
                db.execute("DELETE FROM pending_turns WHERE seq=?", (queued[0],))
                if not automatic:
                    db.execute("UPDATE queue_control SET paused=0 WHERE id=1")
                db.execute("UPDATE sessions SET status='running',pid=NULL,updated_at=?,worker_provenance=? WHERE id=?", (now(), encoded_identity, sid))
                self._event(db, sid, "queue_changed", {"reason": "started"})
                from worker.streaming import message_uuid
                db.execute("INSERT INTO stream_deliveries VALUES (?,?,?,?,?)", (sid, request['request_id'], message_uuid(sid, request['request_id']), 'staging', now()))
                self._event(db, sid, "user", {"text": text, "attachments": files, "request_id": request["request_id"]})
                self._event(db, sid, "status", {"status": "running"})
                self.children[sid] = {"process": None, "stop": False, "request": request}
            threading.Thread(target=self._run, args=(sid, cwd, text, files, mode, model, effort, claude_id, bool(row[0])), daemon=True).start()
            return {"started": True}

    def confirm_interrupted(self, sid):
        current = provenance()
        with self.mutex, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,worker_provenance,pid FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row or row[0] != "unknown":
                raise WorkerError("\u4f1a\u8bdd\u5e76\u975e\u72b6\u6001\u672a\u77e5", 409)
            try:
                previous = json.loads(row[1] or "null")
            except (TypeError, ValueError):
                previous = None
            from worker.scopes import is_released
            released_here=bool(previous and previous==current and sid not in self.children and is_released(self,sid,row[2],current))
            if not (self.safe_restart(previous,current) or released_here):
                raise WorkerError("\u65e0\u6cd5\u786e\u8ba4\u65e7\u6267\u884c\u8fdb\u7a0b\u5df2\u5168\u90e8\u9000\u51fa\uff0c\u4efb\u52a1\u4fdd\u6301\u672a\u77e5", 409)
            db.execute("UPDATE sessions SET status='interrupted',pid=NULL,input_paused=1,updated_at=? WHERE id=?", (now(), sid))
            self._event(db, sid, "error", {"message": "\u5df2\u786e\u8ba4\u65e7\u6267\u884c\u5df2\u4e2d\u65ad\uff1b\u539f\u4efb\u52a1\u53ef\u80fd\u5df2\u7ecf\u90e8\u5206\u4fee\u6539\u6587\u4ef6\uff0c\u672a\u81ea\u52a8\u91cd\u8bd5\u3002"})
            self._event(db, sid, "status", {"status": "interrupted"})
            remaining=db.execute("SELECT 1 FROM sessions WHERE status IN ('running','stopping','unknown') LIMIT 1").fetchone()
            if not remaining:db.execute('UPDATE queue_control SET paused=0 WHERE id=1')
        self._dispatch_global()
        return {"status": "interrupted"}

    def cancel_queued(self, sid, request_id):
        if not self.valid_id(sid) or not self.valid_id(request_id):
            raise WorkerError("请求 ID 无效")
        with self.mutex, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("DELETE FROM pending_turns WHERE session_id=? AND request_id=?", (sid, request_id))
            if not result.rowcount:
                raise WorkerError("排队消息不存在或已经开始", 409)
            # Keep request id tombstone so delayed retries cannot resurrect a cancelled message.
            self._event(db, sid, "queue_changed", {"reason": "cancelled"})
        return {"cancelled": True}

    def stop(self, sid):
        identity = provenance()
        with self.mutex, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            child = self.children.get(sid)
            row = db.execute("SELECT status,worker_provenance FROM sessions WHERE id=?", (sid,)).fetchone()
            if not child or not row or row[0] != 'running':
                raise WorkerError("\u4f1a\u8bdd\u6ca1\u6709\u53ef\u505c\u6b62\u7684\u8fd0\u884c\u4efb\u52a1", 409)
            if not identity or not row[1] or json.loads(row[1]) != identity:
                raise WorkerError("\u65e0\u6cd5\u9a8c\u8bc1\u6267\u884c\u670d\u52a1\uff0c\u4e0d\u80fd\u5b89\u5168\u505c\u6b62", 409)
            db.execute("UPDATE sessions SET status='stopping',updated_at=? WHERE id=?", (now(), sid))
            self._event(db, sid, "status", {"status": "stopping"})
            child['stop'] = True
        child.get('wake', threading.Event()).set()
        return self._interrupt_child(sid, child)

    def pending_approvals(self, sid):
        from worker.controls import pending
        return pending(self,sid)

    def answer_approval(self,sid,rid,body):
        from worker.controls import answer
        return answer(self,sid,rid,body)

    def _interrupt_child(self, sid, child):
        from worker.controls import request_interrupt
        if 'ready' in child:
            request_interrupt(self, sid, child)
        return {"status": "stopping"}

    def observe(self):
        # Generation changes on worker restart; observations never restore liveness.
        with self.mutex:
            self.observation_revision += 1
            return {'generation': self.observation_generation, 'revision': self.observation_revision}

    def status(self, sid):
        with self.mutex:
            data = self._status(sid)
            data.update(self.observe())
            approval = data['approvals'][0]['state'] if data['approvals'] else None
            data['display_status'] = ('approval_unknown' if approval == 'unknown' else
                'answering' if approval == 'answering' else 'requires_action' if approval else
                data['activity'].get('phase') or data['status'])
            return data

    def _status(self, sid):
        with self.db() as db:
            row = db.execute("SELECT status,updated_at,input_paused FROM sessions WHERE id=?", (sid,)).fetchone()
            pending = db.execute("SELECT request_id,payload,created_at,blocked FROM pending_turns WHERE session_id=? ORDER BY seq", (sid,)).fetchall()
        queue = []
        for request_id, raw, created, blocked in pending:
            request = json.loads(raw)
            with self.mutex:
                active_config = self.children.get(sid, {}).get('config')
            from worker.collaboration import profile, signature
            collaboration = profile(request)
            requested_config = (request.get('cwd'), request.get('permission_mode') or 'dontAsk',
                collaboration['coder_model'] if collaboration else request.get('model') or '',
                collaboration['coder_effort'] if collaboration else request.get('reasoning_effort') or request.get('effort') or '',
                signature(request))
            wait_reason = ('\u5f53\u524d\u540e\u53f0\u4efb\u52a1\u4f7f\u7528\u5176\u4ed6\u8bbe\u7f6e\uff0c\u7ed3\u675f\u540e\u5c06\u5e94\u7528\u65b0\u8bbe\u7f6e' if active_config and requested_config != active_config else None)
            queue.append({"request_id": request_id, "text": request.get("prompt", ""),
                          "attachment_count": len(request.get("attachments") or []), "created_at": created,
                          "blocked": blocked, "wait_reason": wait_reason})
        status = row[0] if row else "idle"
        if queue and status not in ("running", "stopping", "unknown"):
            status = "queued"
        with self.db() as db:
            paused = bool(row and row[2])
        with self.db() as db:
            deliveries = [{"request_id": r[0], "state": r[1]} for r in db.execute("SELECT request_id,state FROM stream_deliveries WHERE session_id=? ORDER BY created_at DESC LIMIT 20", (sid,))]
        with self.mutex:
            child = self.children.get(sid, {})
            stream_state = {"active": child.get("stream", False), "input_idle": child.get("idle", False), "background_count": len(child.get("background", {})), "phase":child.get("phase")}
        from worker.activity import snapshot as activity_snapshot
        activity = activity_snapshot(self, sid, status, row[1] if row else None)
        return {"status": status, "updated_at": row[1] if row else None, "queue": queue, "queue_paused": paused, "deliveries": deliveries, "stream": stream_state, "activity": activity, "approvals": self.pending_approvals(sid)}

    def receipt(self, sid, request_id):
        if not self.valid_id(sid) or not self.valid_id(request_id):
            raise WorkerError('Invalid request ID')
        with self.db() as db:
            exists = db.execute('SELECT 1 FROM turn_requests WHERE session_id=? AND request_id=?',
                                (sid, request_id)).fetchone()
            queued = db.execute('SELECT 1 FROM pending_turns WHERE session_id=? AND request_id=?',
                                (sid, request_id)).fetchone()
            delivery = db.execute('SELECT state FROM stream_deliveries WHERE session_id=? AND request_id=?',
                                  (sid, request_id)).fetchone()
            event = db.execute("SELECT seq FROM events WHERE session_id=? AND type='user' "
                               "AND json_extract(data,'$.request_id')=? ORDER BY seq LIMIT 1",
                               (sid, request_id)).fetchone()
        return dict(request_id=request_id, accepted=bool(exists), queued=bool(queued),
                    delivery_state=delivery[0] if delivery else None, event_seq=event[0] if event else None)

    def events(self, sid, after=0, before=None, limit=100):
        limit = max(1, min(limit, 500))
        with self.db() as db:
            if before:
                rows = db.execute("SELECT seq,type,data,created_at FROM events WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT ?", (sid, before, limit+1)).fetchall()
                more = len(rows) > limit
                rows = list(reversed(rows[:limit]))
            else:
                rows = db.execute("SELECT seq,type,data,created_at FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?", (sid, after, limit+1)).fetchall()
                more = len(rows) > limit
                rows = rows[:limit]
        return {"events": [{"seq": r[0], "type": r[1], "data": json.loads(r[2]), "created_at": r[3]} for r in rows], "has_more": more}

    def delete(self, sid):
        with self.mutex, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()
            if row and row[0] in ("running", "stopping", "unknown"):
                raise WorkerError("The task is running or in an unknown state", 409)
            db.execute("DELETE FROM events WHERE session_id=?", (sid,))
            db.execute("DELETE FROM turn_requests WHERE session_id=?", (sid,))
            db.execute("DELETE FROM stream_deliveries WHERE session_id=?", (sid,))
            db.execute("DELETE FROM pending_turns WHERE session_id=?", (sid,))
            db.execute("DELETE FROM approvals WHERE session_id=?", (sid,))
            db.execute("DELETE FROM execution_scopes WHERE session_id=?", (sid,))
            db.execute("DELETE FROM reply_index WHERE session_id=?",(sid,))
            db.execute("DELETE FROM execution_resources WHERE session_id=?",(sid,))
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            staged = self.db_path.parent / "worker-inputs" / sid
            if staged.exists():
                shutil.rmtree(staged)
            if self.upload_root:
                stored = self.upload_root / sid
                if stored.is_dir() and not stored.is_symlink():
                    shutil.rmtree(stored)
        return {"deleted": True, "provider_history_retained": True}

    def _model_snapshot(self, stale=False):
        info = {"available": bool(self.claude), "logged_in": False, "auth_status_known": False,
                "version": None, "models": [], "model_list_available": False,
                "model_list_stale": False, "model_list_updated_at": self._model_cache_time,
                "model_list_error": None, "auth_status_stale": bool(stale and self._auth_cache is not None),
                "efforts": [], "supports_steer": False,
                "permission_modes": ["plan", "dontAsk", "acceptEdits", "bypassPermissions"]}
        if self._auth_cache is not None:
            info.update(self._auth_cache)
        if self._model_cache:
            info["models"] = self._model_cache
            info["model_list_available"] = True
            info["model_list_stale"] = bool(stale)
        elif self.claude:
            if self._capability_probe_active:
                info["model_list_error"] = "\u6a21\u578b\u5217\u8868\u67e5\u8be2\u4e2d\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5"
            elif stale:
                info["model_list_error"] = "\u4efb\u52a1\u8fd0\u884c\u6216\u6392\u961f\u671f\u95f4\u6682\u65e0\u53ef\u7528\u5217\u8868"
            else:
                info["model_list_error"] = "\u6a21\u578b\u5217\u8868\u8bfb\u53d6\u5931\u8d25\u6216\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5"
        return info

    def capabilities(self):
        if not self.claude:
            return self._model_snapshot()
        # Never queue callers behind a slow probe. They receive an honest cached
        # snapshot (or unavailable) and may retry later.
        if not self._model_lock.acquire(blocking=False):
            return self._model_snapshot(stale=True)
        try:
            with self.mutex:
                try:
                    with self.db() as db:
                        active = db.execute("SELECT 1 FROM sessions WHERE status IN ('running','stopping','unknown') LIMIT 1").fetchone()
                        queued = db.execute("SELECT 1 FROM pending_turns LIMIT 1").fetchone()
                except sqlite3.Error:
                    return self._model_snapshot(stale=True)
                if active or queued:
                    return self._model_snapshot(stale=True)
                if time.monotonic() < self._model_cache_until:
                    return self._model_snapshot()
                # start() will reject promptly, before making any DB changes.
                self._capability_probe_active = True
            env_keys = ("HOME", "PATH", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "LANG", "LC_ALL", "TZ")
            clean_env = {key: self.environment[key] for key in env_keys if key in self.environment}
            auth_info = {"logged_in": False, "auth_status_known": False, "version": None}
            models = None
            try:
                version = subprocess.run([self.claude, "--version"], capture_output=True, text=True, timeout=2, env=clean_env)
                if version.returncode == 0:
                    auth_info["version"] = version.stdout.strip()[:100]
                auth = subprocess.run([self.claude, "auth", "status"], capture_output=True, text=True, timeout=2, env=clean_env)
                if auth.returncode == 0:
                    payload = json.loads(auth.stdout)
                    auth_info["logged_in"] = bool(payload.get("loggedIn"))
                    auth_info["auth_status_known"] = True
                    auth_info["auth_status_checked_at"] = now()
                if auth_info["logged_in"]:
                    with tempfile.TemporaryDirectory(prefix="model-discovery-", dir=self.db_path.parent) as cwd:
                        helper = str(Path(__file__).with_name("model_discovery.py"))
                        proc = subprocess.Popen([sys.executable, helper, self.claude, cwd], cwd=cwd, env=clean_env,
                                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                text=True, start_new_session=(os.name != "nt"))
                        try:
                            stdout, _ = proc.communicate(timeout=8)
                        except subprocess.TimeoutExpired:
                            if os.name == "nt":
                                proc.kill()
                            else:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            try:
                                proc.communicate(timeout=1)
                            except subprocess.TimeoutExpired:
                                if proc.stdout:
                                    proc.stdout.close()
                                proc.kill()
                            raise
                        if proc.returncode:
                            raise subprocess.CalledProcessError(proc.returncode, "model discovery")
                        parsed = json.loads(stdout)
                        if isinstance(parsed, dict) and isinstance(parsed.get("models"), list) and parsed["models"]:
                            models = parsed["models"]
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            finally:
                with self.mutex:
                    self._capability_probe_active = False
            self._auth_cache = auth_info
            if models:
                self._model_cache = models
                self._model_cache_time = now()
            else:
                # Discovery failure must not make old candidates look current.
                self._model_cache = None
                self._model_cache_time = None
            self._model_cache_until = time.monotonic() + (60 if models else 5)
            return self._model_snapshot()
        finally:
            self._model_lock.release()

    def search(self, sid, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise WorkerError("搜索内容无效")
        with self.db() as db:
            rows = db.execute("SELECT data FROM events WHERE session_id=? AND type IN ('user','claude')", (sid,))
            return {"matched": any(query.casefold() in row[0].casefold() for row in rows)}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, worker, token):
        if address[0] not in ("127.0.0.1", "::1"):
            raise ValueError("worker must listen on loopback")
        self.worker = worker
        self.token = token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Do not log prompts, URLs, tokens, or tool outputs.

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        header = self.headers.get("Authorization", "")
        if not hmac.compare_digest(header, "Bearer " + self.server.token):
            return self.reply(401, {"error": "未授权"})
        parsed = urlsplit(self.path)
        worker = self.server.worker
        try:
            if self.command == 'POST' and parsed.path == '/directories':
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 4096:
                    raise fileipc.FileIPCError('Invalid directory request')
                body = json.loads(self.rfile.read(length))
                return self.reply(201, fileipc.create_directory(worker, body))
            if self.command == "POST" and parsed.path == "/directories/project":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise fileipc.FileIPCError("Invalid request body")
                body = json.loads(self.rfile.read(length))
                return self.reply(200, fileipc.create_project(worker, body))
            if self.command == "GET" and parsed.path == "/directories":
                q = parse_qs(parsed.query, keep_blank_values=True)
                return self.reply(200, fileipc.directories(worker, q.get("path", [None])[0]))
            receipt = re.fullmatch(r"/sessions/([0-9a-f-]+)/requests/([0-9a-f-]+)", parsed.path)
            if self.command == 'GET' and receipt:
                return self.reply(200, worker.receipt(receipt[1], receipt[2]))
            match = re.fullmatch(r"/sessions/([0-9a-f-]+)/register", parsed.path)
            if self.command == "POST" and match and worker.valid_id(match[1]):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise fileipc.FileIPCError("Invalid registration body")
                body = json.loads(self.rfile.read(length))
                return self.reply(200, fileipc.register(worker, match[1], body))
            match = re.fullmatch(r"/sessions/([0-9a-f-]+)/attachments/([0-9a-f-]+)", parsed.path)
            if self.command == "POST" and match and worker.valid_id(match[1]) and worker.valid_id(match[2]):
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise fileipc.FileIPCError("Content-Length is required", 411)
                self.connection.settimeout(30)
                result = fileipc.receive(worker, match[1], match[2], self.rfile, int(raw_length), self.headers.get("X-File-Suffix", ""))
                return self.reply(200, result)
            project_view=re.fullmatch(r'/sessions/([0-9a-f-]+)/project-(tree|text|diff|changes)',parsed.path)
            if self.command=='GET' and project_view and worker.valid_id(project_view[1]):
                from worker import project_view as view
                raw=parse_qs(parsed.query,keep_blank_values=True).get('path',[''])[0]
                return self.reply(200,getattr(view,project_view[2])(worker,project_view[1],raw))
            match = re.fullmatch(r"/sessions/([0-9a-f-]+)/project-file", parsed.path)
            if self.command == "GET" and match and worker.valid_id(match[1]):
                q = parse_qs(parsed.query, keep_blank_values=True)
                fd, length = fileipc.open_project(worker, match[1], q.get("path", [""])[0])
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    left = length
                    while left:
                        block = os.read(fd, min(65536, left)) if isinstance(fd, int) else fd.read(min(65536, left))
                        if not block:
                            break
                        self.wfile.write(block)
                        left -= len(block)
                except OSError:
                    # A partial response must terminate, not append a second HTTP header.
                    self.close_connection = True
                finally:
                    os.close(fd) if isinstance(fd, int) else fd.close()
                return
            if self.command == 'GET' and parsed.path == '/diagnostics':
                with worker.mutex:
                    with worker.db() as db:
                        counts=dict(db.execute('SELECT status,count(*) FROM sessions GROUP BY status'))
                        pending=db.execute('SELECT count(*) FROM pending_turns').fetchone()[0]
                    return self.reply(200, {**worker.observe(), 'process_count':len(worker.children),
                        'active_sessions':sum(counts.get(k,0) for k in ('running','stopping')),
                        'unknown_sessions':counts.get('unknown',0),'pending_messages':pending})
            if self.command == "GET" and parsed.path == "/quota":
                return self.reply(200, live_quota.snapshot(worker))
            if self.command == "POST" and parsed.path == "/quota/refresh":
                return self.reply(200, live_quota.refresh(worker))
            if self.command == "GET" and parsed.path == "/capabilities":
                return self.reply(200, worker.capabilities())
            if self.command=='POST' and parsed.path=='/reply-cursors':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024*1024:raise WorkerError('Invalid cursor request')
                cursors=json.loads(self.rfile.read(length)).get('cursors')
                if not isinstance(cursors,dict) or any(not worker.valid_id(k) or type(v) is not int or v<0 for k,v in cursors.items()):raise WorkerError('Invalid cursors')
                from worker.attention import heads
                with worker.db() as db:
                    result=heads(db,list(cursors))
                    for sid,item in result.items():item['unread']=db.execute('SELECT COUNT(*) FROM reply_index WHERE session_id=? AND seq>?',(sid,min(cursors[sid],item['seq']))).fetchone()[0]
                return self.reply(200,{'attention':result})
            resource_match=re.fullmatch(r'/sessions/([0-9a-f-]+)/resources',parsed.path)
            if self.command=='GET' and resource_match and worker.valid_id(resource_match[1]):
                from worker.resource_usage import read
                return self.reply(200,read(worker,resource_match[1]))
            if self.command == 'POST' and parsed.path == '/session-statuses':
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 1024*1024:raise WorkerError('Invalid status request')
                data=json.loads(self.rfile.read(length))
                ids=data.get('ids') if isinstance(data,dict) else None
                if not isinstance(ids,list) or any(not isinstance(sid,str) or not worker.valid_id(sid) for sid in ids):raise WorkerError('Invalid session IDs')
                focus=data.get('focus')
                if focus is not None and focus not in ids:raise WorkerError('Invalid focused session')
                with worker.mutex:
                    with worker.db() as db:
                        active=[sid for (sid,) in db.execute("SELECT id FROM sessions WHERE status IN ('running','stopping','unknown')") if sid in set(ids)]
                    if focus and focus not in active:active.append(focus)
                    result={sid:worker.status(sid) for sid in active}
                    observation=worker.observe()
                    from worker.attention import heads
                    with worker.db() as db:attention=heads(db,ids)
                return self.reply(200,{'sessions':result,'attention':attention, **observation})
            approval=re.fullmatch(r'/tasks/([0-9a-f-]+)/approvals/([A-Za-z0-9_-]{1,200})',parsed.path)
            if self.command=='POST' and approval and worker.valid_id(approval[1]):
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536:raise WorkerError('Invalid approval size')
                return self.reply(200,worker.answer_approval(approval[1],approval[2],json.loads(self.rfile.read(length))))
            if self.command == "POST" and parsed.path == "/tasks":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2 * 1024 * 1024:
                    raise WorkerError("请求大小无效")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise WorkerError("请求格式无效")
                return self.reply(200, worker.start(data))
            match = re.fullmatch(r"/tasks/([0-9a-f-]+)/confirm-interrupted", parsed.path)
            if self.command == "POST" and match and worker.valid_id(match[1]):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024:
                    raise WorkerError("\u8bf7\u6c42\u5927\u5c0f\u65e0\u6548")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or data.get("confirm") is not True:
                    raise WorkerError("\u9700\u8981\u660e\u786e\u786e\u8ba4")
                return self.reply(200, worker.confirm_interrupted(match[1]))
            match = re.fullmatch(r"/tasks/([0-9a-f-]+)/resume-queue", parsed.path)
            if self.command == "POST" and match and worker.valid_id(match[1]):
                return self.reply(200, worker._dispatch_next(match[1]))
            match = re.fullmatch(r"/tasks/([0-9a-f-]+)/queue/([0-9a-f-]+)", parsed.path)
            if self.command == "DELETE" and match and worker.valid_id(match[1]) and worker.valid_id(match[2]):
                return self.reply(200, worker.cancel_queued(match[1], match[2]))
            match = re.fullmatch(r"/tasks/([0-9a-f-]+)/stop", parsed.path)
            if self.command == "POST" and match and worker.valid_id(match[1]):
                return self.reply(200, worker.stop(match[1]))
            if parsed.path in ('/account/login','/account/code','/account/cancel'):
                from worker import account_login
                try:
                    if self.command == 'GET' and parsed.path == '/account/login':
                        return self.reply(200, account_login.status(worker))
                    if self.command == 'POST':
                        if parsed.path == '/account/login':
                            return self.reply(200, account_login.start(worker))
                        length = int(self.headers.get('Content-Length','0'))
                        if not 0 < length <= 8192:
                            raise ValueError('Invalid authorization body')
                        body = json.loads(self.rfile.read(length))
                        if parsed.path == '/account/code':
                            return self.reply(200, account_login.send_code(worker, body.get('id'), body.get('code')))
                        return self.reply(200, account_login.cancel(worker, body.get('id')))
                except ValueError as exc:
                    return self.reply(409, {'error': str(exc)})
            match = re.fullmatch(r"/sessions/([0-9a-f-]+)", parsed.path)
            if self.command == "DELETE" and match and worker.valid_id(match[1]):
                return self.reply(200, worker.delete(match[1]))
            match = re.fullmatch(r"/sessions/([0-9a-f-]+)/(status|events|search|history|history-detail|updates)", parsed.path)
            if self.command == "GET" and match and worker.valid_id(match[1]):
                if match[2] == "status":
                    return self.reply(200, worker.status(match[1]))
                q = parse_qs(parsed.query)
                if match[2] == "search":
                    return self.reply(200, worker.search(match[1], q.get("q", [""])[0]))
                if match[2] == "updates":
                    return self.reply(200, historyipc.updates(worker, match[1], int(q.get("after", [0])[0]), int(q.get("limit", [2000])[0])))
                if match[2] == "history":
                    before = int(q["before"][0]) if "before" in q else None
                    return self.reply(200, historyipc.page(worker, match[1], before, int(q.get("limit", [100])[0])))
                if match[2] == "history-detail":
                    result = historyipc.detail(worker, match[1], int(q.get("seq", [0])[0]), int(q.get("block", [-1])[0]))
                    return self.reply(200 if result is not None else 404, result or {"error": "Detail not found"})
                after = int(q.get("after", [0])[0])
                before = int(q["before"][0]) if "before" in q else None
                limit = int(q.get("limit", [100])[0])
                return self.reply(200, worker.events(match[1], after, before, limit))
            self.reply(404, {"error": "不存在"})
        except (WorkerError, fileipc.FileIPCError) as exc:
            self.reply(exc.status, {"error": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError):
            self.reply(400, {"error": "请求格式无效"})
        except Exception:
            self.reply(500, {"error": "执行服务内部错误"})

    do_GET = handle_request
    do_POST = handle_request
    do_DELETE = handle_request


def main():
    os.umask(0o077)
    token = os.environ.get("WEB_AI_WORKER_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("WEB_AI_WORKER_TOKEN must contain at least 32 characters")
    home = os.environ.get("WEB_AI_CLAUDE_HOME")
    if not home or not Path(home).is_dir():
        raise SystemExit("WEB_AI_CLAUDE_HOME must name an isolated existing home")
    roots = roots_from_env()
    if not roots:
        raise SystemExit("WEB_AI_WORKSPACE_ROOTS must name at least one existing directory")
    upload = os.environ.get("WEB_AI_UPLOAD_ROOT")
    db = os.environ.get("WEB_AI_WORKER_DB", "worker-events.sqlite3")
    port = int(os.environ.get("WEB_AI_WORKER_PORT", "18731"))
    # Lock the journal for the entire process lifetime, including across ports.
    # A second worker must not mark the first worker's active tasks as orphaned.
    lock_path = Path(db).resolve().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another worker is using this journal")
    Server(("127.0.0.1", port), Worker(db, roots, upload), token).serve_forever()
