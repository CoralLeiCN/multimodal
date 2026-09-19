# 品牌聊天 Agent 后端

[English](chat_agent_backend.md) | 简体中文

英文版为主文档。此后端实现“配置公司品牌 → 开启聊天 → 引用搜索结果图片 ID →
按品牌风格生成 → 在同一聊天继续修改”的产品流程，替代原设计中单次生成表单的主流程。
搜索页聊天侧栏已接入此后端。

## 聊天服务与 sandbox 执行端

Gemini Flash 在可信后端处理聊天，读取品牌和会话历史，回复或澄清需求。只有用户明确
要求生图／编辑时，才整理出执行任务交给 Modal sandbox。问候和澄清不会创建 sandbox。
Worker 异步处理聊天，不把长时间模型调用放进 API 请求。

流程：用户消息与品牌 → Gemini Flash 整理任务 → sandbox 按 ID 查图、调用 Nano Banana、
检查并最多修订一次 → 返回图片与执行状态 → Gemini Flash 组织聊天回复。
Sandbox 只收到本轮用户要求、整理后的 prompt、图片 ID／已有 asset ID、比例、品牌版本、
本轮生效的品牌快照与覆盖项，不接收聊天历史，也没有聊天或回复工具。
工具网关代理外部调用，模型、存储和数据库密钥留在可信服务。

**优先级：本轮明确要求 > 相关历史要求 > 品牌预设。**
例如品牌预设红色，用户要求本轮用蓝色，则复制品牌快照并覆盖为蓝色。
colors、preserve、avoid 的明确覆盖会合并到任务里，未指定的品牌特征继续保留。
品牌原始版本不修改。Nano Banana 生图与视觉评估都使用本轮生效的规则，
不会仅因用户指定颜色与品牌预设不同就判为不合格。原始本轮要求也随任务传递。

执行完成或失败后，聊天服务调用 Flash 解释结果。若最终总结失败，仍保留真实生成图片并
返回备用回复。下一轮默认使用最后成功生成的图片，也可显式选择其他图片。
会话、消息与结果持久保存，空闲会话不保留 sandbox。

聊天模型默认 `AGENT_CHAT_MODEL=gemini-3.8-flash`，使用 Interactions、结构化 reply／execute、
`store=False` 和应用持久化历史。固定 SDK 存在零重试配置被改写的问题，因此显式关闭
Interactions resource 的重试；所有付费调用先记录意图，结果不确定时不盲目重放。
Logfire 的 `chat.prepare`、sandbox 工具与 `chat.result` span 连接整个流程。

状态：`chat_queued → chat_preparing` 后直接回复，或进入
`queued → starting → running → execution_done → chat_returning → succeeded/failed`。
取消和超时继续可用。

## API

所有接口复用工作空间 bearer key 或签名 cookie；品牌、上传、素材下载接口继续可用。

| 接口 | 行为 |
| --- | --- |
| `POST /api/v1/agent/conversations` | 使用 `brand_version` 与可选 `title` 创建会话 |
| `GET /api/v1/agent/conversations` | 按最近活动列出最多 100 个会话 |
| `GET /api/v1/agent/conversations/{id}` | 读取消息、每轮状态／错误、附件及来源 |
| `POST /api/v1/agent/conversations/{id}/messages` | 提交 `content` 和可选 `subject_asset_id`；必须带 `Idempotency-Key`；返回 202、消息 ID 与 run |
| `GET /api/v1/agent/runs/{run_id}/events` | SSE 进度，支持 Last-Event-ID 重连；回复发布时产生 assistant_message |
| `GET /api/v1/agent/runs/{run_id}` | 轮询状态，或查看失败前已完成的图片 |
| `POST /api/v1/agent/runs/{run_id}/cancel` | 取消本轮，之后可以继续发送新消息 |

助手文字和附件原子写入会话。SSE 提供执行进度，并非逐 token 文本流；完成后读取会话。
失败／取消保留用户消息与运行状态，不伪造成功回复。相同幂等键与内容返回原 run；
内容不同返回 409。同一会话只接受一个未完成轮次，否则返回 `conversation_busy`。
工作空间最多一个活动 sandbox。每会话最多 20 条用户消息，每轮一次 Flash 需求整理、最多一次 Flash 结果回复，
执行任务最多 1 张初始图和 1 次修订；上下文不静默截断。澄清问题是普通助手消息，回答作为下一轮。
成功生成的最后一张图成为下一轮默认主体，显式选图可以覆盖它。

## 图片 ID 与来源

使用搜索结果的 `image_id`，不是标题或博物馆 `record_uid`。用户可以直接在聊天文字中
写入 ID。Sandbox 调用网关查找图片：默认通过只读 SQLite 查询当前 ready 馆藏版本；配置
`AGENT_COLLECTION_API_URL` 后改用可信线上 HTTPS API 的图片详情与文件接口。
可通过 `AGENT_COLLECTION_API_TOKEN` 配置 bearer 鉴权。它不跟随重定向，也不使用模型或
元数据提供的下载 URL。精确 ID 查询不依赖 Qdrant 或重新计算 embedding。
本地读取验证图片根路径和 checksum，线上读取核对返回 ID 并限制响应大小；两种方式
都验证图片大小和解码结果，再复制到私有 agent 存储。
导入副本固定在会话中，不随馆藏后续变化而替换。不存在的 ID 会得到检查 ID 的聊天回复，
不会发起生成；模型不能提供任意文件路径或外部 URL。

