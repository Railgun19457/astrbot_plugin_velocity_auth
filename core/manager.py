"""多服务器模板的监督与业务编排。

- 每个模板一条 :class:`VelocityAuthClient`，断线后指数退避重连；
- 验证码注册表：登记服务端下发的待验证码，群消息命中后上报；
- 对命令层提供统一的查询入口（模板解析、结果解析、错误信息）。
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .client import ClientHooks, QueryError, VelocityAuthClient
from .config import PluginSettings
from .models import (
    QUERY_TYPE_ACCOUNT,
    QUERY_TYPE_LOOKUP,
    QUERY_TYPE_STATS,
    AccountView,
    LookupView,
    PendingCode,
    ServerTemplate,
    StatsView,
    parse_account,
    parse_lookup,
    parse_stats,
)
from .state import StateStore

COD_DEFAULT_TTL = 300
MAX_UNPAIRED_ATTEMPTS = 3

MatchedCallback = Callable[[ServerTemplate, PendingCode, bool], Awaitable[None]]
PairedCallback = Callable[[ServerTemplate], Awaitable[None]]


@dataclass(slots=True)
class ServerRuntime:
    """一个模板的运行态。"""

    template: ServerTemplate
    client: VelocityAuthClient
    task: asyncio.Task[None] | None = None
    started_at: float = 0.0
    last_error: str = ""
    attempts: int = 0

    @property
    def name(self) -> str:
        return self.template.name


class ServerManager(ClientHooks):
    """管理所有模板的连接、验证码注册表与查询。"""

    def __init__(
        self,
        settings: PluginSettings,
        store: StateStore,
        on_matched: MatchedCallback | None = None,
        on_paired: PairedCallback | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runtimes: dict[str, ServerRuntime] = {}
        self._pending: dict[tuple[str, str], PendingCode] = {}
        self._supervisor_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._on_matched = on_matched
        self._on_paired = on_paired

    # ------------------------------------------------------------------ 生命周期

    @property
    def settings(self) -> PluginSettings:
        return self._settings

    def update_settings(self, settings: PluginSettings) -> None:
        """热更新配置；由调用方决定是否需要重启连接。"""
        self._settings = settings

    async def start(self) -> None:
        if self._supervisor_task is not None:
            return
        self._stopping.clear()
        enabled = [item for item in self._settings.servers if item.enabled]
        if not enabled:
            logger.warning("[VelocityAuth] 没有启用的服务器模板。")
            return
        for template in enabled:
            runtime = ServerRuntime(
                template=template,
                client=VelocityAuthClient(
                    template, self._store, self._settings.connect, self
                ),
            )
            self._runtimes[template.key] = runtime
            runtime.task = asyncio.create_task(
                self._supervise(runtime), name=f"velocityauth-{template.name}"
            )
        logger.info("[VelocityAuth] 已启动 %d 个服务器模板。", len(enabled))

    async def stop(self) -> None:
        self._stopping.set()
        for runtime in self._runtimes.values():
            if runtime.task is not None:
                runtime.task.cancel()
        tasks = [r.task for r in self._runtimes.values() if r.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for runtime in self._runtimes.values():
            with contextlib.suppress(Exception):
                await runtime.client.close()
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            self._supervisor_task = None
        self._runtimes.clear()
        self._pending.clear()
        if self._stopping.is_set():
            logger.info("[VelocityAuth] 已停止全部服务器模板。")

    async def restart(self, name: str | None = None) -> list[str]:
        """强制重连；`name` 为空时重连全部。返回被重连的模板名。"""
        targets = [
            runtime
            for runtime in self._runtimes.values()
            if name is None or runtime.name == name
        ]
        for runtime in targets:
            runtime.client = VelocityAuthClient(
                runtime.template, self._store, self._settings.connect, self
            )
            with contextlib.suppress(Exception):
                await runtime.client.close()
            if runtime.task is not None:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await runtime.task
            runtime.last_error = ""
            runtime.task = asyncio.create_task(
                self._supervise(runtime), name=f"velocityauth-{runtime.name}"
            )
        return [runtime.name for runtime in targets]

    async def _supervise(self, runtime: ServerRuntime) -> None:
        delay = self._settings.connect.reconnect_initial_seconds
        max_delay = max(delay, self._settings.connect.reconnect_max_seconds)
        while not self._stopping.is_set():
            reason = ""
            try:
                reason = await runtime.client.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 监督循环必须存活
                reason = f"未预期异常：{exc}"
                logger.exception("[VelocityAuth] 模板 %s 异常", runtime.name)
            if self._stopping.is_set():
                return
            runtime.last_error = reason
            if not runtime.client.paired:
                # 未配对时反复重连没有意义：有限重试后停下，等配置修正或手动重连
                runtime.attempts += 1
                runtime.client = VelocityAuthClient(
                    runtime.template, self._store, self._settings.connect, self
                )
                if runtime.attempts >= MAX_UNPAIRED_ATTEMPTS:
                    logger.error(
                        "[VelocityAuth] 模板 %s 连续 %d 次未能完成配对，已停止重连：%s"
                        "（修好配置后执行 /vauth 重连）",
                        runtime.name,
                        runtime.attempts,
                        reason or "原因未知",
                    )
                    return
                try:
                    await asyncio.sleep(max_delay)
                except asyncio.CancelledError:
                    raise
                continue
            jitter = random.uniform(0, delay * 0.2)
            wait = min(delay + jitter, max_delay)
            logger.warning(
                "[VelocityAuth] 模板 %s 断开（%s），%.1fs 后重连。",
                runtime.name,
                reason or "连接结束",
                wait,
            )
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, max_delay)
            runtime.client = VelocityAuthClient(
                runtime.template, self._store, self._settings.connect, self
            )

    # ------------------------------------------------------------------ 运行态

    def runtimes(self) -> list[ServerRuntime]:
        return list(self._runtimes.values())

    def runtime(self, name: str) -> ServerRuntime | None:
        for runtime in self._runtimes.values():
            if runtime.name == name:
                return runtime
        return None

    def default_runtime(self) -> ServerRuntime | None:
        runtimes = self.runtimes()
        return runtimes[0] if runtimes else None

    def resolve_runtime(self, umo: str, name: str | None = None) -> ServerRuntime | None:
        """按模板名或当前会话所属的管理模板解析。"""
        if name:
            return self.runtime(name)
        owned = [
            runtime
            for runtime in self._runtimes.values()
            if umo in runtime.template.admin_umos
        ]
        if len(owned) == 1:
            return owned[0]
        if owned:
            return owned[0]
        return self.default_runtime()

    def admin_runtimes(self, umo: str) -> list[ServerRuntime]:
        return [
            runtime
            for runtime in self._runtimes.values()
            if umo in runtime.template.admin_umos
        ]

    def pending_count(self, template: ServerTemplate | None = None) -> int:
        self.purge_expired()
        if template is None:
            return len(self._pending)
        return sum(
            1 for code in self._pending.values() if code.server_key == template.key
        )

    # ------------------------------------------------------------------ 验证码

    def register_code(self, template: ServerTemplate, code: str, ttl: int) -> None:
        now = time.time()
        self._pending[(template.key, code)] = PendingCode(
            code=code,
            server_key=template.key,
            provider=template.provider,
            expires_at=now + max(1, ttl),
            registered_at=now,
        )

    def cancel_code(self, code: str, server_key: str) -> None:
        self._pending.pop((server_key, code), None)

    def code_for(self, template: ServerTemplate, code: str) -> PendingCode | None:
        return self._pending.get((template.key, code))

    def purge_expired(self, now: float | None = None) -> None:
        now = now or time.time()
        for key in [key for key, value in self._pending.items() if value.expired(now)]:
            self._pending.pop(key, None)

    def match_message(
        self, umo: str, text: str, sender_id: str, sender_name: str, group_id: str
    ) -> PendingCode | None:
        """在验证会话里查找命中的待验证码；返回命中的记录（已标记 matched）。"""
        if not text:
            return None
        self.purge_expired()
        if not self._pending:
            return None
        now = time.time()
        for (server_key, code), record in self._pending.items():
            if record.matched or record.expired(now):
                continue
            runtime = self._runtimes.get(server_key)
            if runtime is None or not runtime.client.ready:
                continue
            if umo not in runtime.template.verify_umos:
                continue
            if runtime.template.match_mode == "exact":
                hit = text.strip() == code
            else:
                hit = bool(
                    re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(code)}(?![A-Za-z0-9_-])", text
                    )
                )
            if not hit:
                continue
            record.matched = True
            record.matched_umo = umo
            record.subject = sender_id
            record.display_name = sender_name
            record.group_id = group_id
            return record
        return None

    async def report_matched(self, record: PendingCode) -> bool:
        """上报命中；成功返回 True。"""
        runtime = self._runtimes.get(record.server_key)
        if runtime is None or not runtime.client.ready:
            logger.warning(
                "[VelocityAuth] 模板 %s 已断开，无法上报命中 %s",
                record.server_key,
                record.code,
            )
            return False
        try:
            await runtime.client.report_matched(
                record.code,
                subject=record.subject,
                scope=record.group_id,
                display_name=record.display_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VelocityAuth] 上报命中失败：%s", exc)
            return False
        logger.info(
            "[VelocityAuth] 模板 %s 上报命中 code=%s subject=%s",
            runtime.name,
            record.code,
            record.subject,
        )
        return True

    # ------------------------------------------------------------------ 查询

    async def query(
        self,
        runtime: ServerRuntime,
        message_type: str,
        payload: dict[str, Any],
        *,
        max_rows: int | None = None,
    ) -> dict[str, str]:
        """发送 query 并返回原始字符串载荷（已做 limit 裁剪）。"""
        if not runtime.client.ready:
            raise QueryError(
                "not_ready",
                f"模板 {runtime.name} 尚未建立会话"
                + (f"（{runtime.last_error}）" if runtime.last_error else ""),
            )
        params = dict(payload)
        if max_rows:
            params["limit"] = max(1, max_rows)
        response = await runtime.client.send_query(message_type, params)
        return dict(response.payload)

    async def account(self, runtime: ServerRuntime, by: str, value: str) -> AccountView:
        payload = await self.query(
            runtime, QUERY_TYPE_ACCOUNT, {"by": by, "value": value}
        )
        return parse_account(payload)

    async def lookup(
        self, runtime: ServerRuntime, by: str, value: str, max_rows: int
    ) -> LookupView:
        payload = await self.query(
            runtime, QUERY_TYPE_LOOKUP, {"by": by, "value": value}, max_rows=max_rows
        )
        return parse_lookup(payload)

    async def stats(self, runtime: ServerRuntime) -> StatsView:
        raw = await self.query(runtime, QUERY_TYPE_STATS, {})
        return parse_stats(raw)

    # ------------------------------------------------------------------ ClientHooks

    async def on_ready(self, client: VelocityAuthClient) -> None:
        runtime = self._runtimes.get(client.template.key)
        if runtime is not None:
            runtime.last_error = ""
            runtime.started_at = time.time()
            runtime.attempts = 0

    async def on_paired(self, client: VelocityAuthClient) -> None:
        """首次配对成功（服务端公钥已固化）。"""
        if self._on_paired is not None:
            await self._on_paired(client.template)

    async def on_closed(self, client: VelocityAuthClient, reason: str) -> None:
        runtime = self._runtimes.get(client.template.key)
        if runtime is not None:
            runtime.last_error = reason
        self._drop_codes(client.template.key)

    async def on_verify_watch(
        self, client: VelocityAuthClient, code: str, ttl: int
    ) -> bool:
        template = client.template
        if not code:
            return False
        if ttl <= 0:
            ttl = COD_DEFAULT_TTL
        self.register_code(template, code, ttl)
        logger.info(
            "[VelocityAuth] 模板 %s 登记验证码（ttl=%ss，provider=%s）",
            template.name,
            ttl,
            template.provider,
        )
        return True

    async def on_verify_cancel(self, client: VelocityAuthClient, code: str) -> None:
        self.cancel_code(code, client.template.key)
    async def on_matched_ack(
        self, client: VelocityAuthClient, code: str, consumed: bool
    ) -> None:
        template = client.template
        record = self._pending.pop((template.key, code), None)
        if record is None:
            return
        record.matched = True
        if consumed:
            logger.info(
                "[VelocityAuth] 模板 %s 验证码 %s 已消费（subject=%s）",
                template.name,
                code,
                record.subject,
            )
        else:
            logger.info(
                "[VelocityAuth] 模板 %s 验证码 %s 未被消费（subject=%s）",
                template.name,
                code,
                record.subject,
            )
        if self._on_matched is not None:
            await self._on_matched(template, record, consumed)

    def _drop_codes(self, server_key: str) -> None:
        for key in [key for key in self._pending if key[0] == server_key]:
            self._pending.pop(key, None)


__all__ = ["ServerManager", "ServerRuntime", "PendingCode", "QueryError"]
