"""Tynode analytics layer (fastapi + pydantic + stdlib only).

mount(app, db, key) adds: migrations, price table + cost engine, rich ingest
(/v1/ingest), analytics API (/v1/analytics/*), insights, trace waterfall,
audit log. All /v1/analytics/*, /v1/pricing, /v1/audit need a signed-in session.
`tz` query param = minutes EAST of UTC (Trier: 60 winter, 120 summer).
"""
import hmac, os, random, re, sqlite3, time, uuid
from collections import Counter, defaultdict
from contextlib import closing
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

HOUR, DAY = 3600000, 86400000
RANGES = {"24h": (HOUR, 24), "7d": (DAY, 7), "30d": (DAY, 30), "90d": (DAY, 90)}

EXPLAIN = {
    "spend": {
        "what": "Estimated LLM spend per period: tokens x the model price from your price table.",
        "read": "A spike means longer prompts, more calls, or a loop. Compare it with Tokens and the run count.",
        "act": "Click a bar to list the most expensive runs of that period.",
    },
    "tokens": {
        "what": "Input and output tokens sent to and received from models.",
        "read": "Input growth usually means bigger prompts or context. Output growth means longer answers.",
        "act": "Click a point to see which runs used the most tokens.",
    },
    "latency": {
        "what": "End-to-end duration of one agent run. p50 is the typical run, p95 is the slow tail.",
        "read": "If p95 grows while p50 stays flat, a few runs are stuck (retries, slow tools, rate limits).",
        "act": "Open the slowest runs and check the waterfall for the longest step.",
    },
    "reliability": {
        "what": "Share of runs in which no step failed. One failed step marks the whole run as failed.",
        "read": "A sudden drop with one repeated error message points to a provider or rate-limit problem.",
        "act": "Click the error rate to list failing runs and open one to see the failing step.",
    },
}

SEED_PRICES = [  # USD per 1K tokens (in, out). Verify against current provider pricing.
    ("gpt-4o", .0025, .01), ("gpt-4o-mini", .00015, .0006),
    ("gpt-4.1", .002, .008), ("gpt-4.1-mini", .0004, .0016),
    ("claude-sonnet", .003, .015), ("claude-haiku", .001, .005),
]

MIGRATIONS = [
    (1, ["alter table spans add column agent text default ''",
         "alter table spans add column project text default 'default'",
         "alter table spans add column parent_id text default ''",
         "alter table spans add column kind text default ''",
         "alter table spans add column input text default ''",
         "alter table spans add column output text default ''",
         "alter table spans add column meta text default ''"]),
    (2, ["create index if not exists ix_spans_start on spans(start_ms)",
         "create index if not exists ix_spans_agent on spans(agent)",
         "create index if not exists ix_spans_model on spans(model)"]),
    (3, ["create table if not exists prices(model text primary key, in_per_1k real, out_per_1k real, updated integer)"]),
    (4, ["create table if not exists audit_log(id integer primary key autoincrement, ts integer, email text, action text, detail text)"]),
]

