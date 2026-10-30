"""Supabase email-OTP login for Tynode (stdlib only).

POST /v1/auth/otp/send   {email}        -> Supabase emails a one-time code
POST /v1/auth/otp/verify {email, token} -> local session (same shape as /v1/auth/login)
GET  /v1/auth/config                    -> {"otp": true/false}
Config (apps/console/.env or environment): SUPABASE_URL, SUPABASE_ANON_KEY
Never put the service_role / secret key here.
"""
import json, os, re, time, urllib.error, urllib.request
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

HERE = Path(__file__).parent
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_env():
    p = HERE / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class OtpSend(BaseModel):
    email: str


class OtpVerify(BaseModel):
    email: str
    token: str


def _call(path, body):
    url = os.environ["SUPABASE_URL"].rstrip("/") + path
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "apikey": os.environ["SUPABASE_ANON_KEY"],
        "User-Agent": "tynode-engine/0.3"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode() or "{}")
        except Exception:
            data = {}
        return e.code, data
    except Exception:
        raise HTTPException(502, "Could not reach Supabase. Check your connection and SUPABASE_URL.")


def _msg(data):
    return data.get("msg") or data.get("message") or data.get("error_description") or ""


def mount(app, db, make_session):
    load_env()
    last_sent = {}
    fails = {}

    with closing(db()) as c:
        c.execute("create table if not exists supabase_users(email text primary key, supabase_id text, created integer, last_login integer)")
        c.commit()

    def ready():
        return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))

    @app.get("/v1/auth/config")
    def auth_config():
        return {"otp": ready(), "password": True}

    @app.post("/v1/auth/otp/send")
    def otp_send(b: OtpSend):
        if not ready():
            raise HTTPException(503, "Email login is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY to apps/console/.env.")
        email = b.email.strip().lower()
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise HTTPException(400, "Enter a valid email address.")
        now = time.time()
        if now - last_sent.get(email, 0) < 60:
            raise HTTPException(429, "Please wait 60 seconds before requesting another code.")
        code, data = _call("/auth/v1/otp", {"email": email, "create_user": True})
        if code == 429 or data.get("error_code") in ("over_email_send_rate_limit", "over_request_rate_limit"):
            raise HTTPException(429, "Too many emails requested. Wait a minute and try again.")
        if code >= 400:
            raise HTTPException(400 if code < 500 else 502, _msg(data) or "Could not send the code.")
        if len(last_sent) > 1000:
            last_sent.clear()
        last_sent[email] = now
        return {"sent": True}

    @app.post("/v1/auth/otp/verify")
    def otp_verify(b: OtpVerify):
        if not ready():
            raise HTTPException(503, "Email login is not configured.")
        email = b.email.strip().lower()
        token = re.sub(r"\s+", "", b.token)
        if not EMAIL_RE.match(email) or not re.fullmatch(r"\d{4,10}", token):
            raise HTTPException(400, "Enter your email and the numeric code from the email.")
        n, first = fails.get(email, (0, time.time()))
        if time.time() - first > 600:
            n, first = 0, time.time()
        if n >= 5:
            raise HTTPException(429, "Too many wrong codes. Try again in 10 minutes.")
        code, data = _call("/auth/v1/verify", {"type": "email", "email": email, "token": token})
        if code >= 400 or not data.get("access_token"):
            fails[email] = (n + 1, first)
            print("otp verify failed:", code, json.dumps(data)[:300], flush=True)
            raise HTTPException(401, "Invalid or expired code. (" + str(code) + " " + (_msg(data) or str(data.get("error_code", ""))) + ")")
        fails.pop(email, None)
        user = data.get("user") or {}
        em = (user.get("email") or email).strip().lower()
        now = int(time.time())
        with closing(db()) as c:
            c.execute("insert or ignore into users values(?,?,?,?)", (em, "", "", now))
            c.execute("insert or replace into supabase_users values(?,?,coalesce((select created from supabase_users where email=?),?),?)",
                      (em, user.get("id", ""), em, now, now))
            try:
                c.execute("insert into audit_log(ts,email,action,detail) values(?,?,?,?)",
                          (now * 1000, em, "auth.otp_login", "supabase"))
            except Exception:
                pass
            c.commit()
        s = make_session(em)
        s["provider"] = "supabase"
        return s