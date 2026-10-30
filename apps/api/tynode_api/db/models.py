from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SpanRow(Base):
    __tablename__ = "spans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    project: Mapped[str] = mapped_column(String(128), index=True, default="default")
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32), default="chain")
    start_ts: Mapped[float] = mapped_column(Float, index=True)
    end_ts: Mapped[float] = mapped_column(Float)
    input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    attrs: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)