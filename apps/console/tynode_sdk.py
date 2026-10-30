"""Tynode SDK (stdlib only).

    from tynode_sdk import span

    @span("agent.run", agent="support-bot")       # whole function = one trace
    def run(task): ...

    with span("generate", model="gpt-4o") as s:    # one LLM call
        r = client.chat.completions.create(...)
        s.usage(r.usage.prompt_tokens, r.usage.completion_tokens)

Nested spans are linked automatically (parent_id). Cost is computed on the
server from the price table unless you pass cost=.
Env: TYNODE_URL, TYNODE_API_KEY, TYNODE_AGENT, TYNODE_PROJECT
"""
import atexit, contextvars, functools, inspect, json, os, queue, threading, time, urllib.request, uuid

URL = os.getenv("TYNODE_URL", "http://127.0.0.1:8799")
def _find_key():
    k = os.getenv("TYNODE_API_KEY")
    if k:
        return k
    for d in (os.getenv("TYNODE_DATA_DIR"), os.path.join(os.getenv("LOCALAPPDATA", ""), "Tynode")):
        try:
            if d:
                k = open(os.path.join(d, "api.key"), encoding="utf-8").read().strip()
                if k:
                    return k
        except Exception:
            pass
    return ""

KEY = _find_key()
AGENT = os.getenv("TYNODE_AGENT", "")
PROJECT = os.getenv("TYNODE_PROJECT", "default")

_trace = contextvars.ContextVar("tynode_trace", default=None)
_parent = contextvars.ContextVar("tynode_parent", default=None)
_agent = contextvars.ContextVar("tynode_agent", default=None)
_q = queue.Queue(maxsize=10000)
_started = False
_lock = threading.Lock()


def _post(batch):
    try:
        req = urllib.request.Request(URL + "/v1/ingest", json.dumps(batch).encode(),
                                     {"Content-Type": "application/json", "x-api-key": KEY})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass  # observability must never break the app


def _worker():
    while True:
        batch = [_q.get()]
        end = time.time() + 0.5
        while len(batch) < 100:
            left = end - time.time()
            if left <= 0:
                break
            try:
                batch.append(_q.get(timeout=left))
            except queue.Empty:
                break
        _post(batch)
        for _ in batch:
            _q.task_done()


def _enqueue(item):
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_worker, daemon=True).start()
            _started = True
    try:
        _q.put_nowait(item)
    except queue.Full:
        pass


def flush(timeout=3.0):
    end = time.time() + timeout
    while _q.unfinished_tasks and time.time() < end:
        time.sleep(0.05)


atexit.register(flush)

_lq = queue.Queue(maxsize=2000)
_lstarted = False


def _live_worker():
    while True:
        batch = [_lq.get()]
        try:
            while len(batch) < 50:
                batch.append(_lq.get_nowait())
        except queue.Empty:
            pass
        try:
            req = urllib.request.Request(URL + "/v1/tn/live/event", json.dumps(batch).encode(),
                                         {"Content-Type": "application/json", "x-api-key": KEY})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:
            pass


def _live(kind, s, et=None, ev=None):
    global _lstarted
    try:
        with _lock:
            if not _lstarted:
                threading.Thread(target=_live_worker, daemon=True).start()
                _lstarted = True
        _lq.put_nowait({"type": kind, "trace_id": s.trace_id, "span_id": s.span_id, "parent_id": s.parent_id,
                        "name": s.name, "kind": s.kind, "agent": s.agent, "model": s.model,
                        "ts": int(time.time() * 1000),
                        "dur": int((time.time() - s.t0) * 1000) if kind == "end" else 0,
                        "status": "error" if et else "ok",
                        "error": ("%s: %s" % (et.__name__, ev)) if et else ""})
    except Exception:
        pass


class span:
    def __init__(self, name, model="", agent=None, kind="", input="", output=""):
        self.name, self.model, self.agent_arg, self.kind = name, model, agent, kind or ("llm" if model else "")
        self.input, self.output = str(input)[:8000], str(output)[:8000]
        self.tin, self.tout, self.cost = 0, 0, None

    def usage(self, tokens_in, tokens_out, cost=None):
        self.tin, self.tout, self.cost = tokens_in, tokens_out, cost

    def set_input(self, text):
        self.input = str(text)[:8000]

    def set_output(self, text):
        self.output = str(text)[:8000]

    def __enter__(self):
        self.trace_token = _trace.set(uuid.uuid4().hex[:10]) if _trace.get() is None else None
        self.trace_id = _trace.get()
        self.parent_id = _parent.get() or ""
        self.span_id = uuid.uuid4().hex[:12]
        self.agent = self.agent_arg or _agent.get() or AGENT
        self.parent_token = _parent.set(self.span_id)
        self.agent_token = _agent.set(self.agent) if self.agent else None
        self.t0 = time.time()
        _live('start', self)
        return self

    def __exit__(self, et, ev, tb):
        _live('end', self, et, ev)
        _parent.reset(self.parent_token)
        if self.agent_token is not None:
            _agent.reset(self.agent_token)
        _enqueue({"trace_id": self.trace_id, "span_id": self.span_id, "parent_id": self.parent_id,
                  "name": self.name, "kind": self.kind, "agent": self.agent, "project": PROJECT,
                  "model": self.model, "start_ms": int(self.t0 * 1000),
                  "duration_ms": int((time.time() - self.t0) * 1000),
                  "tokens_in": self.tin, "tokens_out": self.tout, "cost": self.cost,
                  "status": "error" if et else "ok",
                  "error": ("%s: %s" % (et.__name__, ev)) if et else "",
                  "input": self.input, "output": self.output})
        if self.trace_token is not None:
            _trace.reset(self.trace_token)

    def __call__(self, fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrap(*a, **k):
                with span(self.name, self.model, self.agent_arg, self.kind):
                    return await fn(*a, **k)
            return awrap

        @functools.wraps(fn)
        def wrap(*a, **k):
            with span(self.name, self.model, self.agent_arg, self.kind):
                return fn(*a, **k)
        return wrap