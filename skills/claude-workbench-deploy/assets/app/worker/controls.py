"""Official CLI control messages: interrupts and explicit tool/question replies."""
import json
import os
import select
import threading
import time
import uuid


def write(child, message):
    proc = child.get('process')
    if proc is None or proc.poll() is not None:
        raise RuntimeError('Claude process has exited')
    data = (json.dumps(message, ensure_ascii=False) + '\n').encode()
    until = time.monotonic() + 30
    with child['stdin_lock']:
        offset = 0
        while offset < len(data):
            if time.monotonic() > until or proc.poll() is not None:
                raise RuntimeError('Claude control input unavailable')
            if select.select([], [proc.stdin.fileno()], [], .1)[1]:
                try:
                    offset += os.write(proc.stdin.fileno(), data[offset:])
                except BlockingIOError:
                    pass


def request_interrupt(worker, sid, child):
    with worker.mutex:
        if child.get('interrupt_started'):return
        child['interrupt_started']=True
    def run():
        try:
            deadline=time.monotonic()+65
            while not child['ready'].wait(.1):
                if child['done_event'].is_set():return
                if time.monotonic()>deadline:raise RuntimeError('Claude process not ready')
            rid = 'interrupt-' + uuid.uuid4().hex
            event = threading.Event()
            child['control_waiters'][rid] = {'event':event, 'response':None}
            write(child, {'type':'control_request', 'request_id':rid, 'request':{'subtype':'interrupt'}})
            until=time.monotonic()+30
            while not event.wait(.1):
                if child['done_event'].is_set():return
                if time.monotonic()>until:raise RuntimeError('Official interrupt acknowledgement timed out')
            response = child['control_waiters'].pop(rid)['response']
            if response.get('subtype') != 'success':
                raise RuntimeError('Official interrupt was rejected')
            child['interrupt_ack'] = True
            # EOF closes this completed/interrupted CLI run; the next queued message
            # resumes the same Claude session. Never restart the shared executor.
            with child['stdin_lock']:
                if not child['process'].stdin.closed:
                    child['process'].stdin.close()
            try:
                child['process'].wait(timeout=8)
            except Exception:
                child['forced_stop'] = True
                from worker.scopes import kill
                from worker.service import request_child_stop
                if child.get('scope'):kill(child['scope'])
                else:request_child_stop(child['process'])
        except Exception as exc:
            with worker.mutex:
                if worker.children.get(sid) is not child or child.get('done_event',threading.Event()).is_set():return
                child['forced_stop'] = True
                worker.append(sid,'error',{'message':'\u5b98\u65b9\u4e2d\u65ad\u672a\u5b8c\u6210\uff0c\u6b63\u5728\u4ec5\u6e05\u7406\u5f53\u524d\u4f1a\u8bdd', 'cause':type(exc).__name__})
            from worker.scopes import kill
            from worker.service import request_child_stop
            if child.get('scope'):
                kill(child['scope'])
            elif child.get('process'):
                request_child_stop(child['process'])
    threading.Thread(target=run,daemon=True).start()


def receive(worker, sid, child, message):
    typ = message.get('type')
    if typ == 'control_response':
        response = message.get('response') or {}
        waiter = child['control_waiters'].get(response.get('request_id'))
        if waiter is not None:
            waiter['response'] = response
            waiter['event'].set()
        return True
    if typ == 'control_cancel_request':
        with worker.db() as db:
            db.execute("UPDATE approvals SET state='cancelled' WHERE session_id=? AND request_id=? AND state IN ('pending','answering','unknown')", (sid,message.get('request_id')))
        return True
    if typ != 'control_request':
        return False
    request = message.get('request') or {}
    rid = message.get('request_id')
    if request.get('subtype') != 'can_use_tool' or not isinstance(rid,str):
        write(child, {'type':'control_response','response':{'subtype':'error','request_id':rid,'error':'Unsupported official control request'}})
        return True
    from worker.service import now
    with worker.mutex,worker.db() as db:
        db.execute('INSERT OR IGNORE INTO approvals VALUES (?,?,?,?,?,?)',
            (sid,rid,str(request.get('tool_name','')),json.dumps(request.get('input') or {},ensure_ascii=False),'pending',now()))
        worker._event(db,sid,'approval_required',{'request_id':rid,'tool_name':request.get('tool_name')})
    child['wake'].set()
    return True


def pending(worker, sid):
    with worker.db() as db:
        return [dict(request_id=r[0],tool_name=r[1],input=json.loads(r[2]),state=r[3],created_at=r[4]) for r in db.execute("SELECT request_id,tool_name,input,state,created_at FROM approvals WHERE session_id=? AND state IN ('pending','answering','unknown') ORDER BY created_at",(sid,))]


def answer(worker,sid,rid,body):
    from worker.service import WorkerError
    if not isinstance(body,dict) or set(body)-{'allow','answers','comment'} or type(body.get('allow')) is not bool:
        raise WorkerError('\u5ba1\u6279\u56de\u590d\u683c\u5f0f\u65e0\u6548')
    with worker.mutex,worker.db() as db:
        row=db.execute("SELECT tool_name,input,state FROM approvals WHERE session_id=? AND request_id=?",(sid,rid)).fetchone()
        child=worker.children.get(sid)
        if not row or row[2]!='pending' or not child or child.get('stop'):
            raise WorkerError('\u8be5\u5ba1\u6279\u5df2\u7ed3\u675f\u6216\u4e0d\u5c5e\u4e8e\u5f53\u524d\u8fd0\u884c',409)
        original=json.loads(row[1])
        if body['allow'] and row[0]=='AskUserQuestion':
            answers=body.get('answers')
            questions=original.get('questions') or []
            if not isinstance(answers,dict) or set(answers)!={q.get('question') for q in questions} or any(not isinstance(v,str) or not v.strip() or len(v)>4000 for v in answers.values()):
                raise WorkerError('\u8bf7\u56de\u7b54\u5168\u90e8\u5b98\u65b9\u95ee\u9898')
            original=dict(original,answers=answers)
        response={'behavior':'allow','updatedInput':original} if body['allow'] else {'behavior':'deny','message':str(body.get('comment') or 'User declined')[:4000]}
        db.execute("UPDATE approvals SET state='answering' WHERE session_id=? AND request_id=?",(sid,rid))
    try:
        write(child,{'type':'control_response','response':{'subtype':'success','request_id':rid,'response':response}})
    except Exception:
        # No automatic retry after a write of uncertain delivery.
        with worker.db() as db:db.execute("UPDATE approvals SET state='unknown' WHERE session_id=? AND request_id=? AND state='answering'",(sid,rid))
        raise WorkerError('\u5ba1\u6279\u56de\u4f20\u7ed3\u679c\u5f85\u6838\u9a8c\uff0c\u4e0d\u4f1a\u81ea\u52a8\u91cd\u53d1',503)
    with worker.db() as db:
        db.execute("UPDATE approvals SET state='answered' WHERE session_id=? AND request_id=? AND state='answering'",(sid,rid))
        worker._event(db,sid,'approval_answered',{'request_id':rid,'allowed':body['allow']})
    return {'answered':True}
