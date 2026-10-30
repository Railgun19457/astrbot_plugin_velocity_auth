"""VelocityAuth 插件（AstrBot 侧 bot 实现）。

能力：

1. 连接一个或多个 VelocityAuth 插件实例（``servers`` 模板列表），
   首次用配对码建立互信，之后用 Ed25519 + X25519 + AES-256-GCM 会话通信；
2. 监听模板里配置的“验证会话”，消息命中待验证码时上报服务端
   （``verify.matched``），并在服务端确认消费后回一条提示；
3. 管理会话内提供指令（玩家查询 / 服务器统计），默认渲染为图片，渲染不可用时降级为文本。
"""

from __future__ import annotations

import asyncio
import contextlib
import string
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .core.client import QueryError
from .core.config import PluginSettings, load_settings
from .core.manager import ServerManager, ServerRuntime
from .core.models import PendingCode, ServerTemplate
from .core.render import TypstRenderer
from .core.state import StateStore

PLUGIN_NAME = "astrbot_plugin_velocity_auth"

UUID_HEX_LENGTH = 32
HEX_DIGITS = set("0123456789abcdefABCDEF")
QUERY_USAGE = (
    "用法：/vauth 查询 <游戏ID | 渠道账号>\n"
    "也可显式指定：name:玩家名 / uuid:… / ip:… / <渠道id>:账号"
)


