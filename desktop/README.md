# 研辩台 Windows 版

双击 `dist/研辩台.exe` 打开。程序内置后端、页面和 Python 运行环境，无需单独启动终端、Node.js 或 Python。

窗口使用 Windows WebView2 Runtime；如系统未安装，需要先安装该运行环境。关闭窗口会停止本次启动的本地服务。

## 文档工作区

首次打开显示空白工作区，不预载示例论文。可以新建文档，或导入 Markdown、TXT、DOCX、带文字层的 PDF；左侧目录取自当前文档的真实标题。复杂排版、公式和图片需对照原稿核验。扫描型 PDF 请先 OCR。

编辑内容和文档名称自动保存，也可手动保存。文档库支持切换已有文档；重新启动后恢复上次打开的文档、编辑内容和关联的修改记录。关闭窗口前会先保存最后的改动，保存失败时保留窗口并显示原因。

本地格式扫描根据当前正文生成建议，修改对照展示真实原文与采纳后的版本。模型服务未配置时不会生成模拟审查意见。

## 数据和模型配置

文档、草稿、材料和历史版本保存在 `%LOCALAPPDATA%/ThesisDebate/reviews.sqlite3`，不会保存在 EXE 临时目录。复制或更新 EXE 不会清空该数据目录。

推荐在右上角“Agent 设置”填写现有兼容服务，保存后立即生效。也可在 `%LOCALAPPDATA%/ThesisDebate/config.env` 配置后重新打开程序：

```dotenv
AGENT_BASE_URL=
AGENT_MODEL=
AGENT_API_KEY=
```

配置留空时可以使用本地格式修订与修改对照；模型审查按钮保持禁用。实际密钥不随 EXE 打包。

## 从源码构建

使用 Windows x64 和 Python 3.12，在项目根目录执行：

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r .\desktop\requirements-build.txt
.\backend\.venv\Scripts\python.exe .\desktop\build_windows.py
```

默认先运行前端 lint 和 webpack 静态导出，再生成 `desktop/dist/研辩台.exe`。已有最新 `frontend/out` 时可加 `--skip-frontend`。前端依赖需事先安装；脚本不会修改依赖目录。

构建工具固定为 PyInstaller 6.22.3、pywebview 6.2.1、pythonnet 3.2.1，Agents SDK 固定为 0.25.0。实际构建命令、运行依赖版本和 EXE SHA-256 记录于 `desktop/dist/build.json`。
