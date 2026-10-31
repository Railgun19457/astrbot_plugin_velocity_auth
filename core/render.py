"""Typst 卡片渲染：玩家信息、列表与统计。

- 模板位于插件 ``templates/`` 目录，通过 ``sys.inputs`` 传入 JSON 数据；
- 中文字体优先使用系统字体，其次使用插件数据目录里缓存/下载的字体；
- 渲染失败时由调用方回退为文本（见 ``to_*_text``）。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import logger

from .config import PluginSettings
from .models import (
    IDENTITY_LABELS,
    STATUS_LABELS,
    AccountView,
    LookupView,
    StatsView,
)
FONT_CACHE_DIR_NAME = "fonts"
CHINESE_FONT_FILENAME = "wqy-microhei.ttc"
CHINESE_FONT_URLS = (
    "https://raw.githubusercontent.com/anthonyfok/fonts-wqy-microhei/master/wqy-microhei.ttc",
    "https://github.com/anthonyfok/fonts-wqy-microhei/raw/master/wqy-microhei.ttc",
)
FONT_DOWNLOAD_TIMEOUT = 60
FONT_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024

# (字体文件, 该文件在 Typst 里应使用的 family 名)
FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
    ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
    ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf", "Noto Sans CJK SC"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
    ("/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc", "WenQuanYi Micro Hei"),
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
)

# Typst 逐个尝试的字体族；系统里找不到的会被跳过
FALLBACK_FAMILIES = (
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "PingFang SC",
    "WenQuanYi Micro Hei",
    "SimHei",
    "DejaVu Sans",
)

PAGE_WIDTH_PT = 660.0
RENDER_PPI = 192.0


@dataclass(slots=True)
class FontResolution:
    """字体解析结果：传给 Typst 的目录与字体族优先级。"""

    font_dirs: tuple[str, ...]
    families: tuple[str, ...]


def _typst_available() -> bool:
    try:
        import typst  # noqa: F401
    except ImportError:
        return False
    return True


class TypstRenderer:
    """把查询结果渲染为 PNG。"""

    def __init__(
        self,
        template_dir: Path,
        data_dir: Path,
        temp_dir: Path,
        settings: PluginSettings,
    ) -> None:
        self._template_dir = template_dir
        self._data_dir = data_dir
        self._temp_dir = temp_dir
        self._settings = settings
        self._fonts: FontResolution | None = None
        self._lock = asyncio.Lock()

    def update_settings(self, settings: PluginSettings) -> None:
        """热更新配置（字体的解析结果在下次渲染时重算）。"""
        if settings.render.font_paths != self._settings.render.font_paths:
            self._fonts = None
        self._settings = settings

    # ------------------------------------------------------------------ 字体

    def resolve_fonts(self) -> FontResolution:
        if self._fonts is not None:
            return self._fonts

        dirs: list[str] = []
        families: list[str] = []

        for configured in self._settings.render.font_paths:
            path = Path(configured)
            if path.is_dir():
                dirs.append(str(path))
            elif path.is_file():
                dirs.append(str(path.parent))

        cached = self._data_dir / FONT_CACHE_DIR_NAME
        cached_font = cached / CHINESE_FONT_FILENAME
        if cached_font.exists():
            dirs.append(str(cached))
            families.append("WenQuanYi Micro Hei")

        for raw_path, family in FONT_CANDIDATES:
            path = Path(raw_path)
            if path.is_file():
                dirs.append(str(path.parent))
                families.append(family)

        if not dirs and self._settings.render.auto_download_font:
            downloaded = self._download_font(cached_font)
            if downloaded is not None:
                dirs.append(str(downloaded.parent))
                families.append("WenQuanYi Micro Hei")

        if not dirs:
            logger.warning(
                "[VelocityAuth] 未找到中文字体，图片渲染将回退为文本；"
                "可在插件配置的“中文字体目录”里手动指定。"
            )

        ordered: list[str] = []
        for family in (*families, *FALLBACK_FAMILIES):
            if family not in ordered:
                ordered.append(family)
        self._fonts = FontResolution(
            font_dirs=tuple(dict.fromkeys(dirs)), families=tuple(ordered)
        )
        logger.info(
            "[VelocityAuth] Typst 字体目录：%s", "、".join(self._fonts.font_dirs) or "(系统字体)"
        )
        return self._fonts

    def _download_font(self, target: Path) -> Path | None:
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        for url in CHINESE_FONT_URLS:
            try:
                logger.info("[VelocityAuth] 尝试下载中文字体：%s", url)
                request = urllib.request.Request(url, headers={"User-Agent": "AstrBot"})
                with urllib.request.urlopen(request, timeout=FONT_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
                    data = resp.read(FONT_DOWNLOAD_MAX_BYTES + 1)
                if len(data) > FONT_DOWNLOAD_MAX_BYTES:
                    raise ValueError("字体文件超过大小限制")
                temp = target.with_suffix(target.suffix + ".tmp")
                temp.write_bytes(data)
                temp.replace(target)
                logger.info("[VelocityAuth] 中文字体已缓存：%s", target)
                return target
            except Exception as exc:  # noqa: BLE001 - 逐个镜像尝试
                logger.warning("[VelocityAuth] 下载中文字体失败：%s", exc)
        return None

    # ------------------------------------------------------------------ 渲染

    async def render_card(self, payload: dict) -> Path | None:
        return await self._render("card.typ", payload)

    async def render_stats(self, payload: dict) -> Path | None:
        return await self._render("stats.typ", payload)

    async def _render(self, template_name: str, payload: dict) -> Path | None:
        if not self._settings.render.enabled:
            return None
        if not _typst_available():
            logger.warning("[VelocityAuth] 未安装 typst，已回退为文本输出。")
            return None
        template_path = self._template_dir / template_name
        if not template_path.is_file():
            logger.warning("[VelocityAuth] 缺少模板文件：%s", template_path)
            return None

        fonts = self.resolve_fonts()
        theme = {
            "font_families": list(fonts.families),
            "page_width": PAGE_WIDTH_PT,
        }
        sys_inputs = {
            "payload": json.dumps(payload, ensure_ascii=False),
            "theme": json.dumps(theme, ensure_ascii=False),
        }
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        target = self._temp_dir / f"{template_name.split('.')[0]}-{uuid.uuid4().hex[:12]}.png"

        async with self._lock:
            try:
                await asyncio.to_thread(self._compile, template_path, sys_inputs, target, fonts)
            except Exception as exc:  # noqa: BLE001 - 渲染失败统一降级
                logger.warning("[VelocityAuth] Typst 渲染失败（%s）：%s", template_name, exc)
                return None
        self._cleanup_old_images()
        return target

    def _compile(
        self,
        template_path: Path,
        sys_inputs: dict[str, str],
        target: Path,
        fonts: FontResolution,
    ) -> None:
        import typst

        data = typst.compile(
            str(template_path),
            root=str(self._template_dir),
            font_paths=list(fonts.font_dirs),
            format="png",
            ppi=RENDER_PPI,
            sys_inputs=sys_inputs,
        )
        target.write_bytes(data)

    def _cleanup_old_images(self, keep: int = 40) -> None:
        try:
            images = sorted(
                self._temp_dir.glob("*.png"), key=lambda path: path.stat().st_mtime
            )
        except OSError:
            return
        for path in images[:-keep]:
            try:
                path.unlink()
            except OSError:
                continue

    # ------------------------------------------------------------------ 载荷

    def account_payload(
        self,
        server: str,
        account: AccountView,
        *,
        source: str = "",
        extra_notes: list[str] | None = None,
    ) -> dict:
        rows = [
            {"label": "账号 ID", "value": str(account.id)},
            {"label": "UUID", "value": account.uuid or "-"},
            {"label": "状态", "value": account.status_label},
            {"label": "注册时间", "value": account.created_text},
            {"label": "最近登录", "value": account.last_login_text},
            {"label": "最近 IP", "value": account.last_ip or "-"},
            {"label": "上次所在服", "value": account.last_server or "-"},
        ]
        sections: list[dict] = [{"title": "账号信息", "rows": rows}]

        binding_rows = [
            {
                "label": binding.provider,
                "value": f"{binding.identifier}（{binding.verified_text} 验证）",
            }
            for binding in account.bindings
        ]
        sections.append(
            {
                "title": "已绑定渠道",
                "rows": binding_rows,
                "empty": "未绑定任何渠道",
            }
        )

        identity_rows = [
            {
                "label": IDENTITY_LABELS.get(identity.kind, identity.kind or "未知"),
                "value": identity.identifier,
            }
            for identity in account.identities
        ]
        sections.append(
            {
                "title": "游戏身份",
                "rows": identity_rows,
                "empty": "无关联游戏身份",
            }
        )

        subtitle = f"来源：{source}" if source else ""
        notes = list(extra_notes or [])
        return {
            "title": "玩家信息",
            "headline": account.name,
            "subtitle": subtitle,
            "server": server,
            "sections": sections,
            "notes": notes,
            "footer": f"生成于 {_now_text()}",
        }

    def lookup_payload(
        self, server: str, view: LookupView, *, max_rows: int, source: str = ""
    ) -> dict:
        shown = view.accounts[:max_rows]
        rows = [[name] for name in shown]
        notes: list[str] = []
        if len(view.accounts) > len(shown):
            notes.append(f"仅展示前 {len(shown)} 条，共 {view.count} 条。")
        by_label = "IP" if view.by == "ip" else f"渠道 {view.by}"
        return {
            "title": "反查结果",
            "headline": f"{by_label} {view.value}",
            "subtitle": source,
            "server": server,
            "tables": [
                {
                    "columns": ["绑定的账号名"],
                    "rows": rows,
                    "empty": "没有匹配到任何账号",
                }
            ],
            "notes": notes,
            "footer": f"共 {view.count} 条 · 生成于 {_now_text()}",
        }

    def stats_payload(self, server: str, view: StatsView) -> dict:
        kpis = [
            {"label": "总账号", "value": str(view.accounts_total)},
            {"label": "在线", "value": str(view.online)},
            {"label": "bot 连接", "value": str(view.bot_connected)},
        ]
        sections: list[dict] = []
        status_rows = [
            {"label": _status_label(name), "value": str(count)}
            for name, count in view.accounts_by_status().items()
        ]
        sections.append({"title": "账号状态", "rows": status_rows, "empty": "无数据"})
        provider_rows = [
            {"label": name, "value": str(count)}
            for name, count in view.bound_by_provider().items()
        ]
        sections.append({"title": "各渠道绑定账号数", "rows": provider_rows, "empty": "无数据"})
        windows = [
            ("注册", view.registered()),
            ("成功登录", view.logins()),
        ]
        for title, items in windows:
            sections.append(
                {
                    "title": f"近{title}",
                    "rows": [
                        {"label": _window_label(name), "value": str(count)}
                        for name, count in items
                    ],
                    "empty": "无数据",
                }
            )
        return {
            "title": "服务器统计",
                "headline": "",
            "subtitle": "",
            "server": server,
            "kpis": kpis,
            "sections": sections,
            "notes": [],
            "footer": f"生成于 {_now_text()}",
        }

    # ------------------------------------------------------------------ 降级文本

    def account_text(self, server: str, account: AccountView, *, source: str = "") -> str:
        lines = [
            f"【玩家信息】{server}",
            f"账号：{account.name}（#{account.id}）",
            f"UUID：{account.uuid or '-'}",
            f"状态：{account.status_label}",
            f"注册：{account.created_text}",
            f"最近登录：{account.last_login_text}",
            f"最近 IP：{account.last_ip or '-'}",
            f"上次所在服：{account.last_server or '-'}",
        ]
        if source:
            lines.append(f"来源：{source}")
        if account.bindings:
            lines.append("已绑定渠道：")
            lines.extend(f"  · {item.label}（{item.verified_text}）" for item in account.bindings)
        else:
            lines.append("已绑定渠道：无")
        if account.identities:
            lines.append("游戏身份：")
            lines.extend(f"  · {item.label}" for item in account.identities)
        return "\n".join(lines)

    def lookup_text(self, server: str, view: LookupView, *, max_rows: int) -> str:
        by_label = "IP" if view.by == "ip" else f"渠道 {view.by}"
        lines = [
            f"【反查结果】{server}",
            f"{by_label}：{view.value}",
            f"命中 {view.count} 个账号：",
        ]
        lines.extend(f"  · {name}" for name in view.accounts[:max_rows])
        if not view.accounts:
            lines.append("  （无）")
        return "\n".join(lines)

    def stats_text(self, server: str, view: StatsView) -> str:
        lines = [
            f"【服务器统计】{server}",
            f"总账号：{view.accounts_total}",
            f"在线：{view.online}",
            f"bot 连接：{view.bot_connected}",
        ]
        lines.extend(
            f"{_status_label(name)}：{count}"
            for name, count in view.accounts_by_status().items()
        )
        lines.extend(
            f"已绑定 {name}：{count}" for name, count in view.bound_by_provider().items()
        )
        lines.extend(
            f"{_window_label(name)}注册：{value}" for name, value in view.registered()
        )
        lines.extend(f"{_window_label(name)}登录：{value}" for name, value in view.logins())
        return "\n".join(lines)

    def cleanup(self) -> None:
        """卸载时清理渲染产物。"""
        if not self._temp_dir.exists():
            return
        for path in self._temp_dir.glob("*.png"):
            try:
                path.unlink()
            except OSError:
                continue


def _status_label(name: str) -> str:
    return STATUS_LABELS.get(name, name)


def _window_label(name: str) -> str:
    return {"24h": "24 小时", "7d": "7 天", "30d": "30 天"}.get(name, name)


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
