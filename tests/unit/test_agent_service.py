"""Unit tests for the pluggable agent layer and its config-mutation protocol."""

import pytest

from api import agent_service
from api.config_service import AGENT_PROVIDERS, ConfigStore, apply_updates

pytestmark = pytest.mark.unit

GIB = 1024 ** 3


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path)


@pytest.fixture
def context():
    return {
        "section": "system",
        "hostname": "test-node",
        "files": 3,
        "peers_total": 2,
        "peers_online": 1,
        "collective": {
            "used_bytes": 1024,
            "used_percent": 0.1,
            "shards_total": 12,
            "shards_available": 12,
        },
    }


# ── provider registry ───────────────────────────────────────────────


def test_every_provider_is_reported():
    ids = [entry["id"] for entry in agent_service.provider_status()]
    assert ids == list(AGENT_PROVIDERS)


def test_builtin_is_always_available():
    builtin = next(e for e in agent_service.provider_status() if e["id"] == "builtin")
    assert builtin["available"] is True


@pytest.mark.parametrize(
    "provider,expected_head",
    [("codewhale", ["codewhale", "exec"]), ("claude", ["claude", "-p"]), ("codex", ["codex", "exec"])],
)
def test_command_shapes(provider, expected_head):
    command = agent_service._build_command(provider, "hello", "")
    assert command[: len(expected_head)] == expected_head
    assert "hello" in command


def test_model_override_reaches_the_command():
    assert "--model" in agent_service._build_command("codewhale", "hi", "some-model")
    assert 'model="some-model"' in agent_service._build_command("codex", "hi", "some-model")


# ── ACTION protocol ─────────────────────────────────────────────────


def test_extract_action_parses_a_trailing_object():
    action = agent_service.extract_action(
        'Raising the quota.\nACTION:{"type":"config.update","payload":{"storage.quota_bytes":"1GB"}}'
    )
    assert action["type"] == "config.update"
    assert action["payload"]["storage.quota_bytes"] == "1GB"


def test_extract_action_handles_trailing_prose_after_json():
    action = agent_service.extract_action(
        'ACTION:{"type":"navigate","payload":{"path":"/sections/files"}} thanks!'
    )
    assert action == {"type": "navigate", "payload": {"path": "/sections/files"}}


def test_extract_action_returns_none_without_a_marker():
    assert agent_service.extract_action("just a normal answer") is None


def test_strip_action_leaves_only_prose():
    text = 'All set.\nACTION:{"type":"config.update","payload":{"upload.concurrency":3}}'
    assert agent_service.strip_action(text) == "All set."


# ── deterministic interpreter ───────────────────────────────────────


@pytest.mark.parametrize(
    "message,field,value",
    [
        ("allocate 500GB to the collective", "storage.quota_bytes", 500 * GIB),
        ("set storage to 2 TB", "storage.quota_bytes", 2 * 1024 ** 4),
        ("give the collective 100 gb of space", "storage.quota_bytes", 100 * GIB),
        ("set parity shards to 6", "erasure.parity_shards", 6),
        ("use 12 data shards", "erasure.data_shards", 12),
        ("set the write cutoff to 80", "storage.high_watermark_percent", 80),
        ("max upload size 2GB", "upload.max_file_bytes", 2 * GIB),
        ("set default tier to cold", "contracts.default_tier", "cold"),
        ("disable challenges", "contracts.challenges_enabled", False),
        ("enable challenges", "contracts.challenges_enabled", True),
        ("switch to claude", "agent.provider", "claude"),
        ("use codex please", "agent.provider", "codex"),
    ],
)
def test_interpreter_maps_instructions_to_fields(message, field, value, store):
    action = agent_service.interpret(message, store.load())
    assert action == {"type": "config.update", "payload": {field: value}}


def test_relative_size_change_uses_the_current_value(store):
    config, _ = apply_updates(store, {"storage.quota_bytes": "50GB"})
    action = agent_service.interpret("increase space by 10GB", config)
    assert action["payload"]["storage.quota_bytes"] == 60 * GIB


def test_relative_decrease(store):
    config, _ = apply_updates(store, {"storage.quota_bytes": "50GB"})
    action = agent_service.interpret("reduce space by 20GB", config)
    assert action["payload"]["storage.quota_bytes"] == 30 * GIB


def test_relative_int_change(store):
    config, _ = apply_updates(store, {"erasure.parity_shards": 4})
    action = agent_service.interpret("increase parity shards by 2", config)
    assert action["payload"]["erasure.parity_shards"] == 6


@pytest.mark.parametrize(
    "message",
    [
        "how much space is left?",
        "what is stored here?",
        "hello",
        "",
        "tell me about the parity budget",  # a question, not an instruction
    ],
)
def test_interpreter_ignores_non_instructions(message, store):
    assert agent_service.interpret(message, store.load()) is None


def test_summary_reports_live_state(context, store):
    summary = agent_service.summarize_state(context, store.load())
    assert "test-node" in summary
    assert "Erasure coding" in summary
    assert "12/12 shards" in summary


# ── end-to-end chat turn ────────────────────────────────────────────


