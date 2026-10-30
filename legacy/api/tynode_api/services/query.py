import time
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db.models import SpanRow


def span_out(r: SpanRow) -> Dict[str, Any]:
    return {
        "id": r.id, "trace_id": r.trace_id, "parent_id": r.parent_id, "project": r.project,
        "name": r.name, "kind": r.kind, "start": r.start_ts, "end": r.end_ts,
        "duration_ms": max(0.0, (r.end_ts - r.start_ts) * 1000),
        "input": r.input, "output": r.output, "model": r.model,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost": r.cost,
        "error": r.error, "session_id": r.session_id,
        "metadata": r.attrs or {}, "tags": r.tags or [],
    }


def list_traces(db: Session, limit: int = 50, q: Optional[str] = None,
                project: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(SpanRow).where(SpanRow.parent_id.is_(None))
    if q:
        stmt = stmt.where(SpanRow.name.ilike(f"%{q}%"))
    if project:
        stmt = stmt.where(SpanRow.project == project)
    roots = db.execute(stmt.order_by(SpanRow.start_ts.desc()).limit(limit)).scalars().all()
    if not roots:
        return []
    ids = [r.trace_id for r in roots]
    agg = db.execute(
        select(
            SpanRow.trace_id,
            func.count(),
            func.sum(SpanRow.cost),
            func.sum(SpanRow.tokens_in + SpanRow.tokens_out),
            func.sum(case((SpanRow.error.is_not(None), 1), else_=0)),
        ).where(SpanRow.trace_id.in_(ids)).group_by(SpanRow.trace_id)
    ).all()
    by_id = {a[0]: a for a in agg}
    out = []
    for r in roots:
        a = by_id.get(r.trace_id)
        out.append({
            "trace_id": r.trace_id, "name": r.name, "project": r.project,
            "start": r.start_ts, "duration_ms": max(0.0, (r.end_ts - r.start_ts) * 1000),
            "spans": int(a[1]) if a else 1, "cost": float(a[2] or 0) if a else 0.0,
            "tokens": int(a[3] or 0) if a else 0,
            "errors": int(a[4] or 0) if a else 0,
            "status": "error" if a and (a[4] or 0) > 0 else "ok",
            "session_id": r.session_id,
        })
    return out


def get_trace(db: Session, trace_id: str) -> List[Dict[str, Any]]:
    rows = db.execute(
        select(SpanRow).where(SpanRow.trace_id == trace_id).order_by(SpanRow.start_ts)
    ).scalars().all()
    return [span_out(r) for r in rows]


def _pct(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))]


def overview(db: Session, hours: int = 24) -> Dict[str, Any]:
    now = time.time()
    since = now - hours * 3600
    rows = db.execute(
        select(SpanRow).where(SpanRow.start_ts >= since).limit(50000)
    ).scalars().all()
    roots = [r for r in rows if r.parent_id is None]
    err_traces = {r.trace_id for r in rows if r.error}
    durations = [(r.end_ts - r.start_ts) * 1000 for r in roots]

    n = min(hours, 48)
    size = hours * 3600 / n
    series = [{"t": since + i * size, "traces": 0, "cost": 0.0, "errors": 0} for i in range(n)]
    for r in rows:
        b = series[max(0, min(n - 1, int((r.start_ts - since) / size)))]
        b["cost"] += r.cost
        if r.parent_id is None:
            b["traces"] += 1
            if r.trace_id in err_traces:
                b["errors"] += 1

    models: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.kind != "llm" or not r.model:
            continue
        m = models.setdefault(r.model, {"model": r.model, "calls": 0, "tokens": 0, "cost": 0.0, "_lat": 0.0})
        m["calls"] += 1
        m["tokens"] += r.tokens_in + r.tokens_out
        m["cost"] += r.cost
        m["_lat"] += (r.end_ts - r.start_ts) * 1000
    model_list = []
    for m in models.values():
        m["avg_latency_ms"] = m.pop("_lat") / max(1, m["calls"])
        model_list.append(m)
    model_list.sort(key=lambda x: x["cost"], reverse=True)

    return {
        "hours": hours,
        "traces": len(roots),
        "llm_calls": sum(1 for r in rows if r.kind == "llm"),
        "cost": sum(r.cost for r in rows),
        "tokens": sum(r.tokens_in + r.tokens_out for r in rows),
        "error_traces": len([r for r in roots if r.trace_id in err_traces]),
        "error_rate": (len([r for r in roots if r.trace_id in err_traces]) / len(roots)) if roots else 0.0,
        "p50_ms": _pct(durations, 50), "p95_ms": _pct(durations, 95),
        "series": series, "models": model_list,
    }