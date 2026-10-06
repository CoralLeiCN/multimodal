# 品牌聊天 Agent 后端

[English](chat_agent_backend.md) | 简体中文

本文描述现有 Image Studio 原型。首个 Web 版本使用 Codex 探索馆藏，生图留待后续。
Codex 馆藏探索已在独立 Web 版本中实现；本文的 Studio API 不提供该接入。
架构和待完成的托管验证见[版本与 harness 决策](product_versions.md)。

英文版为主文档。此后端实现“配置公司品牌 → 开启聊天 → 引用搜索结果图片 ID →
按品牌风格生成 → 在同一聊天继续修改”的产品流程。设置 `AGENT_ENABLED=true` 后，
搜索页聊天侧栏接入此后端。聊天和生图不包含在 HF 的首个搜索版本中。

## 聊天服务与 任务进程 执行端

OpenAI 兼容 harness 在可信后端处理聊天，读取品牌和会话历史，回复或澄清需求。只有用户明确
要求生图／编辑时，才整理出执行任务交给容器内的任务进程。问候和澄清不会创建任务进程。
Worker 异步处理聊天，不把长时间模型调用放进 API 请求。

流程：用户消息与品牌 → OpenAI 兼容 harness 整理任务 → 任务进程 按 ID 查图、调用 Nano Banana、
检查并最多修订一次 → 返回图片与执行状态 → OpenAI 兼容 harness 组织聊天回复。
任务进程 只收到本轮用户要求、整理后的 prompt、图片 ID／已有 asset ID、比例、品牌版本、
本轮生效的品牌快照与覆盖项，不接收聊天历史，也没有聊天或回复工具。
工具网关代理外部调用，模型、存储和数据库密钥留在可信服务。

**优先级：本轮明确要求 > 相关历史要求 > 品牌预设。**
例如品牌预设红色，用户要求本轮用蓝色，则复制品牌快照并覆盖为蓝色。
colors、preserve、avoid 的明确覆盖会合并到任务里，未指定的品牌特征继续保留。
品牌原始版本不修改。Nano Banana 生图与视觉评估都使用本轮生效的规则，
不会仅因用户指定颜色与品牌预设不同就判为不合格。原始本轮要求也随任务传递。

执行完成或失败后，聊天服务调用 harness 解释结果。若最终总结失败，仍保留真实生成图片并
返回备用回复。下一轮默认使用最后成功生成的图片，也可显式选择其他图片。
会话、消息与结果持久保存，空闲会话不保留 任务进程。

聊天、规划与评估使用 OpenAI Python 客户端的 Chat Completions 接口，返回严格 JSON Schema
约束的结构化结果，设置 `store=False`，历史由应用保存。配置 `AGENT_OPENAI_BASE_URL`、
`AGENT_OPENAI_API_KEY` 和 `AGENT_MODEL`；`AGENT_CHAT_MODEL`、`AGENT_EVALUATION_MODEL`
为空时使用共享模型。规划和评估模型必须支持图片输入。Nano Banana 图片生成仍使用 Gemini。
先安装 `agent` 可选依赖；客户端设置 `max_retries=0`、120 秒超时。所有付费调用先记录意图，
结果不确定时不自动重放。Logfire 仅记录模型、操作、令牌数、耗时和脱敏错误，不记录提示或图片。

状态：`chat_queued → chat_preparing` 后直接回复，或进入
`queued → starting → running → execution_done → chat_returning → succeeded/failed`。
取消和超时继续可用。

## API

所有接口复用工作空间 bearer key 或签名 cookie；品牌、上传、素材下载接口继续可用。

| 接口 | 行为 |
| --- | --- |
| `POST /api/v1/agent/conversations` | 使用 `brand_version` 与可选 `title` 创建会话 |
| `GET /api/v1/agent/conversations` | 按最近活动列出最多 100 个会话 |
| `GET /api/v1/agent/conversations/{id}` | 读取消息、每轮状态／错误与 `review_status`、附件及来源 |
| `POST /api/v1/agent/conversations/{id}/messages` | 提交 `content` 和可选 `subject_asset_id`；必须带 `Idempotency-Key`；返回 202、消息 ID 与 run |
| `GET /api/v1/agent/runs/{run_id}/events` | SSE 进度，支持 Last-Event-ID 重连；回复发布时产生 assistant_message |
| `GET /api/v1/agent/runs/{run_id}` | 轮询状态，或查看失败前已完成的图片 |
| `POST /api/v1/agent/runs/{run_id}/cancel` | 取消本轮，之后可以继续发送新消息 |

