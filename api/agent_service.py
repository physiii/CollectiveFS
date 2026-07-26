"""Pluggable agent backend for the section chats.

`codewhale` is the default provider; `claude` and `codex` are drop-in
alternatives selected at runtime from the UI (or `AGENT_PROVIDER`). Each is a
local CLI driven non-interactively, so switching providers is a config change
rather than a code change.

The System chat is not read-only. The model is handed the configuration schema
and answers with an ``ACTION:{...}`` line; :func:`run_chat` parses it, applies
the change through :mod:`api.config_service` (which validates and audits), and
reports the resulting diff. A deterministic ``builtin`` interpreter implements
the same action protocol without an LLM, so configuration edits still work when
no CLI is installed — and it is what the tests drive.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from api.config_service import (
    AGENT_PROVIDERS,
    ConfigError,
    ConfigStore,
    apply_updates,
    describe_settings,
    human_bytes,
    parse_size,
)

# ── provider registry ───────────────────────────────────────────────────

PROVIDER_INFO: Dict[str, Dict[str, Any]] = {
    "codewhale": {
        "label": "Codewhale",
        "binary": "codewhale",
        "description": "Default local agent runtime.",
    },
    "claude": {
        "label": "Claude Code",
        "binary": "claude",
        "description": "Anthropic Claude Code CLI.",
    },
    "codex": {
        "label": "Codex",
        "binary": "codex",
        "description": "OpenAI Codex CLI.",
    },
    "builtin": {
        "label": "Built-in",
        "binary": None,
        "description": "Deterministic in-process interpreter. No LLM required.",
    },
}


def _binary_path(provider: str) -> Optional[str]:
    binary = PROVIDER_INFO.get(provider, {}).get("binary")
    if not binary:
        return None
    return shutil.which(binary)


def provider_status() -> List[Dict[str, Any]]:
    """What the UI shows in the provider switcher."""
    out = []
    for name in AGENT_PROVIDERS:
        info = PROVIDER_INFO.get(name, {})
        path = _binary_path(name)
        out.append(
            {
                "id": name,
                "label": info.get("label", name),
                "description": info.get("description", ""),
                "available": True if name == "builtin" else bool(path),
                "path": path,
            }
        )
    return out


def _build_command(provider: str, prompt: str, model: str) -> List[str]:
    if provider == "codewhale":
        cmd = ["codewhale", "exec"]
        if model:
            cmd += ["--model", model]
        cmd += [prompt]
        return cmd
    if provider == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            cmd += ["--model", model]
        return cmd
    if provider == "codex":
        cmd = ["codex", "exec"]
        if model:
            cmd += ["-c", f'model="{model}"']
        cmd += [prompt]
        return cmd
    raise ConfigError(f"provider {provider!r} has no command form")


async def run_cli(provider: str, prompt: str, model: str, timeout: int) -> Tuple[bool, str]:
    """Run a provider CLI once. Returns (ok, text)."""
    if not _binary_path(provider):
        return False, f"{PROVIDER_INFO.get(provider, {}).get('label', provider)} CLI is not installed on this node."
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_command(provider, prompt, model),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"},
        )
    except (FileNotFoundError, OSError) as exc:
        return False, f"Could not start {provider}: {exc}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False, f"{provider} timed out after {timeout}s."

    text = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 and not text:
        err = stderr.decode("utf-8", errors="replace").strip()
        return False, f"{provider} exited with status {proc.returncode}: {err[:600]}"
    return True, text


# ── action protocol ─────────────────────────────────────────────────────

_ACTION_RE = re.compile(r"ACTION:\s*(\{.+)", re.DOTALL)


def extract_action(text: str) -> Optional[Dict[str, Any]]:
    """Pull the trailing ACTION:{...} object out of a model reply."""
    match = _ACTION_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1)
    for end in range(len(raw), 0, -1):
        if raw[end - 1] != "}":
            continue
        try:
            parsed = json.loads(raw[:end])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type"):
            return parsed
    return None


def strip_action(text: str) -> str:
    return _ACTION_RE.sub("", text or "").strip()


# ── deterministic interpreter ───────────────────────────────────────────

_FIELD_ALIASES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(quota|allocat\w*|pledge\w*|space|capacity|storage)\b"), "storage.quota_bytes"),
    (re.compile(r"\b(reserve|reserved|headroom)\b"), "storage.reserve_bytes"),
    (re.compile(r"\b(watermark|cutoff)\b"), "storage.high_watermark_percent"),
    (re.compile(r"\bdata\s*shards?\b"), "erasure.data_shards"),
    (re.compile(r"\b(parity|redundan\w*)\s*shards?\b"), "erasure.parity_shards"),
    (re.compile(r"\bparity\b"), "erasure.parity_shards"),
    (re.compile(r"\b(max\w*\s*(file|upload)|upload\s*(size|limit))\b"), "upload.max_file_bytes"),
    (re.compile(r"\bconcurren\w*\b"), "upload.concurrency"),
    (re.compile(r"\btier\b"), "contracts.default_tier"),
    (re.compile(r"\bchallenges?\b"), "contracts.challenges_enabled"),
    (re.compile(r"\b(max\w*\s*peers|peer\s*limit)\b"), "contracts.max_peers"),
    (re.compile(r"\b(provider|backend|agent)\b"), "agent.provider"),
]

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(b|kb|kib|mb|mib|gb|gib|tb|tib|pb|pib)\b", re.I)
_PLAIN_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_DELTA_RE = re.compile(
    r"\b(increase|raise|grow|add|bump|expand|decrease|reduce|lower|shrink|drop|cut)\b", re.I
)
_NEGATIVE_DELTA = {"decrease", "reduce", "lower", "shrink", "drop", "cut"}


def _field_for(message: str) -> Optional[str]:
    for pattern, field in _FIELD_ALIASES:
        if pattern.search(message):
            return field
    return None


def interpret(message: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a plain-language instruction to a config.update action.

    Returns ``None`` when the message is not an actionable configuration
    change, in which case the caller answers with a status summary instead.
    """
    text = (message or "").strip()
    lowered = text.lower()
    if not lowered:
        return None

    # Provider switching reads naturally and has no numeric argument.
    for provider in ("codewhale", "claude", "codex", "builtin"):
        if re.search(rf"\b(switch|change|use|set)\b[^.]*\b{provider}\b", lowered):
            return {"type": "config.update", "payload": {"agent.provider": provider}}

    field = _field_for(lowered)
    if not field:
        return None

    from api.config_service import FIELD_SPECS, _get_path  # local import: avoids a cycle

    spec = FIELD_SPECS.get(field, {})
    kind = spec.get("type", "str")

    if kind == "bool":
        if re.search(r"\b(enable|turn on|switch on|activate|resume)\b", lowered):
            return {"type": "config.update", "payload": {field: True}}
        if re.search(r"\b(disable|turn off|switch off|deactivate|stop|pause)\b", lowered):
            return {"type": "config.update", "payload": {field: False}}
        return None

    if kind == "enum":
        for choice in spec.get("choices", []):
            if re.search(rf"\b{re.escape(choice)}\b", lowered):
                return {"type": "config.update", "payload": {field: choice}}
        return None

    if kind == "bytes":
        size_match = _SIZE_RE.search(text)
        if not size_match:
            return None
        amount = parse_size(size_match.group(0))
        delta_match = _DELTA_RE.search(lowered)
        if delta_match and re.search(r"\bby\b", lowered):
            current = int(_get_path(config, field) or 0)
            sign = -1 if delta_match.group(1).lower() in _NEGATIVE_DELTA else 1
            return {
                "type": "config.update",
                "payload": {field: max(current + sign * amount, 0)},
            }
        return {"type": "config.update", "payload": {field: amount}}

    if kind == "int":
        number_match = _PLAIN_NUMBER_RE.search(text)
        if not number_match:
            return None
        amount = int(float(number_match.group(1)))
        delta_match = _DELTA_RE.search(lowered)
        if delta_match and re.search(r"\bby\b", lowered):
            current = int(_get_path(config, field) or 0)
            sign = -1 if delta_match.group(1).lower() in _NEGATIVE_DELTA else 1
            return {"type": "config.update", "payload": {field: max(current + sign * amount, 0)}}
        return {"type": "config.update", "payload": {field: amount}}

    return None


