"""VelocityAuth bot 协议 v1 的文本层实现。

对应 docs/PROTOCOL.md 第 2/3/6/7 节：规范化、签名输入、内层明文与解析。
本模块只做纯文本处理，不涉及密钥与网络。
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

VERSION = 1
HANDSHAKE_PREFIX = "VELOCITYAUTH-V1"
MESSAGE_PREFIX = "VELOCITYAUTH-MSG-V1"
CAPABILITY_VERIFY = "verify.v1"
CAPABILITY_QUERY = "query.v1"


class ProtocolError(Exception):
    """协议层错误（帧结构、字段缺失等）。"""


def b64u(data: bytes) -> str:
    """base64url（无 padding）编码。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    """base64url（可带 padding）解码。"""
    if isinstance(text, bytes):
        text = text.decode("ascii")
    raw = text.strip()
    if not raw:
        return b""
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_b64u(data: bytes) -> str:
    return b64u(sha256(data))


def render_value(value: Any) -> str:
    """canonical() 的取值渲染规则（PROTOCOL.md 3.2）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.replace("\r", " ").replace("\n", " ")
    if isinstance(value, (list, tuple)):
        return ",".join(sorted(render_value(item) for item in value))
    raise ProtocolError(f"payload 仅支持扁平类型，收到 {type(value).__name__}")


def canonical(payload: dict[str, Any]) -> str:
    """key 按 UTF-8 字节序升序，逐行 ``key=value``。"""
    return "".join(f"{key}={render_value(payload[key])}\n" for key in sorted(payload))


def signature_input(
    context: str,
    type_: str,
    id_: str,
    ts: int,
    nonce: str,
    payload: dict[str, Any],
    code: str | None = None,
) -> str:
    """握手帧的签名输入（PROTOCOL.md 3.3）。"""
    text = f"{HANDSHAKE_PREFIX}\n"
    text += f"context={context}\n"
    text += f"type={type_}\n"
    text += f"id={id_}\n"
    text += f"ts={ts}\n"
    text += f"nonce={nonce}\n"
    if code is not None:
        text += f"code={code}\n"
    return text + canonical(payload)


def message_plaintext(type_: str, id_: str, ts: int, payload: dict[str, Any]) -> bytes:
    """密封帧内层明文（PROTOCOL.md 7）。"""
    text = f"{MESSAGE_PREFIX}\n"
    text += f"type={type_}\n"
    text += f"id={id_}\n"
    text += f"ts={ts}\n"
    return (text + canonical(payload)).encode("utf-8")


def capabilities_sha256(capabilities: list[str]) -> str:
    """``sha256_b64u("\\n".join(sorted(capabilities)))``。"""
    return sha256_b64u("\n".join(sorted(capabilities)).encode("utf-8"))


@dataclass(slots=True)
class ParsedMessage:
    """解开的密封帧内层消息；payload 的值全部是字符串。"""

    type: str
    id: str
    ts: int
    payload: dict[str, str] = field(default_factory=dict)

    def text(self, key: str, default: str = "") -> str:
        return self.payload.get(key, default)

    def number(self, key: str, default: int = 0) -> int:
        raw = self.payload.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self.payload.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"true", "1", "yes"}


def parse_message(plaintext: bytes) -> ParsedMessage:
    """解析密封帧内层明文（PROTOCOL.md 7）。"""
    text = plaintext.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not lines or lines[0] != MESSAGE_PREFIX:
        raise ProtocolError("内层明文前缀不匹配")
    type_: str | None = None
    id_: str | None = None
    ts = 0
    payload: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        key, _, value = line.partition("=")
        if key == "type":
            type_ = value
        elif key == "id":
            id_ = value
        elif key == "ts":
            ts = int(value) if value.isdigit() else 0
        else:
            payload[key] = value
    if type_ is None or id_ is None:
        raise ProtocolError("内层明文缺少 type/id")
    return ParsedMessage(type=type_, id=id_, ts=ts, payload=payload)


@dataclass(slots=True)
class Frame:
    """一条 WebSocket 帧（明文握手帧或密封帧）。"""

    kind: str
    type: str = ""
    id: str = ""
    ts: int = 0
    nonce: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sig: str = ""
    seq: int = 0
    ct: str = ""

    @property
    def sealed(self) -> bool:
        return self.kind == "sealed"

    def text(self, key: str, default: str = "") -> str:
        value = self.payload.get(key)
        if value is None:
            return default
        return str(value)

    def number(self, key: str, default: int = 0) -> int:
        value = self.payload.get(key)
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    def flag(self, key: str, default: bool = False) -> bool:
        value = self.payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return default

    def strings(self, key: str) -> list[str]:
        value = self.payload.get(key)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [part for part in (item.strip() for item in value.split(",")) if part]
        return []


def parse_frame(text: str) -> Frame:
    """解析服务端帧；kind 非 sealed 时按明文握手帧处理。"""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"帧不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ProtocolError("帧不是 JSON 对象")
    if raw.get("v") != VERSION:
        raise ProtocolError(f"协议版本不支持：{raw.get('v')!r}")
    if raw.get("kind") == "sealed":
        return Frame(kind="sealed", seq=int(raw.get("seq", 0)), ct=str(raw.get("ct", "")))
    payload = raw.get("payload") or {}
    if not isinstance(payload, dict):
        raise ProtocolError("payload 必须是扁平对象")
    return Frame(
        kind="plain",
        type=str(raw.get("type", "")),
        id=str(raw.get("id", "")),
        ts=int(raw.get("ts", 0)),
        nonce=str(raw.get("nonce", "")),
        payload=payload,
        sig=str(raw.get("sig", "")),
    )
