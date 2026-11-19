# 研辩台 ThesisDebate

面向学生、导师和评审管理员的论文逻辑质检与格式修订工作台。当前候选版包含 Markdown 导入、确定性格式检查、逐项接受/拒绝、撤销/恢复、对齐差异、独立修改比照核验、五 Agent 稿件审查、SQLite 版本历史和 LaTeX 公式编辑。

规则核验始终返回 `engine=deterministic`、`llm_enabled=false`；五 Agent 是另一条实际模型调用链路，需要先配置兼容服务。它由方法论、创新点、学术规范、修订对照四位审查官和证据裁判官组成，只基于稿件与上传资料输出可复核意见。模型不能直接改稿，用户手动接受才生成新版本。配置就绪不等于真实服务已成功执行，任务状态与事件才是运行依据。

## Windows 启动

双击 `desktop/dist/研辩台.exe`。程序自带 Python、后端和页面，无需另开终端。窗口顶部“研辩台 → 模型配置”可打开配置文件；填写现有兼容服务后重新启动。论文和历史保存在 `%LOCALAPPDATA%/ThesisDebate`，关闭窗口会停止本地服务。构建方式见 [Windows 说明](desktop/README.md)。

## Workspace

- `frontend/`: Next.js + TypeScript workbench UI，生产页面由 Windows 版内置服务提供。
- `desktop/`: Windows 原生窗口、单文件 EXE 入口与构建脚本。
- `backend/`: FastAPI API、确定性修订核验、OpenAI Agents SDK 审查引擎及 SQLite 存储。
- `docker-compose.yml`: 预留 PostgreSQL/pgvector、Redis 和 MinIO 服务；当前运行不依赖这些服务。

## Run

