# 修改比照与 Agent 能力对照

核验日期：2026-11-19。对象：研辩台 ThesisDebate 当前本地项目。Star 数来自当日 GitHub REST API，不代表论文审查准确率，也不代表适合直接引入其全部依赖。

## 当前软件究竟内置了什么

优化前已有 Markdown 格式规则、修改锚点、逐项接受/拒绝、撤销/恢复、原文与接受后预览。页面中的多 Agent 辩论内容来自静态示例，没有真实模型调用、独立审查运行或持久化辩论记录。因而不能把已有彩色差异界面称为真实的 AI 修改审查 Agent。

本轮保留**独立、确定性的修改比照核验**，并新增**真实模型调用的五 Agent 审查链路**。前者通过 `GET /format-runs/{id}/review` / `get_revision_review` 验证原文与接受后预览，始终标明 `engine=deterministic`、`llm_enabled=false`。后者使用 OpenAI Agents SDK 调用配置的 OpenAI-compatible 服务，输出结构化意见、字面证据和覆盖情况；两条链路的状态和结果分别展示，不能用规则通过冒充模型审查成功。

五个角色为方法论审查官、创新点审查官、学术规范审查官、修订对照审查官、证据裁判官。专家先审查不可变正文快照，裁判只评议已有发现，最多发起一轮有界质疑。模型不能直接修改正文，只有带有效依据及唯一锚点的建议才能由用户手动接受。当前实现是否能连通所配置的实际服务，需要该服务的运行验收；离线注入测试不能代替这一结果。

## 高 Star 项目对照

