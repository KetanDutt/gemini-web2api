# gemini-web2api

<p align="center">
  <img src="logo.png" width="200" alt="gemini-web2api logo">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="docs/README.md">文档</a> ·
  <a href="docs/API.md">API 参考</a> ·
  <a href="docs/CHANGELOG.md">更新日志</a>
</p>

将 Google Gemini 网页端转换为 **OpenAI 兼容 API**。无需 Google API key、无需付费、无编译依赖——核心仅使用 Python 标准库。

把任意 OpenAI 客户端指向 `http://localhost:8081/v1` 即可使用：Cherry Studio、ChatBox、NextChat、LobeChat、OpenAI SDK、Codex CLI、Gemini CLI。

```
  你的 OpenAI 客户端  ──►  gemini-web2api  ──►  gemini.google.com
   /v1/chat/completions      协议转换层           StreamGenerate
   /v1/responses                                   (网页端自有接口)
   /v1beta/...
```

## 特性

- **OpenAI 兼容** —— `/v1/chat/completions`、`/v1/completions`、`/v1/responses`、`/v1/models`、`/v1/models/{id}`
- **Google 原生接口** —— `/v1beta/...`，兼容 Gemini CLI
- **流式输出** —— 安装 `httpx` 时为真正的增量 SSE，否则降级为缓冲输出
- **工具调用** —— 同时支持 OpenAI 与 Google 两种格式的 Function Calling，含 `tool_choice`
- **图片输入** —— 支持 URL 与 base64，内置 SSRF 防护，走 Gemini 自有上传通道
- **九个模型** —— Flash、扩展思考、Pro、Auto、Lite，思考深度可调
- **可选鉴权** —— 默认开放；配置密钥后支持 Bearer / `x-api-key` / `x-goog-api-key`，并可选限流
- **自愈能力** —— 自动刷新 Google 的构建标签（`bl`），前端改版不会导致服务失效
- **Web 控制台** —— 打开 `http://localhost:8081/`，含对话 Playground、实时状态、请求活动、模型选择与可直接粘贴的客户端配置
- **生产级打包** —— 非 root Docker 镜像、健康检查、环境变量配置、优雅停机、CI
- **Windows 一键启动** —— 双击 `start.bat` 即可创建虚拟环境、安装依赖、生成安全的 `config.json` 并启动服务
- **零必需依赖** —— Python 3.8+，`httpx` 为可选

## 快速开始

**Windows** —— 双击仓库根目录的 [`start.bat`](start.bat)。它会自动查找 Python、创建虚拟环境、安装依赖、生成一份绑定 `127.0.0.1` 的 `config.json`、启动服务并打开控制台，无需其他操作。

**macOS / Linux**

```bash
pip install httpx          # 可选，但流式输出需要它
python -m gemini_web2api
```

启动后：

| 用途 | 地址 |
|---|---|
| OpenAI Base URL | `http://localhost:8081/v1` |
| Web 控制台 | `http://localhost:8081/` |
| 健康检查 | `http://localhost:8081/health` |
| 状态与指标 | `http://localhost:8081/status` |

也可以正式安装：

```bash
pip install ".[streaming]"
gemini-web2api --port 8081
```

`python gemini_web2api.py` 同样可用——该文件只是一个转发到包的兼容入口。

### Docker

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -e GEMINI_WEB2API_API_KEYS="sk-your-key" \
  ghcr.io/ketandutt/gemini-web2api:latest
```

### Docker Compose

```bash
GEMINI_WEB2API_API_KEYS="sk-your-key" docker compose up -d
```

如果 Google 拒绝 Docker 的 NAT 网段（空回复、403/429），改用 host 网络变体：

```bash
docker compose -f docker-compose.local.yml up -d
```

systemd、反向代理与 Cloudflare Workers 见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 客户端配置

### Cherry Studio / ChatBox / NextChat / 任意 OpenAI 客户端

| 字段 | 值 |
|------|-----|
| Base URL | `http://localhost:8081/v1` |
| API Key | 你配置的任意 `api_keys`；未开启鉴权时随便填 |
| Model | `gemini-3.6-flash` |

### curl

```bash
curl http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-key" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"你好!"}]}'
```

流式（注意 `-N` 关闭 curl 自身的缓冲）：

