# 研辩台后端

FastAPI + 确定性修订核验 + 有界五 Agent 审查 + SQLite。本地单用户、单 API worker 部署。规则核验与模型审查是两个独立接口，规则通过不代表模型或学术审查通过。

## Run

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -c "import sqlite3; print(sqlite3.sqlite_version)"
uvicorn app.main:app --reload --port 8000
```

需要完整、可正常加载 SQLite 的 Python 3.12+ 环境。如果系统 Python 的 `import sqlite3` 报缺失 DLL，请使用完整 Python 安装重建虚拟环境，不要复用损坏环境。候选启动器 `../START_REVIEW.ps1 -Mode backend` 使用候选版自己的虚拟环境并可读取后端本地 `.env`；启动器不安装依赖。候选版默认端口为 8001，普通手动命令如上为 8000。

Endpoints: `/health`, `/demo`, `/format-profiles`, `POST /format-runs`, `GET /format-runs/{id}/changes`, `GET /format-runs/{id}/preview`, `GET /format-runs/{id}/review`, `POST /format-runs/{id}/restore`, `POST /format-changes/{id}/decision`, `POST /latex/compile`；模型与历史接口见下文。

`POST /format-runs` accepts `content` and `requested_rules`. When `content` is supplied, the prototype deterministically proposes Markdown heading numbering/spacing, reference-list numbering, and trailing-whitespace changes. Accept or reject proposals, then read `/format-runs/{id}/preview` to compare the original source with accepted changes. Omitting `content` retains the static demo findings.

`GET /format-runs/{run_id}` 返回运行、`document_id`、`base_version_id` 与 `current_version_id`。初次导入可省略文档 ID；对已有文档手动重新扫描时，`POST /format-runs` 必须同时提供已有 `document_id` 与当前 `expected_head_hash`。缺少或过期的版本头返回 HTTP 409，不覆盖已有文档。

The LaTeX endpoint performs lightweight source validation only. It does not invoke XeLaTeX/PDFLaTeX/LuaLaTeX and does not produce a PDF.

## Independent deterministic revision review

`GET /format-runs/{id}/review` calls `get_revision_review` and recomputes its result from the captured source plus the actually accepted changes. It does not invoke a language model.

| Field | Meaning |
| --- | --- |
| `run_id` | Formatting run being reviewed |
| `engine`, `llm_enabled` | Always `deterministic`, `false` for this implementation |
| `source_hash`, `preview_hash` | SHA-256 digests binding the review to its input/output text |
| `status` | `passed`, `needs_review`, or `blocked` |
| `summary` | Human-readable result and scope |
| `applied_count`, `accepted_count`, `pending_count`, `rejected_count` | Actual preview application and decision counts |
| `blocked_change_ids` | Changes that could not safely be applied |
| `checks` | Entries containing `id`, `title`, `status` (`passed` / `warning` / `failed`) and `detail` |
| `diff_rows` | Aligned `equal` / `insert` / `delete` / `replace` rows with nullable `source_line_no`, `preview_line_no`, `before`, `after` |
| `unified_diff` | Conventional text diff of source and accepted preview |

The checks cover source and patch integrity, unresolved decisions, and heuristic number/formula/citation token changes. Stable tokens do not prove semantic equivalence, factual accuracy, citation validity, or academic quality. A changed token requests review rather than establishing that the edit is wrong. Demo findings without an actual source cannot receive a passing review.

`POST /format-changes/{id}/decision` additionally accepts an optional lowercase SHA-256 `expected_source_hash` for the run's original content:

```json
{"decision": "accept", "expected_source_hash": "<64-character lowercase source SHA-256>"}
```

If the provided digest does not match, the endpoint returns HTTP 409 before changing state. The run also retains its initial source digest to detect source mutation. On acceptance, UTF-16 offsets must resolve to complete characters, the quote/context digest must match the original source, a replacement must be available, and the change must not conflict with accepted changes. Non-conflicting zero-length insertions at boundaries are supported.

The run source remains a versioned snapshot; changing editor content requires a new run. 格式状态会持久化到当前 SQLite，API 重启后读取。格式接受、撤销或恢复导致预览变化时会新增不可变版本；旧运行与旧审查不能继续覆盖新的文档版本。

## Five-Agent model review

引擎使用 `openai-agents==0.25.0`，通过 `AsyncOpenAI(base_url=...)` 和 `OpenAIChatCompletionsModel` 显式调用兼容服务。角色为 `methods`（方法论）、`innovation`（创新点）、`norms`（学术规范）、`comparator`（修订对照）、`judge`（证据裁判）。专家与裁判输出均由 Pydantic 严格 schema 校验；不提供工具、网络检索、handoff、文件写入或正文修改能力。

在服务端环境配置以下三项，可分别回退读取右栏变量。没有默认模型，也没有提供方回退：

| 配置 | 兼容变量 |
| --- | --- |
| `AGENT_BASE_URL` | `OPENAI_BASE_URL` |
| `AGENT_MODEL` | `OPENAI_MODEL` |
| `AGENT_API_KEY` | `OPENAI_API_KEY` |

API 基址必须为不带用户名、密码、查询参数或片段的 HTTP(S) URL。`GET /agent-config` 返回 `configured`、`base_url`、`model`、`reason`，不返回密钥。配置就绪只说明字段有效，实际运行才校验 SDK、服务连通性及严格 JSON schema 兼容性。未配置创建任务返回 HTTP 503；不兼容服务如实失败，不将静态文本当作成功结果。

| Method / route | 用途 |
| --- | --- |
| `POST /agent-reviews` | 校验快照后排队执行，HTTP 202 |
| `GET /agent-reviews?format_run_id=...` | 列出全部或指定格式运行的审查 |
| `GET /agent-reviews/{review_id}` | 读取角色、意见、裁决、覆盖情况和真实状态 |
| `GET /agent-reviews/{review_id}/events?after=0` | SSE 事件流，支持 `after` 或 `Last-Event-ID` 游标重连 |
| `POST /agent-reviews/{review_id}/cancel` | 取消当前任务；已结束任务保持其终态 |
| `POST /agent-reviews/{review_id}/retry` | 对失败、部分完成或取消任务显式重试，仅限相同快照 |
| `POST /agent-reviews/{review_id}/findings/{finding_id}/decision` | 用户手动接受或拒绝意见 |
| `GET /format-runs/{run_id}/versions` | 读取所属文档版本历史 |
| `POST /format-runs/{run_id}/versions/{version_id}/restore` | 恢复历史内容为一个新版本 |

创建任务请求体：

| 字段 | 约束 |
| --- | --- |
| `format_run_id` | 已有格式运行 ID，必须指向文档当前版本 |
| `expected_source_hash` | 必填，从规则核验响应取得的 64 位小写原稿 SHA-256 |
| `expected_preview_hash` | 必填，从同一响应取得的 64 位小写预览 SHA-256 |
| `materials` | 可选 `[{"name":"参考.md","content":"资料正文"}]`，最多十份；每份非空、名称不超过 255 字符、全部内容合计不超过 300,000 字符 |

前端只读取 TXT/MD 材料并提交文本。服务端保存材料 ID、名称、内容摘要及内容快照。审查绑定原稿、修订稿和材料；缺少原稿、补丁完整性被阻断、摘要变化或已有正在执行的同版本任务时，不会创建另一个假成功任务。

重试与历史恢复请求体包含必填 `expected_preview_hash` 和可选 `expected_source_hash`。意见决策还需 `decision: "accept" | "reject"`。接受仅允许审查终态为 `completed` / `partial`、意见 `verdict=supported` 且 `applicable=true`，并再次验证唯一 UTF-16 锚点、精确引文和当前版本头。有效接受新建版本并返回 `format_run_id`、`version`、`followup_review`；客户端应切换到新运行及跟进任务。接受后正文不得超过 300,000 字符。重复同一决定复用已保存的新运行、版本和跟进审查身份，相反决定返回 HTTP 409；撤销已接受的意见应通过版本历史进行。

接受与历史恢复都会对新内容重新生成确定性规则建议，并自动排队一次完整五 Agent 跟进审查。跟进任务的对照原稿是操作前预览，修订稿为操作后内容，继承此前上传的材料，并以 `comparison_base_hash` 标明对照版本；不会把相同的新原稿/预览误当作已完成修改对照。原审查的意见不能直接迁移覆盖新正文。自动跟进不自动接受新意见，服务不可用时按真实失败处理，可取消或对同快照显式重试。

## Execution bounds and evidence

- 最多三个并发请求，单次调用 90 秒、总计 600 秒；最多两轮及一次裁判质疑轮。SDK 传输重试一次，结构化输出无效只允许一次显式修复，仍无效则标明失败。
- 每个窗口最多 4,000 字符，重叠 200 字符；记录各角色的覆盖窗口及完整性。长稿未覆盖或某角色失败时明确显示部分结果，不把局部审查称为全文完成。
- 证据 `source_id` 只允许 `manuscript`、`original` 或 `material:<id>`，引文必须在对应来源中逐字存在。没有可验证证据保留 `needs_evidence`，不能建议自动接受。裁判只评议已存在的发现，缺席专家不能伪装成完成。
- 终态为 `completed`、`partial`、`failed`、`cancelled`；排队和执行分别为 `queued`、`running`。取消停止后续工作，保留已经落库的结果。API 重启将仍处于排队/执行的任务标记为中断的部分结果，保留已完成角色，等待显式重试，不自动重发模型请求。
- 没有联网检索工具，审查依据限于本次稿件与材料。引文出现只能证明可定位，不能证明论文结论正确、来源可靠、无抄袭或世界首创，也不能证明模型完全没有依赖预训练知识。
- 每次 SDK 运行设置 `tracing_disabled=True`，以本地 SQLite 事件留痕。正文与资料会发送至配置的模型服务；凭据仅从环境读取，不写入记录。服务端不向客户端回显原始模型服务异常正文。

## Durable SQLite state

默认路径为 `backend/data/reviews.sqlite3`，通过 `THESISDEBATE_DB_PATH` 可覆盖。存储使用 SQLite WAL、外键和事务，原生表为 `records`、`reviews`、`events`、`versions`、`heads`、`decisions`。版本内容不可变，以 SHA-256 记录；历史恢复新增版本而不删除旧记录。模型接受的版本写入、版本头更新和接受决定在一个事务中提交，过期头冲突不留下孤立版本或假成功决定。同一接受请求的响应丢失后重放不会重复修改正文。

数据库含正文、资料和审查记录。当前没有多用户权限、协同编辑、分布式任务队列或备份管理；运行一个 API worker。预留的 PostgreSQL 迁移和 Docker 服务不是当前 SQLite 的运行依赖。没有完整 token/费用核算。DOCX/PDF 原生解析、OMML、Word 原生修订和真实 `.tex` 编译尚未实现。

高 Star 项目对照、一手来源与剩余差距见 [AGENT_COMPARISON.md](../docs/AGENT_COMPARISON.md)。真实服务是否通过验收以实际运行记录为准；离线测试提供方只验证协议、状态和存储行为。

## Tests

```powershell
.venv\Scripts\python.exe -B -m py_compile app/main.py app/agent_api.py app/agent_engine.py app/storage.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
