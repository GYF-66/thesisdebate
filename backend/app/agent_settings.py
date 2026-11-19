"""Local model configuration; credentials never leave the write-only input path."""
from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr


router = APIRouter()
_LOCK = threading.RLock()
_ENV_SESSION = uuid4().bytes
ENVIRONMENT_ID = "environment"
LEGACY_ID = "legacy-default"


class SettingsInput(BaseModel):
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=255)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    expected_revision: int | None = Field(default=None, ge=0)


class ChannelInput(SettingsInput):
    name: str = Field(min_length=1, max_length=80)


class RevisionInput(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)


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


def _read_library() -> dict | None:
    """Read/migrate in memory only. GET never writes or re-encrypts credentials."""
    path = settings_path()
    if not path.is_file():
        return None
    stored = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(stored, dict):
        raise ValueError("invalid settings library")
    if stored.get("version", 1) == 1:
        stored = {"version": 2, "revision": 0, "active_channel_id": LEGACY_ID,
                  "channels": [{"id": LEGACY_ID, "name": "默认渠道", "config_revision": 1,
                                "base_url": stored.get("base_url", ""), "model": stored.get("model", ""),
                                "credential": stored.get("credential")} ]}
    if (stored.get("version") != 2 or type(stored.get("revision")) is not int or stored["revision"] < 0
            or not isinstance(stored.get("channels"), list) or len(stored["channels"]) > 100):
        raise ValueError("invalid settings library")
    identities = set()
    for channel in stored["channels"]:
        if (not isinstance(channel, dict) or not isinstance(channel.get("id"), str)
                or not channel["id"] or channel["id"] == ENVIRONMENT_ID or len(channel["id"]) > 100
                or channel["id"] in identities or not isinstance(channel.get("name"), str)
                or not channel["name"].strip() or len(channel["name"]) > 80
                or not isinstance(channel.get("base_url"), str) or len(channel["base_url"]) > 2000
                or not isinstance(channel.get("model"), str) or len(channel["model"]) > 255
                or type(channel.get("config_revision")) is not int or channel["config_revision"] < 1
                or (channel.get("credential") is not None and not isinstance(channel["credential"], dict))):
            raise ValueError("invalid settings channel")
        identities.add(channel["id"])
    if stored.get("active_channel_id") is not None and stored["active_channel_id"] not in identities:
        raise ValueError("invalid active channel")
    return stored


def _channel_record(library: dict, channel_id: str) -> dict:
    record = next((item for item in library["channels"] if item["id"] == channel_id), None)
    if record is None:
        raise HTTPException(404, "渠道不存在，请刷新渠道库。")
    return record


def _record_config(record: dict) -> dict:
    return {"base_url": record["base_url"].strip(), "model": record["model"].strip(),
            "api_key": _decode_key(record["credential"]) if record.get("credential") else "",
            "source": "local", "storage_error": False, "channel_id": record["id"],
            "channel_name": record["name"], "config_revision": record["config_revision"]}


def _environment_config() -> dict:
    return {**_environment(), "source": "environment", "storage_error": False,
            "channel_id": ENVIRONMENT_ID, "channel_name": "环境配置（只读）", "config_revision": 0}


def _empty_config(storage_error=False) -> dict:
    return {"base_url": "", "model": "", "api_key": "", "source": "local", "storage_error": storage_error,
            "channel_id": None, "channel_name": None, "config_revision": 0}


def resolve_config(channel_id: str | None = None) -> dict:
    """Private immutable-by-copy snapshot; local channels never inherit env keys."""
    with _LOCK:
        try:
            library = _read_library()
            if library is None:
                if channel_id not in (None, ENVIRONMENT_ID):
                    return _empty_config(True)
                return _environment_config()
            selected = channel_id if channel_id is not None else library["active_channel_id"]
            if selected is None:
                return _empty_config()
            return _record_config(_channel_record(library, selected))
        except (OSError, ValueError, TypeError, KeyError, HTTPException):
            return _empty_config(True)


def config_binding(config: dict) -> dict:
    """Non-secret execution identity. Environment snapshots are session-bound."""
    identity = [config.get("channel_id"), config.get("config_revision", 0), config.get("base_url"), config.get("model")]
    data = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if config.get("source") == "environment":
        token = hmac.new(_ENV_SESSION, data + b"\0" + config.get("api_key", "").encode("utf-8"), hashlib.sha256).hexdigest()
    else:
        token = hashlib.sha256(data).hexdigest()
    name, key = config.get("channel_name"), config.get("api_key", "")
    return {"channel_id": config.get("channel_id"), "channel_name": name.replace(key, "[redacted]") if name and key else name,
            "channel_config_revision": config.get("config_revision", 0), "channel_identity": token,
            "model": config.get("model", "").replace(key, "[redacted]") if key and config.get("model") else config.get("model") or None}


