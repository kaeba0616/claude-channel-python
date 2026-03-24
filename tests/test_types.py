from claude_channel import ChannelEvent, PermissionBehavior, PermissionRequest


class TestChannelEvent:
    def test_create_with_defaults(self):
        event = ChannelEvent(content="hello")
        assert event.content == "hello"
        assert event.meta == {}

    def test_create_with_meta(self):
        event = ChannelEvent(content="alert", meta={"severity": "high", "run_id": "42"})
        assert event.content == "alert"
        assert event.meta == {"severity": "high", "run_id": "42"}


class TestPermissionBehavior:
    def test_values(self):
        assert PermissionBehavior.ALLOW == "allow"
        assert PermissionBehavior.DENY == "deny"

    def test_string_compatibility(self):
        assert PermissionBehavior.ALLOW.value == "allow"
        assert isinstance(PermissionBehavior.ALLOW, str)


class TestPermissionRequest:
    def test_create(self):
        req = PermissionRequest(
            request_id="abcde",
            tool_name="Bash",
            description="Run ls command",
            input_preview='{"command": "ls"}',
        )
        assert req.request_id == "abcde"
        assert req.tool_name == "Bash"
        assert req.description == "Run ls command"
        assert req.input_preview == '{"command": "ls"}'
