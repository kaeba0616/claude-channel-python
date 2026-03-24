from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionBehavior(str, Enum):
    """Verdict for a permission relay request."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass
class ChannelEvent:
    """A channel notification event to push into a Claude Code session.

    Attributes:
        content: The event body. Delivered as the body of the ``<channel>`` tag.
        meta: Each entry becomes an attribute on the ``<channel>`` tag.
              Keys must be identifiers (letters, digits, underscores only).
    """

    content: str
    meta: dict[str, str] = field(default_factory=dict)


@dataclass
class PermissionRequest:
    """An incoming permission relay request from Claude Code.

    Attributes:
        request_id: Five lowercase letters (``a``-``z`` without ``l``).
        tool_name: Name of the tool Claude wants to use (e.g. ``Bash``, ``Write``).
        description: Human-readable summary of the tool call.
        input_preview: Tool arguments as JSON, truncated to ~200 chars.
    """

    request_id: str
    tool_name: str
    description: str
    input_preview: str