def resolve_bound_config(binding: dict) -> dict:
    channel_id = binding.get("channel_id")
    if not channel_id or not binding.get("channel_identity"):
        raise HTTPException(409, "该历史审查未绑定渠道，请新建审查；旧分段不会跨渠道复用。")
    config = resolve_config(channel_id)
    current = config_binding(config)
    if (current["channel_identity"] != binding["channel_identity"]
            or current["channel_config_revision"] != binding.get("channel_config_revision")):
        raise HTTPException(409, "原渠道已删除或配置已变化，请新建审查；旧分段不会跨渠道复用。")
    return dict(config)


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
            "has_api_key": bool(key), "source": config.get("source", "local"), "reason": reason,
            "channel_id": config.get("channel_id"), "channel_name": config.get("channel_name", "").replace(key, "[redacted]") if config.get("channel_name") and key else config.get("channel_name"),
            "config_revision": config.get("config_revision", 0)}


def candidate_config(payload: SettingsInput, current: dict | None = None) -> dict:
    current = resolve_config() if current is None else current
    key = "" if payload.clear_api_key else ((payload.api_key.get_secret_value().strip() if payload.api_key else "") or current["api_key"])
    endpoint, model = payload.base_url.strip(), payload.model.strip()
    if endpoint and normalized_endpoint(endpoint) is None:
        raise HTTPException(422, "服务地址必须是无凭据、查询参数或片段的 http(s) URL。")
    if any(character in model or character in key for character in ["\r", "\n", "\x00"]) or len(key) > 8192:
        raise HTTPException(422, "模型名称或密钥格式无效。")
    return {"base_url": normalized_endpoint(endpoint) or "", "model": model, "api_key": key,
            "source": "local", "storage_error": False}


def _mutable_library(expected_revision: int | None) -> dict:
    try:
        library = _read_library() or {"version": 2, "revision": 0, "active_channel_id": None, "channels": []}
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(409, "本机渠道库无法读取，未覆盖原设置；请先恢复设置文件。") from None
    if expected_revision is not None and expected_revision != library["revision"]:
        raise HTTPException(409, "渠道库已变化，请刷新后再保存或切换。")
    return library