助手文字和附件原子写入会话。SSE 提供执行进度，并非逐 token 文本流；完成后读取会话。
失败／取消保留用户消息与运行状态，不伪造成功回复。相同幂等键与内容返回原 run；
内容不同返回 409。同一会话只接受一个未完成轮次，否则返回 `conversation_busy`。
工作空间最多一个活动任务进程。每会话最多 20 条用户消息，每轮一次 harness 需求整理、最多一次 harness 结果回复，
执行任务最多 1 张初始图和 1 次修订；上下文不静默截断。澄清问题是普通助手消息，回答作为下一轮。
成功生成的最后一张图成为下一轮默认主体，显式选图可以覆盖它。
用户明确要求从头生成、不要沿用上一张图时，聊天模型设置 `execution.clear_subject=true`，
并省略 `image_id` 和 `asset_id`。协调器在提交任务前清除会话当前主体，保留品牌风格参考图
和历史素材。普通后续编辑默认 `clear_subject=false`；清除主体不能与显式来源 ID 同时使用。

## 图片 ID 与来源

使用搜索结果的 `image_id` 或 `co` 藏品记录 ID。用户可以直接在聊天文字中
写入 ID。任务进程 调用网关查找图片：默认通过 `DATABASE_URL` 只读查询 PostgreSQL 当前 ready 馆藏版本；配置
`AGENT_COLLECTION_API_URL` 后改用可信线上 HTTPS API 的图片详情与文件接口。
可通过 `AGENT_COLLECTION_API_TOKEN` 配置 bearer 鉴权。线上读取拒绝重定向，也不使用模型或
元数据提供的下载 URL。精确 ID 查询不依赖 Qdrant 或重新计算 embedding。
直接读取默认使用 `IMAGE_ROOT`，`AGENT_COLLECTION_IMAGE_ROOT` 可覆盖该路径。
云端通过只读 HF bucket 挂载读取馆藏图片；本地开发使用相同相对路径的目录。缺失文件返回错误。
本地读取验证图片根路径和 checksum，线上 API 读取核对返回 ID 并限制响应大小；两种方式
都验证图片大小和解码结果，再复制到私有 agent 存储。
导入副本固定在会话中，不随馆藏后续变化而替换。不存在的 ID 会得到检查 ID 的聊天回复，
不会发起生成；模型不能提供任意文件路径或外部 URL。

保存馆藏版本、image ID、record/image UID、标题、licence、copyright 和 credit。
生成素材记录输入 asset ID，后续修改可以继续追溯来源。此桥接负责引用解析和元数据保留，
不判定衍生用途是否获得授权；操作者应选择已获相应用途授权的素材。若部署需要强制许可
审核，应在对外开放馆藏生成前接入相应策略。结果不进入官方馆藏索引。

## 本地运行

在仓库根目录执行 `uv sync --locked --all-packages --extra agent`，创建本地 `.env`。

使用 `AGENT_WORKSPACE=chat-dev`、`AGENT_DATABASE_URL=sqlite:///data/agent-chat/agent.sqlite3`，
以本地 SQLite／文件模式开发。设置 `AGENT_ENABLED=true`、`AGENT_ENVIRONMENT=development`、
`AGENT_STORAGE=local`、`AGENT_ACCESS_TOKEN` 和 `GEMINI_API_KEY`。
`DATABASE_URL` 指向共享馆藏数据库，`AGENT_COLLECTION_IMAGE_ROOT` 可指定本地图片路径。
完整配置见[配置指南](image_agent_setup.md)。

同时启动 API 和 worker：

```sh
uv run --package multimodal-backend --extra agent python -m app.space --agent-only --host 127.0.0.1 --port 8002
```

监督进程自动将内部网关设为 `http://127.0.0.1:8002`，无需公网隧道。
Worker 协调聊天，并在同一个容器内启动任务子进程。
每个工作空间只运行一个 worker。子进程使用任务 token，环境中不传递模型、数据库或 HF 密钥；
但各进程共享容器的文件系统与网络，不构成安全沙箱。子进程在父进程退出或超过时限时结束。
容器或 worker 重启后，中断的任务标记失败，保留已生成图片，不自动重放不确定的付费调用。
云端网关可连接配置好的线上馆藏 API，也可读取挂载的馆藏数据库和图片；现有生产镜像
不会自动包含本地馆藏。

通过 `/docs` 或 HTTP 客户端测试：先保存品牌，用响应 `id` 创建 conversation，再向
messages 接口发送 `{"content":"把 ID 为 xxx 的图片设计成公司的风格"}`，附带新幂等键。
轮询返回的 run，完成后读取会话，然后用另一个幂等键发送
`{"content":"保留主体，把背景换成品牌蓝"}`。英文版提供完整 HTTP 示例。