Start the API in one terminal:

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -c "import sqlite3; print(sqlite3.sqlite_version)"
uvicorn app.main:app --reload --port 8001
```

Start the UI in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Markdown files can be imported and edited in the workbench. DOCX and PDF can currently be identified but not parsed; convert them to an editable Markdown copy before running a scan. The original file is not overwritten.

使用完整、可正常 `import sqlite3` 的 Python 3.12+ 安装创建虚拟环境；若系统 Python 缺少 SQLite DLL，应换用完整安装重建环境。当前 SQLite 默认写入 `backend/data/reviews.sqlite3`，可通过 `THESISDEBATE_DB_PATH` 指定路径。格式状态、审查、事件、决策和版本可跨 API 重启保留。当前按本地单用户、单 API worker 使用；不要用多个 worker 承诺协同一致性。

`docker compose up -d` 仅启动预留基础设施，当前 FastAPI 不连接这些服务。`backend/migrations/001_format_revision.sql` 是另一条 PostgreSQL 设计记录，不会自动应用到当前 SQLite。

## 模型服务配置

在后端启动环境中提供以下三项；没有默认模型，也不会自动改用其他服务：

| 配置 | 用途 | 兼容回退变量 |
| --- | --- | --- |
| `AGENT_BASE_URL` | OpenAI-compatible API 基址，无 URL 凭据、查询参数或片段 | `OPENAI_BASE_URL` |
| `AGENT_MODEL` | 已配置服务支持的模型标识 | `OPENAI_MODEL` |
| `AGENT_API_KEY` | 该服务的凭据，仅在服务端读取 | `OPENAI_API_KEY` |

`GET /agent-config` 只返回配置就绪状态、端点、模型和原因，不返回密钥。未配置时规则功能仍可使用，模型审查返回未配置；服务必须支持 Chat Completions 和严格 JSON schema 输出，不兼容或失败会显示实际失败，不回退成示例聊天。SDK 使用 `openai-agents==0.25.0`，每次运行禁用远程 tracing，以本地 SQLite 事件记录审查过程。

启动模型审查会把稿件和本次上传的 TXT/MD 材料发送到所配置的模型服务。本轮没有联网检索工具；这意味着只提供稿件/材料证据通道，不是对模型预训练知识的证明。数据库包含正文、材料和审查结果，不包含环境中的模型凭据。

## Review loop

1. Import a Markdown document (or use the included demo content) and start a deterministic formatting run.
2. Inspect proposed changes, anchors, and before/after values.
3. Accept or reject changes individually; accepted changes appear in the server preview. Undo returns one accepted change to pending; restore resets the run.
4. Use the color-coded original/preview comparison.
   The comparison aligns inserted/deleted/replaced lines and shows the independent deterministic review from `GET /format-runs/{id}/review`. Review checks cover source/patch integrity, unresolved decisions, and heuristic number/formula/citation changes. A passing token check does not establish semantic or academic correctness.
5. 在右侧 Agent 审查中查看模型配置状态，按需上传 TXT/MD 参考资料后发起五角色审查。最多十份资料、合计 300,000 字符；任务绑定原稿和预览摘要，展示真实状态、证据、裁判意见与覆盖率。可取消，失败/部分完成/取消后可对同一快照显式重试。
6. 手动接受或拒绝 Agent 意见。只有证据成立、唯一锚点有效且当前版本未变化的文本建议能接受；接受或历史恢复后创建新版本、重跑确定性规则，并自动排队一次完整五 Agent 跟进审查。跟进以操作前预览为对照原稿、操作后内容为修订稿，继承已有材料；它不会自动接受下一条意见。旧审查快照不再能覆盖新版本，历史恢复不删除已有记录。
7. Enter inline or block LaTeX formulas and inspect the KaTeX-rendered editor preview. LaTeX is retained in the editor document.

The deterministic rules operate on Markdown headings, heading spacing, reference-list numbering, and trailing whitespace. 静态示例讨论不再代表模型运行结果；五角色意见来自实际审查任务。PDF 文件识别不等于已解析其内容，当前不声称支持无损 PDF 原地排版。

## Revision review and current limitations

- `GET /format-runs/{id}/review` recomputes the report from the run source and accepted preview. It returns `engine: deterministic`, `llm_enabled: false`, source/preview SHA-256 hashes, check results, aligned rows, and a unified diff. The overall status is `passed`, `needs_review`, or `blocked`. Demo findings without a source never pass review.
- Accepting a change validates its UTF-16 anchor, exact quote, context hash, replacement availability, and conflicts with accepted changes. Requests can include `expected_source_hash`; a mismatch returns HTTP 409 before changing the decision.
- Importing or editing a document requires a new formatting run. Existing run results refer to their captured source version. 对同一文档手动重新扫描时传 `document_id` 及当前 `expected_head_hash`，过期输入返回 HTTP 409。`GET /format-runs/{run_id}` 返回运行及版本元数据。
- 模型推理链路、上传材料证据、严格输出校验和 SQLite 已接入。逐字引文匹配只能验证出处，不能证明学术观点正确、结论有效、引用完整或世界首创；没有证据的发现保留 `needs_evidence`。人工复核仍是必要环节。
- 模型执行最多三并发、单次 90 秒、总计 600 秒、最多两轮及一次质疑。正文分块为 4,000 字符、重叠 200 字符，未覆盖部分和失败角色明确记录；部分完成不显示为完整完成。
- API 重启后将未结束任务标记为中断的部分结果，保留已完成角色，等待显式重试；不会自动重发模型请求。SSE 支持按事件游标恢复读取。
- DOCX/PDF 原生解析、Word 原生修订、OMML 往返、外部检索、查重/首创证明、`.tex` 编译、多用户协同、完整 token/费用核算和生产运维尚未完成。LaTeX API 只校验文本，不生成 PDF。

See [the capability comparison and roadmap](docs/AGENT_COMPARISON.md) for the observed GitHub project comparisons and precise implementation boundary.

## Running this verified candidate copy

This folder is an independent candidate. The original workspace source is preserved. Open two PowerShell terminals in this candidate folder:

```powershell
.\START_REVIEW.ps1 -Mode backend
```

```powershell
.\START_REVIEW.ps1 -Mode frontend
```

Open `http://localhost:3001`; the candidate API runs at `http://localhost:8001`. 使用候选目录自己的 `backend/.venv`；启动器可读取本地 `backend/.env` 供后端使用，密钥文件不应进入源代码包。预备运行目录可能通过 junction 共享前端依赖，源代码归档不依赖该链接。源代码包排除 `.venv`、`node_modules`、数据库及生成构建文件。独立解压后按上方步骤创建完整后端环境，并在 `frontend` 执行 `npm ci`，再启动候选版；启动器不会自动下载依赖。

详细 API 见 [后端说明](backend/README.md)。
