# VelocityAuth 验证

连接 VelocityAuth（Velocity 插件），在群里完成验证码验证，并提供玩家信息与服务器统计查询。

![:name](https://count.getloli.com/@astrbot_plugin_velocity_auth?name=astrbot_plugin_velocity_auth&theme=moebooru&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

> [!note]
> 配套 Velocity 端插件提供 bot WebSocket 通道（协议 v1）：一次性配对码建立互信，之后用
> Ed25519 + X25519 + AES-256-GCM 通信，无需 TLS。

## 主要功能

- 群内验证，玩家在游戏里发起验证后，在群里发送验证码即可完成绑定
- 玩家查询，按游戏 ID / UUID / 渠道账号查询账号详情（绑定渠道、最近 IP、游戏身份等）
- 服务器统计，查看账号总数、各渠道绑定数以及近 24 小时 / 7 天 / 30 天的注册与登录数
- 多服务器，每个服务器模板独立连接、独立配置验证群与管理会话
- 图片渲染，查询结果默认渲染为图片，渲染不可用时自动降级为文本

## 命令

| 命令 | 说明 |
| --- | --- |
| `vauth 会话` | 查看当前会话 UMO，用于填写验证会话 / 管理会话 |
| `vauth 查询 <游戏ID\|渠道账号>` | 查询玩家信息，直接填游戏 ID 或渠道账号（如 QQ 号）即可 |
| `vauth 统计 [模板名]` | 查看服务器统计 |
| `vauth 状态 [模板名]` | 查看连接、配对、待验证码与最近错误 |
| `vauth 重连 [模板名]` | 强制重连并重新握手 |
| `vauth 帮助` | 显示命令列表 |

`vauth 查询` 也支持显式指定查询方式：`name:玩家名`、`uuid:…`、`ip:1.2.3.4`、`qq:10001`。
按渠道账号或 IP 查询会反查命中的账号：唯一命中直接展示玩家详情，多个命中则列出账号名。

管理命令仅在「管理会话 UMO 列表」中的会话可用；默认不额外要求 AstrBot 管理员。

## 配置说明

> 支持添加多服务器，每个服务器模板对应一个 VelocityAuth 插件实例。以下仅说明重要配置。

### 服务器模板

- **模板名称** - 用于命令里选择服务器（如 `vauth 统计 生存服`），需唯一
- **服务器地址 / 端口** - 对应 VelocityAuth 的 `bot.listen` / `bot.port`
- **bot 实例标识 / bot 密钥标识** - 对应 `client_id` / `client_key_id`，保持稳定即可
- **渠道 id** - 对应 VelocityAuth 里配置的渠道（如 `qq`），验证码只发给该渠道的会话
- **首次配对** - 服主执行 `/velocityauth pair new` 后，把 `pairing_id` 与 `pairing_code` 填入
- **验证会话 UMO 列表** - 监听这些会话来收验证码
- **管理会话 UMO 列表** - 只有这些会话能使用管理命令
- **验证码匹配方式** - `contains` 消息包含验证码即命中，`exact` 要求整条消息就是验证码
- **验证成功/失败提示** - 群内回执文案，支持 `{name}`、`{subject}`、`{code}`、`{server}`

会话 UMO 可在目标会话发送 `vauth 会话` 获取，形如 `aiocqhttp:GroupMessage:123456`。

> [!important]
> 玩家需要 **@机器人** 或加唤醒词（默认 `/`）发送验证码——AstrBot 只会把唤醒机器人的消息交给插件。
> 建议在 VelocityAuth 的验证提示里直接带上唤醒词，例如「请在群里发送 `/验证码`」。

配对成功后，插件会把双方密钥固化到 `data/plugin_data/astrbot_plugin_velocity_auth/servers.json`
（权限 0600），并自动清空配置里的配对码，不需要手动处理。

### 其他配置

| 配置组 | 说明 |
| --- | --- |
| 图片渲染与字体 | 是否渲染为图片、中文字体目录、是否允许自动下载字体 |
| 连接与心跳 | 初始 / 最大重连间隔、心跳间隔、查询超时 |
| 管理指令 | 是否额外要求 AstrBot 管理员、列表最多展示行数 |

## 依赖

依赖写在 `requirements.txt` 中：

- `aiohttp`：WebSocket 连接
- `cryptography`：Ed25519 / X25519 / HKDF / AES-GCM
- `typst`：渲染查询结果为图片

## 注意事项

- VelocityAuth 侧需要开启 `bot.enabled`；使用查询与统计还要开启 `bot.query.enabled`
- 首次配对前插件不会连接；配对码失效后重新执行 `/velocityauth pair new` 并更新配置，再执行 `vauth 重连`
- 中文字体会自动探测系统字体，找不到时按配置尝试下载到插件数据目录；关闭图片渲染则完全不依赖字体
- 渲染失败不会报错给用户，会自动降级为文本输出
