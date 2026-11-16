# ARC Web Bridge

OpenAI-compatible HTTP bridge for the ARC agentic CLI.

The bridge is intentionally thin:

- accepts chat requests at `POST /v1/chat/completions`
- spawns ARC in print stream mode
- converts ARC `stream-json` events into OpenAI-style chat completion responses
- emits bridge SSE events for ARC session, tool, approval, and tool-result activity

## Run

```powershell
cd "PATH_TO_IF_EDUBOT_ARC\bridge"
npm run bridge
```

Default URL:

```text
http://127.0.0.1:9797
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:9797/health
```

## Chat Completion

Non-streaming:

```powershell
$body = @{
  model = "Qwen3.6"
  session_id = "arc-web-demo"
  messages = @(
    @{ role = "user"; content = "List the available robot arms, but do not move anything." }
  )
} | ConvertTo-Json -Depth 8

Invoke-RestMethod http://127.0.0.1:9797/v1/chat/completions `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Streaming:

```json
{
  "stream": true,
  "session_id": "arc-web-demo",
  "resume_session": true,
  "messages": [
    { "role": "user", "content": "Continue from the previous turn." }
  ]
}
```

Streaming responses use standard `data: ...` chat completion chunks plus custom events:

```text
event: bridge.session
data: {"session_id":"arc-web-demo","arc_session_id":"...","resume_session":true}

event: bridge.tool
data: {"id":"...","name":"camera_scan","input":{"camera_id":"Cam1"}}
```

Chat components that only understand OpenAI-style SSE can ignore custom `event:` blocks. ARC-aware components can render those blocks as tool cards.

## Environment

| Variable | Default |
| --- | --- |
| `ARC_BRIDGE_HOST` | `127.0.0.1` |
| `ARC_BRIDGE_PORT` | `9797` |
| `ARC_BRIDGE_BIN` | `../external/Agent-Robotic-Cluster/bin/arc` |
| `ARC_BRIDGE_MODEL` | `OPENAI_MODEL` or `local-llama` |
| `ARC_BRIDGE_PROVIDER` | unset |
| `ARC_BRIDGE_PERMISSION_MODE` | `bypassPermissions` (lab/sim default; set `default` to restore prompts) |
| `ARC_BRIDGE_AUTO_APPROVE` | on when permission mode is `bypassPermissions`; set `0` to disable auto-allow of residual tool prompts |
| `ARC_BRIDGE_DANGEROUSLY_SKIP_PERMISSIONS` | unset; set `1` to force skip-permission CLI flags even if mode is not bypass |
| `ARC_BRIDGE_TOOLS` | `default` |
| `ARC_BRIDGE_ALLOWED_TOOLS` | unset |
| `ARC_BRIDGE_WORKSPACE` | `ARC_ROBOT_WORKSPACE` or repo root |
| `ARC_BRIDGE_TIMEOUT_MS` | `180000` |
| `ARC_BRIDGE_BARE` | unset; set `1` to pass `--bare` |

`session_id` may be any stable UI session id. If it is not a UUID, the bridge maps it to an ARC UUID in memory and returns both ids in `bridge.session`, `session_id`, and `arc_session_id`.
