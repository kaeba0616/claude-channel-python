#!/usr/bin/env python3
"""Minimal one-way webhook channel.

Listens for HTTP POSTs on port 8788 and forwards them to Claude Code.
Run with: claude --dangerously-load-development-channels server:webhook

Test with: curl -X POST localhost:8788 -d "build failed on main"
"""

import asyncio

from aiohttp import web

from claude_channel import Channel

channel = Channel(
    "webhook",
    instructions=(
        "Events from the webhook channel arrive as "
        '<channel source="webhook" ...>. '
        "They are one-way: read them and act, no reply expected."
    ),
)


async def handle_post(request: web.Request) -> web.Response:
    body = await request.text()
    await channel.send(
        body,
        meta={"path": request.path, "method": request.method},
    )
    return web.Response(text="ok")


async def main() -> None:
    app = web.Application()
    app.router.add_post("/{path:.*}", handle_post)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8788).start()
    await channel.run_async()


if __name__ == "__main__":
    asyncio.run(main())
