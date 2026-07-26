"""Account tokens.

A token is the whole identity: files are stored under one, and anyone holding it
sees the same filesystem from any node. There are no users, no passwords and no
per-node accounts — that keeps a mount's configuration to a single secret and
lets the same namespace appear on every machine that knows it.

The token is carried in `X-CFS-Token` (or `Authorization: Bearer …`). Requests
without one fall back to the node's default token, so a single-node install and
the web console keep working untouched.
"""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

TOKEN_HEADER = "x-cfs-token"
DEFAULT_LABEL = "default"

_lock = threading.RLock()


def new_token() -> str:
    """URL-safe, 32 bytes of entropy. Long enough to be the only credential."""
    return secrets.token_urlsafe(32)


class AccountStore:
    """Tokens known to this node, and which one unauthenticated calls get."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "accounts.json"
        self._cache: Optional[Dict[str, Any]] = None

    def _load_raw(self) -> Dict[str, Any]:
        with _lock:
            if self._cache is not None:
                return self._cache
            data: Dict[str, Any] = {"default": "", "tokens": {}}
            if self.path.is_file():
                try:
                    stored = json.loads(self.path.read_text())
                    if isinstance(stored, dict):
                        data = {**data, **stored}
                except (OSError, json.JSONDecodeError):
                    pass  # A damaged file must not lock the node out.
            if not data.get("default"):
                token = new_token()
                data["default"] = token
                data.setdefault("tokens", {})[token] = {"label": DEFAULT_LABEL}
                self._write(data)
            self._cache = data
            return data

    def _write(self, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self._cache = data

    # ── public ──────────────────────────────────────────────────────

    def default_token(self) -> str:
        return self._load_raw()["default"]

    def known(self, token: str) -> bool:
        return token in self._load_raw().get("tokens", {})

    def resolve(self, header_value: Optional[str]) -> str:
        """Token for this request, defaulting to the node's own.

        An unknown token is *not* rejected: a node may legitimately be asked for
        a namespace it has never seen (a peer's account, or a brand new one).
        It simply scopes to that token and returns whatever it has, which is
        usually nothing.
        """
        raw = (header_value or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        return raw or self.default_token()

    def register(self, token: str, label: str = "") -> Dict[str, Any]:
        with _lock:
            data = json.loads(json.dumps(self._load_raw()))
            data.setdefault("tokens", {})[token] = {"label": label or "imported"}
            self._write(data)
            return data["tokens"][token]

    def create(self, label: str = "") -> str:
        token = new_token()
        self.register(token, label)
        return token

    def list_tokens(self) -> List[Dict[str, Any]]:
        data = self._load_raw()
        default = data["default"]
        return [
            {
                "token": token,
                "label": meta.get("label", ""),
                "default": token == default,
            }
            for token, meta in data.get("tokens", {}).items()
        ]


def scope(files: List[Dict[str, Any]], token: str, default_token: str) -> List[Dict[str, Any]]:
    """Keep only the files belonging to `token`.

    Files written before tokens existed have none; they belong to the node's
    default account so nothing becomes invisible after an upgrade.
    """
    out = []
    for item in files:
        owner = item.get("token") or default_token
        if owner == token:
            out.append(item)
    return out
