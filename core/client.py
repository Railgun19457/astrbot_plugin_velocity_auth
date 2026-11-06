"""VelocityAuth bot 协议客户端：配对、会话认证、业务消息与查询。

一条连接的生命周期由 :meth:`VelocityAuthClient.run` 负责；断线重连由上层
(``ServerManager``) 监督。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from enum import Enum
from typing import Any

import aiohttp
from astrbot.api import logger

from .config import ConnectSettings
from .crypto import (
    CryptoError,
    EphemeralKeyPair,
    InboundSealer,
    OutboundSealer,
    derive_session_keys,
    ed25519_from_seed,
    ed25519_public_from_raw,
    fingerprint,
    raw_public_key,
    sign,
    verify,
)
from .models import ServerTemplate
from .protocol import (
    CAPABILITY_ADMIN,
    CAPABILITY_QUERY,
    CAPABILITY_VERIFY,
    VERSION,
    Frame,
    ParsedMessage,
    ProtocolError,
    b64u,
    b64u_decode,
    capabilities_sha256,
    message_plaintext,
    parse_frame,
    parse_message,
    sha256_b64u,
    signature_input,
)
from .state import ServerState, StateStore

HANDSHAKE_TIMEOUT_SECONDS = 10.0
CLOSE_SENTINEL = object()


class ClientState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    PAIRING = "pairing"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    CLOSED = "closed"


class HandshakeError(Exception):
    """握手阶段失败（协议错误、验签失败、服务端拒绝等）。"""


class QueryError(Exception):
    """服务端返回的 query 错误或超时。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ClientHooks:
    """客户端事件回调；由 ``ServerManager`` 实现。"""

    async def on_ready(self, client: VelocityAuthClient) -> None:
        """握手完成、会话建立。"""

    async def on_paired(self, client: VelocityAuthClient) -> None:
        """首次配对完成（服务端公钥已写入本地 pin）。"""

    async def on_closed(self, client: VelocityAuthClient, reason: str) -> None:
        """连接结束（正常或异常）。"""

    async def on_verify_watch(self, client: VelocityAuthClient, code: str, ttl: int) -> bool:
        """服务端登记验证码；返回是否接受。"""
        return True

    async def on_verify_cancel(self, client: VelocityAuthClient, code: str) -> None:
        """服务端取消验证码。"""

    async def on_matched_ack(
        self, client: VelocityAuthClient, code: str, consumed: bool
    ) -> None:
        """服务端已消费（或拒绝）命中事件。"""


