# nimbus-cli

`nimbus-cli` is the Python-only command-line adapter for Nimbus.

It supports two profile modes:

- `local`: runs `NimbusRuntime` in-process and stores sessions/events under
  `~/.nimbus/sessions/cli` by default.
- `remote`: sends canonical `/ai/chat/turn` requests to a self-hosted Nimbus
  server using either bearer-token or HMAC request signing.

The CLI stores non-secret profile metadata in `~/.nimbus/config.json`. Secrets
go to the OS keyring when available, with a `0600` `~/.nimbus/secrets.json`
fallback for headless development environments.

## Onboard

```shell
uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY"
uv run nimbus setup remote --profile prod --base-url https://nimbus.example.com --auth hmac
uv run nimbus auth status
```

The local profile defaults to `openai/gpt-oss-120b:free`.

## Chat

```shell
# Starts a new session by default.
uv run nimbus chat "list files under reports/" --profile local

# Resume the last session explicitly.
uv run nimbus resume "continue where we left off" --profile local
```

Local mode streams provider tokens through the runtime event log. Remote mode
uses the existing server turn endpoint and renders the final response.

See `docs/source/nimbus/cli.md` for the full user guide and
`docs/source/nimbus/verification.md` for local and deployed smoke tests.
