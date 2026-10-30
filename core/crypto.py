"""密钥、密钥派生与密封帧实现（PROTOCOL.md 6/7 节）。"""

from __future__ import annotations

import hmac
import os
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .protocol import b64u, sha256

SALT_LABEL = b"VELOCITYAUTH-V1-SALT"
INFO_C2S = b"velocityauth c2s"
INFO_S2C = b"velocityauth s2c"
SEAL_AAD_PREFIX = "VELOCITYAUTH-SEAL-V1"


class CryptoError(Exception):
    """密钥或密封帧相关的错误。"""


@dataclass(slots=True)
class KeySchedule:
    """HKDF 派生的会话密钥与会话密钥确认用的中间值。"""

    shared: bytes
    salt: bytes
    prk: bytes
    key_c2s: bytes
    key_s2c: bytes


def generate_seed() -> bytes:
    return os.urandom(32)


def ed25519_from_seed(seed: bytes) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise CryptoError("Ed25519 种子必须是 32 字节")
    return Ed25519PrivateKey.from_private_bytes(seed)


def raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def ed25519_public_from_raw(raw: bytes) -> Ed25519PublicKey:
    if len(raw) != 32:
        raise CryptoError("Ed25519 公钥必须是 32 字节")
    return Ed25519PublicKey.from_public_bytes(raw)


def sign(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    return private_key.sign(data)


def verify(public_key: Ed25519PublicKey, signature: bytes, data: bytes) -> bool:
    try:
        public_key.verify(signature, data)
    except InvalidSignature:
        return False
    return True


@dataclass(slots=True)
class EphemeralKeyPair:
    """每次连接新生成的 X25519 临时密钥对（前向保密）。"""

    private_key: X25519PrivateKey
    raw_public: bytes

    @classmethod
    def generate(cls) -> EphemeralKeyPair:
        private_key = X25519PrivateKey.generate()
        raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(private_key=private_key, raw_public=raw)

    def shared_secret(self, peer_raw: bytes) -> bytes:
        if len(peer_raw) != 32:
            raise CryptoError("X25519 公钥必须是 32 字节")
        return self.private_key.exchange(X25519PublicKey.from_public_bytes(peer_raw))


def derive_session_keys(
    client_nonce: bytes, server_nonce: bytes, shared: bytes
) -> KeySchedule:
    """HKDF-SHA256 会话密钥派生（PROTOCOL.md 6）。"""
    salt = sha256(SALT_LABEL + client_nonce + server_nonce)
    prk = hmac.new(salt, shared, "sha256").digest()

    def expand(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(
            shared
        )

    return KeySchedule(
        shared=shared,
        salt=salt,
        prk=prk,
        key_c2s=expand(INFO_C2S),
        key_s2c=expand(INFO_S2C),
    )


def _nonce(epoch: int, seq: int) -> bytes:
    return struct.pack(">IQ", epoch & 0xFFFFFFFF, seq & 0xFFFFFFFFFFFFFFFF)


def _aad(seq: int) -> bytes:
    return f"{SEAL_AAD_PREFIX}\nv=1\nseq={seq}\n".encode()


class OutboundSealer:
    """发送方向：seq 从 0 单调递增。"""

    def __init__(self, key: bytes, epoch: int = 0) -> None:
        self._aead = AESGCM(key)
        self._epoch = epoch
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def seal(self, plaintext: bytes) -> tuple[int, bytes]:
        seq = self._seq
        self._seq += 1
        return seq, self._aead.encrypt(_nonce(self._epoch, seq), plaintext, _aad(seq))


class InboundSealer:
    """接收方向：``seq <= last_seq`` 一律拒绝（防重放）。"""

    def __init__(self, key: bytes, epoch: int = 0) -> None:
        self._aead = AESGCM(key)
        self._epoch = epoch
        self._last_seq = -1

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def open(self, seq: int, ciphertext: bytes) -> bytes:
        if seq <= self._last_seq:
            raise CryptoError(f"seq 重放：{seq} <= {self._last_seq}")
        if not ciphertext:
            raise CryptoError("密封帧缺少密文")
        try:
            plaintext = self._aead.decrypt(
                _nonce(self._epoch, seq), ciphertext, _aad(seq)
            )
        except InvalidTag as exc:
            raise CryptoError("密封帧解密失败（tag 校验不通过）") from exc
        self._last_seq = seq
        return plaintext


def fingerprint(raw_public: bytes) -> str:
    """公钥指纹：``sha256(裸公钥)`` 的 base64url。"""
    return b64u(sha256(raw_public))
