"""Python SDK for Claude Code Channels."""

from importlib.metadata import version

from ._channel import Channel
from ._types import ChannelEvent, PermissionBehavior, PermissionRequest

__version__ = version("claude-channel")

__all__ = [
    "Channel",
    "ChannelEvent",
    "PermissionBehavior",
    "PermissionRequest",
    "__version__",
]
