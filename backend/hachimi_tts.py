from __future__ import annotations

import logging
from pathlib import Path
import re
import time

logger = logging.getLogger("tsbot.hachimi.tts")

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
_URL_RE = re.compile(r"https?://\S+")
_BRACKET_TAG_RE = re.compile(r"\[(?:表情|配图|图片|At|引用)[^\]]*\]")
_MD_RE = re.compile(r"[`*_~#>]+")
_MULTI_SPACE_RE = re.compile(r"\s+")

_tts_seq = 0


def compute_prosody(
    *,
    mood: float = 0.0,
    irritation: float = 0.0,
    circadian: str = "",
) -> tuple[str, str]:
    """Map Hachimi's live affect state to Edge-TTS (rate, pitch) for XiaoyiNeural."""
    if irritation >= 0.42:
        return "+9%", "+1Hz"
    if irritation >= 0.25:
        return "+7%", "+2Hz"
    if "休息睡眠" in circadian or "凌晨" in circadian:
        return "-3%", "+2Hz"
    if "清早" in circadian or "早上刚醒" in circadian:
        return "+1%", "+3Hz"
    if mood >= 0.45:
        return "+8%", "+7Hz"
    if mood >= 0.18:
        return "+6%", "+5Hz"
    if mood <= -0.40:
        return "-2%", "+1Hz"
    if mood <= -0.18:
        return "+2%", "+2Hz"
    return "+5%", "+4Hz"


def clean_spoken_text(raw: str, *, max_chars: int = 140) -> str:
    """Sanitize LLM output for natural spoken Chinese TTS."""
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _BRACKET_TAG_RE.sub("", text)
    text = _URL_RE.sub("", text)
    text = _MD_RE.sub("", text)
    # Remove parenthetical stage directions like （轻哼） or (笑)
    text = re.sub(r"[（(][^）)]{1,14}[）)]", "", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        for sep in ("。", "！", "？", "，", "!", "?", ","):
            idx = cut.rfind(sep)
            if idx >= 20:
                cut = cut[: idx + 1]
                break
        text = cut.strip()
    return text


def _prune_tts_dir(out_dir: Path, keep_max: int = 30) -> None:
    try:
        files = sorted(
            (p for p in out_dir.glob("tts_*.mp3") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for old in files[: max(0, len(files) - keep_max)]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


async def synthesize_hachimi_tts(
    text: str,
    *,
    out_dir: Path | None = None,
    voice_name: str = DEFAULT_VOICE,
    mood: float = 0.0,
    irritation: float = 0.0,
    circadian: str = "",
) -> Path | None:
    """Synthesize Edge-TTS audio file for Hachimi with dynamic emotion prosody."""
    global _tts_seq
    spoken = clean_spoken_text(text)
    if not spoken:
        return None

    import edge_tts

    repo_root = Path(__file__).resolve().parent.parent
    target_dir = out_dir or (repo_root / "logs" / "hachimi_tts")
    target_dir.mkdir(parents=True, exist_ok=True)

    _tts_seq = (_tts_seq + 1) % 100000
    out_path = target_dir / f"tts_{int(time.time() * 1000)}_{_tts_seq}.mp3"
    rate, pitch = compute_prosody(mood=mood, irritation=irritation, circadian=circadian)

    communicate = edge_tts.Communicate(
        spoken,
        voice=voice_name or DEFAULT_VOICE,
        rate=rate,
        pitch=pitch,
    )
    await communicate.save(str(out_path))
    _prune_tts_dir(target_dir)
    logger.info(
        "hachimi tts synthesized: chars=%d rate=%s pitch=%s file=%s",
        len(spoken),
        rate,
        pitch,
        out_path.name,
    )
    return out_path


async def speak_hachimi_in_ts(
    voice_client: object,
    audio_path: Path,
    *,
    title: str = "哈基咪语音",
    requested_by: str = "哈基咪",
    voice_volume_percent: int = 150,
    duck_db: float = 12.0,
) -> bool:
    """Play synthesized Hachimi speech in TS6, ducking active music when playing."""
    if not audio_path or not audio_path.is_file():
        return False

    gain = max(0.0, min(3.0, float(voice_volume_percent) / 100.0))
    state = ""
    try:
        st = await voice_client.get_status()  # type: ignore[attr-defined]
        state = str(getattr(st, "state", "") or "").strip().upper()
    except Exception:
        state = ""

    if state in {"STATE_PLAYING", "STATE_BUFFERING"}:
        ok = await voice_client.play_overlay(  # type: ignore[attr-defined]
            source_url=str(audio_path),
            title=title,
            requested_by=requested_by,
            duck_db=duck_db,
            voice_gain=gain,
        )
        return bool(ok)

    if state == "STATE_PAUSED":
        # Preserve paused music track position instead of overwriting playback slot
        logger.info("skipping standalone tts playback while music is STATE_PAUSED to preserve seek position")
        return False

    await voice_client.play(  # type: ignore[attr-defined]
        source_url=str(audio_path),
        title=title,
        requested_by=requested_by,
        notice="",
        voice_gain=gain,
        independent_volume=True,
    )
    return True
