"""VelocityAuth bot 协议实现与业务编排。

子模块：

- ``protocol``：规范化、签名输入、内层明文与帧解析（纯文本，无外部依赖）
- ``crypto``：Ed25519 / X25519 / HKDF / AES-GCM 密封帧
- ``client``：单模板的连接与握手状态机
- ``manager``：多模板监督、验证码注册表、查询入口
- ``render``：Typst 卡片渲染与文本降级
- ``config`` / ``state`` / ``models``：配置、持久状态与数据模型

这里不导出任何符号，避免只需要协议层时被迫加载 astrbot 运行时。
"""
