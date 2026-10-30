# Tynode

**See what your AI agents are doing. Local-first observability for agent developers.**

Tynode records every step your agent takes (LLM calls, tools, retrieval) and shows
traces, token usage, cost, latency and errors in one dashboard. Trace data stays on
your machine.

> **Status: early developer preview (0.1.0).** Expect rough edges. Please tell us
> what breaks or what is missing by opening an issue.

## Quickstart

### 1. Start Tynode

Desktop app from source (Windows):

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r apps/console/requirements.txt
    cd apps/desktop
    npm install
    npm start

Engine only (any OS), then open http://127.0.0.1:8799:

    pip install -r apps/console/requirements.txt
    cd apps/console
    python -m uvicorn server:app --port 8799

Docker (data lives in the `tynode-data` volume, port bound to localhost only):

    docker compose up --build

### 2. Connect your agent

    pip install ./packages/sdk-python

    from tynode_sdk import span

    @span("agent.run", agent="support-bot")      # whole function = one trace
    def run(task): ...

    with span("generate", model="gpt-4o") as s:   # one LLM call
        r = client.chat.completions.create(...)
        s.usage(r.usage.prompt_tokens, r.usage.completion_tokens)

Nested spans are linked automatically. Cost is computed on the engine from a price
table unless you pass `cost=`.

| Variable | Default | Description |
|---|---|---|
| TYNODE_URL | http://127.0.0.1:8799 | engine address |
| TYNODE_API_KEY | auto-detected | on the machine running the app the SDK reads `api.key` from the Tynode data folder (`%LOCALAPPDATA%\Tynode`, or `TYNODE_DATA_DIR`) |
| TYNODE_AGENT | unset | default agent name |
| TYNODE_PROJECT | unset | default project name |

### 3. Or send spans over HTTP

    curl -X POST http://127.0.0.1:8799/v1/spans \
      -H "Content-Type: application/json" -H "X-API-Key: $TYNODE_API_KEY" \
      -d '[{"trace_id":"t1","name":"agent.run","start_ms":1730000000000,"duration_ms":420,"model":"gpt-4o","tokens_in":120,"tokens_out":30}]'

## Sign-in

Email and password accounts are stored locally. Email-code sign-in is optional: set
`SUPABASE_URL` and `SUPABASE_ANON_KEY` in `apps/console/.env` (see `.env.example`).
Only your email address and the one-time code go through Supabase Auth.

## Known limitations

- One local workspace: every account on an engine sees the same trace data.
- Windows desktop app first; the engine itself runs anywhere Python runs.
- Chat completions proxy endpoint is experimental.

## Repository layout

    apps/console        engine (FastAPI) and dashboard UI
    apps/desktop        Electron app that starts the engine
    packages/sdk-python Python SDK (zero dependencies)
    examples            runnable demos
    docker              Dockerfile for the engine
    legacy              first prototype, not maintained

MIT licensed.