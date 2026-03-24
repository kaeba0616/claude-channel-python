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
            "additionalProperties": False,
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

    def test_unsupported_types_default_to_string(self):
        async def func(data: dict, items: list) -> str:
            return ""

        schema = _infer_schema(func)
        assert schema["properties"]["data"] == {"type": "string"}
        assert schema["properties"]["items"] == {"type": "string"}

    def test_additional_properties_false(self):
        async def func(x: str) -> str:
            return ""

        schema = _infer_schema(func)
        assert schema["additionalProperties"] is False


class TestArgumentFiltering:
    """I1: Extra arguments must be filtered before passing to handler."""

    @pytest.mark.asyncio
    async def test_extra_arguments_filtered_out(self):
        ch = Channel("test")
        received_kwargs = {}

        @ch.tool("safe", description="A safe tool")
        async def safe(x: str) -> str:
            received_kwargs.update({"x": x})
            return "ok"

        # Simulate what _register_tools + call_tool does internally
        tool_def, handler = ch._tools["safe"]
        schema_keys = set(tool_def.inputSchema.get("properties", {}).keys())
        args = {"x": "good", "malicious": "evil", "__class__": "bad"}
        filtered = {k: v for k, v in args.items() if k in schema_keys}
        result = await handler(**filtered)

        assert result == "ok"
        assert received_kwargs == {"x": "good"}
        assert "malicious" not in received_kwargs
        assert "__class__" not in received_kwargs


class TestMetaKeyValidation:
    """C1: Meta keys must be valid identifiers."""

    def test_valid_keys(self):
        import asyncio
        ch = Channel("test")
        asyncio.run(ch.send("hello", meta={"severity": "high", "run_id": "42", "_private": "x"}))
        assert len(ch._pending) == 1

    def test_rejects_hyphenated_key(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid meta key"):
            asyncio.run(ch.send("hello", meta={"my-key": "val"}))

    def test_rejects_key_with_quotes(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid meta key"):
            asyncio.run(ch.send("hello", meta={'foo" injected="true': "val"}))

    def test_rejects_key_starting_with_digit(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid meta key"):
            asyncio.run(ch.send("hello", meta={"1abc": "val"}))

    def test_rejects_empty_key(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid meta key"):
            asyncio.run(ch.send("hello", meta={"": "val"}))


class TestPermissionVerdictValidation:
    """C2: request_id and behavior must be valid."""

    def test_valid_verdict(self):
        import asyncio
        ch = Channel("test")
        asyncio.run(ch.send_permission_verdict("abcde", PermissionBehavior.ALLOW))
        assert len(ch._pending) == 1

    def test_rejects_short_request_id(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid request_id"):
            asyncio.run(ch.send_permission_verdict("abc", PermissionBehavior.ALLOW))

    def test_rejects_request_id_with_l(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid request_id"):
            asyncio.run(ch.send_permission_verdict("abcle", PermissionBehavior.ALLOW))

    def test_rejects_uppercase_request_id(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid request_id"):
            asyncio.run(ch.send_permission_verdict("ABCDE", PermissionBehavior.ALLOW))

    def test_rejects_invalid_behavior_string(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid behavior"):
            asyncio.run(ch.send_permission_verdict("abcde", "yolo"))

    def test_rejects_empty_request_id(self):
        import asyncio
        ch = Channel("test")
        with pytest.raises(ValueError, match="Invalid request_id"):
            asyncio.run(ch.send_permission_verdict("", PermissionBehavior.ALLOW))


class TestPendingQueueBound:
    """I2: Pending queue should not grow unbounded."""

    def test_queue_drops_oldest_when_full(self):
        import asyncio
        ch = Channel("test")
        for i in range(1100):
            asyncio.run(ch.send(f"event-{i}"))
        assert len(ch._pending) == 1000
        # Oldest events (0-99) should have been evicted
        _, first_params = ch._pending[0]
        assert first_params["content"] == "event-100"


class TestSendBeforeConnection:
    def test_queues_events(self):
        import asyncio
        ch = Channel("test")
        asyncio.run(ch.send("hello", meta={"key": "val"}))
        assert len(ch._pending) == 1
        method, params = ch._pending[0]
        assert method == "notifications/claude/channel"
        assert params["content"] == "hello"
        assert params["meta"] == {"key": "val"}

    def test_queues_permission_verdict(self):
        import asyncio
        ch = Channel("test")
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

    @pytest.mark.asyncio
    async def test_handle_permission_request_exception_logged_not_raised(self):
        """Exceptions in permission handler are logged, not propagated."""
        ch = Channel("test", permission_relay=True)
        mock_session = MagicMock()
        mock_session.send_message = AsyncMock()
        ch._session = mock_session

        @ch.on_permission_request()
        async def handle(req: PermissionRequest) -> PermissionBehavior:
            raise RuntimeError("handler crashed")

        msg = self._make_session_message(
            "notifications/claude/channel/permission_request",
            {
                "request_id": "abcde",
                "tool_name": "Bash",
                "description": "test",
                "input_preview": "{}",
            },
        )

        # Should not raise
        await ch._handle_permission_request(msg)

        # No verdict sent because handler crashed
        mock_session.send_message.assert_not_called()
