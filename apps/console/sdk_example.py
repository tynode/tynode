import os
"""Minimal ingest ornegi (yalnizca stdlib):  python sdk_example.py"""
import json, time, urllib.request, uuid

def send(spans, url="http://127.0.0.1:8799", key=os.getenv("TYNODE_API_KEY", "")):
    req = urllib.request.Request(url + "/v1/spans", json.dumps(spans).encode(),
        {"Content-Type": "application/json", "x-api-key": key})
    return urllib.request.urlopen(req).read().decode()

now = int(time.time() * 1000); tid = uuid.uuid4().hex[:10]
print(send([{"trace_id": tid, "name": "hello.llm", "model": "gpt-4o", "start_ms": now,
             "duration_ms": 640, "tokens_in": 120, "tokens_out": 48, "cost": 0.0008}]))