from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

import mcp.types as types
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage

from ._types import ChannelEvent, PermissionBehavior, PermissionRequest

logger = logging.getLogger(__name__)

# Mapping from Python type annotations to JSON Schema types
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _infer_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Infer a JSON Schema ``object`` from a function's type hints."""
    sig = inspect.signature(func)
    hints = {}
    try:
        hints = {
            k: v
            for k, v in inspect.get_annotations(func, eval_str=True).items()
            if k != "return"
        }
    except Exception:
        pass

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        ann = hints.get(name)
        json_type = _TYPE_MAP.get(ann, "string") if ann else "string"
        properties[name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _is_permission_request_message(message: SessionMessage | Exception) -> bool:
    """Check if a raw stream message is a permission_request notification.

    This runs *before* the MCP SDK's type validation, so we inspect the raw
    ``JSONRPCNotification`` directly.
    """
    if isinstance(message, Exception):
        return False
    try:
        root = message.message.root
        return (
            isinstance(root, types.JSONRPCNotification)
            and root.method == "notifications/claude/channel/permission_request"
        )
    except Exception:
        return False


class Channel:
    """A Python SDK for building Claude Code Channel servers.

    A channel is an MCP server that pushes events into a Claude Code session.
    It communicates over stdio and declares the ``claude/channel`` experimental
    capability.

    Args:
        name: Server name. Appears as ``source`` attribute on ``<channel>`` tags.
        version: Server version string.
        instructions: Added to Claude's system prompt. Tell Claude what events
            to expect, whether to reply, and how.
        permission_relay: If ``True``, declares ``claude/channel/permission``
            capability so this channel can receive and relay permission prompts.
    """

    def __init__(
        self,
        name: str,
        *,
        version: str = "0.0.1",
        instructions: str | None = None,
        permission_relay: bool = False,
    ) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.permission_relay = permission_relay

        # Registered reply tools: name → (Tool, handler)
        self._tools: dict[str, tuple[types.Tool, Callable[..., Awaitable[str]]]] = {}
        # Permission request handler
        self._permission_handler: Callable[[PermissionRequest], Awaitable[PermissionBehavior | None]] | None = None
        # Session reference, set once run_async() connects
        self._session: ServerSession | None = None
        # Queue for events sent before connection is established
        self._pending: list[tuple[str, dict[str, Any]]] = []

    # ── Sending events ──────────────────────────────────────────────────

    async def send(self, content: str, *, meta: dict[str, str] | None = None) -> None:
        """Send a channel notification event to Claude Code.

        Args:
            content: The event body (becomes body of ``<channel>`` tag).
            meta: Each key-value pair becomes an attribute on the ``<channel>``
                tag. Keys must be identifiers (letters, digits, underscores).
        """
        params: dict[str, Any] = {"content": content}
        if meta:
            params["meta"] = meta

        if self._session is None:
            self._pending.append(("notifications/claude/channel", params))
            return

        await self._send_raw_notification("notifications/claude/channel", params)

    async def send_event(self, event: ChannelEvent) -> None:
        """Send a :class:`ChannelEvent` to Claude Code."""
        await self.send(event.content, meta=event.meta)

    async def send_permission_verdict(
        self,
        request_id: str,
        behavior: PermissionBehavior | str,
    ) -> None:
        """Send a permission verdict back to Claude Code.

        Args:
            request_id: The five-letter ID from the permission request.
            behavior: ``"allow"`` or ``"deny"``.
        """
        if isinstance(behavior, PermissionBehavior):
            behavior = behavior.value

        params = {"request_id": request_id, "behavior": behavior}

        if self._session is None:
            self._pending.append(("notifications/claude/channel/permission", params))
            return

        await self._send_raw_notification(
            "notifications/claude/channel/permission", params
        )

    # ── Decorators ──────────────────────────────────────────────────────

    def tool(
        self,
        name: str | None = None,
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> Callable[..., Any]:
        """Decorator to register a reply tool that Claude can call.

        Args:
            name: Tool name. Defaults to the function name.
            description: Tool description shown to Claude.
            input_schema: JSON Schema for the tool input. If omitted, inferred
                from the function's type hints.
        """

        def decorator(func: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
            tool_name = name or func.__name__
            schema = input_schema or _infer_schema(func)
            mcp_tool = types.Tool(
                name=tool_name,
                description=description or func.__doc__ or "",
                inputSchema=schema,
            )
            self._tools[tool_name] = (mcp_tool, func)
            return func

        return decorator

    def on_permission_request(self) -> Callable[..., Any]:
        """Decorator to register a handler for incoming permission requests.

        The handler receives a :class:`PermissionRequest` and should return a
        :class:`PermissionBehavior` (or ``None`` to skip sending a verdict).
        """

        def decorator(
            func: Callable[[PermissionRequest], Awaitable[PermissionBehavior | None]],
        ) -> Callable[[PermissionRequest], Awaitable[PermissionBehavior | None]]:
            self._permission_handler = func
            return func

        return decorator

    # ── Running ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the channel server synchronously (blocking, stdio transport)."""
        anyio.run(self.run_async)

    async def run_async(self) -> None:
        """Run the channel server asynchronously over stdio."""
        server = Server(self.name, version=self.version, instructions=self.instructions)

        # Register tool handlers if any tools are defined
        if self._tools:
            self._register_tools(server)

        # Build experimental capabilities
        experimental: dict[str, dict[str, Any]] = {"claude/channel": {}}
        if self.permission_relay:
            experimental["claude/channel/permission"] = {}

        init_options = server.create_initialization_options(
            notification_options=NotificationOptions(),
            experimental_capabilities=experimental,
        )

        async with stdio_server() as (read_stream, write_stream):
            # If permission relay is active, intercept the read stream to
            # capture custom notifications before the MCP SDK drops them.
            # The SDK's ServerSession._receive_loop() validates incoming
            # notifications against ClientNotification and silently drops
            # unknown types like notifications/claude/channel/permission_request.
            effective_read: MemoryObjectReceiveStream[SessionMessage | Exception] = read_stream
            intercept_tg = None

            if self.permission_relay and self._permission_handler:
                intercepted_writer, intercepted_reader = anyio.create_memory_object_stream[
                    SessionMessage | Exception
                ](0)
                effective_read = intercepted_reader

                async def _intercept_loop() -> None:
                    async with read_stream, intercepted_writer:
                        async for message in read_stream:
                            if _is_permission_request_message(message):
                                await self._handle_permission_request(message)
                            else:
                                await intercepted_writer.send(message)

            async with AsyncExitStack() as stack:
                lifespan_context = await stack.enter_async_context(server.lifespan(server))

                # Start the interceptor if needed
                if self.permission_relay and self._permission_handler:
                    intercept_tg = anyio.create_task_group()
                    await stack.enter_async_context(intercept_tg)
                    intercept_tg.start_soon(_intercept_loop)  # type: ignore[possibly-undefined]

                session = await stack.enter_async_context(
                    ServerSession(effective_read, write_stream, init_options)
                )
                self._session = session

                # Flush any events queued before connection
                for method, params in self._pending:
                    await self._send_raw_notification(method, params)
                self._pending.clear()

                async with anyio.create_task_group() as tg:
                    async for message in session.incoming_messages:
                        logger.debug("Received message: %s", message)
                        tg.start_soon(
                            server._handle_message,
                            message,
                            session,
                            lifespan_context,
                            False,  # raise_exceptions
                        )

    # ── Internal helpers ────────────────────────────────────────────────

    def _register_tools(self, server: Server[Any, Any]) -> None:
        """Register list_tools and call_tool handlers on the MCP server."""
        tools = self._tools

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [tool for tool, _ in tools.values()]

        @server.call_tool()
        async def call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            if name not in tools:
                raise McpError(
                    error=types.ErrorData(
                        code=types.METHOD_NOT_FOUND,
                        message=f"Unknown tool: {name}",
                    )
                )
            _, handler = tools[name]
            result = await handler(**(arguments or {}))
            return [types.TextContent(type="text", text=str(result))]

    async def _send_raw_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a raw JSONRPC notification via the session."""
        assert self._session is not None
        notification = types.JSONRPCNotification(
            jsonrpc="2.0",
            method=method,
            params=params,
        )
        message = SessionMessage(
            message=types.JSONRPCMessage(notification),  # type: ignore[arg-type]
        )
        await self._session.send_message(message)

    async def _handle_permission_request(self, message: SessionMessage | Exception) -> None:
        """Extract permission request fields and call the user's handler."""
        if not self._permission_handler:
            return
        try:
            assert not isinstance(message, Exception)
            root = message.message.root
            assert isinstance(root, types.JSONRPCNotification)

            params = root.params or {}
            req = PermissionRequest(
                request_id=params.get("request_id", ""),
                tool_name=params.get("tool_name", ""),
                description=params.get("description", ""),
                input_preview=params.get("input_preview", ""),
            )

            verdict = await self._permission_handler(req)
            if verdict is not None:
                await self.send_permission_verdict(req.request_id, verdict)
        except Exception:
            logger.exception("Error handling permission request")

    @property
    def is_connected(self) -> bool:
        """Whether the channel is connected to Claude Code."""
        return self._session is not None