def summarize_state(context: Dict[str, Any], config: Dict[str, Any]) -> str:
    """The builtin provider's answer when nothing needs changing."""
    collective = context.get("collective", {})
    storage = config.get("storage", {})
    erasure = config.get("erasure", {})
    lines = [
        f"**Node** `{context.get('hostname', 'unknown')}` · {context.get('files', 0)} files · "
        f"{collective.get('shards_available', 0)}/{collective.get('shards_total', 0)} shards present",
        "",
        f"- **Quota** {human_bytes(collective.get('used_bytes'))} used of "
        f"{human_bytes(storage.get('quota_bytes'))} pledged "
        f"({collective.get('used_percent', 0)}%), write cutoff at "
        f"{storage.get('high_watermark_percent')}%",
        f"- **Erasure coding** {erasure.get('data_shards')} data + "
        f"{erasure.get('parity_shards')} parity — tolerates "
        f"{erasure.get('parity_shards')} lost shards per file",
        f"- **Peers** {context.get('peers_online', 0)} online of {context.get('peers_total', 0)}",
        f"- **Contracts** default tier `{config.get('contracts', {}).get('default_tier')}`, "
        f"challenges {'on' if config.get('contracts', {}).get('challenges_enabled') else 'off'}",
        "",
        "Ask me to change any of it — for example *allocate 500 GB*, "
        "*set parity shards to 6*, *disable challenges*, or *switch to claude*.",
    ]
    return "\n".join(lines)


