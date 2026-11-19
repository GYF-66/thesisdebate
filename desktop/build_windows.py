"""Build the self-contained Windows application from this candidate tree."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import struct
import subprocess
import sys


DESKTOP = Path(__file__).resolve().parent
ROOT = DESKTOP.parent
BUILD_VERSIONS = {
    "pyinstaller": "6.22.3",
    "pywebview": "6.2.1",
    "pythonnet": "3.2.1",
    "openai-agents": "0.25.0",
    "python-docx": "1.2.0",
    "reportlab": "5.1.0",
    "fonttools": "4.68.0",
}


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(json.dumps({"command": command, "cwd": str(cwd)}, ensure_ascii=False), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-frontend", action="store_true",
                        help="Use an existing frontend/out export.")
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise SystemExit("Use a 64-bit Python environment on Windows.")
    actual = {name: importlib.metadata.version(name) for name in BUILD_VERSIONS}
    if actual != BUILD_VERSIONS:
        raise SystemExit("Install desktop/requirements-build.txt in the candidate environment first.")
    entry = DESKTOP / "main.py"
    if not entry.is_file():
        raise SystemExit("Missing desktop/main.py.")

    if not args.skip_frontend:
        npm = shutil.which("npm.cmd")
        if not npm:
            raise SystemExit("Install Node.js and npm before building the application.")
        frontend = ROOT / "frontend"
        if not (frontend / "node_modules/next/dist/bin/next").is_file():
            raise SystemExit("Install the frontend dependencies before building.")
        run([npm, "run", "lint"], frontend)
        build_env = os.environ.copy()
        # The packaged page must always call its own embedded local API.
        build_env["NEXT_PUBLIC_API_BASE_URL"] = ""
        build_env["NODE_ENV"] = "production"
        run([npm, "run", "build", "--", "--webpack"], frontend, build_env)

    web = ROOT / "frontend/out"
    if not (web / "index.html").is_file():
        raise SystemExit("Missing static frontend export: frontend/out/index.html.")
    run([sys.executable, str(DESKTOP / "create_icon.py")], ROOT)
    icon = DESKTOP / "app.ico"
    if not icon.is_file():
        raise SystemExit("Missing generated application icon.")
    dist = DESKTOP / "dist"
    build = DESKTOP / "build"
    build.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--noupx", "--name", "研辩台",
        "--distpath", str(dist), "--workpath", str(build / "work"),
        "--specpath", str(build), "--paths", str(ROOT / "backend"),
        "--icon", str(icon),
        "--add-data", str(web) + os.pathsep + "web",
        "--add-data", str(icon) + os.pathsep + ".",
        "--recursive-copy-metadata", "openai-agents",
        "--collect-submodules", "agents",
        "--collect-data", "agents",
        "--collect-submodules", "openai",
        "--collect-data", "openai",
        "--collect-submodules", "httpx2",
        "--collect-data", "certifi",
        "--collect-submodules", "docx",
        "--collect-data", "docx",
        "--collect-submodules", "reportlab",
        "--collect-data", "reportlab",
        "--collect-submodules", "pypdf",
        "--collect-data", "pypdf",
        "--collect-submodules", "fontTools",
        "--collect-data", "fontTools",
        "--copy-metadata", "fonttools",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.lifespan.on",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "tkinter",
        str(entry),
    ]
    # Some complete Python distributions keep required CA policy data next to ssl.py.
    # Preserve that public runtime resource verbatim inside the frozen application.
    ca_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if ca_policy.is_file():
        command[-1:-1] = ["--add-data", str(ca_policy) + os.pathsep + "."]
    run(command, ROOT)
    executable = dist / "研辩台.exe"
    data = executable.read_bytes()
    if data[:2] != b"MZ":
        raise SystemExit("The output is not a Windows executable.")
    manifest = {
        "executable": str(executable.resolve()),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "python": sys.version,
        "architecture": platform.machine(),
        "build_versions": actual,
        "runtime_versions": {name: importlib.metadata.version(name) for name in
                             ("fastapi", "uvicorn", "openai", "httpx2", "pydantic", "python-docx", "reportlab", "pypdf", "fonttools")},
        "static_files": sum(path.is_file() for path in web.rglob("*")),
        "command": command,
    }
    (dist / "build.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
