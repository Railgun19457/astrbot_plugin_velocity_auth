"""插件内部数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QUERY_TYPE_ACCOUNT = "query.account"
QUERY_TYPE_LOOKUP = "query.lookup"
QUERY_TYPE_STATS = "query.stats"

STATUS_LABELS = {
    "active": "正常",
    "pending_binding": "待绑定",
    "locked": "已锁定",
    "deleted": "已注销",
}

IDENTITY_LABELS = {"java": "Java", "bedrock": "基岩版"}


@dataclass(slots=True)
class ServerTemplate:
    """一个 VelocityAuth 服务器模板。"""

    index: int
    name: str
    enabled: bool
    host: str
    port: int
    client_id: str
    client_key_id: str
    provider: str
    pairing_id: str
    pairing_code: str
    verify_umos: tuple[str, ...] = ()
    admin_umos: tuple[str, ...] = ()
    match_mode: str = "contains"
    success_text: str = ""
    failed_text: str = ""

    @property
    def key(self) -> str:
        """稳定标识：用于状态文件键与日志。"""
        return f"{self.name}@{self.host}:{self.port}"

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def signature(self) -> tuple[object, ...]:
        """连接相关字段的签名。

        ``pairing_id`` / ``pairing_code`` 不参与：配对成功后插件会自行清空配对码，
        不应因此判定“模板被修改”而重建连接。
        """
        return (
            self.index,
            self.name,
            self.enabled,
            self.host,
            self.port,
            self.client_id,
            self.client_key_id,
            self.provider,
            self.verify_umos,
            self.admin_umos,
            self.match_mode,
            self.success_text,
            self.failed_text,
        )

    def scopes(self) -> str:
        """pair.hello / auth.hello 的监听位置展示串（群号，`,` 连接）。"""
        scopes: list[str] = []
        for umo in self.verify_umos:
            parts = umo.split(":")
            if len(parts) >= 3 and parts[1] == "GroupMessage" and parts[2] not in scopes:
                scopes.append(parts[2])
        return ",".join(scopes)


@dataclass(slots=True)
class PendingCode:
    """服务端登记、等待群内命中的验证码。"""

    code: str
    server_key: str
    provider: str
    expires_at: float
    registered_at: float
    matched: bool = False
    matched_umo: str = ""
    subject: str = ""
    display_name: str = ""
    group_id: str = ""

    def expired(self, now: float) -> bool:
        return now > self.expires_at


@dataclass(slots=True)
class BindingView:
    """``provider:identifier:verified_at``。"""

    provider: str
    identifier: str
    verified_at: int

    @property
    def label(self) -> str:
        return f"{self.provider}: {self.identifier}"

    @property
    def verified_text(self) -> str:
        return format_time(self.verified_at)


@dataclass(slots=True)
class IdentityView:
    """``kind:identifier``。"""

    kind: str
    identifier: str

    @property
    def label(self) -> str:
        kind = IDENTITY_LABELS.get(self.kind, self.kind or "未知")
        return f"{kind}: {self.identifier}"


@dataclass(slots=True)
class AccountView:
    found: bool = False
    id: int = 0
    name: str = ""
    uuid: str = ""
    status: str = ""
    premium_uuid: str = ""
    last_server: str = ""
    last_ip: str = ""
    created_at: int = 0
    last_login_at: int = 0
    bindings: list[BindingView] = field(default_factory=list)
    identities: list[IdentityView] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status or "-")

    @property
    def created_text(self) -> str:
        return format_time(self.created_at)

    @property
    def last_login_text(self) -> str:
        return format_time(self.last_login_at)


@dataclass(slots=True)
class LookupView:
    found: bool = False
    by: str = ""
    value: str = ""
    count: int = 0
    accounts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StatsView:
    """``query.stats.result``：字段按 ``前缀_后缀`` 动态扩展，保留原始字典。"""

    raw: dict[str, str] = field(default_factory=dict)

    def number(self, key: str, default: int = 0) -> int:
        try:
            return int(self.raw.get(key, "") or default)
        except (TypeError, ValueError):
            return default

    @property
    def accounts_total(self) -> int:
        return self.number("accounts_total")

    @property
    def online(self) -> int:
        return self.number("online")

    @property
    def bot_connected(self) -> int:
        return self.number("bot_connected")

    def accounts_by_status(self) -> dict[str, int]:
        return self._prefixed("accounts_", skip={"accounts_total"})

    def bound_by_provider(self) -> dict[str, int]:
        return self._prefixed("bound_")

    def registered(self) -> list[tuple[str, int]]:
        return self._windows("registered_")

    def logins(self) -> list[tuple[str, int]]:
        return self._windows("logins_")

    def _prefixed(self, prefix: str, *, skip: set[str] | None = None) -> dict[str, int]:
        skip = skip or set()
        out: dict[str, int] = {}
        for key, value in self.raw.items():
            if key in skip or not key.startswith(prefix):
                continue
            try:
                out[key[len(prefix) :]] = int(value or 0)
            except ValueError:
                continue
        return dict(sorted(out.items()))

    def _windows(self, prefix: str) -> list[tuple[str, int]]:
        order = {"24h": 0, "7d": 1, "30d": 2}
        windows = self._prefixed(prefix)
        return sorted(windows.items(), key=lambda item: order.get(item[0], 99))


def split_joined(raw: str, separator: str) -> list[str]:
    """按分隔符拆分协议里的连接串，丢弃空项。"""
    return [chunk.strip() for chunk in (raw or "").split(separator) if chunk.strip()]


def parse_time(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_time(timestamp: int) -> str:
    """unix 秒 → 本地时间文本；0 表示无记录。"""
    if not timestamp:
        return "-"
    import datetime

    try:
        return datetime.datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "-"


def parse_account(payload: dict[str, Any]) -> AccountView:
    """解析 ``query.account.result``（值均为字符串）。"""
    text = {key: ("" if value is None else str(value)) for key, value in payload.items()}

    def as_int(key: str) -> int:
        try:
            return int(text.get(key, "") or 0)
        except ValueError:
            return 0

    bindings: list[BindingView] = []
    for chunk in split_joined(text.get("bindings", ""), ";"):
        provider, _, rest = chunk.partition(":")
        identifier, _, verified = rest.rpartition(":")
        if not identifier:
            identifier, verified = rest, ""
        bindings.append(
            BindingView(
                provider=provider,
                identifier=identifier,
                verified_at=parse_time(verified),
            )
        )

    identities: list[IdentityView] = []
    for chunk in split_joined(text.get("identities", ""), ","):
        kind, _, identifier = chunk.partition(":")
        identities.append(IdentityView(kind=kind, identifier=identifier))

    return AccountView(
        found=text.get("found", "").lower() in {"true", "1", "yes"},
        id=as_int("id"),
        name=text.get("name", ""),
        uuid=text.get("uuid", ""),
        status=text.get("status", ""),
        premium_uuid=text.get("premium_uuid", ""),
        last_server=text.get("last_server", ""),
        last_ip=text.get("last_ip", ""),
        created_at=as_int("created_at"),
        last_login_at=as_int("last_login_at"),
        bindings=bindings,
        identities=identities,
    )


def parse_lookup(payload: dict[str, Any]) -> LookupView:
    text = {key: ("" if value is None else str(value)) for key, value in payload.items()}
    accounts = [item for item in (text.get("accounts", "") or "").split(",") if item]
    try:
        count = int(text.get("count", "") or len(accounts))
    except ValueError:
        count = len(accounts)
    return LookupView(
        found=text.get("found", "").lower() in {"true", "1", "yes"},
        by=text.get("by", ""),
        value=text.get("value", ""),
        count=count,
        accounts=accounts,
    )


def parse_stats(payload: dict[str, Any]) -> StatsView:
    return StatsView(
        raw={key: ("" if value is None else str(value)) for key, value in payload.items()}
    )

