# 研辩台后端

后端由 FastAPI、SQLite、文档处理模块与有界五角色审查引擎组成，供本地单用户工作台使用。确定性格式核验与模型审查分别执行、分别记录结果。

## 环境与启动

使用完整的 Python 3.12 安装，在项目根目录创建环境并安装依赖：

```powershell
py -3.12 -m venv backend/.venv
./backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
./START_REVIEW.ps1 -Mode backend
```

启动器默认将 API 绑定到 `127.0.0.1:8001`，以一个 worker 运行，并读取存在于 `backend/.env` 的本地配置。在另一个终端运行 `./START_REVIEW.ps1 -Mode frontend`，前端默认地址为 `http://localhost:3001`，并自动使用对应 API 地址。

可通过 `-ApiPort` 更改 API 端口；前后端启动器应使用相同参数。服务运行后，可访问 `/health` 检查状态，或打开 `/docs` 查看本版本自动生成的接口文档。

## 主要模块

| 模块 | 功能 |
| --- | --- |
| `workspace.py`、`storage.py` | 文档库、草稿、自动保存、版本头与 SQLite 持久化 |
| `document_import.py` | Markdown、UTF-8 TXT、DOCX 导入与兼容性提示 |
| `main.py`、`revision_review.py`、`batch_revision.py` | 本地规则建议、逐项或分组决策、撤销、差异与独立修订核验 |
| `agent_engine.py`、`agent_api.py` | 五角色审查、事件流、取消、重试、覆盖情况与证据记录 |
| `agent_settings.py` | 模型渠道库、配置快照、连接测试和本地凭据存储 |
| `document_layout.py`、`document_fonts.py`、`document_export.py` | 版式、字体目录、Markdown / DOCX / PDF 导出 |
| `docx_preservation.py` | 原 DOCX 结构保护与受约束的文字回写 |
| `document_assistance.py`、`template_api.py` | 返修辅助、资料摘录检索、文献元数据查询及模板规范工具 |

论文正文导入不支持 PDF；资料和格式规范可读取文字层 PDF，不提供扫描件 OCR。DOCX 导入会提取可支持的正文结构并提示兼容性，不能宣称完整还原任意 Word 文件。导出结构保护模式仅允许可唯一定位的普通文字回写；超出范围的复杂修改会被阻止，可另行选择带明确提示的可编辑副本。

## 模型配置与审查

推荐在界面的模型服务设置中创建渠道，填写兼容服务的 API 基址、模型名称与密钥，测试连接后启用。支持保存多个渠道、搜索筛选和切换。配置文件为数据库同目录下的 `agent-settings.json`；Windows 使用 DPAPI 保护密钥，接口不回显实际密钥。

也可使用服务端环境变量 `AGENT_BASE_URL`、`AGENT_MODEL`、`AGENT_API_KEY`。未配置模型时，本地格式修订、编辑和导出仍可使用；模型任务不会以模拟内容返回成功。

审查基于稿件、修订稿和本次提供的材料，输出角色意见、可定位证据与真实执行状态。任务支持取消、对同一快照显式重试；长文覆盖不完整或角色失败时保留部分结果。接受建议需由用户操作，并重新检查当前版本与锚点，避免旧任务覆盖新内容。

资料问答采用本地关键词摘录检索；文献工具查询 Crossref / OpenAlex 的元数据，均不能视为论文论点正确、原创性或查重结果的证明。`/latex/compile` 仅作轻量源码检查，不调用 TeX 编译器。

## 数据与部署

源码运行默认数据库为 `backend/data/reviews.sqlite3`，可用 `THESISDEBATE_DB_PATH` 指定其他位置。文档、资料、版本、审查任务与事件保存在本地 SQLite 中，采用 WAL、外键与事务。历史恢复会创建新版本，保留已有记录。

当前适用于本地单用户、单 API worker。预留的 PostgreSQL 迁移及 Docker 服务不是本版本的运行依赖；本版本没有多人协同、分布式队列或完整 token 费用核算。

## 测试

在 `backend/` 目录执行：

```powershell
./.venv/Scripts/python.exe -m pip install -r requirements-test.txt
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

本地测试提供方用于验证协议、状态流转与存储；真实模型服务兼容性以对应服务的实际运行结果为准。问题可通过 [Issues](https://github.com/GYF-66/thesisdebate/issues) 反馈。

## 友链

[LINUX DO](https://linux.do/)
