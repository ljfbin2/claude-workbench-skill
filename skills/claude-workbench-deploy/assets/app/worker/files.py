"""Session-scoped authenticated file IPC; no gateway-private paths cross the boundary."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from pathlib import Path

MAX_FILE = 512 * 1024 * 1024
MAX_SESSION_BYTES = 2048 * 1024 * 1024

class FileIPCError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def _root(worker):
    root = worker.upload_root
    if not root:
        raise FileIPCError('Worker upload storage is not configured', 503)
    if root.is_symlink() or not root.is_dir():
        raise FileIPCError('Worker upload storage unavailable', 503)
    if root.stat().st_mode & 0o077:
        raise FileIPCError('Worker upload storage must be private', 503)
    return root

def _session(worker, sid):
    if not worker.valid_id(sid):
        raise FileIPCError('Invalid session identifier')
    with worker.db() as db:
        row = db.execute('SELECT cwd FROM sessions WHERE id=?', (sid,)).fetchone()
    if not row:
        raise FileIPCError('Session is not registered', 404)
    return Path(row[0])

def register(worker, sid, body):
    if not worker.valid_id(sid) or not isinstance(body, dict) or set(body) != {'cwd'}:
        raise FileIPCError('Invalid session registration')
    if not isinstance(body['cwd'], str) or not Path(body['cwd']).is_absolute():
        raise FileIPCError('Invalid work directory')
    try:
        cwd = Path(body['cwd']).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise FileIPCError('\u76ee\u5f55\u4e0d\u5b58\u5728\u6216\u4e0d\u53ef\u8bbf\u95ee\uff0c\u8bf7\u5148\u5728\u6d4f\u89c8\u7a97\u53e3\u65b0\u5efa\u6587\u4ef6\u5939\u540e\u9009\u62e9', 404)
    if not cwd.is_dir() or not any(cwd == root or root in cwd.parents for root in worker.work_roots):
        raise FileIPCError('Work directory is not authorized', 403)
    with worker.mutex, worker.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT cwd FROM sessions WHERE id=?', (sid,)).fetchone()
        if row and row[0] != str(cwd):
            raise FileIPCError('Cannot change registered work directory', 409)
        if not row:
            db.execute('INSERT INTO sessions(id,claude_id,status,pid,cwd,updated_at) VALUES (?,?,?,?,?,?)', (sid, None, 'idle', None, str(cwd), worker.timestamp()))
    return {'registered': True}

def create_project(worker, body):
    name = body.get('name') if isinstance(body, dict) and set(body) == {'name'} else None
    if (not isinstance(name, str) or not name or name != name.strip() or
            name in ('.', '..') or '/' in name or chr(92) in name or
            any(ord(c) < 32 or ord(c) == 127 for c in name) or len(name.encode('utf-8')) > 200):
        raise FileIPCError('\u9879\u76ee\u540d\u65e0\u6548\uff0c\u8bf7\u4f7f\u7528\u5355\u7ea7\u6587\u4ef6\u5939\u540d', 400)
    if not worker.work_roots:
        raise FileIPCError('\u5de5\u4f5c\u76ee\u5f55\u4e0d\u53ef\u7528', 503)
    root = worker.work_roots[0]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(str(root), flags)
        try:
            try:
                os.mkdir(name, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            child_fd = os.open(name, flags, dir_fd=root_fd)
            try:
                if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                    raise FileIPCError('\u540c\u540d\u9879\u76ee\u4e0d\u662f\u6587\u4ef6\u5939', 409)
            finally:
                os.close(child_fd)
        finally:
            os.close(root_fd)
    except FileIPCError:
        raise
    except OSError:
        raise FileIPCError('\u6587\u4ef6\u5939\u4e0d\u53ef\u7528\u6216\u4e0d\u5b89\u5168', 409)
    return {'path': str(root / name)}


def create_directory(worker, body):
    if not isinstance(body, dict) or set(body) != {'parent', 'name'}:
        raise FileIPCError('\u65b0\u5efa\u6587\u4ef6\u5939\u53c2\u6570\u65e0\u6548')
    name, raw = body['name'], body['parent']
    if (not isinstance(name, str) or not name or name != name.strip() or name in ('.', '..')
            or '/' in name or chr(92) in name or len(name.encode('utf-8')) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise FileIPCError('\u6587\u4ef6\u5939\u540d\u79f0\u65e0\u6548\uff0c\u8bf7\u4f7f\u7528\u5355\u4e2a\u540d\u79f0\uff0c\u4e0d\u8981\u8f93\u5165\u8def\u5f84')
    if not isinstance(raw, str) or '\x00' in raw:
        raise FileIPCError('\u7236\u76ee\u5f55\u65e0\u6548')
    parent = Path(raw)
    if not parent.is_absolute() or '..' in parent.parts:
        raise FileIPCError('\u7236\u76ee\u5f55\u65e0\u6548')
    roots = sorted(worker.work_roots, key=lambda p: len(p.parts), reverse=True)
    root = next((p for p in roots if parent == p or p in parent.parents), None)
    if root is None:
        raise FileIPCError('\u7236\u76ee\u5f55\u4e0d\u5728\u6388\u6743\u8303\u56f4', 403)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = None
    try:
        fd = os.open(str(root), flags)
        for part in parent.relative_to(root).parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            os.mkdir(name, mode=0o700, dir_fd=fd)
        except FileExistsError:
            raise FileIPCError('\u540c\u540d\u6587\u4ef6\u5939\u6216\u6587\u4ef6\u5df2\u5b58\u5728\uff0c\u4e0d\u4f1a\u8986\u76d6\uff1b\u8bf7\u76f4\u63a5\u9009\u62e9\u6216\u66f4\u6362\u540d\u79f0', 409)
        check = os.open(name, flags, dir_fd=fd)
        try:
            actual = os.stat(str(parent), follow_symlinks=False)
            opened = os.fstat(fd)
            if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
                raise FileIPCError('\u7236\u76ee\u5f55\u5df2\u53d8\u5316\uff0c\u8bf7\u5237\u65b0\u6838\u5bf9\u521b\u5efa\u7ed3\u679c', 409)
            if not stat.S_ISDIR(os.fstat(check).st_mode):
                raise FileIPCError('\u65b0\u76ee\u5f55\u4e0d\u53ef\u7528', 409)
        finally:
            os.close(check)
    except FileIPCError:
        raise
    except OSError:
        raise FileIPCError('\u76ee\u5f55\u4e0d\u5b58\u5728\u3001\u6ca1\u6709\u5199\u5165\u6743\u9650\u6216\u5305\u542b\u7b26\u53f7\u94fe\u63a5\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u8bd5', 409)
    finally:
        if fd is not None:
            os.close(fd)
    return {'path': str(parent / name), 'created': True}


def directories(worker, raw):
    if not raw:
        return {'path': None, 'roots': [str(p) for p in worker.work_roots], 'directories': [{'name': str(p), 'path': str(p)} for p in worker.work_roots]}
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise FileIPCError('Invalid directory')
    try:
        directory = Path(raw).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise FileIPCError('Directory unavailable', 404)
    if not directory.is_dir() or not any(directory == p or p in directory.parents for p in worker.work_roots):
        raise FileIPCError('Directory not authorized', 403)
    try:
        entries = []
        for child in directory.iterdir():
            try:
                resolved = child.resolve(strict=True)
                if child.is_dir() and any(resolved == p or p in resolved.parents for p in worker.work_roots):
                    entries.append({'name': child.name, 'path': str(resolved)})
            except OSError:
                continue
        entries.sort(key=lambda item: item['name'].casefold())
    except OSError:
        raise FileIPCError('Directory unavailable', 403)
    return {'path': str(directory), 'roots': [str(p) for p in worker.work_roots], 'directories': entries[:1000]}

def receive(worker, sid, fileid, stream, length, suffix):
    _session(worker, sid)
    if not worker.valid_id(fileid):
        raise FileIPCError('Invalid attachment identifier')
    if not isinstance(length, int) or length < 0 or length > MAX_FILE:
        raise FileIPCError('Attachment exceeds size limit', 413)
    if not isinstance(suffix, str) or (suffix and not re.fullmatch(r'\.[A-Za-z0-9]{1,15}', suffix)):
        raise FileIPCError('Invalid file suffix')
    root = _root(worker)
    with worker.mutex:
        session_dir = root / sid
        session_dir.mkdir(mode=0o700, exist_ok=True)
        if session_dir.is_symlink() or not session_dir.is_dir() or session_dir.stat().st_mode & 0o077:
            raise FileIPCError('Private attachment storage unavailable', 503)
        target = session_dir / fileid
        temporary = session_dir / ('.upload-' + uuid.uuid4().hex)
        temporary.mkdir(mode=0o700)
        try:
            name = 'file' + suffix.lower()
            digest = hashlib.sha256()
            remaining = length
            file_path = temporary / name
            with file_path.open('xb') as out:
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        raise FileIPCError('Incomplete attachment upload', 400)
                    remaining -= len(block)
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            meta = {'size': length, 'sha256': digest.hexdigest(), 'name': name}
            with (temporary / 'meta.json').open('x', encoding='utf-8') as out:
                json.dump(meta, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            if target.exists():
                try:
                    if target.is_symlink(): raise ValueError()
                    existing = json.loads((target / 'meta.json').read_text(encoding='utf-8'))
                except (OSError, ValueError, json.JSONDecodeError):
                    raise FileIPCError('Attachment storage is inconsistent', 409)
                if existing != meta:
                    raise FileIPCError('Attachment identifier has different content', 409)
                return {'attachment_id': fileid, 'duplicate': True}
            used = 0
            for child in session_dir.iterdir():
                if child.name.startswith('.') or not child.is_dir() or child.is_symlink():
                    continue
                try:
                    used += int(json.loads((child / 'meta.json').read_text(encoding='utf-8'))['size'])
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    raise FileIPCError('Attachment storage is inconsistent', 409)
            if used + length > MAX_SESSION_BYTES:
                raise FileIPCError('\u672c\u4f1a\u8bdd\u9644\u4ef6\u7d2f\u8ba1\u8d85\u8fc7 2 GiB', 413)
            os.rename(temporary, target)
            directory_fd = os.open(str(session_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            return {'attachment_id': fileid, 'duplicate': False}
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

def attachment(worker, sid, fileid):
    _session(worker, sid)
    if not worker.valid_id(fileid):
        raise FileIPCError('Invalid attachment identifier')
    folder = _root(worker) / sid / fileid
    try:
        if folder.is_symlink() or not folder.is_dir(): raise ValueError()
        meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
        name = meta['name']
        if not isinstance(name, str) or not re.fullmatch(r'file(?:\.[a-z0-9]{1,15})?', name): raise ValueError()
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != int(meta['size']): raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise FileIPCError('Attachment not found', 404)
    return str(path)

def open_project(worker, sid, raw):
    base = _session(worker, sid)
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise FileIPCError('Invalid file path')
    requested = Path(raw)
    if any(part in ('.', '..') for part in requested.parts):
        raise FileIPCError('Invalid file path', 403)
    try:
        relative = requested.relative_to(base) if requested.is_absolute() else requested
    except ValueError:
        raise FileIPCError('File is outside the work directory', 403)
    if not relative.parts or any(part in ('', '.', '..', '/') for part in relative.parts):
        raise FileIPCError('Invalid file path', 403)
    if not hasattr(os, 'O_NOFOLLOW'):
        raise FileIPCError('Secure file access unavailable', 503)
    # Ancestors require search permission, not directory-list permission.
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = None
    file_fd = None
    try:
        directory_fd = os.open('/', flags)
        for component in (*base.parts[1:], *relative.parts[:-1]):
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode): raise FileIPCError('Not a regular file', 403)
        if info.st_size > MAX_FILE: raise FileIPCError('File exceeds size limit', 413)
        return file_fd, info.st_size
    except FileIPCError:
        if file_fd is not None: os.close(file_fd)
        raise
    except (OSError, ValueError):
        if file_fd is not None: os.close(file_fd)
        raise FileIPCError('File unavailable', 404)
    finally:
        if directory_fd is not None: os.close(directory_fd)
