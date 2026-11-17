"""One live CLI input stream, with durable send attempts and explicit acknowledgements."""
import json
import os
import shutil
import subprocess
import select
import threading
import time
import uuid
from pathlib import Path
from worker.receipts import accepted_ids
from worker import scopes, controls
from worker.collaboration import profile, signature, cli_arguments

ACK_TIMEOUT = 30


def message_uuid(sid, request_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, sid + '/' + request_id))


def stage_files(worker, sid, request_id, text, files):
    root = worker.db_path.parent / 'worker-inputs' / sid
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('Unsafe session input directory')
    if files:
        stage = root / message_uuid(sid, request_id)
        stage.mkdir(mode=0o700, exist_ok=False)
        paths = []
        for index, source in enumerate(files):
            destination = stage / (str(index) + Path(source).suffix[:16])
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
            paths.append(str(destination))
        text += '\n\nAttached files, read when needed:\n' + '\n'.join(paths)
    return text


def run(worker, sid, cwd, text, files, mode, model, effort, claude_id, resume):
    # Import only when called, so the existing worker module remains the entry point.
    from worker.service import now, signal_child_tree, diagnostic
    import signal
    status = 'unknown'
    run_identity = None
    proc = None
    writer = None
    resource_monitor = None
    scope = None
    stderr = []
    done = threading.Event()
    with worker.mutex:
        child = worker.children[sid]
        request_id = child['request']['request_id']
        child.update(stream=True, closing=False, idle=False, result_seen=False,
                     background={}, cli_queued=0, wake=threading.Event(), write_error=False, ack_deadlines={}, stdin_lock=threading.Lock(), ready=threading.Event(),
                     control_waiters={}, interrupt_ack=False, forced_stop=False, done_event=done,
                     config=(str(cwd), mode, model, effort, signature(child['request'])), phase=None)
    collaboration = profile(child['request'])
    root = worker.db_path.parent / 'worker-inputs' / sid
    args = [worker.claude, '-p', '--verbose', '--input-format', 'stream-json',
            '--output-format', 'stream-json', '--include-partial-messages',
            '--replay-user-messages', '--permission-mode', mode, '--permission-prompt-tool', 'stdio']
    args += ['--resume' if resume else '--session-id', claude_id]
    if model:
        args += ['--model', model]
    if effort:
        args += ['--effort', effort]
    args += cli_arguments(collaboration)
    try:
        with worker.db() as db:
            run_identity = json.loads(db.execute('SELECT worker_provenance FROM sessions WHERE id=?',(sid,)).fetchone()[0] or 'null')
            run_since = db.execute('SELECT created_at FROM stream_deliveries WHERE session_id=? AND request_id=?', (sid, request_id)).fetchone()[0]
        initial_text = stage_files(worker, sid, request_id, text, files)
        args += ['--add-dir', str(root)]
        with worker.mutex:
            if child['stop']:
                status = 'interrupted'
                return
        scope = scopes.allocate(worker,sid,run_identity)
        from worker.resource_usage import Monitor
        resource_monitor=Monitor(worker,sid,scope)
        child['scope'] = scope
        args = scopes.command(scope,args)
        proc = subprocess.Popen(args, cwd=str(cwd), env={**worker.environment,**({'WEB_AI_TASK_CGROUP':str(scope.parent)} if scope else {})},
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                start_new_session=True)
        os.set_blocking(proc.stdin.fileno(), False)
        with worker.mutex, worker.db() as db:
            child['process'] = proc
            db.execute('UPDATE sessions SET pid=?,claude_id=? WHERE id=?', (proc.pid, claude_id, sid))
            if child['stop']:
                controls.request_interrupt(worker,sid,child)

        def reap_scope_after_exit():
            proc.wait()
            if scope and not scopes.released(scope):
                try: scopes.kill(scope)
                except OSError: pass
        threading.Thread(target=reap_scope_after_exit,daemon=True).start()

        def drain_stderr():
            for line in proc.stderr:
                stderr.append(line[:1000])
                if len(stderr) > 20:
                    stderr.pop(0)
        threading.Thread(target=drain_stderr, daemon=True).start()

        def write_message(rid, content):
            wire_id = message_uuid(sid, rid)
            # The write boundary is persisted before touching the pipe. It is never retried blindly.
            with worker.mutex, worker.db() as db:
                if done.is_set() or child['stop'] or child['closing']:
                    raise RuntimeError('Input closed')
                db.execute('UPDATE stream_deliveries SET state=? WHERE session_id=? AND request_id=?',
                           ('writing', sid, rid))
                child['idle'] = False
                child['ack_deadlines'][wire_id] = time.monotonic() + ACK_TIMEOUT
            message = {'type': 'user', 'uuid': wire_id, 'session_id': claude_id,
                       'parent_tool_use_id': None, 'message': {'role': 'user', 'content': content}}
            controls.write(child,message)

        def input_loop():
            try:
                init_id='initialize-'+uuid.uuid4().hex
                init_event=threading.Event()
                init_waiter={'event':init_event,'response':None}
                child['control_waiters'][init_id]=init_waiter
                controls.write(child,{'type':'control_request','request_id':init_id,'request':{'subtype':'initialize','hooks':None}})
                if not init_event.wait(60):raise TimeoutError('Claude initialization timed out')
                if (init_waiter['response'] or {}).get('subtype')!='success':raise RuntimeError('Claude initialization rejected')
                child['control_waiters'].pop(init_id,None)
                child['ready'].set()
                if child['stop']:return
                write_message(request_id, initial_text)
                while not done.is_set():
                    claimed = None
                    with worker.mutex, worker.db() as db:
                        if child['stop'] or child['closing']:
                            return
                        if any(deadline < time.monotonic() for deadline in child['ack_deadlines'].values()):
                            raise TimeoutError('Claude acknowledgement timed out')
                        paused = db.execute('SELECT input_paused FROM sessions WHERE id=?',(sid,)).fetchone()[0]
                        pending = db.execute('SELECT seq,payload FROM pending_turns WHERE session_id=? ORDER BY seq LIMIT 1', (sid,)).fetchone()
                        unacked = db.execute("SELECT 1 FROM stream_deliveries WHERE session_id=? AND state IN ('staging','writing') LIMIT 1", (sid,)).fetchone()
                        if pending and not paused and not unacked and not controls.pending(worker,sid):
                            request = json.loads(pending[1])
                            _, next_cwd, prompt, attachments, next_mode, next_model, next_effort = worker.validate(request)
                            if (str(next_cwd), next_mode, next_model, next_effort, signature(request)) == child['config']:
                                rid = request['request_id']
                                db.execute('INSERT INTO stream_deliveries VALUES (?,?,?,?,?)',
                                           (sid, rid, message_uuid(sid, rid), 'staging', now()))
                                db.execute('DELETE FROM pending_turns WHERE seq=?', (pending[0],))
                                worker._event(db, sid, 'user', {'text': prompt, 'attachments': attachments, 'request_id': rid})
                                worker._event(db, sid, 'queue_changed', {'reason': 'sending'})
                                claimed = (rid, prompt, attachments)
                            # A changed configuration waits for this process to release ownership.
                        inflight = db.execute("SELECT 1 FROM stream_deliveries WHERE session_id=? AND state IN ('staging','writing','delivered') LIMIT 1", (sid,)).fetchone()
                        if not claimed and child['idle'] and child['result_seen'] and not child['background'] and not child['cli_queued'] and not inflight and not controls.pending(worker,sid):
                            child['closing'] = True
                    if child['closing']:
                        proc.stdin.close()
                        return
                    if claimed:
                        rid, prompt, attachments = claimed
                        content = stage_files(worker, sid, rid, prompt, attachments)
                        write_message(rid, content)
                    else:
                        child['wake'].wait(.1)
                        child['wake'].clear()
            except Exception as exc:
                if done.is_set():return
                with worker.mutex:
                    child['write_error'] = True
                worker.append(sid, 'error', {'message': '\u7b49\u5f85 Claude \u63a5\u6536\u786e\u8ba4\u8d85\u65f6\uff0c\u672c\u6761\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1' if isinstance(exc,TimeoutError) else '\u6d88\u606f\u8f93\u5165\u5931\u8d25\uff0c\u672c\u6761\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1','cause':type(exc).__name__})
                signal_child_tree(proc, signal.SIGTERM)
                # Escalation must not depend on stdout closing after TERM.
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    if scope:scopes.kill(scope)
                    else:signal_child_tree(proc, signal.SIGKILL)

        writer = threading.Thread(target=input_loop, daemon=True)
        writer.start()
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                worker.append(sid, 'error', {'message': 'Invalid Claude stream event'})
                continue
            if not isinstance(message, dict):
                continue
            if controls.receive(worker,sid,child,message):
                continue
            top_level = message.get('session_id') == claude_id and message.get('parent_tool_use_id') is None
            with worker.mutex, worker.db() as db:
                for wire_id in accepted_ids(message,claude_id):
                    row = db.execute("SELECT request_id,state FROM stream_deliveries WHERE session_id=? AND wire_uuid=?", (sid,wire_id)).fetchone()
                    if row and row[1]=='writing':
                        child['ack_deadlines'].pop(wire_id,None)
                        db.execute("UPDATE stream_deliveries SET state='delivered' WHERE session_id=? AND wire_uuid=?",(sid,wire_id))
                        worker._event(db,sid,'delivery',{'request_id':row[0],'state':'delivered','source':message.get('type')})
                if top_level and message.get('type') in ('assistant', 'stream_event'):
                    child['idle'] = False
                    child['phase'] = 'responding'
                if top_level and message.get('type') == 'system':
                    subtype = message.get('subtype')
                    if subtype=='status' and message.get('status') in ('requesting','compacting'):child['phase']=message['status']
                    if subtype == 'session_state_changed':
                        child['idle'] = message.get('state') == 'idle'
                    elif subtype == 'background_tasks_changed' and isinstance(message.get('tasks'), list):
                        child['background'] = {task.get('task_id', task.get('id', str(i))): task for i, task in enumerate(message['tasks']) if isinstance(task, dict) and task.get('ambient') is not True}
                if top_level and message.get('type') == 'result':
                    child['result_seen'] = True
                    child['phase'] = 'idle'
                    child['idle'] = True
                    child['cli_queued'] = message.get('queued_turn_count', 0)
                    ids = message.get('user_message_uuids') or [message.get('user_message_uuid')]
                    if not isinstance(ids, list):
                        ids = []
                    if not any(isinstance(item, str) for item in ids):
                        inflight = db.execute("SELECT 1 FROM stream_deliveries WHERE session_id=? AND state IN ('staging','writing','delivered') LIMIT 1", (sid,)).fetchone()
                        if inflight:
                            raise ValueError('Cannot associate Claude result with a pending message')
                    failed = bool(message.get('is_error')) or message.get('subtype') not in (None, 'success')
                    for wire_id in ids:
                        if isinstance(wire_id, str):
                            child['ack_deadlines'].pop(wire_id, None)
                            db.execute("UPDATE stream_deliveries SET state=? WHERE session_id=? AND wire_uuid=? AND state IN ('writing','delivered')", ('failed' if failed else 'completed', sid, wire_id))
                child['wake'].set()
            if message.get('type') == 'result' and (message.get('is_error') or message.get('subtype') not in (None, 'success')):
                safe = {key: message[key] for key in ('type','subtype','is_error','session_id','duration_ms','num_turns','usage','total_cost_usd','user_message_uuid','user_message_uuids') if key in message}
                detail = diagnostic([message.get('result', '')] + [str(item) for item in message.get('errors', [])[:3]])
                if detail:
                    safe['result'] = detail
                message = safe
            worker.append(sid, 'claude', message)
        code = proc.wait(timeout=5)
        if code and stderr:
            worker.append(sid,'error',{'message':diagnostic(stderr) or 'Claude process exited with an error','exit_code':code})
        with worker.mutex, worker.db() as db:
            uncertain = db.execute("SELECT 1 FROM stream_deliveries WHERE session_id=? AND state IN ('staging','writing','delivered') LIMIT 1", (sid,)).fetchone()
            failed = db.execute("SELECT 1 FROM stream_deliveries WHERE session_id=? AND created_at>=? AND state='failed' LIMIT 1", (sid, run_since)).fetchone()
            if child['stop'] or child['write_error'] or uncertain or child['background']:
                status = 'unknown'
            elif code == 0 and child['result_seen']:
                status = 'failed' if failed else 'completed'
            else:
                status = 'failed'
    except Exception as exc:
        worker.append(sid, 'error', {'message': 'Claude stream failed: ' + type(exc).__name__})
        status = 'unknown'
    finally:
        done.set()
        child['wake'].set()
        if proc is not None and proc.poll() is None:
            signal_child_tree(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_child_tree(proc, signal.SIGKILL)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:pass
        if writer is not None:
            writer.join(timeout=5)
        if proc is not None:
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        if resource_monitor is not None:
            resource_monitor.stop()
        try:
            scopes.cleanup(scope)
        except OSError:
            pass
        released=scopes.is_released(worker,sid,proc.pid if proc else None,run_identity) if proc else True
        if released and status=='unknown':status='interrupted' if proc else 'failed'
        if not released:status='unknown'
        with worker.mutex, worker.db() as db:
            if released:
                initial_state=db.execute('SELECT state FROM stream_deliveries WHERE session_id=? AND request_id=?',(sid,request_id)).fetchone()
                if not resume and initial_state and initial_state[0]=='staging':
                    db.execute('UPDATE sessions SET claude_id=NULL WHERE id=?',(sid,))
                db.execute("UPDATE stream_deliveries SET state='failed' WHERE session_id=? AND state='staging'",(sid,))
                db.execute("UPDATE stream_deliveries SET state='interrupted' WHERE session_id=? AND state='delivered'",(sid,))
            db.execute("UPDATE stream_deliveries SET state='unknown' WHERE session_id=? AND state IN ('staging','writing','delivered')", (sid,))
            worker.children.pop(sid, None)
            db.execute('UPDATE sessions SET status=?,pid=?,updated_at=? WHERE id=?',(status,proc.pid if proc and not released else None,now(),sid))
            if status=='interrupted' and child['interrupt_ack'] and not child['forced_stop']:
                db.execute('UPDATE sessions SET input_paused=0 WHERE id=?',(sid,))
            if status!='completed' and not (status=='interrupted' and child['interrupt_ack'] and not child['forced_stop']):
                db.execute('UPDATE sessions SET input_paused=1 WHERE id=?',(sid,))
            if status=='unknown' or child['forced_stop']:
                db.execute('UPDATE sessions SET input_paused=1 WHERE id=?',(sid,))
            db.execute("UPDATE approvals SET state='cancelled' WHERE session_id=? AND state IN ('pending','answering','unknown')",(sid,))
            worker._event(db,sid,'status',{'status':status,'execution_released':released})
        if released:worker._dispatch_global()