# ── prompt construction ─────────────────────────────────────────────────

_SECTION_BRIEF = {
    "files": (
        "You are the Archivist, the Files section agent for a CollectiveFS node. "
        "Files are erasure-coded into Reed-Solomon shards, encrypted with Fernet, "
        "and spread across untrusted peers. Answer questions about stored files, "
        "folders, shard placement and recoverability."
    ),
    "system": (
        "You are the Infrastructure Steward, the System & Infrastructure agent for a "
        "CollectiveFS node. You correlate host resources (CPU, memory, network) with "
        "collective-specific state (quota headroom, shard durability, erasure fault "
        "budget, peer contracts) and you may change this node's configuration."
    ),
}


def build_prompt(
    section: str,
    message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    brief = _SECTION_BRIEF.get(section, _SECTION_BRIEF["system"])
    parts = [brief, ""]

    parts.append("## Live state")
    parts.append("```json")
    parts.append(json.dumps(context, indent=2, default=str)[:4000])
    parts.append("```")
    parts.append("")

    parts.append("## Current configuration")
    parts.append("```json")
    parts.append(json.dumps(config, indent=2))
    parts.append("```")
    parts.append("")

    if section == "system":
        parts.append("## Changing configuration")
        parts.append(
            "You can modify this node. To apply a change, end your reply with a single "
            'line: ACTION:{"type":"config.update","payload":{"<field>":<value>}}'
        )
        parts.append("Sizes may be plain byte counts or strings like \"500GB\".")
        parts.append("Writable fields:")
        for spec in describe_settings():
            bits = [f"- `{spec['field']}` ({spec['type']}) — {spec['label']}"]
            if spec.get("choices"):
                bits.append(f" one of {', '.join(spec['choices'])}")
            if spec.get("min") is not None:
                bits.append(f" min {spec['min']}")
            if spec.get("max") is not None:
                bits.append(f" max {spec['max']}")
            parts.append("".join(bits))
        parts.append("")
        parts.append(
            "Only emit an ACTION when the user actually asks for a change. Never emit "
            "more than one. Say plainly what you changed and why."
        )
        parts.append("")

    parts.append("## Rules")
    parts.append("1. Answer every message. Be concise — under 180 words unless asked for detail.")
    parts.append("2. Use the live state above rather than guessing.")
    parts.append("3. If something is unavailable, say so directly.")
    parts.append("")

    if history:
        parts.append("## Conversation so far")
        for entry in history[-10:]:
            role = entry.get("role", "user")
            content = (entry.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        parts.append("")

    parts.append("## User message")
    parts.append(message)
    return "\n".join(parts)


# ── orchestration ───────────────────────────────────────────────────────


async def run_chat(
    *,
    store: ConfigStore,
    section: str,
    message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    provider_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one chat turn, applying any configuration action it produces."""
    config = store.load()
    agent_cfg = config.get("agent", {})
    provider = (provider_override or agent_cfg.get("provider") or "codewhale").strip().lower()
    if provider not in AGENT_PROVIDERS:
        provider = "codewhale"
    model = (agent_cfg.get("model") or "").strip()
    timeout = int(agent_cfg.get("timeout_seconds") or 180)

    requested = provider
    reply = ""
    notes: List[str] = []

    if provider != "builtin":
        ok, text = await run_cli(provider, build_prompt(section, message, history, context, config), model, timeout)
        if ok and text:
            reply = text
        else:
            notes.append(text or f"{provider} returned nothing.")
            provider = "builtin"

    action: Optional[Dict[str, Any]] = None
    if provider == "builtin":
        action = interpret(message, config)
        if action is None:
            reply = summarize_state(context, config)
    else:
        action = extract_action(reply)
        reply = strip_action(reply)

    applied: List[Dict[str, Any]] = []
    error: Optional[str] = None
    navigate: Optional[str] = None

    if action and action.get("type") == "config.update":
        payload = action.get("payload") or {}
        try:
            config, applied = apply_updates(
                store,
                payload,
                source=f"chat:{section}",
                actor=f"agent:{requested}",
            )
        except ConfigError as exc:
            error = str(exc)
    elif action and action.get("type") == "navigate":
        path = (action.get("payload") or {}).get("path")
        if isinstance(path, str):
            navigate = path

    if provider == "builtin" and (applied or error):
        reply = _describe_result(applied, error, config)

    if not reply:
        reply = "No response."

    return {
        "reply": reply,
        "provider": provider,
        "provider_requested": requested,
        "fell_back": provider != requested,
        "notes": notes,
        "applied": applied,
        "error": error,
        "navigate": navigate,
        "config": config,
    }


def _fmt_value(change: Dict[str, Any], key: str) -> str:
    value = change.get(key)
    if change.get("type") == "bytes":
        return human_bytes(value)
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    return str(value)


def _describe_result(
    applied: List[Dict[str, Any]], error: Optional[str], config: Dict[str, Any]
) -> str:
    if error:
        return f"**Not applied.** {error}\n\nNothing was changed."
    if not applied:
        return "That setting is already at the requested value — nothing to change."
    lines = ["**Applied.**", ""]
    for change in applied:
        lines.append(
            f"- {change['label']} (`{change['field']}`): "
            f"{_fmt_value(change, 'before')} → **{_fmt_value(change, 'after')}**"
        )
    fields = {change["field"] for change in applied}
    if fields & {"erasure.data_shards", "erasure.parity_shards"}:
        erasure = config.get("erasure", {})
        lines.append("")
        lines.append(
            f"New uploads will use {erasure.get('data_shards')} data + "
            f"{erasure.get('parity_shards')} parity shards "
            f"(tolerates {erasure.get('parity_shards')} losses). "
            "Existing files keep the layout they were encoded with."
        )
    if "agent.provider" in fields:
        lines.append("")
        lines.append("Subsequent chat turns in every section will use the new provider.")
    return "\n".join(lines)
