from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SpanIn(BaseModel):
    id: str
    trace_id: str
    parent_id: Optional[str] = None
    project: str = "default"
    name: str
    kind: str = "chain"
    start: float
    end: float
    input: Optional[str] = None
    output: Optional[str] = None
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)