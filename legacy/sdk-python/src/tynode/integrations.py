import functools
from typing import Any, Callable

from .tracing import span


def _wrap(orig: Callable, label: str, extract: Callable[[span, Any], None], is_async: bool) -> Callable:
    if is_async:
        @functools.wraps(orig)
        async def awrapper(self, *a, **k):
            async with span(label, "llm", input=k.get("messages")) as s:
                s.model = k.get("model")
                r = await orig(self, *a, **k)
                try:
                    extract(s, r)
                except Exception:  # noqa: BLE001
                    pass
                return r
        awrapper._tynode = True  # type: ignore[attr-defined]
        return awrapper

    @functools.wraps(orig)
    def wrapper(self, *a, **k):
        with span(label, "llm", input=k.get("messages")) as s:
            s.model = k.get("model")
            r = orig(self, *a, **k)
            try:
                extract(s, r)
            except Exception:  # noqa: BLE001
                pass
            return r
    wrapper._tynode = True  # type: ignore[attr-defined]
    return wrapper


def _openai_extract(s: span, r: Any) -> None:
    s.tokens_in = r.usage.prompt_tokens
    s.tokens_out = r.usage.completion_tokens
    s.output = r.choices[0].message.content


def _anthropic_extract(s: span, r: Any) -> None:
    s.tokens_in = r.usage.input_tokens
    s.tokens_out = r.usage.output_tokens
    s.output = "".join(getattr(b, "text", "") for b in r.content)


def _patch(cls: Any, label: str, extract: Callable, is_async: bool) -> None:
    orig = getattr(cls, "create", None)
    if orig is None or getattr(orig, "_tynode", False):
        return
    cls.create = _wrap(orig, label, extract, is_async)


def instrument_openai() -> None:
    try:
        from openai.resources.chat import completions as m
    except Exception:  # noqa: BLE001
        return
    _patch(getattr(m, "Completions", None) or object, "openai.chat", _openai_extract, False)
    _patch(getattr(m, "AsyncCompletions", None) or object, "openai.chat", _openai_extract, True)


def instrument_anthropic() -> None:
    try:
        from anthropic.resources import messages as m
    except Exception:  # noqa: BLE001
        return
    _patch(getattr(m, "Messages", None) or object, "anthropic.messages", _anthropic_extract, False)
    _patch(getattr(m, "AsyncMessages", None) or object, "anthropic.messages", _anthropic_extract, True)


def instrument_all() -> None:
    instrument_openai()
    instrument_anthropic()