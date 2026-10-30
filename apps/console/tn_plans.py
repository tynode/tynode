"""Tynode plans: Free / Pro (offline-verified license), limits, retention."""
import base64, json, os, threading, time
from contextlib import closing
from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PUBKEY_HEX = "b6398810822fb518d7f7679418496b31cb4e4b37a9bbddf6919fe5b52fdde94e"
CHECKOUT_URL = os.environ.get("TYNODE_CHECKOUT_URL", "https://tynode.com/pricing")
PLANS = {
    "free": {"name": "Free", "agents": 3, "retention": 7, "features": ["livemap"]},
    "pro": {"name": "Pro", "agents": 1000, "retention": 90, "features": ["livemap", "export", "alerts"]},
}


class KeyIn(BaseModel):
    key: str


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(key):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        p = (key or "").strip().split(".")
        if len(p) != 3 or p[0] != "TN1":
            return None
        payload, sig = _b64d(p[1]), _b64d(p[2])
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBKEY_HEX)).verify(sig, payload)
        d = json.loads(payload)
        if d.get("plan") not in PLANS:
            return None
        if d.get("exp") and d["exp"] < time.time():
            return None
        return d
    except Exception:
        return None


def current(c):
    r = c.execute("select v from tn_meta where k='license'").fetchone()
    d = verify(r[0]) if r else None
    return (d["plan"] if d else "free"), d


def can_add_agent(c, name):
    if c.execute("select 1 from tn_agents where name=?", (name,)).fetchone():
        return True
    plan, _ = current(c)
    n = c.execute("select count(*) from tn_agents").fetchone()[0]
    return n < PLANS[plan]["agents"]


def purge(db):
    with closing(db()) as c:
        plan, _ = current(c)
        cut = int((time.time() - PLANS[plan]["retention"] * 86400) * 1000)
        c.execute("delete from spans where start_ms < ?", (cut,))
        c.commit()


def _loop(db):
    time.sleep(15)
    while True:
        try:
            purge(db)
        except Exception:
            pass
        time.sleep(3600)


def mount(app, db, KEY):
    with closing(db()) as c:
        c.execute("create table if not exists tn_meta(k text primary key, v text)")
        c.execute("create table if not exists tn_agents(name text primary key, description text, framework text, model text, created integer, source text)")
        c.commit()

    def need(authorization):
        t = (authorization or "").replace("Bearer ", "")
        with closing(db()) as c:
            ok = c.execute("select 1 from sessions where token=?", (t,)).fetchone()
        if not ok:
            raise HTTPException(401, "Not signed in")

    def snapshot(c):
        plan, lic = current(c)
        d = PLANS[plan]
        n = c.execute("select count(*) from tn_agents").fetchone()[0]
        return {"plan": plan, "name": d["name"], "features": d["features"],
                "limits": {"agents": d["agents"], "retention": d["retention"]}, "usage": {"agents": n},
                "plans": PLANS, "checkout_url": CHECKOUT_URL, "api_key": KEY,
                "license": ({"email": lic.get("email", ""), "exp": lic.get("exp", 0), "id": lic.get("id", "")} if lic else None)}

    @app.middleware("http")
    async def _no_demo(request, call_next):
        if request.url.path.rstrip("/") == "/v1/demo":
            return JSONResponse({"detail": "Demo data has been removed."}, status_code=404)
        return await call_next(request)

    @app.get("/v1/tn/plan")
    def plan_get(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            return snapshot(c)

    @app.post("/v1/tn/plan/activate")
    def plan_activate(b: KeyIn, authorization: str = Header(default="")):
        need(authorization)
        try:
            import cryptography  # noqa
        except ImportError:
            raise HTTPException(500, "The 'cryptography' package is missing. Run: pip install cryptography")
        if not verify(b.key):
            raise HTTPException(400, "This license key is not valid or has expired.")
        with closing(db()) as c:
            c.execute("insert or replace into tn_meta values('license',?)", (b.key.strip(),))
            c.commit()
            return snapshot(c)

    @app.post("/v1/tn/plan/deactivate")
    def plan_deactivate(authorization: str = Header(default="")):
        need(authorization)
        with closing(db()) as c:
            c.execute("delete from tn_meta where k='license'")
            c.commit()
            return snapshot(c)

    threading.Thread(target=_loop, args=(db,), daemon=True).start()