## 验证与限制

执行 `uv run --all-packages --extra agent pytest backend/tests/test_chat.py backend/tests/test_agent.py` 和
`uv run ruff check .`。测试覆盖真实对话循环配合 provider 替身、馆藏 fixtures、鉴权 API、
持久化历史、编辑、取消、调用结果不确定、数据库迁移与 SDK 零重试。
边界测试验证普通聊天不创建 任务进程、执行端不能调用聊天工具，以及颜色覆盖同时传到
生成和评估而不修改原品牌。线上查图使用 HTTP 替身验证固定地址、重定向和大小限制。
馆藏侧栏提供聊天前端，鉴权仍为单工作空间操作者，尚无公司成员管理／SSO。
真实模型对话质量、云端馆藏读取和完整 tracing 仍需配置开发环境联调。

共享搜索目录使用 Neon PostgreSQL，Agent 默认通过 `DATABASE_URL` 解析图片 UUID。
可通过 `AGENT_COLLECTION_API_URL` 改用可信 HTTPS 搜索 API。藏品记录 ID 始终先通过
`DATABASE_URL` 解析。目录不可用返回 `collection_unavailable`，图片不存在返回 `image_missing`。
Agent 读取 `.env` 和 `.env.local`，后者优先。上传图片生图不依赖搜索目录。

## 已连接的聊天侧栏

在搜索页面打开 Chat，登录工作区并选择已保存的品牌。搜索图片上的 **Use in chat**
会将准确的 `image_id` 填入草稿，不会自动发送。发送后会创建持久化会话并提交任务。
侧栏打开时每两秒读取消息、任务状态和生成图片。刷新后可从 Conversation 选择历史会话。
结果支持下载和选作后续编辑对象，取消和失败状态会保留。请求响应丢失后的重试使用相同
幂等键，避免重复提交。明确的 `queue_full`（429）或 `not_configured`（503）拒绝会解除
提交锁定，用户可以编辑草稿或切换会话。不确定的服务错误仍保留原请求内容和幂等键。
Image Studio 的 `/create` 页面配置六项品牌设计字段。

规划和评估使用 OpenAI 客户端的 Chat Completions 解析接口和 Pydantic 响应契约。
Gemini 仅用于图片生成。
更新后端代码后需重启 API 和 worker，已运行的进程不会自动加载 provider 修复。

聊天执行端同时接受图片 UUID 和 `co41679` 这样的藏品记录 ID。可信网关通过
`DATABASE_URL` 在 PostgreSQL 的当前 ready 索引中精确查询 `record_uid`，返回全部
不同图片。唯一匹配会解析为 UUID 后读取图片；多个匹配会列出 UUID 并请用户选择。
目录不可用与当前索引未收录使用不同错误。导入图片保留原始记录 ID、UUID 和版权信息。
在线读取图片时，网关仍需要数据库连接来解析藏品 ID。更新后重启 API 和 worker。

## 品牌设计模板

`/create` 仅保留品牌名称、描述、配色、个性、字体和插画风格六个字段，生图在聊天中进行。
提示词集中在 `backend/app/prompts/image_agent.yaml`，聊天、规划、生图和评估都会收到六项
品牌信息。编辑保存时，新版本保留所选版本的 `preserve`、`avoid` 和
`reference_asset_ids`；这些字段由 API 接受，页面不显示。
参阅[设计师提示词指南](brand_prompts.CN.md)。

### 生图完成后的评估失败

评估失败时，已保存的候选图片仍附在聊天中，并保留下载和编辑入口。页面标注
“Needs review · Not approved”，不会把这些图片当作通过评估，也不会自动付费重试。
聊天提示词明确区分“生图成功”和“评估失败”。
消息记录包含 `review_status`：`accepted`、`needs_review` 或空值。即使执行成功，若用完
修订次数后最终评估仍要求修改，页面也会显示人工审查提示；刷新历史会话后该提示继续显示。

模型调用失败时，在调用记录、任务结果、服务日志和 Logfire 中记录操作、模型名、
异常类型和 HTTP 状态码，不记录原始报错或图片内容。404 使用
`provider_model_unavailable`，429 使用 `provider_rate_limited`，不确定的网络错误
仍使用 `outcome_unknown`。旧模型返回 404 时，检查 `.env` 中的 `AGENT_MODEL` 和
`AGENT_EVALUATION_MODEL`；修改后重启 API 和 worker。

云端生成素材保存在独立的私有 HF bucket。配置 `AGENT_STORAGE=hf`、
`AGENT_HF_BUCKET` 和 `AGENT_HF_TOKEN`；工作进程与网关共用存储，任务进程 不接收 HF 凭证。
