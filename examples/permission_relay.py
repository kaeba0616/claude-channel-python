#!/usr/bin/env python3
"""Channel with permission relay.

When Claude wants to run a tool that needs approval, the request is
forwarded to this channel. The handler auto-allows Read/Grep and denies
everything else.
"""

import sys

from claude_channel import Channel, PermissionBehavior, PermissionRequest

channel = Channel(
    "secure",
    permission_relay=True,
    instructions=(
        'Messages arrive as <channel source="secure" ...>. '
        "Permission prompts are relayed for remote approval."
    ),
)

SAFE_TOOLS = {"Read", "Grep", "Glob"}


@channel.on_permission_request()
async def handle(req: PermissionRequest) -> PermissionBehavior:
    print(
        f"Permission request: {req.tool_name} — {req.description}",
        file=sys.stderr,
    )
    if req.tool_name in SAFE_TOOLS:
        return PermissionBehavior.ALLOW
    return PermissionBehavior.DENY


if __name__ == "__main__":
    channel.run()
