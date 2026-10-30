"""Tynode: setup wizard, agent registry, live map and a tiny OpenAI-compatible proxy."""
import json, re, threading, time, urllib.error, urllib.request, uuid
from collections import deque
from contextlib import closing
from typing import List

from fastapi import Body, Header, HTTPException, Response
from pydantic import BaseModel

# estimated USD per 1M tokens (input, output)
PRICES = [("gpt-4o-mini", .15, .6), ("gpt-4o", 2.5, 10), ("gpt-4.1-mini", .4, 1.6), ("gpt-4.1", 2, 8),
          ("gpt-oss-120b", .15, .6), ("gpt-oss-20b", .075, .3), ("llama-3.3", .59, .79)]


def cost_of(model, tin, tout):
    m = (model or "").lower()
    for k, pi, po in sorted(PRICES, key=lambda x: -len(x[0])):
        if k in m:
            return (tin * pi + tout * po) / 1e6
    return 0.0


class Ev(BaseModel):
    type: str
    trace_id: str
    span_id: str = ""
    parent_id: str = ""
    name: str = ""
    kind: str = ""
    agent: str = ""
    model: str = ""
    ts: int = 0
    dur: int = 0
    status: str = "ok"
    error: str = ""


class AgentIn(BaseModel):
    name: str
    description: str = ""
    framework: str = ""
    model: str = ""


class BulkIn(BaseModel):
    text: str


class StartIn(BaseModel):
    workspace: str = "My workspace"
    reset: bool = True


class DoneIn(BaseModel):
    workspace: str = ""


