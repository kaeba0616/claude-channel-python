#!/usr/bin/env python3
"""Two-way channel with a reply tool.

Claude can call the ``reply`` tool to send messages back.
This example just prints replies to stderr (visible in debug logs).
"""

import asyncio
import sys

from claude_channel import Channel

channel = Channel(
    "chat",
    instructions=(
        'Messages arrive as <channel source="chat" chat_id="...">. '
        "Reply with the reply tool, passing the chat_id from the tag."
    ),
)


@channel.tool("reply", description="Send a message back over this channel")
async def reply(chat_id: str, text: str) -> str:
    # In a real bridge, POST to your chat platform here.
    print(f"[reply to {chat_id}] {text}", file=sys.stderr)
    return "sent"


async def main() -> None:
    # Simulate an incoming message after 2 seconds, then run the channel.
    async def send_demo_message() -> None:
        await asyncio.sleep(2)
        await channel.send("Hello from the outside!", meta={"chat_id": "1"})

    async with asyncio.TaskGroup() as tg:
        tg.create_task(send_demo_message())
        tg.create_task(channel.run_async())


if __name__ == "__main__":
    asyncio.run(main())
