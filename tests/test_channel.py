import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import mcp.types as types
from mcp.shared.message import SessionMessage

from claude_channel import Channel, ChannelEvent, PermissionBehavior, PermissionRequest
from claude_channel._channel import _infer_schema, _is_permission_request_message


class TestChannelConstruction:
    def test_defaults(self):
        ch = Channel("test")
        assert ch.name == "test"
        assert ch.version == "0.0.1"
        assert ch.instructions is None
        assert ch.permission_relay is False
        assert ch._tools == {}
        assert ch._permission_handler is None
        assert ch.is_connected is False

    def test_custom_options(self):
        ch = Channel(
            "mybot",
            version="1.2.3",
            instructions="Handle events",
            permission_relay=True,
        )
        assert ch.name == "mybot"
        assert ch.version == "1.2.3"
        assert ch.instructions == "Handle events"
        assert ch.permission_relay is True


class TestToolDecorator:
    def test_register_tool(self):
        ch = Channel("test")

        @ch.tool("reply", description="Send a reply")
        async def reply(chat_id: str, text: str) -> str:
            return "sent"

        assert "reply" in ch._tools
        tool, handler = ch._tools["reply"]
        assert tool.name == "reply"
        assert tool.description == "Send a reply"
        assert handler is reply

    def test_register_tool_default_name(self):
        ch = Channel("test")

        @ch.tool(description="Greet someone")
        async def greet(name: str) -> str:
            return f"Hello {name}"

        assert "greet" in ch._tools

    def test_register_tool_explicit_schema(self):
        ch = Channel("test")
        schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        }

        @ch.tool("calc", description="Calculate", input_schema=schema)
        async def calc(x: float) -> str:
            return str(x)

        tool, _ = ch._tools["calc"]
        assert tool.inputSchema == schema


class TestPermissionDecorator:
    def test_register_handler(self):
        ch = Channel("test", permission_relay=True)

        @ch.on_permission_request()
        async def handle(req: PermissionRequest) -> PermissionBehavior:
            return PermissionBehavior.ALLOW

        assert ch._permission_handler is handle


class TestInferSchema:
    def test_simple_strings(self):
        async def func(a: str, b: str) -> str:
            return ""

        schema = _infer_schema(func)
        assert schema == {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
            },
            "required": ["a", "b"],
        }

    def test_mixed_types(self):
        async def func(name: str, count: int, ratio: float, flag: bool) -> str:
            return ""

        schema = _infer_schema(func)
        assert schema["properties"]["name"] == {"type": "string"}
        assert schema["properties"]["count"] == {"type": "integer"}
        assert schema["properties"]["ratio"] == {"type": "number"}
        assert schema["properties"]["flag"] == {"type": "boolean"}
        assert schema["required"] == ["name", "count", "ratio", "flag"]

    def test_optional_params(self):
        async def func(required: str, optional: str = "default") -> str:
            return ""

        schema = _infer_schema(func)
        assert schema["required"] == ["required"]
        assert "optional" in schema["properties"]


class TestSendBeforeConnection:
    def test_queues_events(self):
        ch = Channel("test")
        import asyncio

        asyncio.run(ch.send("hello", meta={"key": "val"}))
        assert len(ch._pending) == 1
        method, params = ch._pending[0]
        assert method == "notifications/claude/channel"
        assert params["content"] == "hello"
        assert params["meta"] == {"key": "val"}

    def test_queues_permission_verdict(self):
        ch = Channel("test")
        import asyncio

        asyncio.run(ch.send_permission_verdict("abcde", PermissionBehavior.ALLOW))
        assert len(ch._pending) == 1
        method, params = ch._pending[0]
        assert method == "notifications/claude/channel/permission"
        assert params["request_id"] == "abcde"
        assert params["behavior"] == "allow"


