"""Windows entry point: a local API, exported UI and one native window."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from queue import Queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import quote
from urllib.error import HTTPError

TITLE = "研辩台"
CONFIG_TEMPLATE = ("# 修改后重新打开研辩台。填写现有兼容服务的地址、模型和密钥。\n"
                   "AGENT_BASE_URL=\nAGENT_MODEL=\nAGENT_API_KEY=\n")
HTTP = build_opener(ProxyHandler({}))


def write_json(path: Path | None, value: dict) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def data_directory(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
            / "ThesisDebate").resolve()


def initialize(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    config = data / "config.env"
    try:
        with config.open("x", encoding="utf-8") as stream:
            stream.write(CONFIG_TEMPLATE)
    except FileExistsError:
        pass
    from dotenv import load_dotenv
    load_dotenv(config, override=False, encoding="utf-8-sig")
    # Importing app.main opens the database; set this before any app import.
    os.environ["THESISDEBATE_DB_PATH"] = str(data / "reviews.sqlite3")
    os.environ.setdefault("PYWEBVIEW_LOG", "error")
    handler = RotatingFileHandler(data / "desktop.log", maxBytes=1_000_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    # A --windowed executable has no stdout; third-party formatters expect a stream.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


class InstanceLock:
    """One process per data directory; separate disposable directories can coexist."""
    def __init__(self, data: Path):
        self.handle = None
        self.acquired = True
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            self.kernel.CreateMutexW.restype = ctypes.c_void_p
            self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            name = hashlib.sha256(str(data).casefold().encode()).hexdigest()[:24]
            self.handle = self.kernel.CreateMutexW(None, False, "Local\\ThesisDebate." + name)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def request_json(base: str, path: str, payload: dict | None = None, method: str | None = None) -> dict | list:
    content = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(base + path, data=content, headers={"Content-Type": "application/json"}, method=method)
    with HTTP.open(request, timeout=10) as response:
        return json.load(response)


class LocalRuntime:
    def __init__(self, data: Path, port: int, control: bool = False):
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from app import main, agent_api
        from fastapi import HTTPException, Request as APIRequest
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        import uvicorn

        resources = (Path(sys._MEIPASS) / "web" if getattr(sys, "frozen", False)
                     else Path(__file__).resolve().parents[1] / "frontend/out")
        if not (resources / "index.html").is_file():
            raise FileNotFoundError("Missing frontend/out/index.html. Build the frontend first.")
        icon = (Path(sys._MEIPASS) / "app.ico" if getattr(sys, "frozen", False)
                else Path(__file__).with_name("app.ico"))
        main.app.add_api_route("/favicon.ico", lambda: FileResponse(icon, media_type="image/x-icon"),
                               include_in_schema=False)
        self.data = data
        self.token = secrets.token_urlsafe(32)
        self.error: BaseException | None = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(("127.0.0.1", port))
        self.port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        previous_lifespan = main.app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            try:
                async with previous_lifespan(app):
                    yield
            finally:
                for cancellation in list(agent_api.CANCELLATIONS.values()):
                    cancellation.set()
                tasks = list(agent_api.TASKS.values())
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if agent_api.STORE is not None:
                    agent_api.STORE.close()

        main.app.router.lifespan_context = lifespan
        if control:
            # Only the explicit headless CLI exposes this local control endpoint.
            # The secret is written solely to the caller-selected state file.
            async def shutdown(request: APIRequest):
                if not secrets.compare_digest(request.headers.get("X-Desktop-Token", ""), self.token):
                    raise HTTPException(403, "Forbidden")
                self.server.should_exit = True
                return {"stopping": True}
            # A local import cannot resolve a postponed annotation in FastAPI.
            shutdown.__annotations__["request"] = APIRequest
            main.app.add_api_route("/__desktop__/shutdown", shutdown, methods=["POST"])
        # Keep the API routes ahead of the catch-all static mount.
        main.app.mount("/", StaticFiles(directory=str(resources), html=True), name="desktop-ui")
        self.server = uvicorn.Server(uvicorn.Config(
            main.app, host="127.0.0.1", port=self.port, loop="asyncio", http="h11",
            ws="none", workers=1, log_config=None, access_log=False,
            lifespan="on", timeout_graceful_shutdown=5,
        ))
        self.thread = threading.Thread(target=self._serve, name="thesisdebate-api", daemon=True)

    def _serve(self):
        try:
            self.server.run(sockets=[self.socket])
        except BaseException as error:
            self.error = error
            logging.exception("Local service stopped unexpectedly")

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.error or not self.thread.is_alive():
                raise RuntimeError("Local service did not start") from self.error
            if self.server.started:
                if request_json(self.url, "/health").get("status") == "ok":
                    return
            time.sleep(0.05)
        raise TimeoutError("Local service startup timed out")

    def stop(self):
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=12)
        if self.thread.is_alive():
            self.server.force_exit = True
            self.thread.join(timeout=2)
        self.socket.close()


def attach_close_save(window, runtime: LocalRuntime) -> None:
    """Keep the UI thread free while saving the last edit before closing."""
    allowed = threading.Event()
    closing = threading.Event()

    def evaluate(script: str):
        replies = Queue(maxsize=1)
        def work():
            try:
                replies.put((True, window.evaluate_js(script)))
            except BaseException as error:
                replies.put((False, error))
        threading.Thread(target=work, daemon=True).start()
        succeeded, value = replies.get(timeout=8)
        if not succeeded:
            raise value
        return value

    def save_then_close():
        try:
            deadline = time.monotonic() + 22
            while evaluate("window.__thesisSavePending === true"):
                if time.monotonic() >= deadline:
                    raise TimeoutError("A document save is still pending")
                time.sleep(.1)
            snapshot = evaluate("typeof window.__thesisCloseSnapshot === 'function' ? window.__thesisCloseSnapshot() : null")
            if snapshot:
                path = "/workspace/documents/" + quote(snapshot["id"], safe="")
                payload = {key: snapshot[key] for key in ("name", "content", "editor_json",
                           "expected_revision", "last_run_id") if key in snapshot}
                try:
                    request_json(runtime.url, path, payload, method="PUT")
                except HTTPError as error:
                    # A just-finished browser save may have committed the same bytes.
                    current = request_json(runtime.url, path) if error.code == 409 else None
                    if not current or any(current.get(key) != payload.get(key) for key in
                                          ("name", "content", "editor_json", "last_run_id")):
                        raise
            allowed.set()
            window.destroy()
        except BaseException:
            logging.exception("Close postponed because a document could not be saved")
            message = "文档尚未保存，窗口已保留。请保存或导出后再关闭。"
            try:
                evaluate("window.__thesisCloseResume && window.__thesisCloseResume(" + json.dumps(message) + ")")
            except BaseException:
                pass
            if os.name == "nt":
                ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x30)
            closing.clear()

    def on_closing():
        if allowed.is_set():
            return True
        if not closing.is_set():
            closing.set()
            threading.Thread(target=save_then_close, name="thesisdebate-close", daemon=True).start()
        return False

    window.events.closing += on_closing


def smoke_native(window, runtime: LocalRuntime, report: dict):
    try:
        if not window.events.loaded.wait(45):
            raise TimeoutError("Native page did not load")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            dom = window.evaluate_js("({title: document.title, text: document.body.innerText, ready: !!document.querySelector('[data-workspace-ready=\"true\"]'), empty: !!document.querySelector('[data-testid=\"workspace-empty\"]'), errors: performance.getEntriesByType('resource').filter(x => x.responseStatus >= 400).map(x => x.name)})")
            if dom and dom.get("ready") and "研辩台" in dom.get("text", ""):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Exported UI did not hydrate in WebView2")
        report["asset_errors"] = dom["errors"]
        if dom["errors"]:
            raise RuntimeError("Page assets failed to load")
        # Import the actual SDK from inside the frozen bundle, without a request.
        from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner
        from app.agent_schemas import AgentOutput
        Agent(name="desktop-import-check", output_type=AgentOutput)
        workspace = request_json(runtime.url, "/workspace")
        if not workspace["documents"] and not dom["empty"]:
            raise RuntimeError("Empty workspace did not show the document start screen")
        if "数字化学习平台研究与设计" in dom["text"]:
            raise RuntimeError("A demo manuscript was preloaded")
        run = request_json(runtime.url, "/format-runs", {
            "content": "参考文献\n- Entry. 2024.  \n",
            "document_name": "桌面启动测试.md", "document_type": "markdown",
        })
        changes = request_json(runtime.url, f"/format-runs/{run['id']}/changes")
        reference = next(change for change in changes if change["rule_id"] == "reference.gb7714")
        request_json(runtime.url, f"/format-changes/{reference['id']}/decision", {"decision": "accept"})
        preview = request_json(runtime.url, f"/format-runs/{run['id']}/preview")
        if preview["applied_count"] != 1 or "[1] Entry. 2024." not in preview["preview"]:
            raise RuntimeError("Packaged revision flow failed")
        report.update(status="passed", native_renderer="edgechromium", hydrated=True,
                      title=dom["title"], asset_errors=dom["errors"], sdk_import=True,
                      empty_workspace=not workspace["documents"], demo_preloaded=False,
                      run_id=run["id"], preview=preview["preview"],
                      agent_config=request_json(runtime.url, "/agent-config"))
        document = request_json(runtime.url, "/workspace/documents", {
            "name": "文档恢复检查.md", "content": "初始正文。",
            "editor_json": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "初始正文。"}]}]},
        })
        window.evaluate_js("location.reload()")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if window.evaluate_js("!!document.querySelector('[data-workspace-ready=\"true\"]') && document.querySelector('.tiptap')?.textContent.includes('初始正文。')"):
                break
            time.sleep(.1)
        else:
            raise RuntimeError("A saved workspace document did not restore in the native window")
        # A real contenteditable input, followed by close before the autosave delay.
        window.evaluate_js("(() => { const node=document.querySelector('.tiptap'); node.focus(); const selection=window.getSelection(); const range=document.createRange(); range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range); return document.execCommand('insertText', false, '关闭前最后一笔修改。'); })()")
        snapshot = window.evaluate_js("window.__thesisCloseSnapshot()")
        if not snapshot or snapshot["id"] != document["id"] or snapshot["content"].strip() != "关闭前最后一笔修改。":
            raise RuntimeError("The native close hook did not capture the last editor input")
        report["native_close_snapshot"] = snapshot
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        logging.exception("Desktop smoke failed")
    finally:
        window.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.no_window and args.smoke_test:
        parser.error("--smoke-test requires the native window")
    temporary = tempfile.TemporaryDirectory(prefix="thesisdebate-") if args.smoke_test and not args.data_dir else None
    data = data_directory(temporary.name if temporary else args.data_dir)
    runtime = None
    lock = None
    report = {"status": "starting", "pid": os.getpid(), "data_directory": str(data)}
    result = 1
    try:
        initialize(data)
        lock = InstanceLock(data)
        if not lock.acquired:
            if os.name == "nt" and not args.no_window:
                user32 = ctypes.WinDLL("user32")
                user32.FindWindowW.restype = ctypes.c_void_p
                user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
                user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
                user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                handle = user32.FindWindowW(None, TITLE)
                if handle:
                    user32.ShowWindow(handle, 9)
                    user32.SetForegroundWindow(handle)
            report["status"] = "already_running"
            result = 0
            return 0
        runtime = LocalRuntime(data, args.port, control=args.no_window)
        runtime.start()
        report.update(status="ready", url=runtime.url)
        if args.no_window:
            report["control_token"] = runtime.token
        write_json(args.state_file, report)
        if args.no_window:
            while runtime.thread.is_alive():
                runtime.thread.join(0.25)
            if runtime.error:
                raise RuntimeError("Local service stopped unexpectedly") from runtime.error
        else:
            import webview
            from webview.menu import Menu, MenuAction
            webview.settings["ALLOW_DOWNLOADS"] = True
            menu = [Menu(TITLE, [
                MenuAction("模型配置", lambda: subprocess.Popen(["notepad.exe", str(data / "config.env")])),
                MenuAction("打开数据目录", lambda: os.startfile(str(data))),
            ])]
            window = webview.create_window(TITLE, runtime.url, width=1440, height=940,
                                           min_size=(1060, 700), hidden=args.smoke_test,
                                           text_select=True, background_color="#f7f8fa")
            attach_close_save(window, runtime)
            localization = {"global.quitConfirmation": "关闭研辩台？",
                            "global.ok": "确定", "global.cancel": "取消",
                            "global.saveFile": "保存文件"}
            webview.start(smoke_native if args.smoke_test else None,
                          args=(window, runtime, report) if args.smoke_test else None,
                          gui="edgechromium", debug=False, private_mode=False,
                          storage_path=str(data / "webview"), menu=menu, localization=localization)
            if args.smoke_test and report.get("status") == "passed":
                snapshot = report.get("native_close_snapshot")
                if not snapshot:
                    raise RuntimeError("The native close save was not exercised")
                saved = request_json(runtime.url, "/workspace/documents/" + quote(snapshot["id"], safe=""))
                if any(saved.get(key) != snapshot.get(key) for key in ("name", "content", "editor_json", "last_run_id")):
                    raise RuntimeError("Closing the native window did not persist the exact last edit")
                report.update(native_close_save=True, native_close_saved_revision=saved["revision"],
                              native_close_saved_content=saved["content"])
        result = 0 if not args.smoke_test or report["status"] == "passed" else 1
    except KeyboardInterrupt:
        result = 0
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        logging.exception("Desktop startup failed")
        if os.name == "nt" and not (args.no_window or args.smoke_test):
            ctypes.windll.user32.MessageBoxW(None,
                f"启动失败。请确认已安装 Microsoft Edge WebView2 Runtime。\n详情：{data / 'desktop.log'}",
                TITLE, 0x10)
    finally:
        if runtime:
            runtime.stop()
            report["service_stopped"] = not runtime.thread.is_alive()
        if lock:
            lock.close()
        report["exit_status"] = result
        write_json(args.state_file, report)
        if temporary:
            temporary.cleanup()
    return result


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