INSERT_SQL = ("insert or replace into spans(span_id,trace_id,name,model,start_ms,duration_ms,tokens_in,tokens_out,"
              "cost,status,error,agent,project,parent_id,kind,input,output,meta) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

SECRETS = [re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"xai-[A-Za-z0-9]{20,}"), re.compile(r"gsk_[A-Za-z0-9]{20,}"),
           re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")]


def migrate(db):
    with closing(db()) as c:
        try:
            c.execute("pragma journal_mode=wal")
        except sqlite3.DatabaseError:
            pass
        c.execute("create table if not exists schema_migrations(version integer primary key, applied integer)")
        # ensure spans table exists on a fresh database
        c.execute("create table if not exists spans(span_id text primary key, trace_id text, name text, model text, start_ms integer, duration_ms integer, tokens_in integer, tokens_out integer, cost real, status text, error text)")
        done = {x[0] for x in c.execute("select version from schema_migrations")}
        for ver, stmts in MIGRATIONS:
            if ver in done:
                continue
            for s in stmts:
                try:
                    c.execute(s)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
            c.execute("insert into schema_migrations values(?,?)", (ver, int(time.time())))
        now = int(time.time())
        for m, i, o in SEED_PRICES:
            c.execute("insert or ignore into prices values(?,?,?,?)", (m, i, o, now))
        c.commit()


def mask(t):
    if not t or os.getenv("TYNODE_REDACT", "1") == "0":
        return t
    for p in SECRETS:
        t = p.sub("[redacted]", t)
    return t


def pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def delta(cur, prev):
    return None if not prev else (cur - prev) / prev


def money(x):
    return "$%.2f" % x if x >= 1 else "$%.4f" % x


def rootish(s):
    return s["name"].endswith(".run") or s.get("kind") == "agent"


def label(ts, bucket, tz):
    g = time.gmtime(ts / 1000 + tz * 60)
    return time.strftime("%H:00" if bucket == HOUR else "%a %d %b", g)


def window(rng, tz):
    b, n = RANGES.get(rng, RANGES["7d"])
    off = tz * 60000
    now = int(time.time() * 1000)
    cur = ((now + off) // b) * b - off
    return b, n, cur - (n - 1) * b, cur + b


def price_for(prices, model):
    m = (model or "").lower()
    best = None
    for k in prices:
        if m == k or m.startswith(k):
            if best is None or len(k) > len(best):
                best = k
    return prices[best] if best else None


def calc_cost(prices, model, tin, tout):
    p = price_for(prices, model)
    return (tin * p[0] + tout * p[1]) / 1000 if p else None


def summarize(trs):
    n = len(trs)
    errs = sum(1 for t in trs if t["err"])
    d = [t["dur"] for t in trs]
    cost = sum(t["cost"] for t in trs)
    tin = sum(t["tin"] for t in trs)
    tout = sum(t["tout"] for t in trs)
    return {"traces": n, "errors": errs, "error_rate": errs / n if n else 0, "cost": cost,
            "tokens": tin + tout, "tokens_in": tin, "tokens_out": tout,
            "p50": pct(d, .5), "p95": pct(d, .95), "p99": pct(d, .99), "avg_cost": cost / n if n else 0}


class SpanIn(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    span_id: str = ""
    parent_id: str = ""
    name: str = Field(min_length=1, max_length=200)
    kind: str = ""
    agent: str = ""
    project: str = "default"
    model: str = ""
    start_ms: int
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Optional[float] = None
    status: str = "ok"
    error: str = ""
    input: str = ""
    output: str = ""
    meta: str = ""


class PriceIn(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    in_per_1k: float = Field(ge=0)
    out_per_1k: float = Field(ge=0)


def mount(app, db, key):
    migrate(db)
    r = APIRouter()

    def q(sql, args=()):
        with closing(db()) as c:
            return [dict(x) for x in c.execute(sql, list(args))]

    def run(sql, args=()):
        with closing(db()) as c:
            cur = c.execute(sql, list(args))
            c.commit()
            return cur.rowcount

    def insert_rows(rows):
        with closing(db()) as c:
            c.executemany(INSERT_SQL, rows)
            c.commit()

    def session(authorization: str = Header(default="")):
        t = authorization.replace("Bearer ", "").strip()
        rows = q("select email from sessions where token=?", (t,)) if t else []
        if not rows:
            raise HTTPException(401, "Not signed in")
        return rows[0]["email"]

    def audit(email, action, detail=""):
        run("insert into audit_log(ts,email,action,detail) values(?,?,?,?)",
            (int(time.time() * 1000), email, action, detail[:500]))

    def load_prices():
        return {x["model"].lower(): (x["in_per_1k"], x["out_per_1k"]) for x in q("select * from prices")}

    def flt(agent, model, project):
        w, a = [], []
        if agent is not None:
            w.append("agent=?")
            a.append("" if agent == "(unnamed)" else agent)
        if model:
            w.append("model=?")
            a.append(model)
        if project:
            w.append("project=?")
            a.append(project)
        return w, a

    def traces_in(t0, t1, w, a):
        sql = ("select trace_id, min(start_ms) t0, max(start_ms+duration_ms)-min(start_ms) dur, count(*) n, "
               "coalesce(sum(cost),0) cost, coalesce(sum(tokens_in),0) tin, coalesce(sum(tokens_out),0) tout, "
               "max(status='error') err, max(agent) agent from spans where start_ms>=? and start_ms<?"
               + "".join(" and " + x for x in w) + " group by trace_id")
        return q(sql, [t0, t1] + a)

    def spans_in(t0, t1, w, a):
        sql = ("select trace_id, span_id, name, model, agent, project, kind, start_ms, duration_ms, tokens_in, "
               "tokens_out, cost, status, error from spans where start_ms>=? and start_ms<?"
               + "".join(" and " + x for x in w))
        return q(sql, [t0, t1] + a)

    def compute(rng, tz, agent, model, project):
        b, n, start, end = window(rng, tz)
        w, a = flt(agent, model, project)
        both = traces_in(start - n * b, end, w, a)
        cur = [t for t in both if t["t0"] >= start]
        prev = [t for t in both if t["t0"] < start]
        buckets = defaultdict(list)
        for t in cur:
            buckets[min(n - 1, (t["t0"] - start) // b)].append(t)
        series = []
        for i in range(n):
            ts = buckets.get(i, [])
            d = [t["dur"] for t in ts]
            errs = sum(1 for t in ts if t["err"])
            series.append({"t": start + i * b, "label": label(start + i * b, b, tz), "traces": len(ts),
                           "errors": errs, "error_rate": errs / len(ts) if ts else 0,
                           "cost": sum(t["cost"] for t in ts), "tokens_in": sum(t["tin"] for t in ts),
                           "tokens_out": sum(t["tout"] for t in ts), "p50": pct(d, .5), "p95": pct(d, .95)})
        return {"b": b, "n": n, "start": start, "end": end, "cur": cur, "prev": prev,
                "cs": summarize(cur), "ps": summarize(prev), "series": series,
                "sp": spans_in(start, end, w, a)}

    def insights_for(c):
        cs, ps, sp, series, b = c["cs"], c["ps"], c["sp"], c["series"], c["b"]
        out = []

        def add(sev, title, text, action=None):
            out.append({"severity": sev, "title": title, "text": text, "action": action})

        if cs["traces"] == 0:
            add("info", "No runs in this period",
                "Send spans with the SDK or POST /v1/ingest, or pick a longer range.")
            return out
        win = {"from": c["start"], "to": c["end"]}
        tmap = {t["trace_id"]: t for t in c["cur"]}

        bad = [s for s in sp if s["status"] == "error" and not rootish(s)]
        if cs["errors"] and bad:
            (msg, step, mdl), cnt = Counter((s["error"] or "unknown error", s["name"], s["model"] or "-")
                                            for s in bad).most_common(1)[0]
            spike = ps["traces"] > 0 and cs["error_rate"] >= max(ps["error_rate"] * 1.5, ps["error_rate"] + 0.02)
            sev = "crit" if cs["error_rate"] >= .15 else ("warn" if spike or cs["error_rate"] >= .05 else "info")
            head = "Error rate is %.1f%% (%d of %d runs)" % (cs["error_rate"] * 100, cs["errors"], cs["traces"])
            if ps["traces"]:
                head += ", previous period %.1f%%" % (ps["error_rate"] * 100)
            add(sev, "Error rate spiked" if spike else "Failures in this period",
                head + ". Most common cause: '%s' in step '%s' on %s (%d of %d failing spans)."
                % (msg, step, mdl, cnt, len(bad)), dict(type="traces", status="error", **win))
            worst = max(series, key=lambda s: s["errors"])
            if worst["errors"] >= 3 and sum(1 for s in series if s["errors"]) > 1:
                add("info", "Failures peaked on " + worst["label"],
                    "%d failed runs in that bucket, the most in this range." % worst["errors"],
                    {"type": "traces", "status": "error", "from": worst["t"], "to": worst["t"] + b})

        mc = defaultdict(float)
        for s in sp:
            mc[s["model"] or "-"] += s["cost"] or 0
        total = sum(mc.values())
        if total > 0 and len(mc) > 1:
            m, v = max(mc.items(), key=lambda kv: kv[1])
            if v / total >= .5:
                add("info", m + " drives most of your spend",
                    "%s is %.0f%% of spend (%s of %s)." % (m, v / total * 100, money(v), money(total)),
                    {"type": "traces", "model": m, "sort": "cost", **win})
        if ps["cost"] > 0 and cs["cost"] >= ps["cost"] * 1.25 and cs["cost"] - ps["cost"] >= .01:
            add("warn", "Spend is up",
                "Spend is %s, up %.0f%% from %s in the previous period." % (
                    money(cs["cost"]), (cs["cost"] / ps["cost"] - 1) * 100, money(ps["cost"])),
                {"type": "traces", "sort": "cost", **win})

        if ps["traces"] and cs["p95"] > 1000 and cs["p95"] >= ps["p95"] * 1.3:
            add("warn", "Runs got slower", "p95 latency is %.1f s, up from %.1f s." % (
                cs["p95"] / 1000, ps["p95"] / 1000), {"type": "traces", "sort": "duration", **win})
        steps = defaultdict(list)
        for s in sp:
            if not rootish(s):
                steps[s["name"]].append(s["duration_ms"])
        steps = {k: v for k, v in steps.items() if len(v) >= 3}
        if steps:
            nm, ds = max(steps.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
            add("info", "Slowest step: " + nm, "'%s' averages %.1f s over %d calls (p95 %.1f s)." % (
                nm, sum(ds) / len(ds) / 1000, len(ds), pct(ds, .95) / 1000))

        cnt = Counter((s["trace_id"], s["name"], s["model"]) for s in sp if not rootish(s))
        loops = sorted(((k, v) for k, v in cnt.items() if v >= 5), key=lambda kv: -kv[1])
        if loops:
            (tid, nm, _m), v = loops[0]
            cost = tmap.get(tid, {}).get("cost", 0)
            add("crit" if v >= 10 else "warn", "Possible loop in %d run%s" % (len(loops), "" if len(loops) == 1 else "s"),
                "Run %s called '%s' %d times (run cost %s). Check for a missing stop condition." % (
                    tid, nm, v, money(cost)), {"type": "trace", "trace_id": tid})

        costs = [t["cost"] for t in c["cur"] if t["cost"] > 0]
        if len(costs) >= 5:
            med = pct(costs, .5)
            big = max(c["cur"], key=lambda t: t["cost"])
            if med > 0 and big["cost"] >= 3 * med:
                add("info", "Cost outlier", "Run %s cost %s, %.1fx the median run." % (
                    big["trace_id"], money(big["cost"]), big["cost"] / med),
                    {"type": "trace", "trace_id": big["trace_id"]})

        prices = load_prices()
        miss = sorted({s["model"] for s in sp if s["model"] and (s["tokens_in"] or s["tokens_out"])
                       and price_for(prices, s["model"]) is None})
        if miss:
            add("warn", "Models without a price", "No price for: %s. Their cost shows as $0. "
                "Add them with PUT /v1/pricing." % ", ".join(miss[:5]))

        order = {"crit": 0, "warn": 1, "info": 2}
        out.sort(key=lambda i: order[i["severity"]])
        return out[:8]

    # ------------------------------------------------------------ ingest
    @r.post("/v1/ingest")
    def ingest2(spans: List[SpanIn], x_api_key: str = Header(default="")):
        if not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(401, "invalid api key")
        if len(spans) > 1000:
            raise HTTPException(413, "max 1000 spans per request")
        prices = load_prices()
        rows = []
        for s in spans:
            cost = s.cost
            if cost is None or (cost == 0 and (s.tokens_in or s.tokens_out)):
                c2 = calc_cost(prices, s.model, s.tokens_in, s.tokens_out)
                cost = c2 if c2 is not None else (s.cost or 0.0)
            rows.append((s.span_id or uuid.uuid4().hex[:12], s.trace_id, s.name, s.model, s.start_ms,
                         s.duration_ms, s.tokens_in, s.tokens_out, cost, "error" if s.status == "error" else "ok",
                         mask(s.error)[:500], s.agent, s.project or "default", s.parent_id, s.kind,
                         mask(s.input)[:8000], mask(s.output)[:8000], s.meta[:2000]))
        insert_rows(rows)
        return {"ingested": len(rows)}

    # --------------------------------------------------------- analytics
    @r.get("/v1/analytics/meta")
    def meta(_: str = Depends(session)):
        prices = load_prices()
        models = [x["model"] for x in q("select distinct model from spans where model!='' order by model")]
        tot = q("select count(*) n, min(start_ms) first, max(start_ms) last from spans")[0]
        return {"agents": [x["agent"] or "(unnamed)" for x in q("select distinct agent from spans order by agent")],
                "models": models,
                "projects": [x["project"] or "default" for x in q("select distinct project from spans order by project")],
                "unpriced_models": [m for m in models if price_for(prices, m) is None],
                "spans": tot["n"], "first_ms": tot["first"], "last_ms": tot["last"]}

    @r.get("/v1/analytics/overview")
    def overview(rng: str = Query("7d", alias="range"), tz: int = 0, agent: Optional[str] = None,
                 model: Optional[str] = None, project: Optional[str] = None, _: str = Depends(session)):
        c = compute(rng, tz, agent, model, project)
        cs, ps = c["cs"], c["ps"]
        kpis = {k: {"value": cs[k], "prev": ps[k], "delta": delta(cs[k], ps[k])}
                for k in ("traces", "errors", "error_rate", "cost", "tokens", "tokens_in", "tokens_out",
                          "p50", "p95", "p99", "avg_cost")}
        mm = defaultdict(lambda: {"calls": 0, "cost": 0.0, "errors": 0})
        for s in c["sp"]:
            if s["model"]:
                x = mm[s["model"]]
                x["calls"] += 1
                x["cost"] += s["cost"] or 0
                x["errors"] += 1 if s["status"] == "error" else 0
        tot = sum(x["calls"] for x in mm.values()) or 1
        models = sorted(({"model": k, "calls": v["calls"], "share": v["calls"] / tot, "cost": v["cost"],
                          "error_rate": v["errors"] / v["calls"]} for k, v in mm.items()),
                        key=lambda x: -x["calls"])[:8]
        return {"range": rng, "bucket_ms": c["b"], "start": c["start"], "end": c["end"], "tz": tz,
                "kpis": kpis, "series": c["series"], "models": models,
                "insights": insights_for(c), "explain": EXPLAIN}

    @r.get("/v1/analytics/insights")
    def insights(rng: str = Query("7d", alias="range"), tz: int = 0, agent: Optional[str] = None,
                 model: Optional[str] = None, project: Optional[str] = None, _: str = Depends(session)):
        return insights_for(compute(rng, tz, agent, model, project))

    @r.get("/v1/analytics/breakdown")
    def breakdown(by: str = "model", rng: str = Query("7d", alias="range"), tz: int = 0, sort: str = "",
                  agent: Optional[str] = None, model: Optional[str] = None, project: Optional[str] = None,
                  _: str = Depends(session)):
        keyf = {"model": lambda s: s["model"], "agent": lambda s: s["agent"] or "(unnamed)",
                "project": lambda s: s["project"] or "default", "step": lambda s: s["name"],
                "error": lambda s: (s["error"] or "(no message)")[:80]}.get(by)
        if keyf is None:
            raise HTTPException(400, "by must be model, agent, project, step or error")
        _b, _n, start, end = window(rng, tz)
        w, a = flt(agent, model, project)
        src = [s for s in spans_in(start, end, w, a)
               if (by != "model" or s["model"]) and (by != "error" or (s["status"] == "error" and not rootish(s)))
               and (by != "step" or not rootish(s))]
        groups = defaultdict(list)
        for s in src:
            groups[keyf(s)].append(s)
        total_cost = sum(s["cost"] or 0 for s in src)
        rows = []
        for k, g in groups.items():
            durs = [s["duration_ms"] for s in g]
            errs = sum(1 for s in g if s["status"] == "error")
            last = max(g, key=lambda s: s["start_ms"])
            cost = sum(s["cost"] or 0 for s in g)
            rows.append({"label": k, "calls": len(g), "traces": len({s["trace_id"] for s in g}), "cost": cost,
                         "cost_share": cost / total_cost if total_cost else 0,
                         "tokens_in": sum(s["tokens_in"] or 0 for s in g), "tokens_out": sum(s["tokens_out"] or 0 for s in g),
                         "avg_ms": sum(durs) / len(durs), "p95_ms": pct(durs, .95), "errors": errs,
                         "error_rate": errs / len(g), "last_ms": last["start_ms"], "example_trace": last["trace_id"]})
        key_ = sort or ("calls" if by == "error" else "cost")
        keymap = {"cost": "cost", "calls": "calls", "latency": "avg_ms", "errors": "errors", "last": "last_ms"}
        rows.sort(key=lambda x: -x[keymap.get(key_, "cost")])
        return {"by": by, "range": rng, "rows": rows[:200],
                "totals": {"calls": len(src), "cost": total_cost}}

    @r.get("/v1/analytics/traces")
    def traces_list(t_from: Optional[int] = Query(None, alias="from"), t_to: Optional[int] = Query(None, alias="to"),
                    status: str = "", agent: Optional[str] = None, model: Optional[str] = None,
                    project: Optional[str] = None, search: str = "", min_cost: float = 0, sort: str = "time",
                    limit: int = 50, offset: int = 0, _: str = Depends(session)):
        now = int(time.time() * 1000)
        lo = t_from if t_from is not None else now - 7 * DAY
        hi = t_to if t_to is not None else now + DAY
        w, a = flt(agent, model, project)
        where = ["start_ms>=?", "start_ms<?"] + w
        args = [lo, hi] + a
        if search:
            where.append("(trace_id like ? or name like ? or error like ?)")
            args += ["%" + search + "%"] * 3
        having, hargs = [], []
        if status == "error":
            having.append("max(status='error')=1")
        elif status == "ok":
            having.append("max(status='error')=0")
        if min_cost > 0:
            having.append("sum(cost)>=?")
            hargs.append(min_cost)
        base = ("select trace_id, min(start_ms) t0, max(start_ms+duration_ms)-min(start_ms) dur, count(*) n, "
                "coalesce(sum(cost),0) cost, coalesce(sum(tokens_in),0) tin, coalesce(sum(tokens_out),0) tout, "
                "max(status='error') err, max(agent) agent, "
                "group_concat(distinct case when model!='' then model end) models, "
                "max(case when status='error' then error end) first_error "
                "from spans where " + " and ".join(where) + " group by trace_id"
                + (" having " + " and ".join(having) if having else ""))
        order = {"time": "t0 desc", "cost": "cost desc", "duration": "dur desc", "tokens": "(tin+tout) desc"}.get(sort, "t0 desc")
        limit = max(1, min(limit, 200))
        total = q("select count(*) c from (" + base + ")", args + hargs)[0]["c"]
        rows = q("select * from (" + base + ") order by " + order + " limit ? offset ?", args + hargs + [limit, max(0, offset)])
        return {"total": total, "rows": [
            {"trace_id": x["trace_id"], "start_ms": x["t0"], "duration_ms": x["dur"], "spans": x["n"],
             "cost": x["cost"], "tokens": x["tin"] + x["tout"], "error": bool(x["err"]), "agent": x["agent"] or "",
             "models": (x["models"] or "").split(",") if x["models"] else [], "first_error": x["first_error"] or ""}
            for x in rows]}

    @r.get("/v1/analytics/trace/{tid}")
    def trace_detail(tid: str, _: str = Depends(session)):
        rows = q("select * from spans where trace_id=? order by start_ms, duration_ms desc", (tid,))
        if not rows:
            raise HTTPException(404, "trace not found")
        t0 = min(x["start_ms"] for x in rows)
        t1 = max(x["start_ms"] + x["duration_ms"] for x in rows)
        total = max(1, t1 - t0)
        byid = {x["span_id"]: x for x in rows}
        has_parent = any(x.get("parent_id") for x in rows)
        root_id = None
        if not has_parent and len(rows) > 1:
            top = max(rows, key=lambda x: x["duration_ms"])
            if top["start_ms"] <= t0 + 1 and top["start_ms"] + top["duration_ms"] >= t1 - 1:
                root_id = top["span_id"]

        def depth(x):
            if has_parent:
                d, seen, p = 0, set(), x.get("parent_id")
                while p and p in byid and p not in seen:
                    seen.add(p)
                    d += 1
                    p = byid[p].get("parent_id")
                return d
            if root_id:
                return 0 if x["span_id"] == root_id else 1
            return 0

        spans = []
        for x in rows:
            spans.append({"span_id": x["span_id"], "parent_id": x.get("parent_id") or "", "name": x["name"],
                          "kind": x.get("kind") or "", "agent": x.get("agent") or "", "model": x["model"],
                          "depth": depth(x), "offset_ms": x["start_ms"] - t0, "duration_ms": x["duration_ms"],
                          "left_pct": (x["start_ms"] - t0) / total * 100,
                          "width_pct": max(0.5, x["duration_ms"] / total * 100),
                          "cost": x["cost"] or 0, "tokens_in": x["tokens_in"] or 0, "tokens_out": x["tokens_out"] or 0,
                          "status": x["status"], "error": x["error"] or "",
                          "input": (x.get("input") or "")[:4000], "output": (x.get("output") or "")[:4000]})
        work = [s for s in spans if s["span_id"] != root_id and not (s["name"].endswith(".run") and s["depth"] == 0 and len(spans) > 1)]
        notes = []
        failed = [s for s in spans if s["status"] == "error" and s["span_id"] != root_id]
        if failed:
            f = failed[0]
            notes.append("Step '%s' failed%s: %s" % (f["name"], " on " + f["model"] if f["model"] else "", f["error"] or "no message"))
        if work:
            slow = max(work, key=lambda s: s["duration_ms"])
            notes.append("Slowest step: '%s', %.1f s (%.0f%% of the run)." % (slow["name"], slow["duration_ms"] / 1000, slow["duration_ms"] / total * 100))
            exp = max(work, key=lambda s: s["cost"])
            if exp["cost"] > 0:
                notes.append("Most expensive step: '%s', %s." % (exp["name"], money(exp["cost"])))
        return {"trace_id": tid, "start_ms": t0, "duration_ms": total, "spans": spans, "notes": notes,
                "totals": {"cost": sum(s["cost"] for s in work), "tokens_in": sum(s["tokens_in"] for s in work),
                           "tokens_out": sum(s["tokens_out"] for s in work), "errors": len(failed), "spans": len(spans)}}

    # ----------------------------------------------------------- pricing
    @r.get("/v1/pricing")
    def pricing_get(_: str = Depends(session)):
        prices = load_prices()
        models = [x["model"] for x in q("select distinct model from spans where model!=''")]
        return {"prices": q("select model, in_per_1k, out_per_1k, updated from prices order by model"),
                "unpriced_models": [m for m in models if price_for(prices, m) is None]}

    @r.put("/v1/pricing")
    def pricing_put(p: PriceIn, email: str = Depends(session)):
        run("insert or replace into prices values(?,?,?,?)",
            (p.model.strip().lower(), p.in_per_1k, p.out_per_1k, int(time.time())))
        audit(email, "pricing.update", "%s in=%s out=%s" % (p.model, p.in_per_1k, p.out_per_1k))
        return {"ok": True}

    @r.post("/v1/pricing/recompute")
    def pricing_recompute(email: str = Depends(session)):
        prices = load_prices()
        todo = q("select span_id, model, tokens_in, tokens_out from spans where (cost is null or cost=0) "
                 "and (tokens_in>0 or tokens_out>0) and model!=''")
        upd = []
        for x in todo:
            c2 = calc_cost(prices, x["model"], x["tokens_in"] or 0, x["tokens_out"] or 0)
            if c2:
                upd.append((c2, x["span_id"]))
        with closing(db()) as c:
            c.executemany("update spans set cost=? where span_id=?", upd)
            c.commit()
        audit(email, "pricing.recompute", "%d spans" % len(upd))
        return {"updated": len(upd), "skipped_unpriced": len(todo) - len(upd)}

    @r.get("/v1/audit")
    def audit_list(limit: int = 100, _: str = Depends(session)):
        return q("select ts, email, action, detail from audit_log order by id desc limit ?", (max(1, min(limit, 500)),))

    # -------------------------------------------------------------- demo
    @r.post("/v1/demo/rich")
    def demo_rich(email: str = Depends(session)):
        rnd = random.Random()
        now = int(time.time() * 1000)
        prices = load_prices()
        fleet = {"support-bot": ["gpt-4o", "gpt-4o-mini"], "research-agent": ["claude-sonnet", "gpt-4o"],
                 "code-reviewer": ["claude-sonnet", "gpt-4.1-mini"]}
        steps = ["plan", "retrieve", "tool.search", "generate", "review"]
        rows = []
        made = [0]

        def make(agent, t0, bad=False, err="", loop=False, big=False):
            tid, rid, cur = uuid.uuid4().hex[:10], uuid.uuid4().hex[:12], t0
            plan = rnd.sample(steps, rnd.randint(3, 5))
            if "generate" not in plan:
                plan[-1] = "generate"
            if loop:
                plan = ["plan"] + ["tool.search"] * rnd.randint(8, 12) + ["generate"]
            for st in plan:
                tool = st.startswith("tool") or st == "retrieve"
                m = "" if tool else rnd.choice(fleet[agent])
                ti, tout = (0, 0) if tool else (rnd.randint(200, 3000), rnd.randint(50, 900))
                dur = rnd.randint(250, 2600)
                if big and st == "generate":
                    m, ti = "claude-sonnet", 95000
                fail = bad and st == "generate"
                cost = calc_cost(prices, m, ti, tout) or 0.0
                rows.append((uuid.uuid4().hex[:12], tid, st, m, cur, dur, ti, tout, cost,
                             "error" if fail else "ok", err if fail else "", agent, "default", rid,
                             "tool" if tool else "llm", "", "", "demo"))
                cur += dur
            rows.append((rid, tid, "agent.run", "", t0, cur - t0, 0, 0, 0.0, "error" if bad else "ok",
                         err if bad else "", agent, "default", "", "agent", "", "", "demo"))
            made[0] += 1

        for _i in range(150):
            ag = rnd.choice(list(fleet))
            bad = rnd.random() < .03
            make(ag, now - int(rnd.random() ** 1.4 * 30 * DAY), bad, rnd.choice(["timeout", "invalid_tool_args"]))
        for _i in range(16):
            make("support-bot", now - 2 * DAY + rnd.randint(-4 * HOUR, 4 * HOUR), True, "rate_limit_exceeded (429)")
        for _i in range(2):
            make("research-agent", now - rnd.randint(HOUR, 3 * DAY), loop=True)
        make("research-agent", now - 5 * HOUR, big=True)
        insert_rows(rows)
        audit(email, "demo.seed", "%d traces" % made[0])
        return {"seeded_traces": made[0], "spans": len(rows)}

    @r.delete("/v1/demo/rich")
    def demo_rich_clear(email: str = Depends(session)):
        n = run("delete from spans where meta='demo'")
        audit(email, "demo.clear", "%d spans" % n)
        return {"deleted_spans": n}

    app.include_router(r)