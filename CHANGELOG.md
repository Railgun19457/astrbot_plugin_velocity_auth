# 更新日志

## v0.1.0

初始实现。

- 实现 VelocityAuth bot 协议 v1 客户端：一次性配对（`pair.*`）、会话认证（`auth.*`）、
  Ed25519 签名校验、X25519 临时密钥 + HKDF-SHA256 会话密钥、
  AES-256-GCM 密封帧（`seq` 按方向单调递增并拒绝重放）、双向 `auth.confirm` 密钥确认
- 支持 `servers` 模板列表：每个模板独立配置连接信息、验证会话 UMO、管理会话 UMO、
  渠道 id、匹配方式与提示文案；配对成功后把 bot 身份与服务端公钥固化到
  `data/plugin_data/astrbot_plugin_velocity_auth/servers.json`（0600），
  并自动清空配置里的配对码
- 断线指数退避重连；未完成配对时有限重试后停止，避免无效连接风暴
- 验证流程：`verify.watch` 登记验证码 → 验证会话消息命中 → `verify.matched` 上报 →
  `verify.matched.ack` 后在群内回执（支持成功/失败文案与占位符）
- 命令：`vauth 查询`（按游戏 ID / UUID / 渠道账号查询玩家，唯一命中直接展示详情）、
  `vauth 统计`、`vauth 状态`、`vauth 重连`、`vauth 会话`、`vauth 帮助`
- 清理逻辑：服务端断开或取消验证码后清理待验证码；渲染产物按数量与卸载时清理
- Typst 渲染玩家信息卡片与统计卡片为 PNG，中文字体自动探测（可选自动下载），
  渲染不可用时自动降级为文本