class TestSendWithMockSession:
    @pytest.mark.asyncio
    async def test_send_notification(self):
        ch = Channel("test")
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        await ch.send("test event", meta={"severity": "high"})

        mock_session.send_message.assert_called_once()
        session_msg = mock_session.send_message.call_args[0][0]
        raw = json.loads(session_msg.message.model_dump_json(by_alias=True, exclude_none=True))
        assert raw["method"] == "notifications/claude/channel"
        assert raw["params"]["content"] == "test event"
        assert raw["params"]["meta"]["severity"] == "high"

    @pytest.mark.asyncio
    async def test_send_permission_verdict(self):
        ch = Channel("test")
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        await ch.send_permission_verdict("abcde", PermissionBehavior.DENY)

        mock_session.send_message.assert_called_once()
        session_msg = mock_session.send_message.call_args[0][0]
        raw = json.loads(session_msg.message.model_dump_json(by_alias=True, exclude_none=True))
        assert raw["method"] == "notifications/claude/channel/permission"
        assert raw["params"]["request_id"] == "abcde"
        assert raw["params"]["behavior"] == "deny"

    @pytest.mark.asyncio
    async def test_send_event_object(self):
        ch = Channel("test")
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        event = ChannelEvent(content="alert!", meta={"run_id": "42"})
        await ch.send_event(event)

        mock_session.send_message.assert_called_once()
        session_msg = mock_session.send_message.call_args[0][0]
        raw = json.loads(session_msg.message.model_dump_json(by_alias=True, exclude_none=True))
        assert raw["params"]["content"] == "alert!"
        assert raw["params"]["meta"]["run_id"] == "42"


class TestPermissionRequestInterceptor:
    """Test that _is_permission_request_message correctly identifies
    custom notifications at the raw stream level (before MCP SDK validation)."""

    def _make_session_message(self, method: str, params: dict) -> SessionMessage:
        notification = types.JSONRPCNotification(
            jsonrpc="2.0", method=method, params=params
        )
        return SessionMessage(
            message=types.JSONRPCMessage(notification)  # type: ignore[arg-type]
        )

    def test_detects_permission_request(self):
        msg = self._make_session_message(
            "notifications/claude/channel/permission_request",
            {
                "request_id": "abcde",
                "tool_name": "Bash",
                "description": "Run ls",
                "input_preview": '{"command": "ls"}',
            },
        )
        assert _is_permission_request_message(msg) is True

    def test_ignores_regular_notification(self):
        msg = self._make_session_message(
            "notifications/initialized", {}
        )
        assert _is_permission_request_message(msg) is False

    def test_ignores_channel_notification(self):
        msg = self._make_session_message(
            "notifications/claude/channel",
            {"content": "hello"},
        )
        assert _is_permission_request_message(msg) is False

    def test_ignores_exceptions(self):
        assert _is_permission_request_message(RuntimeError("oops")) is False

    @pytest.mark.asyncio
    async def test_handle_permission_request_calls_handler(self):
        ch = Channel("test", permission_relay=True)
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        handler_called_with = []

        @ch.on_permission_request()
        async def handle(req: PermissionRequest) -> PermissionBehavior:
            handler_called_with.append(req)
            return PermissionBehavior.ALLOW

        msg = self._make_session_message(
            "notifications/claude/channel/permission_request",
            {
                "request_id": "fghij",
                "tool_name": "Write",
                "description": "Write file",
                "input_preview": '{"path": "/tmp/x"}',
            },
        )

        await ch._handle_permission_request(msg)

        assert len(handler_called_with) == 1
        req = handler_called_with[0]
        assert req.request_id == "fghij"
        assert req.tool_name == "Write"

        # Should have sent a verdict
        mock_session.send_message.assert_called_once()
        session_msg = mock_session.send_message.call_args[0][0]
        raw = json.loads(session_msg.message.model_dump_json(by_alias=True, exclude_none=True))
        assert raw["method"] == "notifications/claude/channel/permission"
        assert raw["params"]["request_id"] == "fghij"
        assert raw["params"]["behavior"] == "allow"

    @pytest.mark.asyncio
    async def test_handle_permission_request_none_verdict_no_send(self):
        ch = Channel("test", permission_relay=True)
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        @ch.on_permission_request()
        async def handle(req: PermissionRequest) -> PermissionBehavior | None:
            return None  # skip verdict

        msg = self._make_session_message(
            "notifications/claude/channel/permission_request",
            {
                "request_id": "abcde",
                "tool_name": "Bash",
                "description": "test",
                "input_preview": "{}",
            },
        )

        await ch._handle_permission_request(msg)
        mock_session.send_message.assert_not_called()
