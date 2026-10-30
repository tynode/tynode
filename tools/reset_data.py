import os, sqlite3, sys
here = os.path.dirname(os.path.abspath(__file__))
roots = [os.path.dirname(here)]
for k in ("APPDATA", "LOCALAPPDATA"):
    p = os.environ.get(k)
    if p and os.path.isdir(p):
        roots += [os.path.join(p, n) for n in os.listdir(p) if "ynode" in n.lower()]
dbs = []
for r in roots:
    for dp, dn, fn in os.walk(r):
        dn[:] = [x for x in dn if x not in (".venv", "node_modules", ".git", "__pycache__", "site-packages")]
        for f in fn:
            if f.lower().endswith((".db", ".sqlite", ".sqlite3")):
                p = os.path.join(dp, f)
                try:
                    c = sqlite3.connect(p)
                    ok = c.execute("select 1 from sqlite_master where name='spans'").fetchone()
                    c.close()
                    if ok: dbs.append(p)
                except Exception:
                    pass
if not dbs:
    print("Veritabani bulunamadi."); sys.exit(0)
for p in dbs:
    c = sqlite3.connect(p, timeout=30)
    c.execute("pragma busy_timeout=30000")
    n = c.execute("select count(*) from spans").fetchone()[0]
    c.execute("delete from spans")
    tb = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    if "tn_agents" in tb: c.execute("delete from tn_agents")
    if "tn_meta" in tb: c.execute("delete from tn_meta where k in ('setup_done','workspace')")
    c.commit()
    try: c.execute("vacuum")
    except Exception: pass
    c.close()
    print("Temizlendi: %s (%d span silindi)" % (p, n))