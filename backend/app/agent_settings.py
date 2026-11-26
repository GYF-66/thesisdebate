"""Local model configuration; credentials never leave the write-only input path."""
from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr


router = APIRouter()
_LOCK = threading.RLock()


class SettingsInput(BaseModel):
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=255)
    api_key: SecretStr | None = None
    clear_api_key: bool = False


def settings_path() -> Path:
    explicit = os.environ.get("THESISDEBATE_AGENT_SETTINGS_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    database = os.environ.get("THESISDEBATE_DB_PATH")
    folder = Path(database).expanduser().resolve().parent if database else Path(__file__).resolve().parents[1] / "data"
    return folder / "agent-settings.json"


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError("local credential protection failed")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def _encode_key(key: str) -> dict:
    if os.name == "nt":
        return {"encoding": "windows-dpapi", "value": base64.b64encode(_dpapi(key.encode("utf-8"))).decode("ascii")}
    # The desktop distribution is Windows; other hosts use an owner-only file.
    return {"encoding": "owner-only-text", "value": key}


def _decode_key(value: dict) -> str:
    if value.get("encoding") == "windows-dpapi":
        return _dpapi(base64.b64decode(value["value"], validate=True), decrypt=True).decode("utf-8")
    if value.get("encoding") == "owner-only-text" and os.name != "nt":
        return str(value.get("value", ""))
    raise ValueError("unsupported local credential encoding")


def _environment() -> dict:
    return {key: (os.environ.get(primary) or os.environ.get(alias) or "").strip()
            for key, primary, alias in [("base_url", "AGENT_BASE_URL", "OPENAI_BASE_URL"),
                                        ("model", "AGENT_MODEL", "OPENAI_MODEL"),
                                        ("api_key", "AGENT_API_KEY", "OPENAI_API_KEY")]}


def resolve_config() -> dict:
    """Private snapshot for one review; a saved configuration overrides env as a unit."""
    with _LOCK:
        path = settings_path()
        if not path.is_file():
            return {**_environment(), "source": "environment", "storage_error": False}
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            key = _decode_key(stored["credential"]) if stored.get("credential") else ""
            return {"base_url": str(stored.get("base_url", "")).strip(),
                    "model": str(stored.get("model", "")).strip(), "api_key": key,
                    "source": "local", "storage_error": False}
        except (OSError, ValueError, TypeError, KeyError):
            return {"base_url": "", "model": "", "api_key": "", "source": "local", "storage_error": True}


def normalized_endpoint(endpoint: str) -> str | None:
    try:
        parsed = urlsplit(endpoint)
        if (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and not any(character.isspace() for character in endpoint)):
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        pass
    return None


def public_config(config: dict | None = None) -> dict:
    config = resolve_config() if config is None else config
    endpoint, model, key = (config.get(name, "") for name in ["base_url", "model", "api_key"])
    safe_endpoint = normalized_endpoint(endpoint)
    missing = [label for label, value in [("服务地址", endpoint), ("模型名称", model), ("API 密钥", key)] if not value]
    reason = ("本机配置无法读取，请在设置中重新填写。" if config.get("storage_error") else
              "缺少配置：" + "、".join(missing) if missing else
              "服务地址必须是无凭据、查询参数或片段的 http(s) URL。" if not safe_endpoint else
              "已配置；可测试连接后启动 Agent 审查。")
    return {"configured": not missing and bool(safe_endpoint) and not config.get("storage_error", False),
            "base_url": safe_endpoint.replace(key, "[redacted]") if key and safe_endpoint else safe_endpoint,
            "model": (model.replace(key, "[redacted]") if key else model) or None,
            "has_api_key": bool(key), "source": config.get("source", "local"), "reason": reason}


def candidate_config(payload: SettingsInput) -> dict:
    current = resolve_config()
    key = "" if payload.clear_api_key else ((payload.api_key.get_secret_value().strip() if payload.api_key else "") or current["api_key"])
    endpoint, model = payload.base_url.strip(), payload.model.strip()
    if endpoint and normalized_endpoint(endpoint) is None:
        raise HTTPException(422, "服务地址必须是无凭据、查询参数或片段的 http(s) URL。")
    if any(character in model or character in key for character in ["\r", "\n", "\x00"]) or len(key) > 8192:
        raise HTTPException(422, "模型名称或密钥格式无效。")
    return {"base_url": normalized_endpoint(endpoint) or "", "model": model, "api_key": key,
            "source": "local", "storage_error": False}


def save_config(payload: SettingsInput) -> dict:
    with _LOCK:
        config = candidate_config(payload)
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = {"version": 1, "base_url": config["base_url"], "model": config["model"],
                  "credential": _encode_key(config["api_key"]) if config["api_key"] else None}
        descriptor, temporary = tempfile.mkstemp(prefix=".agent-settings-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(stored, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return public_config(config)


def _guard_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(403, "仅允许从本机工作台管理模型设置。")


async def settings_input(request: Request) -> SettingsInput:
    _guard_origin(request)
    try:
        return SettingsInput.model_validate(await request.json())
    except (ValueError, TypeError):
        # FastAPI's default validation response echoes the offending input.
        raise HTTPException(422, "设置字段格式无效，请检查服务地址、模型名称和密钥。") from None


@router.get("/agent-settings")
def get_settings(request: Request):
    _guard_origin(request)
    return public_config()


@router.put("/agent-settings")
def put_settings(request: Request, payload: SettingsInput = Depends(settings_input)):
    _guard_origin(request)
    try:
        return save_config(payload)
    except (OSError, ValueError):
        raise HTTPException(500, "无法保存本机模型设置，请检查本机数据目录。") from None


@router.post("/agent-settings/test")
async def test_settings(request: Request, payload: SettingsInput = Depends(settings_input)):
    _guard_origin(request)
    from app.agent_engine import _SDKProvider, _safe_error
    from app.agent_schemas import AgentOutput
    config = candidate_config(payload)
    status = public_config(config)
    if not status["configured"]:
        raise HTTPException(422, status["reason"])
    adapter = _SDKProvider(config)
    try:
        result = await asyncio.wait_for(adapter("methods", "连接测试。仅输出符合指定 schema 的 JSON，summary 写连接成功，findings 为空数组。",
                                               {"phase": "connection_test", "manuscript": {"content": "连接测试，无论文正文。"}}), timeout=20)
        AgentOutput.model_validate(result.model_dump() if hasattr(result, "model_dump") else result)
        return {"ok": True, "message": "连接成功，模型已返回兼容的结构化审查结果。"}
    except Exception as error:
        raise HTTPException(502, _safe_error(error)) from None
    finally:
        await adapter.close()
