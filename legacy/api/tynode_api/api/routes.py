from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.security import require_key
from ..db.session import get_db
from ..schemas import SpanIn
from ..services import ingest, query

router = APIRouter(dependencies=[Depends(require_key)])


@router.post("/v1/spans")
def ingest_spans(payload: Union[SpanIn, List[SpanIn]], db: Session = Depends(get_db)):
    spans = payload if isinstance(payload, list) else [payload]
    return {"ok": True, "ingested": ingest.ingest_spans(db, spans)}


@router.get("/api/v1/overview")
def overview(hours: int = Query(24, ge=1, le=720), db: Session = Depends(get_db)):
    return query.overview(db, hours)


@router.get("/api/v1/traces")
def traces(limit: int = Query(50, ge=1, le=500), q: Optional[str] = None,
           project: Optional[str] = None, db: Session = Depends(get_db)):
    return query.list_traces(db, limit, q, project)


@router.get("/api/v1/traces/{trace_id}")
def trace(trace_id: str, db: Session = Depends(get_db)):
    spans = query.get_trace(db, trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail="Trace not found")
    return spans