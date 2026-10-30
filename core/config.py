"""插件配置解析：全局设置 + 服务器模板列表。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .models import ServerTemplate

DEFAULT_SUCCESS_TEXT = "✅ 验证成功，请在游戏内继续操作。"
DEFAULT_FAILED_TEXT = "❌ 验证码无效或已过期。"
MATCH_MODES = ("contains", "exact")


@dataclass(slots=True)
class ConnectSettings:
    reconnect_initial_seconds: int = 3
    reconnect_max_seconds: int = 300
    heartbeat_seconds: int = 30
    request_timeout_seconds: int = 15


@dataclass(slots=True)
class CommandSettings:
    require_astrbot_admin: bool = False
    max_list_rows: int = 20


@dataclass(slots=True)
class RenderSettings:
    enabled: bool = True
    font_paths: tuple[str, ...] = ()
    auto_download_font: bool = True


@dataclass(slots=True)
class PluginSettings:
    render: RenderSettings = field(default_factory=RenderSettings)
    connect: ConnectSettings = field(default_factory=ConnectSettings)
    command: CommandSettings = field(default_factory=CommandSettings)
    servers: tuple[ServerTemplate, ...] = ()

    def server(self, name: str) -> ServerTemplate | None:
        for template in self.servers:
            if template.name == name:
                return template
        return None

    def default_server(self) -> ServerTemplate | None:
        return self.servers[0] if self.servers else None


def load_settings(config: Mapping[str, Any] | None) -> PluginSettings:
    raw = _as_mapping(config)
    connect_raw = _as_mapping(raw.get("connect"))
    command_raw = _as_mapping(raw.get("command"))
    render_raw = _as_mapping(raw.get("render"))
    servers = tuple(
        template
        for template in (
            _parse_template(index, item)
            for index, item in enumerate(_as_list(raw.get("servers")), start=1)
        )
        if template is not None
    )
    return PluginSettings(
        render=RenderSettings(
            enabled=_as_bool(render_raw.get("enabled"), default=True),
            font_paths=tuple(
                item
                for item in (
                    _as_str(path) for path in _as_list(render_raw.get("font_paths"))
                )
                if item
            ),
            auto_download_font=_as_bool(
                render_raw.get("auto_download_font"), default=True
            ),
        ),
        connect=ConnectSettings(
            reconnect_initial_seconds=_clamp_int(
                connect_raw.get("reconnect_initial_seconds"), default=3, minimum=1, maximum=300
            ),
            reconnect_max_seconds=_clamp_int(
                connect_raw.get("reconnect_max_seconds"), default=300, minimum=5, maximum=3600
            ),
            heartbeat_seconds=_clamp_int(
                connect_raw.get("heartbeat_seconds"), default=30, minimum=0, maximum=600
            ),
            request_timeout_seconds=_clamp_int(
                connect_raw.get("request_timeout_seconds"), default=15, minimum=3, maximum=120
            ),
        ),
        command=CommandSettings(
            require_astrbot_admin=_as_bool(
                command_raw.get("require_astrbot_admin"), default=False
            ),
            max_list_rows=_clamp_int(
                command_raw.get("max_list_rows"), default=20, minimum=1, maximum=200
            ),
        ),
        servers=servers,
    )


def _parse_template(index: int, value: Any) -> ServerTemplate | None:
    raw = _as_mapping(value)
    name = _as_str(raw.get("name")) or f"服务器 {index}"
    host = _as_str(raw.get("host"))
    if not host:
        logger.warning("[VelocityAuth] 模板 %s 未配置服务器地址，已跳过。", name)
        return None

    match_mode = _as_str(raw.get("match_mode")).lower()
    if match_mode not in MATCH_MODES:
        match_mode = MATCH_MODES[0]

    verify_umos = _as_str_list(raw.get("verify_umos"))
    admin_umos = _as_str_list(raw.get("admin_umos"))
    if not verify_umos and not admin_umos:
        logger.warning(
            "[VelocityAuth] 模板 %s 既未配置验证会话也未配置管理会话，仍会建立连接。", name
        )

    return ServerTemplate(
        index=index,
        name=name,
        enabled=_as_bool(raw.get("enabled"), default=True),
        host=host,
        port=_clamp_int(raw.get("port"), default=8765, minimum=1, maximum=65535),
        client_id=_as_str(raw.get("client_id")) or "astrbot-1",
        client_key_id=_as_str(raw.get("client_key_id")) or "astrbot-k1",
        provider=_as_str(raw.get("provider")) or "qq",
        pairing_id=_as_str(raw.get("pairing_id")),
        pairing_code=_as_str(raw.get("pairing_code")),
        verify_umos=verify_umos,
        admin_umos=admin_umos,
        match_mode=match_mode,
        success_text=_as_text(raw.get("success_text"), DEFAULT_SUCCESS_TEXT),
        failed_text=_as_text(raw.get("failed_text"), DEFAULT_FAILED_TEXT),
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if value is None or value == "":
        return []
    return [value]


def _as_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_str_list(value: Any) -> tuple[str, ...]:
    items: list[str] = []
    for item in _as_list(value):
        text = _as_str(item)
        if text and text not in items:
            items.append(text)
    return tuple(items)


def _as_text(value: Any, default: str) -> str:
    return default if value is None else str(value).strip()


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on", "启用", "是"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", "禁用", "否"}:
            return False
    return bool(value)


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def iter_umos(templates: Iterable[ServerTemplate]) -> list[str]:
    """所有模板里出现过的会话 UMO。"""
    seen: list[str] = []
    for template in templates:
        for umo in (*template.verify_umos, *template.admin_umos):
            if umo not in seen:
                seen.append(umo)
    return seen
