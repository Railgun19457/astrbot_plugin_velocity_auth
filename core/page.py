"""Dashboard 页面的后端 API（注册到 AstrBot WebUI）。

页面通过 AstrBot 注入的 ``AstrBotPluginView`` 桥调用这些端点，
因此**不需要**再做一层登录：进入 WebUI 本身已经过认证。

端点命名与 ``astrbot_plugin_image_generation`` 保持一致：``/page/<resource>``。
"""

from __future__ import annotations

from typing import Any

from astrbot.api.web import error_response, json_response, request

from .client import QueryError
from .manager import ServerManager, ServerRuntime

PLUGIN_NAME = "astrbot_plugin_velocity_auth"


class VelocityAuthPageAPI:
    """VelocityAuth 管理页的后端处理器。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    # ------------------------------------------------------------------ 注册

    def register(self) -> None:
        routes = [
            ("/page/state", self.page_state, ["GET"], "VelocityAuth Page state"),
            ("/page/servers", self.page_servers, ["GET"], "VelocityAuth Page servers"),
            ("/page/stats", self.page_stats, ["GET"], "VelocityAuth Page statistics"),
            ("/page/accounts", self.page_accounts, ["GET"], "VelocityAuth Page accounts"),
            (
                "/page/accounts/<account_key>",
                self.page_account_detail,
                ["GET"],
                "VelocityAuth Page account detail",
            ),
            ("/page/sessions", self.page_sessions, ["GET"], "VelocityAuth Page sessions"),
            ("/page/audit", self.page_audit, ["GET"], "VelocityAuth Page audit log"),
            (
                "/page/accounts/<account_key>/status",
                self.page_set_status,
                ["POST"],
                "Lock or unlock an account",
            ),
            (
                "/page/accounts/<account_key>/password",
                self.page_reset_password,
                ["POST"],
                "Reset an account password",
            ),
            (
                "/page/accounts/<account_key>/kick",
                self.page_kick,
                ["POST"],
                "Kick an account's sessions",
            ),
            (
                "/page/accounts/<account_key>/clear-session",
                self.page_clear_session,
                ["POST"],
                "Clear an account's remembered sessions",
            ),
            (
                "/page/accounts/<account_key>/bindings/<provider>",
                self.page_remove_binding,
                ["POST"],
                "Unbind a provider from an account",
            ),
            (
                "/page/accounts/<account_key>/delete",
                self.page_delete_account,
                ["POST"],
                "Delete an account",
            ),
        ]
        for route, handler, methods, description in routes:
            self.plugin.context.register_web_api(
                f"/{PLUGIN_NAME}{route}",
                handler,
                methods,
                description,
            )

    # ------------------------------------------------------------------ 工具

    @property
    def manager(self) -> ServerManager:
        return self.plugin.manager

    def _runtimes(self) -> list[ServerRuntime]:
        return self.manager.runtimes()

    def _resolve(self, server: str = "") -> tuple[ServerRuntime | None, str]:
        """按 ``server`` 查询参数（模板名）解析运行态；未指定时取第一个。"""
        runtimes = self._runtimes()
        if not runtimes:
            return None, "尚未配置任何服务器模板"
        name = (server or "").strip()
        if not name:
            return runtimes[0], ""
        for runtime in runtimes:
            if runtime.name == name:
                return runtime, ""
        return None, f"未找到服务器模板：{name}"

    def _query(self, key: str, default: str = "") -> str:
        return str(request.query.get(key, default) or default).strip()

    def _query_int(self, key: str, default: int) -> int:
        try:
            return int(self._query(key, str(default)) or default)
        except ValueError:
            return default

    def _query_flag(self, key: str) -> bool:
        return self._query(key).lower() in {"1", "true", "yes", "on"}

    def _runtime_payload(self, runtime: ServerRuntime) -> dict[str, Any]:
        client = runtime.client
        return {
            "name": runtime.name,
            "address": runtime.template.address,
            "provider": runtime.template.provider,
            "enabled": runtime.template.enabled,
            "ready": client.ready,
            "paired": client.paired,
            "session_id": client.session_id or "",
            "server_id": client.server_id or "",
            "latency_ms": client.latency_ms,
            "capabilities": list(client.server_capabilities),
            "supports_query": client.supports_query,
            "supports_admin": client.supports_admin,
            "verify_umos": list(runtime.template.verify_umos),
            "admin_umos": list(runtime.template.admin_umos),
            "pending_codes": self.manager.pending_count(runtime.template),
            "last_error": runtime.last_error,
        }

    # ------------------------------------------------------------------ 读

    async def page_state(self):
        """页面初始化：模板列表 + 能力开关。"""
        runtimes = self._runtimes()
        return json_response(
            {
                "initialized": bool(runtimes),
                "plugin_version": self.plugin.plugin_version,
                "servers": [self._runtime_payload(runtime) for runtime in runtimes],
                "default_server": runtimes[0].name if runtimes else "",
                "max_page_size": 100,
            }
        )

    async def page_servers(self):
        runtimes = self._runtimes()
        return json_response(
            {"servers": [self._runtime_payload(runtime) for runtime in runtimes]}
        )

    async def page_stats(self):
        """仪表盘：聚合统计 + 活跃度趋势。"""
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return error_response(error, status_code=400)
        days = max(0, min(90, self._query_int("days", 30)))
        try:
            stats, activity = await self.manager.stats_with_activity(runtime, days)
        except QueryError as exc:
            return error_response(self._query_error(exc), status_code=400)

        status_items = [
            {"name": label, "count": count}
            for key, count in stats.accounts_by_status().items()
            for label in [self._status_label(key)]
        ]
        return json_response(
            {
                "server": runtime.name,
                "stats": dict(stats.raw),
                "accounts_total": stats.accounts_total,
                "online": stats.online,
                "bot_connected": stats.bot_connected,
                "status_distribution": status_items,
                "bindings": [
                    {"name": key, "count": count}
                    for key, count in stats.bound_by_provider().items()
                ],
                "registered": [
                    {"name": key, "count": count} for key, count in stats.registered()
                ],
                "logins": [
                    {"name": key, "count": count} for key, count in stats.logins()
                ],
                "activity": {
                    "dau": activity.dau,
                    "wau": activity.wau,
                    "mau": activity.mau,
                    "trend": [
                        {
                            "date": point.date,
                            "logins": point.logins,
                            "accounts": point.accounts,
                        }
                        for point in activity.trend
                    ],
                },
            }
        )

    async def page_accounts(self):
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return error_response(error, status_code=400)
        page = max(1, self._query_int("page", 1))
        limit = max(1, min(100, self._query_int("limit", 20)))
        try:
            result = await self.manager.accounts(
                runtime,
                keyword=self._query("keyword"),
                status=self._query("status"),
                provider=self._query("provider"),
                online_only=self._query_flag("online"),
                page=page,
                page_size=limit,
                sort=self._query("sort"),
            )
        except QueryError as exc:
            return error_response(self._query_error(exc), status_code=400)
        return json_response(
            {
                "server": runtime.name,
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "accounts": [self._account_row(row) for row in result.rows],
            }
        )

    async def page_account_detail(self, account_key: str):
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return error_response(error, status_code=400)
        by, value = self._split_account_key(account_key)
        if not value:
            return error_response("账号标识为空", status_code=400)
        try:
            account = await self.manager.account(runtime, by, value)
        except QueryError as exc:
            return error_response(self._query_error(exc), status_code=400)
        if not account.found:
            return error_response(f"未找到账号：{value}", status_code=404)
        return json_response(
            {
                "server": runtime.name,
                "account": {
                    "id": account.id,
                    "name": account.name,
                    "uuid": account.uuid,
                    "status": account.status,
                    "status_label": account.status_label,
                    "last_server": account.last_server,
                    "last_ip": account.last_ip,
                    "created_at": account.created_at,
                    "created_text": account.created_text,
                    "last_login_at": account.last_login_at,
                    "last_login_text": account.last_login_text,
                    "bindings": [
                        {
                            "provider": binding.provider,
                            "identifier": binding.identifier,
                            "verified_at": binding.verified_at,
                            "verified_text": binding.verified_text,
                        }
                        for binding in account.bindings
                    ],
                    "identities": [
                        {"kind": identity.kind, "identifier": identity.identifier}
                        for identity in account.identities
                    ],
                },
            }
        )

    async def page_sessions(self):
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return error_response(error, status_code=400)
        page = max(1, self._query_int("page", 1))
        limit = max(1, min(100, self._query_int("limit", 20)))
        try:
            result = await self.manager.sessions(runtime, page=page, page_size=limit)
        except QueryError as exc:
            return error_response(self._query_error(exc), status_code=400)
        return json_response(
            {
                "server": runtime.name,
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "sessions": [
                    {
                        "account_id": row.account_id,
                        "account": row.account,
                        "connection": row.connection,
                        "ip": row.ip,
                        "authenticated_at": row.authenticated_at,
                        "authenticated_text": row.authenticated_text,
                    }
                    for row in result.rows
                ],
            }
        )

    async def page_audit(self):
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return error_response(error, status_code=400)
        page = max(1, self._query_int("page", 1))
        limit = max(1, min(100, self._query_int("limit", 20)))
        account_id = self._query_int("account_id", 0)
        try:
            result = await self.manager.audit(
                runtime,
                account_id=account_id or None,
                action=self._query("action"),
                actor=self._query("actor"),
                source=self._query("source"),
                since=self._query_int("since", 0),
                until=self._query_int("until", 0),
                page=page,
                page_size=limit,
            )
        except QueryError as exc:
            return error_response(self._query_error(exc), status_code=400)
        return json_response(
            {
                "server": runtime.name,
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "entries": [
                    {
                        "id": row.id,
                        "at": row.at,
                        "at_text": row.at_text,
                        "account_id": row.account_id,
                        "account": row.account,
                        "action": row.action,
                        "ip": row.ip,
                        "detail": row.detail,
                        "actor": row.actor,
                        "source": row.source,
                        "client_id": row.client_id,
                    }
                    for row in result.rows
                ],
            }
        )

    # ------------------------------------------------------------------ 写

    async def _body(self) -> dict[str, Any]:
        payload = await request.json(default={})
        return payload if isinstance(payload, dict) else {}

    def _actor(self, body: dict[str, Any]) -> str:
        """操作者标识：仅供服务端审计追溯，不参与鉴权。"""
        actor = str(body.get("actor") or "").strip()
        return actor or "astrbot-webui"

    async def _target(self, account_key: str) -> tuple[ServerRuntime | None, dict[str, Any], str]:
        runtime, error = self._resolve(self._query("server"))
        if runtime is None:
            return None, {}, error
        by, value = self._split_account_key(account_key)
        if not value:
            return None, {}, "账号标识为空"
        if by == "uuid":
            return None, {}, "管理操作需要账号名或数字 id，请先按 UUID 查询详情"
        if by == "id":
            try:
                return runtime, {"id": int(value)}, ""
            except ValueError:
                return None, {}, f"账号 id 不是合法整数：{value}"
        return runtime, {"name": value}, ""

    async def page_set_status(self, account_key: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        status = str(body.get("status") or "").strip().lower()
        if status not in {"active", "locked"}:
            return error_response("status 仅支持 active / locked", status_code=400)
        try:
            result = await self.manager.set_account_status(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                status=status,
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    async def page_reset_password(self, account_key: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        password = str(body.get("password") or "")
        if not password:
            return error_response("缺少新密码", status_code=400)
        try:
            result = await self.manager.reset_account_password(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                password=password,
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    async def page_kick(self, account_key: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        try:
            result = await self.manager.kick_account(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                reason=str(body.get("reason") or ""),
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    async def page_clear_session(self, account_key: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        try:
            result = await self.manager.clear_account_sessions(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    async def page_remove_binding(self, account_key: str, provider: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        provider_id = str(provider or "").strip().lower()
        if not provider_id:
            return error_response("缺少 provider", status_code=400)
        try:
            result = await self.manager.remove_binding(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                provider=provider_id,
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    async def page_delete_account(self, account_key: str):
        runtime, target, error = await self._target(account_key)
        if runtime is None:
            return error_response(error, status_code=400)
        body = await self._body()
        confirm = str(body.get("confirm_name") or "").strip()
        if not confirm:
            return error_response("请在 confirm_name 中回填账号名以确认删除", status_code=400)
        try:
            result = await self.manager.delete_account(
                runtime,
                account_id=target.get("id", 0) or 0,
                name=target.get("name", ""),
                confirm_name=confirm,
                actor=self._actor(body),
            )
        except QueryError as exc:
            return error_response(self._admin_error(exc), status_code=400)
        return json_response(result)

    # ------------------------------------------------------------------ 错误与映射

    @staticmethod
    def _split_account_key(account_key: str) -> tuple[str, str]:
        """``id:12`` / ``uuid:...`` / ``alice`` → (查询方式, 值)。"""
        raw = str(account_key or "").strip()
        if not raw:
            return "name", ""
        if ":" in raw:
            prefix, _, value = raw.partition(":")
            prefix = prefix.strip().lower()
            if prefix in {"id", "uuid", "name"}:
                return prefix, value.strip()
        if raw.isdigit():
            return "id", raw
        return "name", raw

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "active": "正常",
            "locked": "已锁定",
            "pending_binding": "待绑定",
        }.get(status, status or "-")

    @staticmethod
    def _account_row(row: Any) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "uuid": row.uuid,
            "status": row.status,
            "status_label": row.status_label,
            "last_server": row.last_server,
            "last_ip": row.last_ip,
            "created_at": row.created_at,
            "created_text": row.created_text,
            "last_login_at": row.last_login_at,
            "last_login_text": row.last_login_text,
            "bindings": [
                {"provider": provider, "identifier": identifier}
                for provider, identifier in row.binding_items
            ],
            "online": row.online,
        }

    @staticmethod
    def _query_error(exc: QueryError) -> str:
        mapping = {
            "query_disabled": "服务端未启用查询接口（VelocityAuth 的 bot.query.enabled）",
            "not_ready": "该服务器模板尚未建立会话",
            "timeout": "查询超时，请稍后重试",
            "bad_request": "参数被服务端拒绝",
        }
        return mapping.get(exc.code, exc.message or exc.code)

    @staticmethod
    def _admin_error(exc: QueryError) -> str:
        mapping = {
            "admin_disabled": "服务端未启用管理接口（VelocityAuth 的 bot.admin.enabled）",
            "admin_read_only": "服务端管理接口处于只读模式",
            "rate_limited": "操作过于频繁，请稍后重试",
            "confirm_required": "需要回填账号名确认该操作",
            "binding_required": "该渠道是账号唯一的必需绑定，解绑后将无法登录",
            "not_bound": "该渠道未绑定",
            "password_too_short": "密码太短",
            "password_too_long": "密码太长",
            "not_ready": "该服务器模板尚未建立会话",
            "timeout": "操作超时，请稍后重试",
        }
        return mapping.get(exc.code, exc.message or exc.code)
