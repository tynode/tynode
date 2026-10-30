import random, time
from tynode_sdk import span

@span("agent.run")
def run(task):
    for step in ["plan", "generate"]:
        with span(step, model="gpt-4o") as s:
            time.sleep(random.uniform(.2, .8))
            if random.random() < .25:
                raise RuntimeError("rate_limit_exceeded")
            s.usage(random.randint(300, 2000), random.randint(50, 500))
    return "done"

for i in range(8):
    try:
        run("demo")
        print(i, "ok")
    except Exception as e:
        print(i, "failed:", e)
