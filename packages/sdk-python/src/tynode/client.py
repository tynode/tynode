import atexit
import json
import logging
import os
import queue
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("tynode")


class Client:
    """Background batching sender. Never blocks or raises into user code."""

    def __init__(self, endpoint: str, api_key: Optional[str], project: str,
                 batch_size: int = 50, flush_interval: float = 1.0, enabled: bool = True):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.project = project
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.enabled = enabled
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._enqueued = 0
        self._done = 0
        self._thread: Optional[threading.Thread] = None
        if enabled:
            self._thread = threading.Thread(target=self._run, name="tynode-sender", daemon=True)
            self._thread.start()
            atexit.register(self.shutdown)

    def enqueue(self, item: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        item.setdefault("project", self.project)
        try:
            self._q.put_nowait(item)
            with self._lock:
                self._enqueued += 1
        except queue.Full:
            log.debug("tynode queue full, dropping span")

    def _run(self) -> None:
        buf: List[Dict[str, Any]] = []
        last = time.time()
        while not self._stop.is_set() or not self._q.empty() or buf:
            try:
                buf.append(self._q.get(timeout=self.flush_interval))
            except queue.Empty:
                pass
            due = time.time() - last >= self.flush_interval
            if buf and (len(buf) >= self.batch_size or due or self._stop.is_set()):
                self._send(buf)
                buf, last = [], time.time()

    def _send(self, batch: List[Dict[str, Any]]) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data = json.dumps(batch, default=str).encode()
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.endpoint + "/v1/spans", data=data, headers=headers)
                urllib.request.urlopen(req, timeout=5).read()
                break
            except Exception as e:  # noqa: BLE001
                log.debug("tynode send failed (%s), attempt %d", e, attempt + 1)
                time.sleep(0.4 * (2 ** attempt))
        with self._lock:
            self._done += len(batch)

    def flush(self, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if self._done >= self._enqueued:
                    return
            time.sleep(0.05)

    def shutdown(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=5)


_client: Optional[Any] = None


def init(endpoint: Optional[str] = None, api_key: Optional[str] = None,
         project: Optional[str] = None, auto_instrument: bool = True,
         enabled: Optional[bool] = None, **kw: Any) -> Client:
    global _client
    if _client is not None and hasattr(_client, "shutdown"):
        _client.shutdown()
    if enabled is None:
        enabled = os.getenv("TYNODE_DISABLED", "").lower() not in ("1", "true", "yes")
    _client = Client(
        endpoint=endpoint or os.getenv("TYNODE_ENDPOINT", "http://localhost:7420"),
        api_key=api_key or os.getenv("TYNODE_API_KEY"),
        project=project or os.getenv("TYNODE_PROJECT", "default"),
        enabled=enabled, **kw,
    )
    if auto_instrument:
        from .integrations import instrument_all
        instrument_all()
    return _client


def get_client() -> Any:
    global _client
    if _client is None:
        init()
    return _client


def flush(timeout: float = 5.0) -> None:
    if _client is not None:
        _client.flush(timeout)


def shutdown() -> None:
    if _client is not None:
        _client.shutdown()