```bash
curl -N http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"从一数到五"}],"stream":true}'
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8081/v1", api_key="sk-your-key")

print([m.id for m in client.models.list().data])

resp = client.chat.completions.create(
    model="gemini-3.5-flash-thinking",
    messages=[{"role": "user", "content": "解释量子计算"}],
)
print(resp.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "写一首关于大海的俳句"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### Gemini CLI

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

支持的原生端点：

- `GET /v1beta/models` —— 模型列表
- `GET /v1beta/models/{model}` —— 单个模型
- `POST /v1beta/models/{model}:generateContent` —— 非流式生成
- `POST /v1beta/models/{model}:streamGenerateContent` —— 流式生成 (SSE)

### Codex CLI

使用 Responses API (`/v1/responses`)，含完整流式事件序列。

## 可用模型

| 模型 | 类别 | 说明 | 输出量 |
|------|------|------|--------|
| `gemini-3.7-flash` | FAST | 最新全能模型 | ~1.2万字 |
| `gemini-3.6-flash` | FAST | 全能模型（**默认**） | ~1.2万字 |
| `gemini-3.5-flash` | FAST | gemini-3.6-flash 别名 | ~1.2万字 |
| `gemini-3.5-flash-thinking` | THINKING | 扩展思考，输出最长 | **~2万字** |
| `gemini-3.5-flash-thinking-lite` | DYNAMIC | 自适应思考深度 | ~1.5万字 |
| `gemini-3.1-pro` | PRO | 高级推理 —— **需要付费账号 cookie** | ~1.2万字 |
| `gemini-3.1-pro-enhanced` | PRO | Pro + 实验性输出整形 | ~1.2万字 |
| `gemini-auto` | AUTO | 由上游自行选择模型 | 不定 |
| `gemini-flash-lite` | FLASH_LITE | 最快、最轻量 | ~1万字 |

实时列表始终以 `GET /v1/models` 为准。

### 思考深度

在模型名后追加 `@think=N` —— `0` 最深，`4` 最浅：

```
gemini-3.5-flash-thinking@think=0   # 最深（该模型默认）
gemini-3.6-flash@think=0            # 让 Flash 也使用深度思考预算
gemini-3.5-flash-thinking@think=2   # 中等
gemini-3.5-flash-thinking@think=4   # 最浅、最快
```

超出范围或非整数值会返回明确的 400 错误。

## 配置文件

创建 `config.json`（全部字段见 [`config.example.json`](config.example.json)）、使用环境变量，或传命令行参数。优先级：
**默认值 → 配置文件 → 环境变量 → 命令行**。

```json
{
  "port": 8081,
  "host": "0.0.0.0",
  "api_keys": ["sk-your-key"],
  "default_model": "gemini-3.6-flash",
  "cookie_file": null,
  "temporary_chats": false,
  "proxy": null,
  "rate_limit_max": 0,
  "log_requests": true
}
```

每个选项都可以用环境变量设置：

```bash
GEMINI_WEB2API_PORT=9000 \
GEMINI_WEB2API_API_KEYS="sk-one,sk-two" \
GEMINI_WEB2API_TEMPORARY_CHATS=true \
python -m gemini_web2api
```

```bash
python -m gemini_web2api --help          # 查看全部参数
```

完整参考：**[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**

> **默认不开启鉴权。** `api_keys: []` 时任何能访问端口的人都能使用服务。本机使用没问题；对外暴露前请先配置密钥。启动时会打印警告，`/health` 也会报告。

把 `temporary_chats` 设为 `true`，这些单轮请求就不会写入 Google 账号的对话历史。

## 工具调用

```python
resp = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "东京天气怎么样？"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某城市天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }],
)
print(resp.choices[0].message.tool_calls)
```

`tool_choice` 支持 `"none"`、`"auto"`、`"required"` 以及指定函数名。Google 原生请求使用 `toolConfig.functionCallingConfig`（`AUTO`/`ANY`/`NONE`）。

工具调用基于 prompt 实现（该端点没有函数调用协议），因此带 `tools` 的请求无法真正流式输出——服务会缓冲、解析，然后一次性返回 `finish_reason: "tool_calls"`。

## 图片输入

```python
resp = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "描述这张图片"},
        {"type": "image_url", "image_url": {"url": "https://example.com/diagram.png"}},
    ]}],
)
```

支持 `http(s)` URL、base64 `data:` URL 以及 Google 原生的 `inlineData`。MIME 类型由字节内容嗅探得出，不信任客户端声明。远程 URL 若解析到环回、链路本地或私有地址会被拒绝，且每一跳重定向都会重新校验。

## 向 Google 认证

匿名访问对所有模型都有效，但没有具备权益的会话时 `gemini-3.1-pro` 不会真正路由到 Pro 模型。payload 第 79 槽位选择的是**模式**（`3 = PRO`）而非具体模型，Google 只对拥有该权益的账号生效，并且拒绝时不给出任何提示。

真正的 Pro 路由需要 **Gemini Advanced（付费）** 的 cookie。配套的浏览器扩展可以一次性导出全部字段：

```bash
python -m gemini_web2api --cookie-file ./gemini-auth.json
```

`gemini-auth.json` 包含 `cookie`、`sapisid`、`xsrf_token`、`gemini_bl` 和 `auth_user`，**全部会自动生效**。你自己在 `config.json` 中显式设置的值仍然优先。

Cookie 文件可以是普通头字符串（分隔符任意）、JSON 对象、JSON 数组，或 Netscape/curl cookie jar（包括 `#HttpOnly_` 记录）。

