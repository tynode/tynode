from typing import List

from sqlalchemy.orm import Session

from ..db.models import SpanRow
from ..schemas import SpanIn


def ingest_spans(db: Session, spans: List[SpanIn]) -> int:
    for s in spans:
        db.merge(
            SpanRow(
                id=s.id, trace_id=s.trace_id, parent_id=s.parent_id, project=s.project,
                name=s.name, kind=s.kind, start_ts=s.start, end_ts=s.end,
                input=s.input, output=s.output, model=s.model,
                tokens_in=s.tokens_in, tokens_out=s.tokens_out, cost=s.cost,
                error=s.error, session_id=s.session_id, attrs=s.metadata, tags=s.tags,
            )
        )
    db.commit()
    return len(spans)