"""Tynode demo agent: a real tool-calling LLM agent, fully traced.

    python agent_demo.py "What is 17% of 2340, plus 85?"
    python agent_demo.py                 # runs 3 built-in tasks

Env: LLM_API_KEY (required), LLM_MODEL, LLM_BASE_URL (optional), AGENT_NAME
     TYNODE_URL, TYNODE_API_KEY (see tynode_sdk.py)
Any OpenAI-compatible API works: xAI Grok (xai-...), Groq (gsk_...), OpenAI (sk-...).
The API key is read from the environment only and is never sent to Tynode.
"""
import ast, json, operator, os, sys, time, urllib.error, urllib.request

from tynode_sdk import span, flush

KEY = os.getenv("LLM_API_KEY", "").strip()
if not KEY:
    sys.exit("Set LLM_API_KEY first.")


def _base():
    if os.getenv("LLM_BASE_URL"):
        return os.environ["LLM_BASE_URL"].rstrip("/")
    if KEY.startswith("xai-"):
        return "https://api.x.ai/v1"
    if KEY.startswith("gsk_"):
        return "https://api.groq.com/openai/v1"
    return "https://api.openai.com/v1"


BASE = _base()


def http(path, body=None):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None,
                                 {"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                                  "User-Agent": "tynode-demo-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %d from LLM API: %s" % (e.code, e.read().decode(errors="replace")[:300]))


def pick_model():
    if os.getenv("LLM_MODEL"):
        return os.environ["LLM_MODEL"]
    ids = [m["id"] for m in http("/models").get("data", [])]
    skip = ("image", "imagine", "video", "embed", "vision", "tts", "whisper", "guard", "moderation", "audio")
    chat = [i for i in ids if not any(s in i.lower() for s in skip)]
    for want in ("grok-4", "grok-4-fast-non-reasoning"):
        if want in chat:
            return want
    pref = [i for i in chat if i.startswith("grok-")] or [i for i in chat if i.startswith("gpt-")] or chat
    if not pref:
        sys.exit("No chat model found. Set LLM_MODEL.")
    print("Available models:", ", ".join(ids[:20]))
    return pref[0]


KB = {
    "refund policy": "Refunds are possible within 30 days of purchase with proof of payment.",
    "shipping": "Standard shipping takes 3-5 business days in the EU; express takes 1-2 days.",
    "warranty": "All devices include a 24-month warranty covering manufacturing defects.",
}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg}


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 100:
            raise ValueError("exponent too large")
        return OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
        return OPS[type(n.op)](_ev(n.operand))
    raise ValueError("unsupported expression")


def t_calculator(expression):
    return _ev(ast.parse(expression.replace(",", ""), mode="eval").body)


def t_search_docs(query):
    time.sleep(0.2)
    q = query.lower()
    hits = [v for k, v in KB.items() if any(w in q for w in k.split())]
    return " | ".join(hits) or "No matching document."


def t_current_time():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


TOOL_FUNCS = {"calculator": t_calculator, "search_docs": t_search_docs, "current_time": t_current_time}
TOOLS = [
    {"type": "function", "function": {"name": "calculator", "description": "Evaluate an arithmetic expression, e.g. 2340*0.17+85",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "search_docs", "description": "Search the company knowledge base (refunds, shipping, warranty).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "current_time", "description": "Current UTC time.",
     "parameters": {"type": "object", "properties": {}}}},
]
SYSTEM = ("You are a concise support and math assistant. Use tools when they help: search_docs for policy questions, "
          "calculator for arithmetic, current_time for the time. Answer in at most 3 sentences.")


def llm(messages, model):
    for attempt in range(3):
        try:
            with span("llm.chat", model=model, kind="llm", input=json.dumps(messages[-1], ensure_ascii=False)[:2000]) as s:
                r = http("/chat/completions", {"model": model, "messages": messages, "tools": TOOLS})
                u = r.get("usage") or {}
                s.usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                msg = r["choices"][0]["message"]
                s.set_output(msg.get("content") or json.dumps(msg.get("tool_calls") or [])[:2000])
                return msg
        except RuntimeError as e:
            retry = any(c in str(e) for c in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503"))
            if attempt == 2 or not retry:
                raise
            time.sleep(1.5 * (attempt + 1))


def run_agent(task, model, name):
    with span("agent.run", agent=name, kind="agent", input=task) as root:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
        for _step in range(6):
            msg = llm(messages, model)
            calls = msg.get("tool_calls") or []
            m = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                m["tool_calls"] = calls
            messages.append(m)
            if not calls:
                root.set_output(m["content"])
                return m["content"]
            for c in calls:
                fn, raw = c["function"]["name"], c["function"].get("arguments") or "{}"
                try:
                    with span("tool." + fn, kind="tool", input=raw) as ts:
                        out = str(TOOL_FUNCS[fn](**json.loads(raw)))
                        ts.set_output(out)
                except Exception as e:
                    out = "tool error: %s" % e
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out[:4000]})
        raise RuntimeError("agent did not finish in 6 steps")


TASKS = ["What is 17% of 2340, plus 85?",
         "A customer asks: can I get a refund, and how long does shipping take?",
         "What time is it in UTC right now? Then compute 2 to the power 10."]

if __name__ == "__main__":
    name = os.getenv("AGENT_NAME", "grok-support-agent")
    try:
        model = pick_model()
    except RuntimeError as e:
        sys.exit("Could not reach the LLM API (check the key): %s" % e)
    print("Provider:", BASE, "| model:", model, "| tynode:", os.getenv("TYNODE_URL", "http://127.0.0.1:8787"))
    for task in (sys.argv[1:] or TASKS):
        t0 = time.time()
        print("\n> " + task)
        try:
            print(run_agent(task, model, name))
        except Exception as e:
            print("FAILED:", e)
        print("(%.1fs)" % (time.time() - t0))
    flush()
    print("\nDone. Open Traces in Tynode and refresh.")