完整指南：**[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md)** ·
扩展安装：**[gemini-cookie-sync-extension/SETUP.md](gemini-cookie-sync-extension/SETUP.md)**

### 手动获取 Cookie

1. 打开 Chrome，访问 [gemini.google.com](https://gemini.google.com) 并登录
2. 开发者工具 (F12) → Application → Cookies → `https://gemini.google.com`
3. 复制 `SID`、`HSID`、`SSID`、`APISID`、`SAPISID`、`__Secure-1PSID`
4. 写入文件：

```
SID=你的SID值; HSID=你的HSID值; SSID=你的SSID值; APISID=你的APISID值; SAPISID=你的SAPISID值; __Secure-1PSID=你的1PSID值
```

如果已登录页面 URL 带账号序号（如 `https://gemini.google.com/u/1/app`），把 `auth_user` 设为该序号。XSRF token 在页面源码中名为 `SNlM0e`，填入 `xsrf_token`。

Pro 路由需要 **Gemini Advanced** 付费订阅。免费账号的 cookie 可以通过认证，但会静默回退到 Flash。

## 代理配置

如果无法直接访问 `gemini.google.com`：

```bash
python -m gemini_web2api --proxy http://127.0.0.1:7890
```

```json
{"proxy": "http://127.0.0.1:7890"}
```

```bash
export HTTPS_PROXY=http://127.0.0.1:7890   # 未设置 proxy 时自动检测
```

支持 Clash、V2Ray、Shadowsocks 等任何 HTTP 代理。

## 监控

```bash
curl -s http://localhost:8081/health | python3 -m json.tool   # 存活探测，始终公开
curl -s http://localhost:8081/status | python3 -m json.tool   # 指标 + 脱敏配置
```

`/status` 返回请求计数、按状态码的错误计数、平均延迟、延迟直方图、按模型统计，以及最近请求的 `history` 数组。也可以直接打开 `/` 的 Web 控制台，内容相同，另带可流式测试的对话 Playground 与可筛选的活动日志。

`history` 保留最近 `history_max` 条请求（默认 200，上限 1000，设为 `0` 即关闭），字段包括时间戳、`X-Request-Id`、方法、路径、状态码、**实际解析后的**模型、耗时与客户端地址。提示词、响应内容与凭据一律不记录，查询字符串也会被剥离。由于条目含客户端地址，`history` 仅通过需鉴权的 `/status` 提供，绝不会嵌入公开的 `GET /` 页面。

每个响应都带 `X-Request-Id`，在服务端日志与 history 条目中同时回显，便于把浏览器里的一次请求对应到具体日志行。

## 文档

| | |
|---|---|
| [docs/README.md](docs/README.md) | 文档索引 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 请求流程、模块划分、协议细节 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 全部配置项、默认值与设置方式 |
| [docs/API.md](docs/API.md) | 全部端点及请求/响应示例 |
| [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) | API 密钥、Cookie、XSRF、`auth_user` |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker、Compose、systemd、反向代理、Workers |
| [docs/SECURITY.md](docs/SECURITY.md) | 威胁模型与默认防护 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 现象 → 原因 → 解决 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 目录结构、测试、新增模型、发布清单 |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | 如何提 issue 与 PR、CI 检查项、项目约定 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本历史 |
| [docs/AUDIT.md](docs/AUDIT.md) | 1.2.0 缺陷审计（含复现方式） |

> 文档目前为英文。

## 已知限制

提 issue 前请先看这里——大部分“故障”报告都属于以下之一。

- **没有付费会话，Pro 就不是 Pro。** `gemini-3.1-pro` 设置的是 UI 模式偏好，不是后端模型切换。免费账号能通过认证，但会静默回退到 Flash。
- **仅单轮对话。** 每次请求都是带新会话 ID 的独立对话。多轮上下文只是因为客户端重发历史、服务端把它拼成一段 prompt——长对话因此消耗更多 token，最终可能超出上游可接受的范围。
- **非官方协议。** 本项目逆向的是私有网页接口。Google 随时可能改变格式、轮换构建标签或收紧限流。构建标签可自愈；格式变更则需要更新代码。
- **频率限制。** Google 会限制高频请求，匿名流量更早。重试是自动的，也可以自行限流，但持续高负载仍可能被封。
- **token 数为估算值。** Gemini 网页端不返回用量，因此按“字符数 ÷ 4”估算。
- **采样参数会被忽略。** `temperature`、`top_p`、`max_tokens`、`n` 会被接受但丢弃——上游没有对应控制。
- **联网搜索不是开关。** 由 Gemini 自行决定是否搜索。
- **服务条款。** 自动化使用消费级网页接口可能违反 Google ToS，请自行评估风险。

## 工作原理

服务向 Gemini 网页端使用的同一个 `StreamGenerate` 接口发请求，在 OpenAI JSON 与 Gemini 内部 protobuf-like 帧格式之间转换。模型选择由 payload 第 `[79]` 字段控制，映射自 Gemini 前端 JS 中的 `MODE_CATEGORY` 枚举。响应是换行分隔的 JSON，且各帧是**累积式**的——每帧都重复当前完整答案——所以最后一个非空帧即为全文。

细节（含 payload 槽位表与图片上传流程）：**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## 开发

```bash
pip install -e ".[dev]"
python -m unittest discover -s tests -t .
ruff check gemini_web2api tests
```

503 个测试，全部离线运行——Gemini 协议在帧级别被模拟。CI 覆盖 Python 3.8–3.13、无第三方依赖的纯标准库运行、构建并安装 wheel 的校验、lint 与 Docker 构建。

**[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**

## 系统要求

- Python 3.8+
- `httpx` —— 可选但强烈建议；缺失时 `stream: true` 只返回一个缓冲块
- 能访问 `gemini.google.com`（部分地区需代理）

## Cloudflare Workers

[`cloudflare/`](cloudflare/README.MD) 是一个独立的无服务器移植版本，支持多 cookie 轮换、浏览器指纹轮换与请求抖动。文档为中文。

它**不是**从 Python 代码构建的，两者在两个方向上都有差异。Worker 额外提供多账号 cookie 轮换、指纹轮换与请求抖动；但缺少图片输入（图片部分会被**静默丢弃**）、`/v1/completions`、`/ready` 与 `/status` 探针，且任何未匹配的 `POST /v1/*` 都会被转发到 chat completions 而不是返回 501。它收录 8 个模型，本服务为 9 个——缺少 `gemini-3.1-pro-enhanced`，因为该模型需要的 payload 槽位 Worker 并未分配。选型前请阅读[差异对照表](cloudflare/README.MD#-与-python-版本的差异)。

两者的版本号是独立序列：Worker 为 `1.6.0-cf-multifingerprint`，本包为 `1.2.0`。

## 致谢

- [linux.do](https://linux.do) 社区
- 开源 API 代理生态

## License

MIT —— 见 [LICENSE](LICENSE)。

---

## 致谢

本项目的开发 agent 能力由 [GenericAgent](https://github.com/lsdefine/GenericAgent) 提供。

### 🚩 友情链接

[![GenericAgent](https://img.shields.io/badge/Agent_Framework-GenericAgent-orange?style=for-the-badge&logo=github)](https://github.com/lsdefine/GenericAgent)
[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)