class VelocityAuthClient:
    """单个 VelocityAuth 服务器模板的连接。"""

    def __init__(
        self,
        template: ServerTemplate,
        store: StateStore,
        settings: ConnectSettings,
        hooks: ClientHooks,
    ) -> None:
        self.template = template
        self.settings = settings
        self.hooks = hooks
        self._store = store
        self._state = ClientState.IDLE
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._frames: asyncio.Queue[Any] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._counter = 0
        self._pending: dict[str, asyncio.Future[ParsedMessage]] = {}
        self._close_reason = ""

        self._server_state: ServerState = store.get(template.key)
        self._identity_seed = b""
        self._identity_private = None
        self._identity_public = b""
        self._ephemeral: EphemeralKeyPair | None = None
        self._outbound: OutboundSealer | None = None
        self._inbound: InboundSealer | None = None

        self.session_id = ""
        self.server_id = ""
        self.server_key_id = ""
        self.server_capabilities: tuple[str, ...] = ()
        self.connected_at = 0.0
        self.last_pong_at = 0.0
        self.latency_ms = 0
        self.attempts = 0

    # ------------------------------------------------------------------ 属性

    @property
    def client_state(self) -> ClientState:
        return self._state

    @property
    def ready(self) -> bool:
        return self._state == ClientState.READY

    @property
    def paired(self) -> bool:
        return self._server_state.paired

    @property
    def pinned_fingerprint(self) -> str:
        return self._server_state.server_fingerprint

    @property
    def supports_query(self) -> bool:
        return CAPABILITY_QUERY in self.server_capabilities

    @property
    def supports_admin(self) -> bool:
        return CAPABILITY_ADMIN in self.server_capabilities

    @property
    def close_reason(self) -> str:
        return self._close_reason

    # ------------------------------------------------------------------ 生命周期

    async def run(self) -> str:
        """建立连接并处理消息，直到连接结束；返回关闭原因。"""
        self._state = ClientState.CONNECTING
        self._close_reason = ""
        self.attempts += 1
        while not self._frames.empty():
            self._frames.get_nowait()
        try:
            await self._connect()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            self._state = ClientState.CLOSED
            self._close_reason = f"连接 {self.template.address} 失败：{exc}"
            return self._close_reason

        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self._handshake()
        except (HandshakeError, ProtocolError, CryptoError, asyncio.TimeoutError) as exc:
            self._close_reason = f"握手失败：{exc}"
            logger.warning(
                "[VelocityAuth] 模板 %s %s", self.template.name, self._close_reason
            )
            await self._shutdown()
            return self._close_reason
        except asyncio.CancelledError:
            await self._shutdown()
            raise

        self._state = ClientState.READY
        self.connected_at = time.time()
        self.last_pong_at = self.connected_at
        self._close_reason = ""
        logger.info(
            "[VelocityAuth] 模板 %s 已连接 %s（session=%s capabilities=%s）",
            self.template.name,
            self.template.address,
            self.session_id,
            ",".join(self.server_capabilities) or "-",
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        with contextlib.suppress(Exception):
            await self.hooks.on_ready(self)

        try:
            await self._business_loop()
        except asyncio.CancelledError:
            raise
        finally:
            self._state = ClientState.CLOSED
            await self._shutdown()
        return self._close_reason or "连接已关闭"

    async def close(self) -> None:
        """主动关闭连接（重连前调用）。"""
        self._close_reason = self._close_reason or "已主动关闭"
        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        # 唤醒业务循环
        await self._frames.put(CLOSE_SENTINEL)

    # ------------------------------------------------------------------ 连接

    async def _connect(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
        session = aiohttp.ClientSession(timeout=timeout)
        url = f"ws://{self.template.host}:{self.template.port}"
        try:
            self._ws = await session.ws_connect(
                url,
                heartbeat=None,
                max_msg_size=4 * 1024 * 1024,
                autoclose=True,
            )
        except BaseException:
            await session.close()
            raise
        self._http_session = session

    async def _shutdown(self) -> None:
        self._state = ClientState.CLOSED
        for attr in ("_heartbeat_task", "_reader_task"):
            task: asyncio.Task[None] | None = getattr(self, attr)
            if task is None:
                continue
            setattr(self, attr, None)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        session, self._http_session = self._http_session, None
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(QueryError("connection_closed", "连接已关闭"))
        self._pending.clear()
        if self._close_reason:
            with contextlib.suppress(Exception):
                await self.hooks.on_closed(self, self._close_reason)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self._frames.put(message.data)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    self._close_reason = f"WebSocket 错误：{self._ws.exception()}"
                    break
                elif message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 读循环异常统一上报
            self._close_reason = f"读取失败：{exc}"
        finally:
            if not self._close_reason:
                code = getattr(self._ws, "close_code", None)
                reason = getattr(self._ws, "close_reason", "") or ""
                self._close_reason = f"服务端关闭连接（code={code} {reason}）".strip()
            await self._frames.put(CLOSE_SENTINEL)

    async def _next_frame(self, timeout: float) -> Frame:
        item = await asyncio.wait_for(self._frames.get(), timeout=timeout)
        if item is CLOSE_SENTINEL:
            raise HandshakeError(self._close_reason or "连接已关闭")
        return parse_frame(item)

    # ------------------------------------------------------------------ 握手

    async def _handshake(self) -> None:
        state = self._store.get_or_create(
            self.template.key, self.template.client_id, self.template.client_key_id
        )
        self._server_state = state
        self._identity_seed = state.seed_bytes()
        self._identity_private = ed25519_from_seed(self._identity_seed)
        self._identity_public = raw_public_key(self._identity_private)
        self._ephemeral = EphemeralKeyPair.generate()
        self._counter = 0

        if state.paired:
            if self.template.pairing_id or self.template.pairing_code:
                logger.info(
                    "[VelocityAuth] 模板 %s 已完成配对，忽略配置中的 pairing_id/pairing_code。",
                    self.template.name,
                )
            await self._authenticate()
        else:
            if not (self.template.pairing_id and self.template.pairing_code):
                raise HandshakeError(
                    "尚未配对：请在配置中填写 pairing_id 与 pairing_code"
                    "（服主执行 /velocityauth pair new 获取）"
                )
            await self._pair()
            self._store.save()
            await self._authenticate()

    async def _pair(self) -> None:
        self._state = ClientState.PAIRING
        code = self.template.pairing_code
        hello_id, hello_nonce = self._new_id(), b64u(self._random(16))
        payload: dict[str, Any] = {
            "pairing_id": self.template.pairing_id,
            "client_id": self.template.client_id,
            "client_key_id": self.template.client_key_id,
            "client_public_key": b64u(self._identity_public),
            "provider": self.template.provider,
            "scopes": self.template.scopes(),
            "capabilities": self._capabilities(),
        }
        await self._send_plain("pair.hello", payload, code, hello_id, hello_nonce)

        challenge = await self._next_frame(HANDSHAKE_TIMEOUT_SECONDS)
        if challenge.type == "error":
            raise HandshakeError(self._error_text(challenge))
        if challenge.type != "pair.challenge":
            raise HandshakeError(f"期望 pair.challenge，收到 {challenge.type or challenge.kind}")

        server_public_key = b64u_decode(challenge.text("server_public_key"))
        self._verify_server_frame(challenge, server_public_key, code)
        server_id = challenge.text("server_id")
        server_key_id = challenge.text("server_key_id")
        challenge_id = challenge.text("challenge_id")
        server_nonce = challenge.text("server_nonce")
        server_capabilities = challenge.strings("capabilities")

        await self._send_plain(
            "pair.prove",
            {
                "pairing_id": challenge.text("pairing_id"),
                "challenge_id": challenge_id,
                "hello_id": hello_id,
                "client_id": self.template.client_id,
                "client_key_id": self.template.client_key_id,
                "client_public_key_sha256": sha256_b64u(self._identity_public),
                "server_id": server_id,
                "server_key_id": server_key_id,
                "server_public_key_sha256": sha256_b64u(server_public_key),
                "client_nonce": hello_nonce,
                "server_nonce": server_nonce,
                "selected_v": VERSION,
                "client_capabilities_sha256": capabilities_sha256(self._capabilities()),
                "server_capabilities_sha256": capabilities_sha256(server_capabilities),
            },
            code,
        )

        complete = await self._next_frame(HANDSHAKE_TIMEOUT_SECONDS)
        if complete.type == "error":
            raise HandshakeError(self._error_text(complete))
        if complete.type != "pair.complete":
            raise HandshakeError(f"期望 pair.complete，收到 {complete.type or complete.kind}")
        if not complete.flag("ok", default=True):
            raise HandshakeError("服务端拒绝了配对")
        self._verify_server_frame(complete, server_public_key, code)

        self._store.update(
            self.template.key,
            server_public_key=b64u(server_public_key),
            server_key_id=server_key_id,
            server_id=server_id,
            server_fingerprint=fingerprint(server_public_key),
            client_fingerprint=fingerprint(self._identity_public),
            paired_at=int(time.time()),
        )
        self._server_state = self._store.get(self.template.key)
        logger.info(
            "[VelocityAuth] 模板 %s 配对完成（server_id=%s）",
            self.template.name,
            server_id,
        )
        with contextlib.suppress(Exception):
            await self.hooks.on_paired(self)

    async def _authenticate(self) -> None:
        self._state = ClientState.AUTHENTICATING
        server_public_key = self._server_state.server_key_bytes()
        if not server_public_key:
            raise HandshakeError("缺少已 pin 的服务端公钥，请重新配对")

        assert self._ephemeral is not None
        hello_id = self._new_id()
        client_nonce_raw = self._random(16)
        client_nonce = b64u(client_nonce_raw)
        payload: dict[str, Any] = {
            "client_id": self.template.client_id,
            "client_key_id": self.template.client_key_id,
            "client_public_key_sha256": sha256_b64u(self._identity_public),
            "client_ephemeral_key": b64u(self._ephemeral.raw_public),
            "client_nonce": client_nonce,
            "provider": self.template.provider,
            "scopes": self.template.scopes(),
            "capabilities": self._capabilities(),
        }
        await self._send_plain("auth.hello", payload, None, hello_id, client_nonce)

        challenge = await self._next_frame(HANDSHAKE_TIMEOUT_SECONDS)
        if challenge.type == "error":
            raise HandshakeError(self._error_text(challenge))
        if challenge.type != "auth.challenge":
            raise HandshakeError(f"期望 auth.challenge，收到 {challenge.type or challenge.kind}")
        self._verify_server_frame(challenge, server_public_key, None)

        pinned = self._server_state.server_fingerprint or fingerprint(server_public_key)
        if challenge.text("server_public_key_sha256") != pinned:
            raise HandshakeError("服务端公钥指纹与本地 pin 不一致，已中止")

        server_ephemeral_raw = b64u_decode(challenge.text("server_ephemeral_key"))
        server_nonce = challenge.text("server_nonce")
        server_capabilities = challenge.strings("capabilities")
        server_id = challenge.text("server_id")
        server_key_id = challenge.text("server_key_id")

        await self._send_plain(
            "auth.prove",
            {
                "challenge_id": challenge.text("challenge_id"),
                "hello_id": hello_id,
                "client_id": self.template.client_id,
                "client_key_id": self.template.client_key_id,
                "client_public_key_sha256": sha256_b64u(self._identity_public),
                "client_ephemeral_key": b64u(self._ephemeral.raw_public),
                "server_id": server_id,
                "server_key_id": server_key_id,
                "server_public_key_sha256": sha256_b64u(server_public_key),
                "server_ephemeral_key": challenge.text("server_ephemeral_key"),
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
                "selected_v": VERSION,
                "client_capabilities_sha256": capabilities_sha256(self._capabilities()),
                "server_capabilities_sha256": capabilities_sha256(server_capabilities),
            },
            None,
        )

        shared = self._ephemeral.shared_secret(server_ephemeral_raw)
        keys = derive_session_keys(client_nonce_raw, b64u_decode(server_nonce), shared)
        self._outbound = OutboundSealer(keys.key_c2s)
        self._inbound = InboundSealer(keys.key_s2c)

        self.server_id = server_id
        self.server_key_id = server_key_id
        self.server_capabilities = tuple(server_capabilities)

        # auth.result 是密封帧（s2c seq=0），能解开即证明双方派生出了相同会话密钥
        result = await self._next_sealed(HANDSHAKE_TIMEOUT_SECONDS)
        if result.type == "error":
            raise HandshakeError(
                f"{result.text('code')}: {result.text('message')}"
            )
        if result.type != "auth.result":
            raise HandshakeError(f"期望 auth.result，收到 {result.type}")
        self.session_id = result.text("session_id")
        if not self.server_capabilities:
            self.server_capabilities = tuple(result.payload.get("capabilities", "").split(","))
        await self._send_sealed("auth.confirm", {"session_id": self.session_id})

    # ------------------------------------------------------------------ 业务循环

    async def _business_loop(self) -> None:
        while True:
            item = await self._frames.get()
            if item is CLOSE_SENTINEL:
                return
            try:
                frame = parse_frame(item)
            except ProtocolError as exc:
                logger.warning("[VelocityAuth] %s 收到非法帧：%s", self.template.name, exc)
                continue
            if not frame.sealed:
                if frame.type == "ping":
                    await self._send_sealed("pong", {})
                else:
                    logger.debug(
                        "[VelocityAuth] %s 忽略明文帧 %s", self.template.name, frame.type
                    )
                continue
            try:
                assert self._inbound is not None
                message = parse_message(self._inbound.open(frame.seq, b64u_decode(frame.ct)))
            except CryptoError as exc:
                logger.warning("[VelocityAuth] %s 密封帧处理失败：%s", self.template.name, exc)
                self._close_reason = f"密封帧处理失败：{exc}"
                return
            except ProtocolError as exc:
                logger.warning("[VelocityAuth] %s 内层明文异常：%s", self.template.name, exc)
                continue
            await self._dispatch(message)

    async def _dispatch(self, message: ParsedMessage) -> None:
        type_ = message.type
        if type_ == "auth.result":
            return
        if type_ == "verify.watch":
            code = message.text("code")
            ttl = message.number("ttl", 300)
            accepted = await self.hooks.on_verify_watch(self, code, ttl)
            await self._send_sealed("verify.watch.ack", {"code": code, "accepted": accepted})
            return
        if type_ == "verify.cancel":
            await self.hooks.on_verify_cancel(self, message.text("code"))
            return
        if type_ == "verify.matched.ack":
            await self.hooks.on_matched_ack(
                self, message.text("code"), message.flag("consumed", default=False)
            )
            return
        if type_ == "ping":
            await self._send_sealed("pong", {})
            return
        if type_ == "pong":
            self.last_pong_at = time.time()
            return
        if type_ == "error":
            request_id = message.text("request_id")
            code = message.text("code")
            text = message.text("message")
            if request_id and request_id in self._pending:
                future = self._pending.pop(request_id)
                if not future.done():
                    future.set_exception(QueryError(code, text))
            else:
                logger.warning(
                    "[VelocityAuth] %s 服务端错误 %s: %s", self.template.name, code, text
                )
            return
        request_id = message.text("request_id")
        if request_id and request_id in self._pending:
            future = self._pending.pop(request_id)
            if not future.done():
                future.set_result(message)
            return
        logger.debug("[VelocityAuth] %s 忽略密封帧 %s", self.template.name, type_)

    async def _heartbeat_loop(self) -> None:
        interval = self.settings.heartbeat_seconds
        if interval <= 0:
            return
        try:
            while True:
                await asyncio.sleep(interval)
                if not self.ready:
                    return
                started = time.perf_counter()
                await self._send_sealed("ping", {})
                self.latency_ms = int((time.perf_counter() - started) * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VelocityAuth] %s 心跳失败：%s", self.template.name, exc)

    # ------------------------------------------------------------------ 发送

    def _new_id(self) -> str:
        self._counter += 1
        return f"b_{self._counter}"

    @staticmethod
    def _random(size: int) -> bytes:
        return os.urandom(size)

    def _capabilities(self) -> list[str]:
        return [CAPABILITY_VERIFY, CAPABILITY_QUERY, CAPABILITY_ADMIN]

    async def _send_json(self, frame: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise HandshakeError("连接已关闭，无法发送")
        await ws.send_str(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    async def _send_plain(
        self,
        type_: str,
        payload: dict[str, Any],
        signing_code: str | None,
        id_: str | None = None,
        nonce: str | None = None,
    ) -> None:
        assert self._identity_private is not None
        id_ = id_ or self._new_id()
        nonce = nonce or b64u(self._random(16))
        ts = int(time.time())
        context = "pair" if signing_code is not None else "auth"
        input_text = signature_input(context, type_, id_, ts, nonce, payload, signing_code)
        signature = sign(self._identity_private, input_text.encode("utf-8"))
        await self._send_json(
            {
                "v": VERSION,
                "kind": "plain",
                "type": type_,
                "id": id_,
                "ts": ts,
                "nonce": nonce,
                "payload": payload,
                "sig": b64u(signature),
            }
        )

    async def _send_sealed(self, type_: str, payload: dict[str, Any]) -> str:
        if self._outbound is None:
            raise HandshakeError(f"尚未建立会话，无法发送 {type_}")
        id_ = self._new_id()
        plaintext = message_plaintext(type_, id_, int(time.time()), payload)
        async with self._send_lock:
            seq, ciphertext = self._outbound.seal(plaintext)
            await self._send_json(
                {"v": VERSION, "kind": "sealed", "seq": seq, "ct": b64u(ciphertext)}
            )
        return id_

    async def _next_sealed(self, timeout: float) -> ParsedMessage:
        frame = await self._next_frame(timeout)
        if not frame.sealed:
            raise HandshakeError(f"期望密封帧，收到明文 {frame.type}")
        assert self._inbound is not None
        try:
            return parse_message(self._inbound.open(frame.seq, b64u_decode(frame.ct)))
        except (CryptoError, ProtocolError) as exc:
            raise HandshakeError(f"密封帧解析失败：{exc}") from exc

    # ------------------------------------------------------------------ 校验

    def _verify_server_frame(
        self, frame: Frame, server_public_key: bytes, signing_code: str | None
    ) -> None:
        context = "pair" if signing_code is not None else "auth"
        input_text = signature_input(
            context,
            frame.type,
            frame.id,
            frame.ts,
            frame.nonce,
            frame.payload,
            signing_code,
        )
        public_key = ed25519_public_from_raw(server_public_key)
        if not verify(public_key, b64u_decode(frame.sig), input_text.encode("utf-8")):
            raise HandshakeError(f"服务端签名校验失败：{frame.type}")

    @staticmethod
    def _error_text(frame: Frame) -> str:
        code = frame.text("code") or "unknown_error"
        message = frame.text("message")
        mapping = {
            "pairing_missing": "配对已失效（pairing_id 不存在、已消费或已过期）",
            "pairing_expired": "配对码已过期",
            "pairing_attempts_exceeded": "配对失败次数超限",
            "signature_invalid": "服务端验签失败（配对码错误或时间偏差过大）",
            "key_not_paired": "本 bot 密钥未被服务端配对，需要重新配对",
            "version_unsupported": "协议版本不受支持",
            "decrypt_failed": "服务端解密失败",
            "replay_detected": "seq 重放被拒绝",
        }
        hint = mapping.get(code, message or code)
        return f"{hint}（{code}）"

    # ------------------------------------------------------------------ 业务 API

    async def watch_ack(self, code: str, accepted: bool) -> None:
        await self._send_sealed("verify.watch.ack", {"code": code, "accepted": accepted})

    async def report_matched(
        self,
        code: str,
        subject: str,
        scope: str = "",
        display_name: str = "",
        matched_at: int | None = None,
    ) -> None:
        """上报命中；``scope`` / ``display_name`` 为空时不发送该字段。"""
        payload: dict[str, Any] = {
            "code": code,
            "subject": subject,
            "matched_at": matched_at or int(time.time()),
        }
        if scope:
            payload["scope"] = scope
        if display_name:
            payload["display_name"] = display_name
        await self._send_sealed("verify.matched", payload)

    async def send_query(
        self, message_type: str, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> ParsedMessage:
        """发送 query.* 并按响应回带的 request_id 关联响应。"""
        if not self.ready:
            raise QueryError("not_ready", f"模板 {self.template.name} 尚未建立会话")
        if not self.supports_query:
            raise QueryError(
                "query_disabled",
                f"服务端未启用查询接口（capabilities={','.join(self.server_capabilities) or '-'}）",
            )
        return await self._request(message_type, payload, timeout)

    async def send_admin(
        self, message_type: str, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> ParsedMessage:
        """发送 admin.* 写操作；错误码由服务端决定（admin_disabled / confirm_required 等）。"""
        if not self.ready:
            raise QueryError("not_ready", f"模板 {self.template.name} 尚未建立会话")
        if not self.supports_admin:
            raise QueryError(
                "admin_disabled",
                "服务端未启用管理接口（VelocityAuth 需设置 bot.admin.enabled=true）",
            )
        return await self._request(message_type, payload, timeout)

    async def _request(
        self, message_type: str, payload: dict[str, Any] | None, timeout: float | None
    ) -> ParsedMessage:
        request_id = await self._send_sealed(message_type, payload or {})
        future: asyncio.Future[ParsedMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            return await asyncio.wait_for(
                future, timeout=timeout or self.settings.request_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise QueryError("timeout", f"请求超时：{message_type}") from exc
