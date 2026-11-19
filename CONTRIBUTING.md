# 参与研辩台

欢迎通过 [Issues](https://github.com/GYF-66/thesisdebate/issues) 反馈问题、分享使用场景或提出功能建议。

## 提交问题

先搜索已有 Issues；如果没有相同问题，请选择“问题反馈”模板，填写软件版本、运行环境、复现步骤、预期结果和实际结果。导入导出相关问题可附上脱敏后的最小文档及截图，便于准确复现。

反馈模型连接或审查异常时，请说明兼容接口类型、模型名称和界面显示的错误；不要上传 API Key、`agent-settings.json`、本地数据库或完整私人论文。

## 提出建议

选择“功能建议”模板，说明正在完成的任务、遇到的困难和希望得到的结果。有现有操作流程、示例文档或界面草图时，也可以一并提供。

## 修改源码

从当前分支创建自己的开发分支，尽量让一次修改聚焦一个具体问题。提交前检查相关功能，并在 Pull Request 中说明行为变化和验证结果。环境准备与启动方法见 [项目说明](README.md)、[后端说明](backend/README.md) 和 [桌面构建说明](desktop/README.md)。

后端测试在 `backend/` 下运行：

```powershell
./.venv/Scripts/python.exe -m pip install -r requirements-test.txt
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

前端检查在 `frontend/` 下运行：

```powershell
npm run lint
node --test
npm run build -- --webpack
```

不要提交依赖目录、虚拟环境、密钥、数据库、运行日志或构建产物。需要改动模型行为时，请同时说明使用真实服务还是本地测试提供方，避免将协议测试结果表述为学术审查质量结论。

## 友链

[LINUX DO](https://linux.do/)
