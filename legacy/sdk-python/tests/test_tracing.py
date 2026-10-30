import asyncio

import pytest

import tynode
from tynode import client as c


class Collector:
    def __init__(self):
        self.items = []

    def enqueue(self, item):
        self.items.append(item)


@pytest.fixture
def col(monkeypatch):
    col = Collector()
    monkeypatch.setattr(c, "_client", col)
    return col


def test_nesting_and_decorator(col):
    @tynode.trace
    def inner(x):
        return x * 2

    @tynode.trace
    def outer(x):
        return inner(x) + 1

    assert outer(3) == 7
    by_name = {i["name"]: i for i in col.items}
    assert by_name["inner"]["parent_id"] == by_name["outer"]["id"]
    assert by_name["inner"]["trace_id"] == by_name["outer"]["trace_id"]
    assert by_name["outer"]["parent_id"] is None


def test_error_is_recorded(col):
    @tynode.trace
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    assert "ValueError: nope" in col.items[0]["error"]


def test_cost_estimate(col):
    with tynode.span("llm", "llm") as s:
        s.set(model="gpt-4o-mini", tokens_in=1_000_000, tokens_out=0)
    assert abs(col.items[0]["cost"] - 0.15) < 1e-9


def test_async_and_session(col):
    @tynode.trace
    async def f():
        return 1

    with tynode.session("u1"):
        assert asyncio.run(f()) == 1
    assert col.items[0]["session_id"] == "u1"