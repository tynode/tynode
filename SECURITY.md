# Security

Tynode is an early developer preview. If you find a security problem, please do
not open a public issue. Use GitHub private vulnerability reporting instead
(Security tab, "Report a vulnerability").

For operators:

- The engine listens on 127.0.0.1 by default. Do not expose it to a network
  without a reverse proxy and TLS.
- Ingest requires the API key stored in the Tynode data folder (`api.key`).
  Treat it like a password.
- Trace data can contain prompts and tool output. Keep it out of public issues.