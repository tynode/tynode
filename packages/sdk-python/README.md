# tynode-sdk

Zero-dependency Python SDK for [Tynode](../../README.md).

    pip install tynode-sdk        # or: pip install ./packages/sdk-python

    from tynode_sdk import span

    @span("agent.run", agent="support-bot")      # whole function = one trace
    def run(task): ...

    with span("generate", model="gpt-4o") as s:   # one LLM call
        r = client.chat.completions.create(...)
        s.usage(r.usage.prompt_tokens, r.usage.completion_tokens)

Environment: `TYNODE_URL` (default `http://127.0.0.1:8799`), `TYNODE_API_KEY`,
`TYNODE_AGENT`, `TYNODE_PROJECT`. On the machine that runs the Tynode app the
API key is found automatically.