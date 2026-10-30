# Tynode

**Trace your AI agents in 2 lines. Local-first. Open source.**

    import tynode
    tynode.init()          # OpenAI and Anthropic calls are traced automatically

    @tynode.trace
    def my_agent(question): ...

## Run the server

    pip install -e apps/api
    uvicorn tynode_api.main:app --port 7420

Open http://localhost:7420 or use `docker compose up`.

## What you get

- Trace tree and waterfall timeline for every agent run
- Token, cost and latency per step and per model
- Error tracking with full input and output
- Your data stays on your machine

## Layout

    apps/api               FastAPI ingest + query API + dashboard
    packages/sdk-python    Python SDK (zero dependencies)
    examples               runnable demos
    docker                 Dockerfile

## Config

| Variable | Default | Description |
|---|---|---|
| TYNODE_DATABASE_URL | sqlite:///./tynode.db | any SQLAlchemy URL (Postgres ready) |
| TYNODE_API_KEY | unset | when set, ingest and API require it |
| TYNODE_ENDPOINT | http://localhost:7420 | SDK target |

MIT licensed.