def _write_library(library: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    library["revision"] += 1
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(library, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _updated_record(payload: SettingsInput, record: dict | None, name: str) -> dict:
    name = name.strip()
    if not name or len(name) > 80 or any(char in name for char in ("\r", "\n", "\0")):
        raise HTTPException(422, "渠道名称须为 1 至 80 个可见字符。")
    current = _record_config(record) if record else _empty_config()
    config = candidate_config(payload, current)
    unchanged = all(config[key] == current[key] for key in ("base_url", "model", "api_key"))
    credential = record.get("credential") if record and config["api_key"] == current["api_key"] else (_encode_key(config["api_key"]) if config["api_key"] else None)
    return {"id": record["id"] if record else "channel-" + uuid4().hex,
            "name": name, "base_url": config["base_url"], "model": config["model"], "credential": credential,
            "config_revision": record["config_revision"] + (0 if unchanged else 1) if record else 1}


def _public_library(library: dict | None) -> dict:
    if library is None:
        config = _environment_config()
        channel = {**public_config(config), "id": ENVIRONMENT_ID, "name": config["channel_name"],
                   "active": True, "read_only": True}
        return {"version": 2, "revision": 0, "active_channel_id": ENVIRONMENT_ID, "channels": [channel], "active": public_config(config)}
    channels = []
    for record in library["channels"]:
        try:
            config = _record_config(record)
        except (OSError, ValueError, TypeError, KeyError):
            config = {**_empty_config(True), "channel_id": record["id"], "channel_name": record["name"], "config_revision": record["config_revision"]}
        channels.append({**public_config(config), "id": record["id"], "name": public_config(config)["channel_name"],
                         "active": record["id"] == library["active_channel_id"], "read_only": False})
    active = next((item for item in channels if item["active"]), None)
    return {"version": 2, "revision": library["revision"], "active_channel_id": library["active_channel_id"],
            "channels": channels, "active": {key: value for key, value in active.items() if key not in {"id", "name", "active", "read_only"}} if active else public_config(_empty_config())}


def channel_library() -> dict:
    with _LOCK:
        return _public_library(_mutable_library(None) if settings_path().is_file() else None)


def save_config(payload: SettingsInput) -> dict:
    """Compatibility PUT: save/activate the old singleton as one default channel."""
    with _LOCK:
        library = _mutable_library(payload.expected_revision)
        record = _channel_record(library, library["active_channel_id"]) if library["active_channel_id"] else None
        # Legacy API historically allowed explicitly importing the environment key.
        if record is None and not settings_path().is_file():
            config = candidate_config(payload)
            payload = payload.model_copy(update={"api_key": SecretStr(config["api_key"])})
        updated = _updated_record(payload, record, record["name"] if record else "默认渠道")
        library["channels"] = [item for item in library["channels"] if item["id"] != updated["id"]] + [updated]
        library["active_channel_id"] = updated["id"]
        _write_library(library)
        return public_config(_record_config(updated))


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


async def _parse_input(request: Request, schema):
    _guard_origin(request)
    try:
        data = await request.body()
        return schema.model_validate(json.loads(data) if data else {})
    except (ValueError, TypeError):
        raise HTTPException(422, "设置字段格式无效，请检查渠道名称、服务地址、模型名称和密钥。") from None


async def channel_input(request: Request) -> ChannelInput:
    return await _parse_input(request, ChannelInput)


async def revision_input(request: Request) -> RevisionInput:
    return await _parse_input(request, RevisionInput)


def _keep_omitted_fields(payload: SettingsInput, current: dict) -> SettingsInput:
    return payload.model_copy(update={key: current[key] for key in ("base_url", "model") if key not in payload.model_fields_set})


def _check_editable(channel_id: str):
    if channel_id == ENVIRONMENT_ID:
        raise HTTPException(403, "环境渠道为只读来源，请新增独立保存的渠道。")


@router.get("/agent-settings/channels")
def get_channels(request: Request):
    _guard_origin(request)
    return channel_library()


@router.post("/agent-settings/channels")
def create_channel(request: Request, payload: ChannelInput = Depends(channel_input)):
    _guard_origin(request)
    try:
        with _LOCK:
            library = _mutable_library(payload.expected_revision)
            if len(library["channels"]) >= 100:
                raise HTTPException(422, "最多保存 100 个渠道，请先清理不再使用的渠道。")
            library["channels"].append(_updated_record(payload, None, payload.name))
            _write_library(library)
            return _public_library(library)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(500, "无法保存本机渠道，请检查本机数据目录。") from None


async def _test_config(config: dict) -> dict:
    from app.agent_engine import _SDKProvider, _safe_error
    from app.agent_schemas import AgentOutput
    status = public_config(config)
    if not status["configured"]:
        raise HTTPException(422, status["reason"])
    adapter = _SDKProvider(dict(config))
    try:
        result = await asyncio.wait_for(adapter("methods", "连接测试。仅输出符合指定 schema 的 JSON，summary 写连接成功，findings 为空数组。",
                                               {"phase": "connection_test", "manuscript": {"content": "连接测试，无论文正文。"}}), timeout=20)
        AgentOutput.model_validate(result.model_dump() if hasattr(result, "model_dump") else result)
        return {"ok": True, "message": "连接成功，模型已返回兼容的结构化审查结果。"}
    except Exception as error:
        raise HTTPException(502, _safe_error(error)) from None
    finally:
        await adapter.close()


# Static draft-test route precedes channel-id routes; it never borrows another key.
@router.post("/agent-settings/channels/test")
async def test_new_channel(request: Request, payload: SettingsInput = Depends(settings_input)):
    _guard_origin(request)
    return await _test_config(candidate_config(payload, _empty_config()))


@router.put("/agent-settings/channels/{channel_id}")
def update_channel(channel_id: str, request: Request, payload: ChannelInput = Depends(channel_input)):
    _guard_origin(request)
    _check_editable(channel_id)
    try:
        with _LOCK:
            library = _mutable_library(payload.expected_revision)
            record = _channel_record(library, channel_id)
            updated = _updated_record(_keep_omitted_fields(payload, _record_config(record)), record, payload.name)
            library["channels"] = [updated if item["id"] == channel_id else item for item in library["channels"]]
            _write_library(library)
            return _public_library(library)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(500, "无法保存本机渠道，请检查本机数据目录。") from None


@router.delete("/agent-settings/channels/{channel_id}")
def delete_channel(channel_id: str, request: Request, expected_revision: int | None = None):
    _guard_origin(request)
    _check_editable(channel_id)
    try:
        with _LOCK:
            library = _mutable_library(expected_revision)
            _channel_record(library, channel_id)
            library["channels"] = [item for item in library["channels"] if item["id"] != channel_id]
            if library["active_channel_id"] == channel_id:
                library["active_channel_id"] = None
            _write_library(library)
            return _public_library(library)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(500, "无法删除本机渠道，请检查本机数据目录。") from None


@router.post("/agent-settings/channels/{channel_id}/activate")
def activate_channel(channel_id: str, request: Request, payload: RevisionInput = Depends(revision_input)):
    _guard_origin(request)
    _check_editable(channel_id)
    try:
        with _LOCK:
            library = _mutable_library(payload.expected_revision)
            config = _record_config(_channel_record(library, channel_id))
            status = public_config(config)
            if not status["configured"]:
                raise HTTPException(422, status["reason"])
            library["active_channel_id"] = channel_id
            _write_library(library)
            return _public_library(library)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(500, "无法启用本机渠道，请检查本机数据目录。") from None


@router.post("/agent-settings/channels/{channel_id}/test")
async def test_channel(channel_id: str, request: Request, payload: SettingsInput = Depends(settings_input)):
    _guard_origin(request)
    try:
        with _LOCK:
            if channel_id == ENVIRONMENT_ID and not settings_path().is_file():
                current = _environment_config()
            else:
                library = _mutable_library(payload.expected_revision)
                current = _record_config(_channel_record(library, channel_id))
            config = candidate_config(_keep_omitted_fields(payload, current), current)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(409, "本机渠道配置无法读取，未执行连接测试；请先恢复设置文件。") from None
    return await _test_config(config)


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
    return await _test_config(candidate_config(payload))
