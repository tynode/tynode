import contextvars
import functools
import inspect
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from .client import get_client
from .pricing import estimate_cost

_current: "contextvars.ContextVar[Optional[span]]" = contextvars.ContextVar("tynode_current", default=None)
_session: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("tynode_session", default=None)
MAX_LEN = 8000


def _ser(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = repr(v)
    return s[:MAX_LEN]


class span:
    """Context manager (sync or async) that records one step of an agent run."""

    def __init__(self, name: str, kind: str = "chain", input: Any = None,
                 metadata: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None,
                 model: Optional[str] = None):
        self.name, self.kind, self.input = name, kind, input
        self.output: Any = None
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.tags: List[str] = list(tags or [])
        self.model = model
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost: Optional[float] = None
        self.error: Optional[str] = None

    def set(self, **kw: Any) -> "span":
        md = kw.pop("metadata", None)
        if md:
            self.metadata.update(md)
        for k, v in kw.items():
            setattr(self, k, v)
        return self

    def __enter__(self) -> "span":
        parent = _current.get()
        self.id = uuid.uuid4().hex[:16]
        self.parent_id = parent.id if parent else None
        self.trace_id = parent.trace_id if parent else uuid.uuid4().hex
        self.session_id = _session.get() or (parent.session_id if parent else None)
        self.start = time.time()
        self._token = _current.set(self)
        return self

    def __exit__(self, et, ev, tb) -> bool:
        end = time.time()
        _current.reset(self._token)
        if ev is not None:
            self.error = f"{et.__name__}: {ev}"
        cost = self.cost if self.cost is not None else estimate_cost(self.model, self.tokens_in, self.tokens_out)
        try:
            get_client().enqueue({
                "id": self.id, "trace_id": self.trace_id, "parent_id": self.parent_id,
                "name": self.name, "kind": self.kind, "start": self.start, "end": end,
                "input": _ser(self.input), "output": _ser(self.output), "model": self.model,
                "tokens_in": int(self.tokens_in or 0), "tokens_out": int(self.tokens_out or 0),
                "cost": float(cost), "error": self.error, "session_id": self.session_id,
                "metadata": self.metadata, "tags": self.tags,
            })
        except Exception:  # noqa: BLE001
            pass
        return False

    async def __aenter__(self) -> "span":
        return self.__enter__()

    async def __aexit__(self, et, ev, tb) -> bool:
        return self.__exit__(et, ev, tb)


class session:
    """Group traces by user or conversation: `with tynode.session("user-42"):`"""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def __enter__(self):
        self._tok = _session.set(self.session_id)
        return self

    def __exit__(self, *a):
        _session.reset(self._tok)
        return False


def update_current(**kw: Any) -> None:
    s = _current.get()
    if s is not None:
        s.set(**kw)


def trace(fn=None, *, name: Optional[str] = None, kind: str = "chain", tags: Optional[List[str]] = None):
    def deco(f):
        label = name or f.__name__
        try:
            sig = inspect.signature(f)
        except (TypeError, ValueError):
            sig = None

        def capture(a, k):
            if sig is None:
                return {"args": a, "kwargs": k}
            try:
                b = sig.bind_partial(*a, **k)
                return {n: v for n, v in b.arguments.items() if n not in ("self", "cls")}
            except Exception:  # noqa: BLE001
                return {"args": a, "kwargs": k}

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def aw(*a, **k):
                async with span(label, kind, input=capture(a, k), tags=tags) as s:
                    r = await f(*a, **k)
                    s.output = r
                    return r
            return aw

        @functools.wraps(f)
        def w(*a, **k):
            with span(label, kind, input=capture(a, k), tags=tags) as s:
                r = f(*a, **k)
                s.output = r
                return r
        return w

    return deco(fn) if fn else deco