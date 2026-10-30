"""身份与配对状态的持久化。

每个模板保存一份 bot 侧 Ed25519 身份与服务端公钥 pin：
`data/plugin_data/astrbot_plugin_velocity_auth/servers.json`
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .crypto import generate_seed
from .protocol import b64u, b64u_decode

STATE_VERSION = 1


@dataclass(slots=True)
class ServerState:
    """单个模板的持久状态。"""

    client_id: str = ""
    client_key_id: str = ""
    client_seed: str = ""
    server_public_key: str = ""
    server_key_id: str = ""
    server_id: str = ""
    server_fingerprint: str = ""
    client_fingerprint: str = ""
    paired_at: int = 0

    @property
    def paired(self) -> bool:
        return bool(self.server_public_key)

    def seed_bytes(self) -> bytes:
        return b64u_decode(self.client_seed)

    def server_key_bytes(self) -> bytes:
        return b64u_decode(self.server_public_key)


class StateStore:
    """``servers.json`` 的读写；写入使用 0600 权限（协议安全要求 11）。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._servers: dict[str, ServerState] = {}
        self.load()

    def load(self) -> None:
        self._servers = {}
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[VelocityAuth] 读取状态文件失败：%s", exc)
            return
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            logger.warning("[VelocityAuth] 状态文件版本不兼容，已忽略：%s", self._path)
            return
        for key, value in (raw.get("servers") or {}).items():
            if not isinstance(value, dict):
                continue
            state = ServerState()
            for field_name in ServerState.__slots__:
                if field_name in value:
                    setattr(state, field_name, value[field_name])
            self._servers[str(key)] = state

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "servers": {key: asdict(state) for key, state in self._servers.items()},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.chmod(temp, 0o600)
            temp.replace(self._path)
        except OSError as exc:
            logger.warning("[VelocityAuth] 写入状态文件失败：%s", exc)

    def get(self, key: str) -> ServerState:
        return self._servers.get(key) or ServerState()

    def get_or_create(self, key: str, client_id: str, client_key_id: str) -> ServerState:
        """取出身份；`client_id` / `client_key_id` 变化时重新生成密钥对。"""
        state = self._servers.get(key)
        if state is None:
            state = ServerState(client_seed=b64u(generate_seed()))
            self._servers[key] = state
        if not state.client_seed:
            state.client_seed = b64u(generate_seed())
        if state.client_id != client_id or state.client_key_id != client_key_id:
            if state.client_id or state.client_key_id:
                logger.warning(
                    "[VelocityAuth] 模板 %s 的身份标识已变更（%s/%s → %s/%s），"
                    "将生成新密钥并在服务端重新配对。",
                    key,
                    state.client_id,
                    state.client_key_id,
                    client_id,
                    client_key_id,
                )
                state.server_public_key = ""
                state.server_key_id = ""
                state.server_id = ""
                state.server_fingerprint = ""
                state.paired_at = 0
            state.client_id = client_id
            state.client_key_id = client_key_id
        return state

    def update(self, key: str, **fields: Any) -> ServerState:
        state = self._servers.setdefault(key, ServerState())
        for field_name, value in fields.items():
            if field_name in ServerState.__slots__:
                setattr(state, field_name, value)
        return state
