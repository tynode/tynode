import os, random, sqlite3, time, uuid
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).parent
DATA = Path(os.getenv("TYNODE_DATA_DIR") or HERE)
DATA.mkdir(parents=True, exist_ok=True)
def _load_key():
    k = os.getenv("TYNODE_API_KEY")
    if k:
        return k
    p = DATA / "api.key"
    try:
        if p.exists():
            k = p.read_text(encoding="utf-8").strip()
            if k:
                return k
    except Exception:
        pass
    import secrets
    k = "tn_" + secrets.token_urlsafe(24)
    try:
        p.write_text(k, encoding="utf-8")
    except Exception:
        pass
    return k

KEY = _load_key()
app = FastAPI(title="Tynode Console", version="0.2.0")

def db():
    c = sqlite3.connect(DATA / "tynode.db")
    c.row_factory = sqlite3.Row
    return c

with db() as c:
    c.execute("""create table if not exists spans(
        span_id text primary key, trace_id text, name text, model text,
        start_ms integer, duration_ms integer, tokens_in integer, tokens_out integer,
        cost real, status text, error text)""")
    c.execute("create index if not exists ix_trace on spans(trace_id)")

class Span(BaseModel):
    trace_id: str
    span_id: str = ""
    name: str
    model: str = ""
    start_ms: int
    duration_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    status: str = "ok"
    error: str = ""

def auth(k):
    if k != KEY:
        raise HTTPException(401, "invalid api key")

@app.get("/health")
def health():
    return {"status": "ok", "service": "tynode-engine"}

@app.post("/v1/spans")
def ingest(spans: list[Span], x_api_key: str = Header(default="")):
    auth(x_api_key)
    with db() as c:
        for s in spans:
            c.execute("insert or replace into spans values(?,?,?,?,?,?,?,?,?,?,?)",
                (s.span_id or uuid.uuid4().hex[:12], s.trace_id, s.name, s.model, s.start_ms,
                 s.duration_ms, s.tokens_in, s.tokens_out, s.cost, s.status, s.error))
    return {"ingested": len(spans)}

TRACES = """select trace_id, min(start_ms) start, max(start_ms+duration_ms)-min(start_ms) duration,
  count(*) spans, sum(cost) cost, sum(tokens_in+tokens_out) tokens,
  max(status='error') error, group_concat(name, '|') names
  from spans group by trace_id order by start desc"""

@app.get("/v1/traces")
def traces(limit: int = 100):
    with db() as c:
        return [dict(r) for r in c.execute(TRACES + " limit ?", (limit,))]

@app.get("/v1/traces/{tid}")
def trace(tid: str):
    with db() as c:
        rows = [dict(r) for r in c.execute("select * from spans where trace_id=? order by start_ms, duration_ms desc", (tid,))]
    if not rows:
        raise HTTPException(404, "trace not found")
    return rows

@app.get("/v1/stats")
def stats():
    with db() as c:
        t = [dict(r) for r in c.execute(TRACES)]
        models = [dict(r) for r in c.execute(
            """select model, count(*) calls, sum(cost) cost, sum(tokens_in) tin, sum(tokens_out) tout,
               avg(duration_ms) latency, avg(status='error') err from spans
               where model!='' group by model order by cost desc""")]
    d = sorted(x["duration"] for x in t)
    pct = lambda p: d[min(len(d) - 1, int(len(d) * p))] if d else 0
    return {"traces": len(t), "cost": sum(x["cost"] or 0 for x in t),
            "tokens": sum(x["tokens"] or 0 for x in t),
            "error_rate": (sum(x["error"] for x in t) / len(t)) if t else 0,
            "p50": pct(.5), "p95": pct(.95), "models": models}

@app.post("/v1/demo")
def demo(x_api_key: str = Header(default="")):
    auth(x_api_key)
    now = int(time.time() * 1000)
    M = {"gpt-4o": (.0025, .01), "claude-sonnet": (.003, .015), "gpt-4o-mini": (.00015, .0006)}
    out = []
    for i in range(40):
        tid, t0 = uuid.uuid4().hex[:10], now - random.randint(0, 86_400_000)
        cur, bad = t0, random.random() < .12
        root = Span(trace_id=tid, name="agent.run", start_ms=t0, duration_ms=0)
        out.append(root)
        for step in random.sample(["plan", "retrieve", "tool.search", "generate", "review"], random.randint(3, 5)):
            m = random.choice(list(M)); ti, to = random.randint(200, 3000), random.randint(50, 900)
            dur = random.randint(250, 2600); fail = bad and step == "generate"
            out.append(Span(trace_id=tid, name=step, model=m, start_ms=cur, duration_ms=dur,
                tokens_in=ti, tokens_out=to, cost=(ti * M[m][0] + to * M[m][1]) / 1000,
                status="error" if fail else "ok", error="rate_limit_exceeded" if fail else ""))
            cur += dur
        root.duration_ms = cur - t0
        root.status = "error" if bad else "ok"
    return ingest(out, x_api_key)

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")

# --- tynode auth ---
import hashlib, hmac, secrets
from fastapi.responses import JSONResponse

with db() as c:
    c.execute("create table if not exists users(email text primary key, salt text, hash text, created integer)")
    c.execute("create table if not exists sessions(token text primary key, email text, created integer)")

def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200000).hex()

def _session(email):
    t = secrets.token_hex(24)
    with db() as c:
        c.execute("insert into sessions values(?,?,?)", (t, email, int(time.time())))
    return {"token": t, "email": email}

class Cred(BaseModel):
    email: str
    password: str

@app.post("/v1/auth/register")
def register(b: Cred):
    email = b.email.strip().lower()
    if "@" not in email or len(b.password) < 8:
        raise HTTPException(400, "Enter a valid email and a password with at least 8 characters.")
    with db() as c:
        if c.execute("select 1 from users where email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists.")
        salt = secrets.token_hex(16)
        c.execute("insert into users values(?,?,?,?)", (email, salt, _hash(b.password, salt), int(time.time())))
    return _session(email)

@app.post("/v1/auth/login")
def login(b: Cred):
    email = b.email.strip().lower()
    with db() as c:
        r = c.execute("select * from users where email=?", (email,)).fetchone()
    if not r or not hmac.compare_digest(r["hash"], _hash(b.password, r["salt"])):
        raise HTTPException(401, "Wrong email or password.")
    return _session(email)

@app.get("/v1/auth/me")
def me(authorization: str = Header(default="")):
    t = authorization.replace("Bearer ", "")
    with db() as c:
        r = c.execute("select email from sessions where token=?", (t,)).fetchone()
    if not r:
        raise HTTPException(401, "Not signed in")
    return {"email": r["email"]}

@app.middleware("http")
async def guard(request, call_next):
    p = request.url.path
    if request.method == "GET" and (p == "/v1/stats" or p.startswith("/v1/traces")):
        t = request.headers.get("authorization", "").replace("Bearer ", "")
        with db() as c:
            ok = c.execute("select 1 from sessions where token=?", (t,)).fetchone()
        if not ok:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return await call_next(request)

# --- tynode analytics layer ---
import sys
sys.path.insert(0, str(HERE))
import analytics
analytics.mount(app, db, KEY)

# --- tynode supabase auth ---
import supa_auth
supa_auth.mount(app, db, _session)

# --- tynode live ---
import tn_live
tn_live.mount(app, db, KEY)

# --- tynode plans ---
import tn_plans
tn_plans.mount(app, db, KEY)
