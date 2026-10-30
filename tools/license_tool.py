import sys, os, json, time, uuid, base64, argparse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as S

KD = r"C:\AI\tynode-keys"
KF = os.path.join(KD, "private.hex")

def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def load(): return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(open(KF).read().strip()))
def pub(k): return k.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw).hex()

cmd = sys.argv[1]
if cmd == "init":
    os.makedirs(KD, exist_ok=True)
    if not os.path.exists(KF):
        k = Ed25519PrivateKey.generate()
        open(KF, "w").write(k.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption()).hex())
    print(pub(load()))
elif cmd == "issue":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd")
    ap.add_argument("--email", required=True); ap.add_argument("--plan", default="pro"); ap.add_argument("--days", type=int, default=365)
    o = ap.parse_args()
    now = int(time.time())
    payload = json.dumps({"plan": o.plan, "email": o.email, "id": uuid.uuid4().hex[:8], "iat": now,
                          "exp": 0 if o.days == 0 else now + o.days * 86400}, separators=(",", ":")).encode()
    print("TN1." + b64(payload) + "." + b64(load().sign(payload)))