保存馆藏版本、image ID、record/image UID、标题、licence、copyright 和 credit。
生成素材记录输入 asset ID，后续修改可以继续追溯来源。此桥接负责引用解析和元数据保留，
不判定衍生用途是否获得授权；操作者应选择已获相应用途授权的素材。若部署需要强制许可
审核，应在对外开放馆藏生成前接入相应策略。结果不进入官方馆藏索引。

## 独立工作区运行

Worktree：`/Users/archie.yang/project/multimodal-worktrees/chat-agent`，分支 `codex/chat-agent`。
原工作区和运行中的服务没有修改。已有未提交实现复制为基线，未复制密钥、数据库、
图片或前端依赖。新目录执行 `uv sync --locked --all-packages`，创建独立 `.env`。

使用 `AGENT_WORKSPACE=chat-dev`、`AGENT_DATABASE_URL=sqlite:///data/agent-chat/agent.sqlite3`、
`AGENT_MODAL_APP=multimodal-chat-dev`，本地 SQLite／文件模式开发。
`AGENT_COLLECTION_DATABASE` 与 `AGENT_COLLECTION_IMAGE_ROOT` 可指向原项目的馆藏数据库
和图片绝对路径，读取不会修改它们。Gemini、Logfire、工作空间登录 key 及 HTTPS 网关
配置见[配置指南](image_agent_setup.md)。使用转发到 8002 的独立隧道，并将新地址设为
`AGENT_GATEWAY_URL`。完整环境变量示例和 Modal app 创建命令见英文版。

不构建前端，直接启动后端：

```sh
uv run --package multimodal-backend uvicorn app.services.agent.application:create_agent_app --factory --host 127.0.0.1 --port 8002
```

另一终端运行 `make agent-worker`，它协调 Flash 聊天，并单独向 Modal 派发执行任务。
纯聊天需要 Gemini；图片执行另需 HTTPS 网关。启动迁移 `agent_0002` 新增会话／消息表，保留既有数据。
Modal 使用已配置的 CLI 凭证；sandbox 只得到网关地址和任务 token。
云端网关可连接配置好的线上馆藏 API，也可读取挂载的馆藏数据库和图片；现有生产镜像
不会自动包含本地馆藏。

通过 `/docs` 或 HTTP 客户端测试：先保存品牌，用响应 `id` 创建 conversation，再向
messages 接口发送 `{"content":"把 ID 为 xxx 的图片设计成公司的风格"}`，附带新幂等键。
轮询返回的 run，完成后读取会话，然后用另一个幂等键发送
`{"content":"保留主体，把背景换成品牌蓝"}`。英文版提供完整 HTTP 示例。

## 验证与限制

执行 `uv run pytest backend/tests/test_chat.py backend/tests/test_agent.py` 和
`uv run ruff check .`。测试覆盖真实对话循环配合 provider 替身、馆藏 fixtures、鉴权 API、
持久化历史、编辑、取消、调用结果不确定、旧数据库迁移与 SDK 零重试。
边界测试验证普通聊天不创建 sandbox、执行端不能调用聊天工具，以及颜色覆盖同时传到
生成和评估而不修改原品牌。线上查图使用 HTTP 替身验证固定地址、重定向和大小限制。
本次不包括聊天前端，鉴权仍为单工作空间操作者，尚无公司成员管理／SSO。
真实 Gemini 对话质量、云端馆藏读取和完整 tracing 仍需配置开发环境联调。


共享搜索目录已迁移至 Neon PostgreSQL。Agent 通过
`AGENT_COLLECTION_API_URL` 配置可信 HTTPS 搜索 API 地址来读取共享目录。
`AGENT_COLLECTION_DATABASE` 仅支持旧版本地 SQLite 快照，不直接连接 Neon。
未配置在线 API 且没有可用快照时，按图片 ID 生图会返回 `collection_unavailable`；
上传图片生图不依赖搜索目录。


## 已连接的聊天侧栏

在搜索页面打开 Chat，登录工作区并选择已保存的品牌。搜索图片上的 **Use in chat**
会将准确的 `image_id` 填入草稿，不会自动发送。发送后会创建持久化会话并提交任务。
侧栏打开时每两秒读取消息、任务状态和生成图片。刷新后可从 Conversation 选择历史会话。
结果支持下载和选作后续编辑对象，取消和失败状态会保留。请求响应丢失后的重试使用相同
幂等键，避免重复提交。品牌说明和参考图片仍在 Image Studio 配置。

规划和评估通过 GenerateContent 的 JSON Schema 字段传递严格的 Pydantic 契约。
更新后端代码后需重启 API 和 worker，已运行的进程不会自动加载 provider 修复。


聊天执行端同时接受图片 UUID 和 `co41679` 这样的藏品记录 ID。可信网关通过
`DATABASE_URL` 在 PostgreSQL 的当前 ready 索引中精确查询 `record_uid`，返回全部
不同图片。唯一匹配会解析为 UUID 后读取图片；多个匹配会列出 UUID 并请用户选择。
目录不可用与当前索引未收录使用不同错误。导入图片保留原始记录 ID、UUID 和版权信息。
在线读取图片时，网关仍需要数据库连接来解析藏品 ID。更新后重启 API 和 worker。
