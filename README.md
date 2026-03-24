# claude-channel

Python SDK for [Claude Code Channels](https://code.claude.com/docs/en/channels-reference). Build channel servers that push events into Claude Code sessions — in Python instead of TypeScript.

## Requirements

- Python 3.11+
- Claude Code v2.1.80+
- claude.ai login (API key auth is not supported)

## Installation

```bash
pip install -e .
```

## Quick Start

### One-way channel (push events to Claude)

```python
from claude_channel import Channel

channel = Channel(
    "webhook",
    instructions="Events from webhook channel. One-way, no reply expected.",
)

# Push an event to Claude Code
await channel.send("build failed on main", meta={"severity": "high"})

# Start the server (blocking, stdio transport)
channel.run()
```

### Two-way channel (Claude can reply)

```python
from claude_channel import Channel

channel = Channel(
    "chat",
    instructions='Messages arrive as <channel source="chat" chat_id="...">. '
                 'Reply with the reply tool, passing the chat_id from the tag.',
)

@channel.tool("reply", description="Send a message back over this channel")
async def reply(chat_id: str, text: str) -> str:
    # Post to your chat platform here
    print(f"[{chat_id}] {text}")
    return "sent"

channel.run()
```

### Permission relay (approve/deny tool use remotely)

```python
from claude_channel import Channel, PermissionBehavior, PermissionRequest

channel = Channel("secure", permission_relay=True)

@channel.on_permission_request()
async def handle(req: PermissionRequest) -> PermissionBehavior:
    if req.tool_name in {"Read", "Grep", "Glob"}:
        return PermissionBehavior.ALLOW
    return PermissionBehavior.DENY

channel.run()
```

## Running Your Channel

### 1. Register with Claude Code

Add your server to `.mcp.json` in your project directory:

```json
{
  "mcpServers": {
    "webhook": {
      "command": "python3",
      "args": ["./examples/one_way.py"]
    }
  }
}
```

### 2. Start Claude Code

During the research preview, custom channels need the development flag:

```bash
claude --dangerously-load-development-channels server:webhook
```

Claude Code spawns your server as a subprocess and communicates over stdio. You don't need to start the server manually.

### 3. Test it

```bash
# Send a test event (for the webhook example)
curl -X POST localhost:8788 -d "build failed on main: https://ci.example.com/run/1234"
```

The event arrives in Claude's context as:

```
<channel source="webhook" path="/" method="POST">
build failed on main: https://ci.example.com/run/1234
</channel>
```

## API Reference

### `Channel`

```python
Channel(
    name: str,                        # Server name (<channel source="name">)
    *,
    version: str = "0.0.1",          # Server version
    instructions: str | None = None,  # Added to Claude's system prompt
    permission_relay: bool = False,    # Enable permission relay capability
)
```

#### Methods

| Method | Description |
|--------|-------------|
| `await channel.send(content, *, meta=None)` | Push an event to Claude Code |
| `await channel.send_event(event)` | Push a `ChannelEvent` object |
| `await channel.send_permission_verdict(request_id, behavior)` | Send allow/deny verdict |
| `channel.run()` | Run server synchronously (blocking) |
| `await channel.run_async()` | Run server asynchronously |

#### Decorators

| Decorator | Description |
|-----------|-------------|
| `@channel.tool(name, *, description, input_schema=None)` | Register a reply tool |
| `@channel.on_permission_request()` | Register permission request handler |

### `ChannelEvent`

```python
from claude_channel import ChannelEvent

event = ChannelEvent(
    content="alert fired",           # Body of <channel> tag
    meta={"severity": "high"},       # Attributes on <channel> tag
)
await channel.send_event(event)
```

### `PermissionRequest`

Received by `@channel.on_permission_request()` handlers:

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `str` | Five-letter ID (echo back in verdict) |
| `tool_name` | `str` | Tool name, e.g. `"Bash"`, `"Write"` |
| `description` | `str` | Human-readable summary of the tool call |
| `input_preview` | `str` | Tool arguments as JSON (~200 chars) |

### `PermissionBehavior`

```python
from claude_channel import PermissionBehavior

PermissionBehavior.ALLOW  # "allow" - let the tool call proceed
PermissionBehavior.DENY   # "deny"  - reject the tool call
```

## Tool Schema Inference

The `@channel.tool()` decorator automatically generates JSON Schema from function type hints:

```python
@channel.tool("reply", description="Send a message back")
async def reply(chat_id: str, text: str, count: int = 1) -> str:
    ...
```

Generates:

```json
{
  "type": "object",
  "properties": {
    "chat_id": {"type": "string"},
    "text": {"type": "string"},
    "count": {"type": "integer"}
  },
  "required": ["chat_id", "text"]
}
```

Supported types: `str`, `int`, `float`, `bool`. Parameters with defaults become optional. You can also pass `input_schema=` explicitly to override.

## Webhook Bridge Example

A complete webhook-to-channel bridge with HTTP server:

```python
import asyncio
from aiohttp import web
from claude_channel import Channel

channel = Channel(
    "webhook",
    instructions="Events from webhook channel. One-way, no reply expected.",
)

async def handle_post(request):
    body = await request.text()
    await channel.send(body, meta={"path": request.path, "method": request.method})
    return web.Response(text="ok")

async def main():
    app = web.Application()
    app.router.add_post("/{path:.*}", handle_post)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8788).start()
    await channel.run_async()

asyncio.run(main())
```

## Pre-connection Queueing

Events sent before the channel connects are automatically queued and flushed once the connection is established:

```python
channel = Channel("mybot")

# These are queued, not lost
await channel.send("event 1")
await channel.send("event 2")

# Queued events are flushed when the channel connects
await channel.run_async()
```

## More Examples

See the [`examples/`](examples/) directory:

- [`one_way.py`](examples/one_way.py) - Webhook bridge (one-way)
- [`two_way.py`](examples/two_way.py) - Chat bridge with reply tool (two-way)
- [`permission_relay.py`](examples/permission_relay.py) - Remote tool approval

## Notes

- Channels require `claude.ai` login. Console and API key authentication is not supported.
- Team and Enterprise organizations must explicitly enable channels.
- During the research preview, use `--dangerously-load-development-channels` to test custom channels.
- Meta keys must be identifiers (letters, digits, underscores). Keys with hyphens are silently dropped.