async def test_builtin_chat_applies_a_change(store, context):
    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="allocate 300GB to the collective",
        history=[],
        context=context,
        provider_override="builtin",
    )
    assert result["provider"] == "builtin"
    assert result["error"] is None
    assert result["applied"][0]["after"] == 300 * GIB
    assert "Applied" in result["reply"]
    assert store.load()["storage"]["quota_bytes"] == 300 * GIB


async def test_builtin_chat_reports_a_rejection_without_writing(store, context):
    before = store.load()
    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="allocate 900PB to the collective",
        history=[],
        context=context,
        provider_override="builtin",
    )
    assert result["applied"] == []
    assert "exceeds the filesystem size" in result["error"]
    assert "Not applied" in result["reply"]
    assert store.load() == before


async def test_builtin_chat_answers_questions(store, context):
    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="how much headroom is left?",
        history=[],
        context=context,
        provider_override="builtin",
    )
    assert result["applied"] == []
    assert "Quota" in result["reply"]


async def test_falls_back_to_builtin_when_the_cli_is_missing(store, context, monkeypatch):
    monkeypatch.setattr(agent_service, "_binary_path", lambda provider: None)
    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="set parity shards to 5",
        history=[],
        context=context,
        provider_override="codewhale",
    )
    assert result["provider_requested"] == "codewhale"
    assert result["provider"] == "builtin"
    assert result["fell_back"] is True
    assert result["notes"]
    # The change still lands — a missing CLI must not silently drop the request.
    assert store.load()["erasure"]["parity_shards"] == 5


async def test_cli_action_is_applied(store, context, monkeypatch):
    async def fake_cli(provider, prompt, model, timeout):
        return True, (
            'Raising the allocation.\n'
            'ACTION:{"type":"config.update","payload":{"storage.quota_bytes":"64GB"}}'
        )

    monkeypatch.setattr(agent_service, "_binary_path", lambda provider: "/usr/bin/fake")
    monkeypatch.setattr(agent_service, "run_cli", fake_cli)

    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="allocate 64GB",
        history=[],
        context=context,
        provider_override="codewhale",
    )
    assert result["provider"] == "codewhale"
    assert result["fell_back"] is False
    assert result["reply"] == "Raising the allocation."
    assert result["applied"][0]["after"] == 64 * GIB
    assert store.load()["storage"]["quota_bytes"] == 64 * GIB


async def test_cli_navigate_action_is_passed_through(store, context, monkeypatch):
    async def fake_cli(provider, prompt, model, timeout):
        return True, 'Opening files.\nACTION:{"type":"navigate","payload":{"path":"/sections/files"}}'

    monkeypatch.setattr(agent_service, "_binary_path", lambda provider: "/usr/bin/fake")
    monkeypatch.setattr(agent_service, "run_cli", fake_cli)

    result = await agent_service.run_chat(
        store=store,
        section="files",
        message="show me my files",
        history=[],
        context=context,
        provider_override="claude",
    )
    assert result["navigate"] == "/sections/files"
    assert result["applied"] == []


async def test_cli_invalid_action_surfaces_the_error(store, context, monkeypatch):
    async def fake_cli(provider, prompt, model, timeout):
        return True, 'Done.\nACTION:{"type":"config.update","payload":{"erasure.parity_shards":99}}'

    monkeypatch.setattr(agent_service, "_binary_path", lambda provider: "/usr/bin/fake")
    monkeypatch.setattr(agent_service, "run_cli", fake_cli)

    result = await agent_service.run_chat(
        store=store,
        section="system",
        message="set parity to 99",
        history=[],
        context=context,
        provider_override="codewhale",
    )
    assert result["applied"] == []
    assert "at most 32" in result["error"]


async def test_configured_provider_is_used_by_default(store, context, monkeypatch):
    apply_updates(store, {"agent.provider": "codex"})
    seen = {}

    async def fake_cli(provider, prompt, model, timeout):
        seen["provider"] = provider
        return True, "ok"

    monkeypatch.setattr(agent_service, "_binary_path", lambda provider: "/usr/bin/fake")
    monkeypatch.setattr(agent_service, "run_cli", fake_cli)

    await agent_service.run_chat(
        store=store, section="system", message="status", history=[], context=context
    )
    assert seen["provider"] == "codex"


# ── prompt construction ─────────────────────────────────────────────


def test_system_prompt_carries_the_schema_and_state(store, context):
    prompt = agent_service.build_prompt("system", "hi", [], context, store.load())
    assert "storage.quota_bytes" in prompt
    assert "ACTION:" in prompt
    assert "test-node" in prompt


def test_files_prompt_omits_the_mutation_protocol(store, context):
    prompt = agent_service.build_prompt("files", "hi", [], context, store.load())
    assert "Archivist" in prompt
    assert "Changing configuration" not in prompt


def test_history_is_included_and_bounded(store, context):
    history = [{"role": "user", "content": f"msg{index}"} for index in range(20)]
    prompt = agent_service.build_prompt("system", "now", history, context, store.load())
    assert "msg19" in prompt
    assert "msg0\n" not in prompt
