from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ..core.config import settings
from .models import Base

_kw = {}
if settings.database_url.startswith("sqlite"):
    _kw["connect_args"] = {"check_same_thread": False}
    if ":memory:" in settings.database_url:
        _kw["poolclass"] = StaticPool

engine = create_engine(settings.database_url, pool_pre_ping=True, **_kw)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()