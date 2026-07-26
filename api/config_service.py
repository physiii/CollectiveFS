"""Runtime configuration for a CollectiveFS node.

The System section's chat is allowed to *change* this file, not just read it, so
every mutation goes through :func:`apply_updates` which validates against the
real machine (you cannot pledge more space than the filesystem has) and appends
an audit record. Nothing here touches already-encoded files: erasure parameters
apply to subsequent uploads, matching how the encoder is invoked per upload.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Providers the agent layer knows how to drive. "builtin" is the deterministic
# in-process interpreter used as a fallback when no CLI is installed.
AGENT_PROVIDERS = ("codewhale", "claude", "codex", "builtin")

GIB = 1024 ** 3

DEFAULT_CONFIG: Dict[str, Any] = {
    "storage": {
        # Space this node pledges to the collective.
        "quota_bytes": 50 * GIB,
        # Headroom never handed out, so the host never fills to 100%.
        "reserve_bytes": 5 * GIB,
        # Refuse new writes above this fraction of the quota.
        "high_watermark_percent": 90,
    },
    "erasure": {
        "data_shards": 8,
        "parity_shards": 4,
    },
    "upload": {
        "max_file_bytes": 5 * GIB,
        "concurrency": 2,
    },
    "contracts": {
        "default_tier": "warm",
        "challenges_enabled": True,
        "max_peers": 32,
    },
    "agent": {
        "provider": "codewhale",
        "model": "",
        "timeout_seconds": 180,
    },
}

# Every field the chat/API is permitted to change, with its validator metadata.
# Keys are dotted paths so an agent can name them unambiguously.
FIELD_SPECS: Dict[str, Dict[str, Any]] = {
    "storage.quota_bytes": {
        "type": "bytes",
        "min": 1 * GIB,
        "label": "Storage allocated to the collective",
    },
    "storage.reserve_bytes": {
        "type": "bytes",
        "min": 0,
        "label": "Reserved free space",
    },
    "storage.high_watermark_percent": {
        "type": "int",
        "min": 50,
        "max": 100,
        "label": "Write cutoff watermark",
    },
    "erasure.data_shards": {
        "type": "int",
        "min": 1,
        "max": 32,
        "label": "Reed-Solomon data shards",
    },
    "erasure.parity_shards": {
        "type": "int",
        "min": 1,
        "max": 32,
        "label": "Reed-Solomon parity shards",
    },
    "upload.max_file_bytes": {
        "type": "bytes",
        "min": 1024 * 1024,
        "label": "Maximum upload size",
    },
    "upload.concurrency": {
        "type": "int",
        "min": 1,
        "max": 16,
        "label": "Concurrent encode jobs",
    },
    "contracts.default_tier": {
        "type": "enum",
        "choices": ["hot", "warm", "cold"],
        "label": "Default contract tier",
    },
    "contracts.challenges_enabled": {
        "type": "bool",
        "label": "Proof-of-storage challenges",
    },
    "contracts.max_peers": {
        "type": "int",
        "min": 1,
        "max": 512,
        "label": "Maximum peer contracts",
    },
    "agent.provider": {
        "type": "enum",
        "choices": list(AGENT_PROVIDERS),
        "label": "Agent provider",
    },
    "agent.model": {
        "type": "str",
        "label": "Agent model override",
    },
    "agent.timeout_seconds": {
        "type": "int",
        "min": 10,
        "max": 1800,
        "label": "Agent timeout",
    },
}

_lock = threading.RLock()


class ConfigError(ValueError):
    """Raised when a requested configuration change is not allowed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _flatten(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{path}."))
        else:
            out[path] = value
    return out


def _set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = data
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _get_path(data: Dict[str, Any], path: str) -> Any:
    cursor: Any = data
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


class ConfigStore:
    """Loads, validates and persists the node configuration."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "config.json"
        self.audit_path = self.root / "config-audit.jsonl"
        self._cache: Optional[Dict[str, Any]] = None

    # ── read ────────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        with _lock:
            if self._cache is not None:
                return json.loads(json.dumps(self._cache))
            data = dict(DEFAULT_CONFIG)
            if self.path.is_file():
                try:
                    stored = json.loads(self.path.read_text())
                    if isinstance(stored, dict):
                        data = _deep_merge(DEFAULT_CONFIG, stored)
                except (OSError, json.JSONDecodeError):
                    # A corrupt config must not take the node down; defaults win
                    # and the next successful write repairs the file.
                    data = dict(DEFAULT_CONFIG)
            # Overlay onto the stored values, not the defaults, or a saved config
            # would be thrown away on every load.
            data = _deep_merge(data, self._env_overrides(data))
            self._cache = data
            return json.loads(json.dumps(data))

    def _env_overrides(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Environment wins on first boot so compose files stay authoritative."""
        overrides: Dict[str, Any] = {}
        env_map = {
            "COLLECTIVE_QUOTA_BYTES": "storage.quota_bytes",
            "ENCODER_DATA_SHARDS": "erasure.data_shards",
            "ENCODER_PAR_SHARDS": "erasure.parity_shards",
            "AGENT_PROVIDER": "agent.provider",
            "AGENT_MODEL": "agent.model",
        }
        stored_exists = self.path.is_file()
        for env_key, field in env_map.items():
            raw = os.environ.get(env_key, "").strip()
            if not raw:
                continue
            # Once an operator has saved config, only the provider/model keep
            # following the environment; sizes stay under UI control.
            if stored_exists and not field.startswith("agent."):
                continue
            spec = FIELD_SPECS.get(field, {})
            try:
                value = _coerce(raw, spec)
            except ConfigError:
                continue
            _set_path(overrides, field, value)
        return overrides

    # ── write ───────────────────────────────────────────────────────────

    def save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with _lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            tmp.replace(self.path)
            self._cache = data
            return json.loads(json.dumps(data))

    def audit(self, entry: Dict[str, Any]) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Auditing is best-effort; never block a valid change.

    def recent_audit(self, limit: int = 25) -> List[Dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        try:
            lines = self.audit_path.read_text().strip().splitlines()
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        out.reverse()
        return out


# ── value coercion / validation ─────────────────────────────────────────

_UNITS = {
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024 ** 2,
    "mb": 1024 ** 2,
    "mib": 1024 ** 2,
    "g": 1024 ** 3,
    "gb": 1024 ** 3,
    "gib": 1024 ** 3,
    "t": 1024 ** 4,
    "tb": 1024 ** 4,
    "tib": 1024 ** 4,
    "p": 1024 ** 5,
    "pb": 1024 ** 5,
    "pib": 1024 ** 5,
}


def parse_size(raw: Any) -> int:
    """Accept 500, "500", "500GB", "1.5 TiB" -> bytes."""
    if isinstance(raw, bool):
        raise ConfigError("expected a size, got a boolean")
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip().lower().replace(",", "")
    if not text:
        raise ConfigError("empty size")
    number = ""
    unit = ""
    for index, char in enumerate(text):
        if char.isdigit() or char == ".":
            number += char
        else:
            unit = text[index:].strip()
            break
    if not number:
        raise ConfigError(f"could not read a number from {raw!r}")
    multiplier = _UNITS.get(unit.replace(" ", ""), 1) if unit else 1
    return int(float(number) * multiplier)


def _coerce(raw: Any, spec: Dict[str, Any]) -> Any:
    kind = spec.get("type", "str")
    if kind == "bytes":
        return parse_size(raw)
    if kind == "int":
        if isinstance(raw, bool):
            raise ConfigError("expected a number, got a boolean")
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ConfigError(f"expected a number, got {raw!r}")
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "yes", "on", "1", "enable", "enabled"):
            return True
        if text in ("false", "no", "off", "0", "disable", "disabled"):
            return False
        raise ConfigError(f"expected true/false, got {raw!r}")
    if kind == "enum":
        text = str(raw).strip().lower()
        choices = spec.get("choices", [])
        if text not in choices:
            raise ConfigError(f"expected one of {', '.join(choices)}, got {raw!r}")
        return text
    return str(raw).strip()


def _check_bounds(field: str, value: Any, spec: Dict[str, Any]) -> None:
    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{field} must be at most {maximum}")


def _filesystem_total(root: Path) -> int:
    try:
        return shutil.disk_usage(str(root)).total
    except OSError:
        return 0


def validate(candidate: Dict[str, Any], root: Path) -> None:
    """Cross-field checks that a single value cannot express on its own."""
    quota = int(_get_path(candidate, "storage.quota_bytes") or 0)
    reserve = int(_get_path(candidate, "storage.reserve_bytes") or 0)
    data_shards = int(_get_path(candidate, "erasure.data_shards") or 0)
    parity_shards = int(_get_path(candidate, "erasure.parity_shards") or 0)
    max_upload = int(_get_path(candidate, "upload.max_file_bytes") or 0)

    total = _filesystem_total(root)
    if total and quota > total:
        raise ConfigError(
            f"storage.quota_bytes ({human_bytes(quota)}) exceeds the filesystem "
            f"size at {root} ({human_bytes(total)})"
        )
    if reserve >= quota:
        raise ConfigError(
            f"storage.reserve_bytes ({human_bytes(reserve)}) must be smaller than "
            f"storage.quota_bytes ({human_bytes(quota)})"
        )
    if data_shards + parity_shards > 40:
        raise ConfigError("erasure.data_shards + erasure.parity_shards must be 40 or fewer")
    if max_upload > quota:
        raise ConfigError(
            f"upload.max_file_bytes ({human_bytes(max_upload)}) cannot exceed the "
            f"storage quota ({human_bytes(quota)})"
        )


def human_bytes(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    value = float(value)
    if abs(value) < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024
        if abs(value) < 1024:
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
    return f"{value:.1f} EB"


# ── public API ──────────────────────────────────────────────────────────


def apply_updates(
    store: ConfigStore,
    updates: Dict[str, Any],
    *,
    source: str = "api",
    actor: str = "operator",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Validate and persist dotted-path updates. Returns (config, changes).

    Raises :class:`ConfigError` without writing anything if any field is
    rejected, so a bad instruction can never leave a half-applied config.
    """
    if not updates:
        raise ConfigError("no changes requested")

    with _lock:
        current = store.load()
        candidate = json.loads(json.dumps(current))
        changes: List[Dict[str, Any]] = []

        for field, raw in updates.items():
            spec = FIELD_SPECS.get(field)
            if spec is None:
                raise ConfigError(
                    f"unknown setting {field!r}. Known settings: "
                    + ", ".join(sorted(FIELD_SPECS))
                )
            value = _coerce(raw, spec)
            _check_bounds(field, value, spec)
            before = _get_path(candidate, field)
            if before == value:
                continue
            _set_path(candidate, field, value)
            changes.append(
                {
                    "field": field,
                    "label": spec.get("label", field),
                    "type": spec.get("type", "str"),
                    "before": before,
                    "after": value,
                }
            )

        if not changes:
            return current, []

        validate(candidate, store.root)
        saved = store.save(candidate)
        store.audit(
            {
                "id": str(uuid.uuid4()),
                "at": _now(),
                "source": source,
                "actor": actor,
                "changes": changes,
            }
        )
        return saved, changes


def describe_settings() -> List[Dict[str, Any]]:
    """Machine-readable schema, handed to the agent so it can name fields."""
    out = []
    for field, spec in FIELD_SPECS.items():
        entry = {"field": field, **spec}
        out.append(entry)
    return out


def flatten_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return _flatten(config)
