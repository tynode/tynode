import os, sys, json, time, uuid, urllib.request, urllib.error
URL = os.environ.get("TYNODE_URL", "http://127.0.0.1:8787").rstrip("/")
os.environ["TYNODE_URL"] = URL
def _key():
    k = os.environ.get("TYNODE_API_KEY")
    if k: return k
    for d in (os.environ.get("TYNODE_DATA_DIR"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tynode")):
        try:
            if d: return open(os.path.join(d, "api.key"), encoding="utf-8").read().strip()
        except Exception: pass
    return ""
KEY = _key()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "console"))
res = []

def rec(name, ok, info=""):
    res.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + ((" -> " + str(info)[:200]) if (not ok and info != "") else ""))

def call(method, path, body=None, token="", key=""):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    if key: h["x-api-key"] = key
    req = urllib.request.Request(URL + path, json.dumps(body).encode() if body is not None else None, h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r: raw, code = r.read(), r.status
    except urllib.error.HTTPError as e: raw, code = e.read(), e.code
    except Exception as e: return 0, {"error": str(e)}
    try: return code, json.loads(raw or b"{}")
    except Exception: return code, {}

code, _ = call("GET", "/v1/auth/config")
rec("engine reachable", code == 200, code)
if code == 0:
    print("Motora ulasilamadi. Tynode acik mi?"); sys.exit(1)

em, pw = "smoke+%s@example.com" % uuid.uuid4().hex[:8], "Smoke-" + uuid.uuid4().hex[:10]
code, j = call("POST", "/v1/auth/register", {"email": em, "password": pw})
rec("register", code == 200 and j.get("token"), (code, j))
code, j = call("POST", "/v1/auth/login", {"email": em, "password": pw}); tok = j.get("token", "")
rec("login", code == 200 and tok, (code, j))
code, j = call("GET", "/v1/auth/me", token=tok); rec("auth/me", code == 200 and j.get("email") == em, (code, j))
code, _ = call("GET", "/v1/traces?limit=5"); rec("traces require sign-in", code in (401, 403), code)
code, _ = call("GET", "/v1/tn/plan"); rec("plan requires sign-in", code in (401, 403), code)

code, pl = call("GET", "/v1/tn/plan", token=tok)
rec("plan endpoint", code == 200 and pl.get("plan") in ("free", "pro"), (code, pl))
code, j = call("POST", "/v1/tn/plan/activate", {"key": "garbage"}, token=tok); rec("bad license rejected", code == 400, (code, j))
code, j = call("POST", "/v1/demo", {}, key=KEY); rec("demo endpoint removed", code == 404, code)

code, ag0 = call("GET", "/v1/tn/agents", token=tok); n0 = len(ag0) if isinstance(ag0, list) else 0
cap = pl.get("limits", {}).get("agents", 3)
code, j = call("POST", "/v1/tn/agents/bulk", {"text": "smoke-a\nsmoke-b\nsmoke-c\nsmoke-d\nsmoke-e"}, token=tok)
exp = min(5, max(0, cap - n0))
rec("agent limit enforced (%s plan, cap %s)" % (pl.get("plan"), cap), code == 200 and j.get("added") == exp, (code, j, "beklenen", exp))

from tynode_sdk import span, flush
@span("agent.run", agent="smoke-agent")
def run():
    with span("plan", model="gpt-4o-mini") as s:
        time.sleep(.2); s.usage(120, 40)
    with span("tool.search"):
        time.sleep(.2)
run(); flush()
found = False
for _ in range(12):
    time.sleep(1)
    code, tr = call("GET", "/v1/traces?limit=50", token=tok)
    if code == 200 and any("tool.search" in (t.get("names") or "") for t in tr): found = True; break
rec("SDK trace ingested", found)
time.sleep(4)
code, ags = call("GET", "/v1/tn/agents", token=tok)
rec("agent listed after run", code == 200 and any(a["name"] == "smoke-agent" and a["runs"] >= 1 for a in ags), (code, ags))
code, st = call("GET", "/v1/tn/live/state?agent=smoke-agent", token=tok)
rec("live map state", code == 200 and any(n["id"] == "tool.search" for n in st.get("nodes", [])), (code, st))
code, ss = call("GET", "/v1/stats", token=tok); rec("stats", code == 200 and "models" in ss, (code, ss))
code, j = call("GET", "/v1/tn/setup", token=tok); rec("setup state", code == 200 and "done" in j, (code, j))

tk = os.environ.get("TN_TEST_KEY", "")
if tk:
    code, j = call("POST", "/v1/tn/plan/activate", {"key": tk}, token=tok)
    rec("pro license activates", code == 200 and j.get("plan") == "pro" and "export" in j.get("features", []), (code, j))
    code, j = call("POST", "/v1/tn/plan/deactivate", token=tok); rec("license removed", code == 200 and j.get("plan") == "free", (code, j))
else:
    print("SKIP  pro license test (TN_TEST_KEY ortam degiskeni yok)")

if "--cleanup" in sys.argv:
    for n in ("smoke-a", "smoke-b", "smoke-c", "smoke-d", "smoke-e", "smoke-agent"):
        call("DELETE", "/v1/tn/agents/" + n, token=tok)
    call("POST", "/v1/tn/data/reset", token=tok)
    print("Temizlendi (tum trace'ler silindi).")
print("\n%d / %d test gecti" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)