class VelocityAuthPlugin(Star):
    """连接 VelocityAuth 插件并处理群内验证与查询。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.raw_config = config or {}
        self.settings: PluginSettings = load_settings(self.raw_config)
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.temp_dir = Path(get_astrbot_temp_path()) / PLUGIN_NAME
        self.store = StateStore(self.data_dir / "servers.json")
        self.renderer = TypstRenderer(
            template_dir=Path(__file__).parent / "templates",
            data_dir=self.data_dir,
            temp_dir=self.temp_dir,
            settings=self.settings,
        )
        self.manager = ServerManager(
            self.settings,
            self.store,
            on_matched=self._on_matched_ack,
            on_paired=self._on_paired,
        )
        self._started = False
        self._restart_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ 生命周期

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        await self._ensure_started()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata) -> None:
        if getattr(metadata, "module_path", None) == self.__module__:
            await self._ensure_started()

    async def terminate(self) -> None:
        if self._restart_task is not None and not self._restart_task.done():
            self._restart_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._restart_task
            self._restart_task = None
        await self.manager.stop()
        self.renderer.cleanup()
        self._started = False
        logger.info("[VelocityAuth] 插件已卸载。")

    async def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        await self.manager.start()

    def _reload_config(self) -> None:
        """重新读取配置；服务器模板有变动时重建连接。

        比对时忽略配对信息（``signature()``），避免配对码被自动清空后误判为变更。
        """
        new_settings = load_settings(self.raw_config)
        changed = [item.signature() for item in new_settings.servers] != [
            item.signature() for item in self.settings.servers
        ]
        self.settings = new_settings
        self.manager.update_settings(new_settings)
        self.renderer.update_settings(new_settings)
        if changed and self._started:
            logger.info("[VelocityAuth] 检测到服务器模板变更，正在重建连接。")
            self._started = False
            # 持有任务引用，避免被垃圾回收中断
            self._restart_task = asyncio.create_task(self._restart_manager())

    async def _restart_manager(self) -> None:
        try:
            await self.manager.stop()
            await self._ensure_started()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 重建失败不应影响插件运行
            logger.error("[VelocityAuth] 重建连接失败：%s", exc)

    async def _on_paired(self, template: ServerTemplate) -> None:
        """配对成功后自行清除配置里的配对码，避免敏感值长期留在配置文件中。"""
        servers = self.raw_config.get("servers")
        if not isinstance(servers, list):
            return
        cleared = False
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == template.name and entry.get("pairing_code"):
                entry["pairing_code"] = ""
                cleared = True
        if not cleared:
            return
        save = getattr(self.raw_config, "save_config_async", None)
        try:
            if save is not None:
                await save()
            else:
                legacy = getattr(self.raw_config, "save_config", None)
                if legacy is not None:
                    legacy()
        except Exception as exc:  # noqa: BLE001 - 写入失败不影响连接
            logger.warning("[VelocityAuth] 清除配对码后写入配置失败：%s", exc)
            return
        logger.info("[VelocityAuth] 模板 %s 已配对，配置中的配对码已自动清除。", template.name)

    # ------------------------------------------------------------------ 验证监听

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """监听“验证会话”，命中待验证码时上报服务端。"""
        if not self.manager.pending_count():
            return
        text = (event.get_message_str() or "").strip()
        if not text:
            return
        record = self.manager.match_message(
            umo=event.unified_msg_origin,
            text=text,
            sender_id=str(event.get_sender_id()),
            sender_name=event.get_sender_name() or "",
            group_id=str(event.get_group_id() or ""),
        )
        if record is None:
            return
        # 验证码消息不再触发其它插件或 LLM
        event.stop_event()
        if not await self.manager.report_matched(record):
            self.manager.cancel_code(record.code, record.server_key)
            await self._reply(
                record.matched_umo,
                f"验证码上报失败，请在游戏内稍后重试（{record.code}）。",
            )

    async def _on_matched_ack(
        self, template: ServerTemplate, record: PendingCode, consumed: bool
    ) -> None:
        """服务端确认消费结果后，在验证会话里回一条提示。"""
        text = template.success_text if consumed else template.failed_text
        if not text or not record.matched_umo:
            return
        await self._reply(
            record.matched_umo,
            self._format_text(
                text,
                name=record.display_name or record.subject,
                subject=record.subject,
                code=record.code,
                server=template.name,
            ),
        )

    async def _reply(self, umo: str, text: str) -> None:
        try:
            await self.context.send_message(umo, MessageChain([Plain(text)]))
        except Exception as exc:  # noqa: BLE001 - 主动发送失败不影响主流程
            logger.warning("[VelocityAuth] 发送到 %s 失败：%s", umo, exc)

    @staticmethod
    def _format_text(text: str, **values: str) -> str:
        try:
            return string.Formatter().vformat(text, (), values)
        except (KeyError, IndexError, ValueError):
            return text

    # ------------------------------------------------------------------ 指令

    @filter.command_group("vauth", alias={"va", "velocityauth"})
    def vauth(self):
        """VelocityAuth 管理指令。"""

    @vauth.command("会话", alias={"umo", "sid"})
    async def vauth_session(self, event: AstrMessageEvent):
        """查看当前会话 UMO（用于配置验证/管理会话）。"""
        owned = [
            runtime.name
            for runtime in self.manager.admin_runtimes(event.unified_msg_origin)
        ]
        lines = [
            "当前会话信息：",
            f"UMO：{event.unified_msg_origin}",
            f"平台：{event.get_platform_name()}",
            f"消息类型：{event.get_message_type()}",
            f"群号：{event.get_group_id() or '-'}",
            f"发送者：{event.get_sender_id()}",
            f"可作为管理会话的模板：{'、'.join(owned) if owned else '无'}",
        ]
        yield event.plain_result("\n".join(lines))

    @vauth.command("帮助", alias={"help"})
    async def vauth_help(self, event: AstrMessageEvent):
        """查看指令列表。"""
        yield event.plain_result(
            "VelocityAuth 指令：\n"
            "/vauth 会话 —— 查看当前会话 UMO\n"
            "/vauth 状态 [模板] —— 连接与配对状态\n"
            "/vauth 重连 [模板] —— 强制重连\n"
            "/vauth 查询 <游戏ID|渠道账号> —— 查询玩家信息\n"
            "/vauth 统计 [模板] —— 服务器聚合统计"
        )

    @vauth.command("状态", alias={"status"})
    async def vauth_status(self, event: AstrMessageEvent):
        """查看连接、配对与待验证码状态。"""
        self._reload_config()
        args = self._args(event, "状态", "status")
        target = args[0] if args else None
        runtimes = self.manager.runtimes()
        if not runtimes:
            yield event.plain_result("尚未启动任何服务器模板（检查 servers 配置）。")
            return
        lines: list[str] = ["VelocityAuth 状态"]
        matched = False
        for runtime in runtimes:
            if target and runtime.name != target:
                continue
            matched = True
            client = runtime.client
            lines.append(f"· {runtime.name} @ {runtime.template.address}")
            lines.append(f"  连接：{'已就绪' if client.ready else '未就绪'}")
            if client.ready:
                lines.append(
                    f"  会话：{client.session_id or '-'}"
                    f"（服务端 {client.server_id or '-'}，延迟 {client.latency_ms}ms）"
                )
                lines.append(f"  能力：{'、'.join(client.server_capabilities) or '-'}")
            pin = (
                f"（服务端指纹 {client.pinned_fingerprint[:16]}…）" if client.paired else ""
            )
            lines.append(f"  配对：{'已完成' if client.paired else '未完成'}{pin}")
            lines.append(
                f"  会话配置：验证 {len(runtime.template.verify_umos)} 个 /"
                f" 管理 {len(runtime.template.admin_umos)} 个"
            )
            lines.append(f"  待验证码：{self.manager.pending_count(runtime.template)} 个")
            if runtime.last_error:
                lines.append(f"  最近错误：{runtime.last_error}")
        if not matched:
            lines.append(f"未找到模板：{target}")
        yield event.plain_result("\n".join(lines))

    @vauth.command("重连", alias={"reconnect"})
    async def vauth_reconnect(self, event: AstrMessageEvent):
        """强制重连指定的服务器模板。"""
        self._reload_config()
        _, error = self._guard(event)
        if error:
            yield event.plain_result(error)
            return
        args = self._args(event, "重连", "reconnect")
        owned = self._owned(event)
        if args and not any(runtime.name == args[0] for runtime in owned):
            yield event.plain_result(f"未找到或无权操作模板：{args[0]}")
            return
        names = await self.manager.restart(args[0] if args else None)
        if not names:
            yield event.plain_result("没有可重连的模板。")
            return
        yield event.plain_result("已触发重连：" + "、".join(names))

    @vauth.command("查询", alias={"玩家", "account", "player", "query"})
    async def vauth_query(self, event: AstrMessageEvent):
        """按游戏 ID / UUID / 渠道账号查询玩家信息。"""
        self._reload_config()
        runtime, error = self._guard(event)
        if runtime is None:
            yield event.plain_result(error)
            return
        args = self._args(event, "查询", "玩家", "account", "player", "query")
        runtime, args, error = self._pick_template(event, args)
        if runtime is None:
            yield event.plain_result(error)
            return
        if not args:
            yield event.plain_result(QUERY_USAGE)
            return

        kind, value = self._classify(runtime.template, args[0])
        if not value:
            yield event.plain_result("查询值不能为空。")
            return
        if kind not in {"name", "uuid", "ip"} and kind != runtime.template.provider:
            yield event.plain_result(
                f"不支持的前缀 {kind}:（可用 name / uuid / ip / {runtime.template.provider}）。"
            )
            return
        try:
            if kind in {"name", "uuid"}:
                account = await self.manager.account(runtime, kind, value)
                if not account.found:
                    yield event.plain_result(f"未找到账号：{value}")
                    return
                async for result in self._card(
                    event,
                    self.renderer.account_payload(runtime.name, account),
                    self.renderer.account_text(runtime.name, account),
                ):
                    yield result
                return

            lookup = await self.manager.lookup(
                runtime, kind, value, self.settings.command.max_list_rows
            )
            if not lookup.accounts:
                label = "IP" if kind == "ip" else f"渠道 {kind}"
                yield event.plain_result(f"没有匹配到账号（{label}={value}）。")
                return
            if len(lookup.accounts) == 1:
                source = f"来源：{kind}={value} 反查"
                account = await self.manager.account(runtime, "name", lookup.accounts[0])
                if account.found:
                    async for result in self._card(
                        event,
                        self.renderer.account_payload(
                            runtime.name, account, source=source
                        ),
                        self.renderer.account_text(runtime.name, account, source=source),
                    ):
                        yield result
                    return
            rows = len(lookup.accounts)
            payload = self.renderer.lookup_payload(runtime.name, lookup, max_rows=rows)
            payload["notes"] = [
                *payload.get("notes", []),
                f"可用 /vauth 查询 name:{lookup.accounts[0]} 查看详情。",
            ]
            async for result in self._card(
                event,
                payload,
                self.renderer.lookup_text(runtime.name, lookup, max_rows=rows),
            ):
                yield result
        except QueryError as exc:
            yield event.plain_result(self._query_error(exc, runtime))



    @vauth.command("统计", alias={"stats"})
    async def vauth_stats(self, event: AstrMessageEvent):
        """查看服务器聚合统计。"""
        self._reload_config()
        runtime, error = self._guard(event)
        if runtime is None:
            yield event.plain_result(error)
            return
        args = self._args(event, "统计", "stats")
        runtime, _, error = self._pick_template(event, args)
        if runtime is None:
            yield event.plain_result(error)
            return
        try:
            stats = await self.manager.stats(runtime)
        except QueryError as exc:
            yield event.plain_result(self._query_error(exc, runtime))
            return
        if self.settings.render.enabled:
            image = await self.renderer.render_stats(
                self.renderer.stats_payload(runtime.name, stats)
            )
            if image is not None:
                yield event.image_result(str(image))
                return
        yield event.plain_result(self.renderer.stats_text(runtime.name, stats))

    # ------------------------------------------------------------------ 指令工具

    def _owned(self, event: AstrMessageEvent) -> list[ServerRuntime]:
        return self.manager.admin_runtimes(event.unified_msg_origin)

    def _guard(self, event: AstrMessageEvent) -> tuple[ServerRuntime | None, str]:
        """校验管理会话与管理员权限。"""
        owned = self._owned(event)
        if not owned:
            return None, (
                "当前会话不在任何模板的“管理会话 UMO 列表”中，无法使用管理指令。"
                "可在会话内发送 /vauth 会话 获取 UMO 后填入配置。"
            )
        if self.settings.command.require_astrbot_admin and not event.is_admin():
            return None, "需要 AstrBot 管理员权限才能使用管理指令。"
        return owned[0], ""

    def _pick_template(
        self, event: AstrMessageEvent, args: list[str]
    ) -> tuple[ServerRuntime | None, list[str], str]:
        """首个参数若命中本会话可管理的模板名，则作为模板选择。"""
        owned = self._owned(event)
        if not owned:
            return None, args, "当前会话没有可管理的模板。"
        if args:
            for runtime in owned:
                if runtime.name == args[0]:
                    return runtime, args[1:], ""
        return owned[0], args, ""

    @staticmethod
    def _args(event: AstrMessageEvent, *command_names: str) -> list[str]:
        tokens = (event.message_str or "").split()
        if tokens:
            tokens.pop(0)  # 指令组名
        if tokens and tokens[0] in command_names:
            tokens.pop(0)
        return tokens

    @staticmethod
    def _classify(template: ServerTemplate, target: str) -> tuple[str, str]:
        """判定查询类型：name / uuid / ip / <渠道 id>。"""
        if ":" in target:
            prefix, _, value = target.partition(":")
            return prefix.strip().lower(), value.strip()
        value = target.strip()
        digits = value.replace("-", "")
        if (
            len(digits) == UUID_HEX_LENGTH
            and value.count("-") in {0, 4}
            and all(char in HEX_DIGITS for char in digits)
        ):
            return "uuid", value
        if value.isdigit():
            return template.provider, value
        return "name", value

    @staticmethod
    def _query_error(exc: QueryError, runtime: ServerRuntime) -> str:
        mapping = {
            "query_disabled": "服务端未启用查询接口（VelocityAuth 配置 bot.query.enabled）。",
            "bad_request": "查询参数被服务端拒绝（by / value / result 不合法）。",
            "timeout": "查询超时，请稍后重试。",
            "not_ready": "与该服务器的会话尚未就绪。",
            "connection_closed": "连接已断开，稍后会自动重连。",
        }
        hint = mapping.get(exc.code, exc.message or exc.code)
        return f"查询失败（{runtime.name}）：{hint}"

    async def _card(self, event: AstrMessageEvent, payload: dict, text: str):
        """优先发送渲染图，不可用时发送文本。"""
        if self.settings.render.enabled:
            image = await self.renderer.render_card(payload)
            if image is not None:
                yield event.image_result(str(image))
                return
        yield event.plain_result(text)
