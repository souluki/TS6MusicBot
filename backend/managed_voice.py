from __future__ import annotations

from pathlib import Path
import os
import uuid

from .managed_assets import ASSET_DIR


VOICE_ASSET_DIR = ASSET_DIR / "voices"
MAX_VOICE_BYTES = 50 * 1024 * 1024
ALLOWED_VOICE_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".webm"}
ALLOWED_VOICE_CONTENT_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "application/ogg",
    "application/octet-stream",
}


def validate_voice_audio(data: bytes, *, filename: str, content_type: str = "") -> str:
    if not data:
        raise ValueError("请选择音频文件")
    if len(data) > MAX_VOICE_BYTES:
        raise ValueError("音频文件不能超过 50 MiB")

    suffix = Path(filename or "").suffix.lower()
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if suffix not in ALLOWED_VOICE_EXTENSIONS and media_type not in ALLOWED_VOICE_CONTENT_TYPES:
        raise ValueError("仅支持 MP3、WAV、OGG、FLAC、M4A、AAC 或 WebM 音频")
    return suffix if suffix in ALLOWED_VOICE_EXTENSIONS else ".audio"


def save_voice_audio(data: bytes, *, filename: str, content_type: str = "") -> tuple[str, str]:
    suffix = validate_voice_audio(data, filename=filename, content_type=content_type)
    VOICE_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = VOICE_ASSET_DIR / stored_name
    temporary = VOICE_ASSET_DIR / f".{stored_name}.tmp"
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, 0o640)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination), stored_name


def delete_voice_audio(path: str) -> bool:
    candidate = Path(path).resolve()
    root = VOICE_ASSET_DIR.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    existed = candidate.is_file()
    candidate.unlink(missing_ok=True)
    return existed
