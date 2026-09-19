# 品牌提示词：设计师指南

[English](brand_prompts.md) | 简体中文

在 `/create` 配置品牌，再去搜索页的 Chat 生成图片。名称、描述必填；配色、个性、字体和
插画风格为可选自由文本。个性示例：premium, calm, technical, optimistic。
修改品牌会保存新版本，新聊天选择新版本后生效；已有聊天保留原版本。

提示词文件：[image_agent.yaml](../backend/app/prompts/image_agent.yaml)。

- `system`：公共规则及用户要求的优先级。
- `brand`：品牌名称、描述、配色、个性、字体、插画风格的模板。
- `chat`：聊天回复、整理设计任务及解释执行结果。
- `plan`：旧版单次任务 API 的设计规划。
- `generate`：Nano Banana 如何运用品牌视觉风格。
- `evaluate`：结果评分及修改建议。

编辑 YAML 中 `|` 下方的英文内容并保留缩进。`brand` 段必须包含六个变量：
`$name`、`$description`、`$colors`、`$personality`、`$typography`、`$illustration_style`。
空字段显示为 Not specified。只做一次文本替换，用户输入不会作为模板执行；其他段落为纯文本。

当前用户要求优先于品牌默认值。字体描述用于用户需要文字的设计，不会自动要求每张图加字或 Logo。
修改模板后重启 API 和 worker；云端需重新部署。YAML 随后端打包，sandbox 只接收已整理的执行任务。
运行 `make agent-test` 和 `make lint` 检查模板及接口，不会调用付费生图。
