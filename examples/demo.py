"""Simulated RAG agent. Run the server first, then: python examples/demo.py"""
import random
import time

import tynode

tynode.init(project="demo", auto_instrument=False)


@tynode.trace(kind="retriever")
def retrieve(q):
    time.sleep(random.uniform(0.05, 0.25))
    return ["Refund policy: 30 days", "Shipping: 2-4 days"]


@tynode.trace(kind="tool")
def check_order(order_id):
    time.sleep(random.uniform(0.1, 0.4))
    if random.random() < 0.15:
        raise TimeoutError("orders-service timed out after 5s")
    return {"order": order_id, "status": "shipped"}


def llm(prompt, model):
    with tynode.span(f"{model}.chat", "llm", input=prompt) as s:
        time.sleep(random.uniform(0.3, 1.1))
        tin, tout = len(prompt) * 3, random.randint(120, 900)
        out = "Your order has shipped and arrives in 2-4 days."
        s.set(model=model, tokens_in=tin, tokens_out=tout, output=out)
        return out


@tynode.trace
def support_agent(question, order_id):
    docs = retrieve(question)
    order = check_order(order_id)
    plan = llm(f"{question}\n{docs}\n{order}", "gpt-4o-mini")
    return llm(f"Polish this answer: {plan}", random.choice(["gpt-4o", "claude-sonnet-4"]))


for i in range(14):
    with tynode.session(f"user-{i % 4}"):
        try:
            support_agent(f"Where is my order #{1000 + i}?", 1000 + i)
        except Exception as e:  # noqa: BLE001
            print("agent failed:", e)

tynode.flush()
print("done -> http://localhost:7420")