| 项目与当日 Star | 一手资料确认的能力 | 对本项目的启发 | 本项目仍有的差距 |
| --- | --- | --- | --- |
| [STORM / Co-STORM](https://github.com/stanford-oval/storm)，31,911 | 检索资料后按多视角提问组织报告与引用；专家、主持人和人类可协作；支持用户资料检索模块 | 审查结论关联到原文或证据，区分提出修改与核验证据 | 已有多角色及上传资料证据；按本轮范围不接文献检索和外部事实核查，不能证明世界首创或完成查重 |
| [OpenAI Agents Python](https://github.com/openai/openai-agents-python)，30,757 | Agent 委派、工具、输入输出校验、人工介入、会话和运行追踪 | 为建议与裁决定义职责，以代码编排、有界执行、结构化输出及人工决策控制流程 | 已实际引入 SDK、五角色及本地事件；没有完整 token/费用核算、跨模型质量基准或分布式调度；本轮有意不提供工具和 handoff |
| [AutoGen](https://github.com/microsoft/autogen)，61,932 | 团队对话、人工反馈、组件及团队状态保存/恢复 | 保存版本、审查和决策，准确区分完成、部分完成、中断和失败 | 已接 SQLite、事件游标和手动重试；尚无多用户协同、分布式任务队列或生产级数据库运维体系 |
| [Open Deep Research](https://github.com/langchain-ai/open_deep_research)，12,675 | 研究任务分解、搜索/MCP、研究结果压缩和评测；legacy workflow 展示人工审批规划 | 用可重复评测验证功能，不依靠演示聊天营造能力 | 已覆盖本地协议、锚点、存储和状态行为；尚无论文语义审查基准及跨模型实测；无搜索证据链 |

维护状态需要单独考虑：Open Deep Research 的 GitHub 页面显示于 **2026-08-21** 归档。AutoGen 当前 README 声明已停止维护并指向 Microsoft Agent Framework，但同日 REST 元数据中的 `archived` 仍为 `false`；这里保留该差异，不把它描述成 API 已标记归档。本轮只引入 `openai-agents==0.25.0` 作为模型编排依赖，其余项目用于设计对照。

这些项目都不直接保证论文原生 DOCX 修订、公式无损往返或修改前后语义等价，不能仅凭引入框架宣称完成这些能力。

## 本轮完成的改进边界

1. **独立检查实际修订结果。** 报告绑定 `source_hash` / `preview_hash`，显示原文及补丁完整性、未处理决策，以及数字、公式、引用标记的启发式变化检查。关键标记稳定不等于语义正确；数字等发生变化也不一定是错误，需要人工复核。无实际原文的演示数据不能得到通过结论。
2. **在接受修改前验证锚点与冲突。** 校验 UTF-16 位置、完整字符边界、精确原文片段、上下文摘要、替换内容与修改重叠。可选 `expected_source_hash` 与服务端原文摘要不一致时返回 HTTP 409，且不改变决策状态。原始 source 在 run 创建后发生变化也应被识别。
3. **展示实际对齐差异和运行状态。** `diff_rows` 提供插入、删除、替换、相同行及两侧独立行号，避免按同一个数组下标直接比较导致连锁误标。Agent 审查只显示真实任务的排队、执行、完成、部分完成、失败或取消，不再以静态示例讨论充当运行结果。
4. **使用五 Agent 的有界模型审查。** 明确配置端点、模型和密钥，不硬编码默认模型或默默切换提供方。最多三个并发请求，单次调用 90 秒、总运行 600 秒、最多两轮、一次质疑轮；正文和资料按 4,000 字符、200 字符重叠切片，并记录覆盖情况。严格结构化输出失败时只允许一次显式修复，仍失败则如实保留失败或部分结果。
5. **把意见约束到逐字证据。** 输入限于稿件和最多十份 TXT/MD 材料（材料合计不超过 300,000 字符）。来源只能是修订稿、原稿或本次上传材料；引文必须在来源中逐字存在。证据缺失标记 `needs_evidence`；裁判不能新增未知发现。无网络检索工具，不代表能证明模型从未使用预训练知识，系统仍需校验其输出依据。
6. **手动应用与版本保护。** Agent 只返回建议，不能自行写入稿件。接受时再次检查当前版本摘要、UTF-16 唯一锚点与裁判结论；有效建议才写为新的不可变版本。版本头更新与接受决策使用同一 SQLite 事务，同一决定重放复用版本、新运行及跟进任务身份，过期版本或相反决定返回冲突。历史恢复生成新版本，保留旧记录。接受与历史恢复会重跑确定性规则并排队完整五角色跟进：实际操作前/后内容分别作为对照原稿/修订稿，继承材料，不自动接受下一项建议。同一文档手动重新扫描必须携带当前版本头摘要。
7. **持久化与中断恢复。** SQLite 保存格式状态、审查、角色结果、事件、版本和决策。重启后进行中的任务标记为部分完成并记录中断原因，保留已完成角色，等待用户显式重试；不会伪造完成或自动重发模型请求。SSE 支持事件游标重连。当前是本地单用户、单 API worker 部署。
8. **控制模型数据流。** 正文与上传材料会发送至配置的模型服务；密钥只从服务端环境读取，不写入审查记录。每次 SDK 运行禁用其远程 tracing，改用本地审查事件。日志和 API 不回显原始服务异常正文或密钥。数据库仍含正文和上传材料，应按实际数据保密要求保存。

完整 API 字段见 [后端说明](../backend/README.md)。本轮测试与还原验证的实际输出由上级事务目录的 `VERIFICATION.txt` 记录；本文不预报测试结果。

## 后续路线：尚未完成

- 模型质量与费用：用具有人工标注的论文片段评估误报、漏报、证据支持率和实际修订质量；补充 token/费用记录。模型调用链路已经实现，不代表某个外部服务或论文质量已经验收。
- 外部检索与证据：本轮按范围不做联网检索。以后如扩展，需要另行设计可访问来源、原始文献定位和证据失效处理。
- 生产部署与协同：SQLite 本地持久化已经接入；多用户权限、协同冲突、任务队列、数据库迁移升级、备份与运维仍需单独实现。现有 PostgreSQL 迁移说明不是当前 SQLite 运行路径。
- DOCX/PDF 与公式：DOCX 结构解析、原生修订、OMML 往返、PDF 引用定位和 `.tex` 编译需要独立实现与样本验证。

## 一手来源

- [STORM 机制与模块](https://github.com/stanford-oval/storm#how-storm--co-storm-works)
- [OpenAI Agents 核心概念](https://github.com/openai/openai-agents-python#core-concepts)
- [OpenAI-compatible 服务接入与结构化输出兼容性](https://openai.github.io/openai-agents-python/models/#ways-to-integrate-non-openai-providers)
- [结构化输出类型](https://openai.github.io/openai-agents-python/agents/#output-types)
- [以代码编排多 Agent](https://openai.github.io/openai-agents-python/multi_agent/#orchestrating-via-code)
- [Agent 运行循环与轮次](https://openai.github.io/openai-agents-python/running_agents/#the-agent-loop)
- [OpenAI Agents 人工介入](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- [OpenAI Agents 追踪](https://openai.github.io/openai-agents-python/tracing/)
- [AutoGen 人工反馈](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html)
- [AutoGen 状态保存与恢复](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html)
- [Open Deep Research 配置、评测与 legacy 实现](https://github.com/langchain-ai/open_deep_research)

Star 与维护状态元数据地址：

- https://api.github.com/repos/stanford-oval/storm
- https://api.github.com/repos/openai/openai-agents-python
- https://api.github.com/repos/microsoft/autogen
- https://api.github.com/repos/langchain-ai/open_deep_research
