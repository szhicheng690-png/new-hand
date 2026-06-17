# 大模型聚合平台 MVP

这是一个前端 HTML + 后端 Python 的可运行原型，对应架构图里的核心能力：

- 对话界面、模型切换、用户额度看板
- API Key 管理与供应商 Base URL 配置
- Prompt 模板管理
- 对话历史与用量统计
- SSE 流式响应
- OpenAI 兼容接口：`POST /v1/chat/completions`
- 简单路由、限流、额度扣减、故障转移骨架

## 启动

```powershell
python app.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

未配置真实供应商 Key 时，后端会返回本地模拟响应，方便先验证业务流程。

## 接口示例

```powershell
$body = @{
  model = "gpt-4o-mini"
  stream = $false
  messages = @(
    @{ role = "user"; content = "介绍一下这个平台的架构" }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-User-ID" = "demo" } `
  -Body $body
```

## 目录

```text
app.py              Python 后端，HTTP API、SSE、SQLite、模型适配器
static/index.html   单页 HTML 前端
static/styles.css   界面样式
static/app.js       前端交互与流式读取
data/platform.db    首次启动后自动生成
```

## 后续可扩展

- 将 API Key 加密替换为 KMS、Vault 或云厂商密钥服务
- 接入 JWT / OAuth2，并把用户、组织、角色拆分成独立表
- 将限流迁移到 Redis，支持集群部署
- 对接真实厂商 SDK，分别处理 Anthropic、DashScope、智谱等非 OpenAI 原生协议
- 增加管理后台、审计日志、模型价格表、团队预算与告警