def mount(app, db, KEY):
    lock = threading.Lock()
    ACTIVE, TR, TRAGENT, KNOWN = {}, {}, {}, set()
    RECENT = deque(maxlen=60)

    def span_cols(c):
        return {r[1] for r in c.execute("pragma table_info(spans)")}

    with closing(db()) as c:
        c.execute("create table if not exists tn_meta(k text primary key, v text)")
        c.execute("create table if not exists tn_agents(name text primary key, description text, framework text, model text, created integer, source text)")
        if "agent" not in span_cols(c):
            try:
                c.execute("alter table spans add column agent text default ''")
            except Exception:
                pass
        c.commit()

    def meta(c, k, default=""):
        r = c.execute("select v from tn_meta where k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(c, k, v):
        c.execute("insert or replace into tn_meta values(?,?)", (k, str(v)))

    def need(authorization):
        t = (authorization or "").replace("Bearer ", "")
        with closing(db()) as c:
            ok = c.execute("select 1 from sessions where token=?", (t,)).fetchone()
        if not ok:
            raise HTTPException(401, "Not signed in")

    def add_agent(c, name, desc="", fw="", model="", source="manual"):
        name = (name or "").strip()[:64]
        if not name:
            return False
        try:
            import tn_plans
            if not tn_plans.can_add_agent(c, name):
                return False
        except ImportError:
            pass
        c.execute("insert or ignore into tn_agents values(?,?,?,?,?,?)", (name, desc, fw, model, int(time.time()), source))
        c.execute("update tn_agents set description=coalesce(nullif(?,''),description), framework=coalesce(nullif(?,''),framework), model=coalesce(nullif(?,''),model) where name=?",
                  (desc, fw, model, name))
        return True

    def auto_register(name):
        if not name or name == "default" or name in KNOWN:
            return
        KNOWN.add(name)
        try:
            with closing(db()) as c:
                add_agent(c, name, source="auto")
                c.commit()
        except Exception:
            pass

    def persist_agent(tid, ag):
        try:
            with closing(db()) as c:
                if "agent" in span_cols(c):
                    c.execute("update spans set agent=? where trace_id=? and coalesce(agent,'')=''", (ag, tid))
                    c.commit()
        except Exception:
            pass

    # ---------- live events ----------
    def apply(e):
        now = int(time.time() * 1000)
        ag = e.get("agent") or "default"
        tid = e["trace_id"]
        with lock:
            t = TR.get(tid)
            if t is None:
                t = TR[tid] = {"agent": ag, "path": [], "ts": now, "done": False}
            t["ts"] = now
            if len(TRAGENT) > 20000:
                TRAGENT.clear()
            TRAGENT[tid] = ag
            if e["type"] == "start":
                if len(t["path"]) < 200:
                    t["path"].append(e.get("name", ""))
                ACTIVE[e.get("span_id") or uuid.uuid4().hex[:12]] = {"trace_id": tid, "name": e.get("name", ""), "t0": now, "agent": ag}
            else:
                ACTIVE.pop(e.get("span_id", ""), None)
                if not e.get("parent_id"):
                    t["done"] = True
                RECENT.appendleft({"ts": now, "name": e.get("name", ""), "agent": ag, "dur": e.get("dur", 0),
                                   "status": e.get("status", "ok"), "error": e.get("error", "")})
        if e["type"] == "start":
            auto_register(ag)
        elif not e.get("parent_id") and ag != "default":
            threading.Timer(3.0, persist_agent, (tid, ag)).start()

    def cleanup(now):
        for k in [k for k, v in ACTIVE.items() if now - v["t0"] > 600000]:
            ACTIVE.pop(k, None)
        live_tr = {v["trace_id"] for v in ACTIVE.values()}
        for k in [k for k, v in TR.items() if k not in live_tr and now - v["ts"] > (4000 if v.get("done") else 120000)]:
            TR.pop(k, None)

    @app.post("/v1/tn/live/event")
    def live_event(evs: List[Ev], x_api_key: str = Header(default="")):
        if x_api_key != KEY:
            raise HTTPException(401, "invalid api key")
        for e in evs[:200]:
            apply(e.dict())
        return {"ok": True}

    # ---------- data helpers ----------
    def load_traces(c, limit):
        cols = span_cols(c)
        ag = "agent" if "agent" in cols else "''"
        rows = c.execute(
            "select trace_id,name,model,start_ms,duration_ms,status,cost,tokens_in,tokens_out,%s as ag from spans "
            "where trace_id in (select trace_id from spans group by trace_id order by min(start_ms) desc limit ?) "
            "order by trace_id,start_ms,duration_ms desc" % ag, (limit,)).fetchall()
        out = {}
        for r in rows:
            t = out.setdefault(r["trace_id"], {"spans": [], "agent": ""})
            t["spans"].append(r)
            if r["ag"] and not t["agent"]:
                t["agent"] = r["ag"]
        for tid, t in out.items():
            t["agent"] = t["agent"] or TRAGENT.get(tid) or "default"
        return out

    def agents_list(c):
        traces = load_traces(c, 2000)
        st = {}
        for t in traces.values():
            a = st.setdefault(t["agent"], {"runs": 0, "errors": 0, "cost": 0.0, "tokens": 0, "last": 0, "models": {}})
            sp = t["spans"]
            a["runs"] += 1
            a["errors"] += 1 if any(s["status"] == "error" for s in sp) else 0
            a["cost"] += sum((s["cost"] or 0) for s in sp)
            a["tokens"] += sum((s["tokens_in"] or 0) + (s["tokens_out"] or 0) for s in sp)
            a["last"] = max(a["last"], sp[0]["start_ms"] or 0)
            for s in sp:
                if s["model"]:
                    a["models"][s["model"]] = a["models"].get(s["model"], 0) + 1
        reg = {r["name"]: dict(r) for r in c.execute("select * from tn_agents")}
        with lock:
            live_ag = {v["agent"] for v in ACTIVE.values()}
        now = int(time.time() * 1000)
        out = []
        for name in sorted(set(st) | set(reg)):
            a = st.get(name) or {"runs": 0, "errors": 0, "cost": 0.0, "tokens": 0, "last": 0, "models": {}}
            r = reg.get(name) or {}
            top = max(a["models"], key=a["models"].get) if a["models"] else ""
            status = "live" if name in live_ag else ("recent" if a["last"] > now - 300000 else ("idle" if a["runs"] else "new"))
            out.append({"name": name, "framework": r.get("framework") or "", "model": r.get("model") or top,
                        "description": r.get("description") or "", "runs": a["runs"], "errors": a["errors"],
                        "cost": a["cost"], "tokens": a["tokens"], "last": a["last"], "status": status,
                        "registered": name in reg})
        return out

    # ---------- setup ----------
    @app.get("/v1/tn/setup")
    def setup_get(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            return {"done": meta(c, "setup_done") == "1", "workspace": meta(c, "workspace", "My workspace")}

    @app.post("/v1/tn/setup/start")
    def setup_start(b: StartIn, authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            set_meta(c, "workspace", (b.workspace or "My workspace").strip()[:80])
            if b.reset:
                c.execute("delete from spans")
                c.execute("delete from tn_agents")
            c.commit()
        if b.reset:
            with lock:
                ACTIVE.clear(); TR.clear(); RECENT.clear(); TRAGENT.clear()
            KNOWN.clear()
        return {"ok": True}

    @app.get("/v1/tn/setup/status")
    def setup_status(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            n = c.execute("select count(distinct trace_id) from spans").fetchone()[0]
            ags = [a for a in agents_list(c) if a["runs"] > 0]
        return {"traces": n, "agents": len(ags)}

    @app.post("/v1/tn/setup/complete")
    def setup_complete(b: DoneIn, authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            if b.workspace:
                set_meta(c, "workspace", b.workspace.strip()[:80])
            set_meta(c, "setup_done", "1")
            c.commit()
        return {"ok": True}

    @app.post("/v1/tn/data/reset")
    def data_reset(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            c.execute("delete from spans")
            c.commit()
        with lock:
            ACTIVE.clear(); TR.clear(); RECENT.clear(); TRAGENT.clear()
        return {"ok": True}

    # ---------- agents ----------
    @app.get("/v1/tn/agents")
    def agents_get(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            return agents_list(c)

    @app.post("/v1/tn/agents")
    def agents_add(b: AgentIn, authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            ok = add_agent(c, b.name, b.description, b.framework, b.model)
            c.commit()
        if not ok:
            raise HTTPException(400, "Enter an agent name.")
        return {"added": 1}

    @app.post("/v1/tn/agents/bulk")
    def agents_bulk(b: BulkIn, authorization: str = Header(default="")):
        need(authorization)
        n = 0
        with closing(db()) as c:
            for line in b.text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    p = [x.strip() for x in line.split("|")] + ["", ""]
                    n += 1 if add_agent(c, p[0], "", p[1], p[2]) else 0
                else:
                    for nm in line.split(","):
                        n += 1 if add_agent(c, nm) else 0
                if n >= 200:
                    break
            c.commit()
        return {"added": n}

    @app.delete("/v1/tn/agents/{name}")
    def agents_del(name: str, authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            c.execute("delete from tn_agents where name=?", (name,))
            c.commit()
        KNOWN.discard(name)
        return {"ok": True}

    # ---------- live map ----------
    @app.get("/v1/tn/live/state")
    def live_state(agent: str = "", authorization: str = Header(default="")):
        need(authorization)
        now = int(time.time() * 1000)
        with closing(db()) as c:
            traces = load_traces(c, 300)
            reg = [r["name"] for r in c.execute("select name from tn_agents")]
        with lock:
            cleanup(now)
            trc = {k: dict(v, path=list(v["path"])) for k, v in TR.items()}
            acc = [dict(v) for v in ACTIVE.values()]
            rec = list(RECENT)[:30]
        names = sorted(set(reg) | {t["agent"] for t in traces.values()} | {t["agent"] for t in trc.values()})
        sel = agent if agent in names else ""
        if not sel:
            live_a = [t["agent"] for t in trc.values() if not t.get("done")]
            with_data = [n for n in names if any(t["agent"] == n for t in traces.values())]
            sel = live_a[0] if live_a else (with_data[0] if with_data else (names[0] if names else ""))
        nodes, edges, flat = {}, {}, []
        for t in traces.values():
            if t["agent"] != sel:
                continue
            seq = t["spans"]
            for s in seq:
                n = nodes.setdefault(s["name"], {"id": s["name"], "runs": 0, "err": 0, "dur": 0, "model": ""})
                n["runs"] += 1
                n["dur"] += s["duration_ms"] or 0
                if s["status"] == "error":
                    n["err"] += 1
                if s["model"]:
                    n["model"] = s["model"]
                flat.append(s)
            nm = [s["name"] for s in seq]
            for a, b in zip(nm, nm[1:]):
                edges[(a, b)] = edges.get((a, b), 0) + 1
        act = []
        for tid, t in trc.items():
            if t["agent"] != sel:
                continue
            cur = [s for s in acc if s["trace_id"] == tid]
            top = max(cur, key=lambda s: s["t0"]) if cur else None
            name = top["name"] if top else (t["path"][-1] if t["path"] else "")
            since = top["t0"] if top else t["ts"]
            n = nodes.get(name)
            avg = (n["dur"] / n["runs"]) if n and n["runs"] else 0
            act.append({"trace_id": tid, "name": name, "since": since, "path": t["path"][-30:],
                        "done": (not cur) and bool(t.get("done")),
                        "slow": bool(top) and (now - since) > max(15000, 3 * avg)})
            for nm in t["path"]:
                nodes.setdefault(nm, {"id": nm, "runs": 0, "err": 0, "dur": 0, "model": ""})
            for a, b in zip(t["path"], t["path"][1:]):
                edges.setdefault((a, b), 0)
        recent = [r for r in rec if r["agent"] == sel][:12]
        if not recent:
            flat.sort(key=lambda s: -(s["start_ms"] or 0))
            recent = [{"ts": s["start_ms"], "name": s["name"], "dur": s["duration_ms"], "status": s["status"]} for s in flat[:10]]
        return {"now": now, "agent": sel, "agents": names,
                "nodes": [{"id": n["id"], "runs": n["runs"], "err": n["err"],
                           "avg": (n["dur"] / n["runs"]) if n["runs"] else 0, "model": n["model"]}
                          for n in sorted(nodes.values(), key=lambda x: x["id"])],
                "edges": [{"a": a, "b": b, "n": n} for (a, b), n in sorted(edges.items())],
                "active": act, "recent": recent}

    # ---------- OpenAI-compatible proxy ----------
    def put(c, row):
        cols = span_cols(c)
        keys = [k for k in row if k in cols]
        c.execute("insert or replace into spans(%s) values(%s)" % (",".join(keys), ",".join("?" * len(keys))), [row[k] for k in keys])

    def usage_from(body, stream):
        try:
            if not stream:
                u = json.loads(body).get("usage") or {}
                return int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
            tin = tout = 0
            for line in body.decode("utf-8", "ignore").splitlines():
                if line.startswith("data:") and "usage" in line and "[DONE]" not in line:
                    d = json.loads(line[5:].strip())
                    u = d.get("usage") or (d.get("x_groq") or {}).get("usage") or {}
                    if u:
                        tin, tout = int(u.get("prompt_tokens", tin)), int(u.get("completion_tokens", tout))
            return tin, tout
        except Exception:
            return 0, 0

    @app.post("/v1/proxy/chat/completions")
    def proxy(payload: dict = Body(...), authorization: str = Header(default=""),
              x_tynode_agent: str = Header(default=""), x_tynode_upstream: str = Header(default="")):
        up = (x_tynode_upstream or "https://api.openai.com/v1").rstrip("/")
        if not (up.startswith("https://") or up.startswith("http://127.0.0.1") or up.startswith("http://localhost")):
            raise HTTPException(400, "X-Tynode-Upstream must be an https URL.")
        ag = (x_tynode_agent or "default").strip()[:64] or "default"
        model = str(payload.get("model", ""))
        stream = bool(payload.get("stream"))
        tid, rid, cid = uuid.uuid4().hex[:10], uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
        t0 = int(time.time() * 1000)
        apply({"type": "start", "trace_id": tid, "span_id": rid, "parent_id": "", "name": "agent.run", "agent": ag})
        apply({"type": "start", "trace_id": tid, "span_id": cid, "parent_id": rid, "name": "llm.chat", "agent": ag})
        req = urllib.request.Request(up + "/chat/completions", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json", "Authorization": authorization,
                                      "User-Agent": "tynode-proxy/1.0", "Accept": "*/*"})
        status, body, ctype, err = 200, b"", "application/json", ""
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body, status = r.read(), r.status
                ctype = (r.headers.get("Content-Type") or ctype).split(";")[0]
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
            err = "HTTP %s from LLM API: %s" % (status, body[:300].decode("utf-8", "ignore"))
        except Exception as e:
            status, err = 502, "Could not reach the LLM API: %s" % e
            body = json.dumps({"error": {"message": err}}).encode()
        dur = int(time.time() * 1000) - t0
        tin, tout = (0, 0) if err else usage_from(body, stream)
        st = "error" if err else "ok"
        try:
            with closing(db()) as c:
                base = {"trace_id": tid, "start_ms": t0, "duration_ms": dur, "status": st, "error": err, "agent": ag,
                        "project": "default", "kind": "agent"}
                put(c, dict(base, span_id=rid, name="agent.run", model="", tokens_in=0, tokens_out=0, cost=0.0, parent_id=""))
                put(c, dict(base, span_id=cid, name="llm.chat", model=model, tokens_in=tin, tokens_out=tout,
                            cost=cost_of(model, tin, tout), parent_id=rid, kind="llm"))
                c.commit()
        except Exception:
            pass
        apply({"type": "end", "trace_id": tid, "span_id": cid, "parent_id": rid, "name": "llm.chat", "agent": ag, "dur": dur, "status": st, "error": err})
        apply({"type": "end", "trace_id": tid, "span_id": rid, "parent_id": "", "name": "agent.run", "agent": ag, "dur": dur, "status": st, "error": err})
        return Response(content=body, status_code=status, media_type=ctype)