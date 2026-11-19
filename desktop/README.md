# 研辩台 Windows 桌面版

从 [v0.0.1 首版 Release](https://github.com/GYF-66/thesisdebate/releases/tag/v0.0.1) 下载 `ThesisDebate_0.0.1.exe` 后双击运行。GitHub 附件使用英文文件名，与原始 `研辩台_0.0.1.exe` 的内容和 SHA-256 完全相同。程序为 Windows x64 单文件应用，内置 Python、FastAPI 后端和界面，无需另外安装 Node.js 或 Python。

界面使用 Windows WebView2 Runtime；系统尚未安装时需先安装该运行环境。程序启动后在 `127.0.0.1` 自动选择本地端口，关闭窗口会停止本次本地服务。

## 开始使用

1. 首次启动为空白工作区，可以新建文档，或导入 Markdown、TXT、DOCX。
2. 编辑正文，选择适用模板，运行本地格式检查。
3. 在修订建议中逐项或分组接受、保留与撤销，查看原文和修改稿的差异。
4. 需要模型审查时，先在模型服务设置中配置兼容模型渠道，再启动审查。
5. 调整字体与版式，按需要导出 Markdown、DOCX 或分页 PDF。

文档名称与内容自动保存，也可手动保存。文档库支持切换已有文档，并恢复上次打开的文档及其关联修改记录。窗口关闭前会先保存最后的改动，保存失败时显示原因。

## 本地数据与渠道

默认数据目录为 `%LOCALAPPDATA%/ThesisDebate`。`reviews.sqlite3` 保存正文、草稿、资料及历史记录；`agent-settings.json` 保存模型渠道配置，Windows 使用 DPAPI 保护密钥。复制或更新 EXE 不会主动清空该目录。备份数据时应先关闭软件，再备份整个数据目录。

在界面中可新增、编辑、搜索、筛选、测试和切换模型渠道，密钥输入后不会被接口回显。未配置模型时，仍可使用文档编辑、本地格式修订、差异核验及导出。模型审查会把本次稿件与材料发送到所配置的服务。

## 文档兼容性

DOCX 导入提取支持的正文、标题、列表、链接、表格及部分公式文本，并提示不完整还原的部分。论文正文不支持 PDF 导入；资料与格式规范可读取文字层 PDF，扫描件不提供 OCR。

DOCX 导出可选择结构保护或可编辑副本。结构保护只回写能够唯一定位的普通文字，其他原始部件保留；公式、图片、批注、修订、域或结构变化等超出支持范围的修改会被阻止。可编辑副本会明确提示复杂对象损失。重新生成的 DOCX / PDF 中公式保留 LaTeX 源码，不保证数学字形排版。

PDF 使用分页预览与导出；所需字体不可直接用于 PDF 时，需要单独确认替代字体，模板要求不会因此被改写。

## 从源码构建

使用 Windows x64、Python 3.12 和 Node.js。在项目根目录执行：

```powershell
py -3.12 -m venv backend/.venv
./backend/.venv/Scripts/python.exe -m pip install -r desktop/requirements-build.txt
./backend/.venv/Scripts/python.exe -m pip install fonttools==4.68.0
cd frontend
npm ci
cd ..
./backend/.venv/Scripts/python.exe desktop/build_windows.py
```

构建脚本先检查前端类型并用 webpack 静态导出，再生成 `desktop/dist/研辩台.exe`。已有与源码一致的 `frontend/out` 时可以加 `--skip-frontend`；默认流程更适合首次构建。构建命令、依赖版本与 EXE SHA-256 会写入本地 `desktop/dist/build.json`。

构建工具要求 PyInstaller 6.22.3、pywebview 6.2.1、pythonnet 3.2.1、Agents SDK 0.25.0、python-docx 1.2.0、reportlab 5.1.0 和 fonttools 4.68.0。其余依赖按仓库锁定文件或对应 requirements 安装。

## 问题反馈

遇到问题可以前往 [Issues](https://github.com/GYF-66/thesisdebate/issues)，提供版本、Windows 环境、复现步骤、错误提示和脱敏截图。请勿上传模型密钥、本地配置文件或完整私人论文。

## 友链

[LINUX DO](https://linux.do/)
