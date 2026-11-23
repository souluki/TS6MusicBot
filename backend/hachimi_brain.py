from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Literal
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("tsbot.hachimi.brain")

POOL_ID = "f951970c-823a-4f1b-9693-a39b820b057f"
DEFAULT_BOT_ID = "2984239231"
OWNER_QQ = "10159641"
TS_GROUP_ID = "ts6:main"
SHARED_AFFECT_GROUPS = ("ts6:main", "728270623", "937380704", "1076998115")

HIGH_AGGRESSION_RE = re.compile(
    r"(脑子.{0,4}(进水|有坑|有病|瓦特|坏|不好使|被驴踢|抽了|长泡)|没脑子|猪脑|蠢货|傻[逼屄Bｂ]|智障|弱智|脑残|废物|滚蛋|给爷爬|去死)",
    re.IGNORECASE,
)

PROVOCATION_BAIT_RE = re.compile(
    r"(叫声?(主人|爸爸|爹|老公|老婆)|喵一个|汪一个|学狗叫|看看腿|发张?自拍|你是谁家的狗|给你脸了|信不信把你删了|傻子ai|人工智障)",
    re.IGNORECASE,
)

WAKE_WORD_RE = re.compile(
    r"(哈基咪|哈吉米|哈基米|小咪|(?:^|[，,。！!？?\s])咪(?:[，,。！!？?\s]|$|你|在|来|帮|放|切|停|换|看|说|怎|呢|呀|啊))",
    re.IGNORECASE,
)

AT_MENTION_RE = re.compile(
    r"(?:@|＠)\s*(?:哈基咪|哈吉米|哈基米|小咪|咪|tsbot|ts6bot|yumi)",
    re.IGNORECASE,
)

NATURAL_MUSIC_INTENT_RE = re.compile(
    r"(?:帮我|给我|来一?首|放一?首|点一?首|播一?首|想听|换一?首|切一?首|下一首|切歌|声音(?:调|大|小)|音量(?:调|大|小)|暂停一下|继续放|恢复播放|歌单|歌品|收藏这首|加进?我?的?歌单|存进?我?的?歌单)",
    re.IGNORECASE,
)


def _sanitize_schema(node: Any, *, in_properties: bool = False) -> Any:
    if isinstance(node, dict):
        if in_properties:
            return {k: _sanitize_schema(v, in_properties=False) for k, v in node.items()}
        return {
            k: _sanitize_schema(v, in_properties=(k in ("properties", "$defs")))
            for k, v in node.items()
            if k not in ("additionalProperties", "title")
        }
    if isinstance(node, list):
        return [_sanitize_schema(item, in_properties=False) for item in node]
    return node


def _decay(value: float, seconds: float, half_life: float) -> float:
    return float(value or 0.0) * math.exp(-math.log(2.0) * max(0.0, float(seconds)) / float(half_life))


def is_rest_hours(shanghai_hour: int) -> bool:
    return 4 <= shanghai_hour < 8


def _circadian_energy(shanghai_hour: int) -> str:
    if is_rest_hours(shanghai_hour):
        return "凌晨4点到早8点休息睡眠时间（被@吵醒有点犯困迷糊）"
    if 8 <= shanghai_hour < 10:
        return "早上刚醒不久精神渐恢复"
    if 10 <= shanghai_hour < 14:
        return "白天精神正常"
    if 14 <= shanghai_hour < 18:
        return "下午摸鱼松弛状态"
    if 18 <= shanghai_hour < 23:
        return "晚上语音频道活跃精神不错"
    if shanghai_hour >= 23 or shanghai_hour < 2:
        return "深夜夜猫子精神头足爱接茬"
    return "凌晨修仙略困但还能接茬"


MusicCmdType = Literal[
    "none",
    "play",
    "add",
    "skip",
    "pause",
    "resume",
    "stop",
    "vol",
    "clear",
    "random",
    "order",
    "repeat",
    "now",
    "queue",
    "like_current",
    "save_song",
    "remove_song",
    "play_user_playlist",
    "bind_netease_playlist",
]


class UserTasteSummarySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taste_summary: str = Field(default="", max_length=90)


class TsHachimiPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(default="", max_length=240)
    should_reply: bool = Field(default=False)
    spoken_reply: str = Field(default="", max_length=120)
    chat_reply: str = Field(default="", max_length=240)
    music_cmd: MusicCmdType = Field(default="none")
    music_arg: str = Field(default="", max_length=120)


class TsWelcomePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spoken_reply: str = Field(default="", max_length=90)
    chat_reply: str = Field(default="", max_length=140)


@dataclass
class AffectSnapshot:
    mood: float = 0.0
    irritation: float = 0.0
    mood_reason: str = ""
    circadian: str = "白天精神正常"


def sanitize_outbound_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if HIGH_AGGRESSION_RE.search(cleaned):
        return "算了，懒得跟你计较。"
    return cleaned


class HachimiTsBrain:
    """Shared persona, memory, and real-time affect engine bridging TS6MusicBot with QQ Hachimi."""

    def __init__(self, hachimi_home: str | Path | None = None) -> None:
        env_home = os.getenv("TSBOT_HACHIMI_HOME", "").strip()
        default_home = (
            Path(env_home)
            if env_home
            else (Path("D:/ai/gemini") if os.name == "nt" else (Path.home() / "ai/gemini"))
        )
        self.hachimi_home = Path(hachimi_home) if hachimi_home else default_home
        self.bot_id = DEFAULT_BOT_ID
        self.embed_url = os.getenv("TSBOT_HACHIMI_EMBED_URL", "http://127.0.0.1:8766").rstrip("/")

        self._pg_pool: Any = None
        self._pg_lock = asyncio.Lock()
        self._pg_unavailable_until = 0.0

        self._genai_client: Any = None
        self._genai_types: Any = None
        self._model_name = "gemini-4-argon"

        self._persona_cache = ""
        self._persona_mtime = 0.0

        self._recent_ts_turns: deque[dict[str, Any]] = deque(maxlen=18)
        self._speaker_followup_until: dict[str, float] = {}
        self._burst_tracker: dict[str, deque[float]] = {}
        self._last_ambient_chat_ts = -1000.0
        self._last_ambient_voice_ts = -1000.0
        self._utterance_lock = asyncio.Lock()

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def _secrets_file(self) -> Path:
        return self.hachimi_home / "runtime" / "local-secrets.json"

    def _cmd_config_file(self) -> Path:
        return self.hachimi_home / "runtime" / "astrbot" / "data" / "cmd_config.json"

    def _persona_file(self) -> Path:
        return self.hachimi_home / "plugins" / "astrbot_plugin_hachimi" / "persona.md"

    def load_persona(self) -> str:
        p = self._persona_file()
        try:
            if p.is_file():
                st = p.stat()
                if st.st_mtime != self._persona_mtime or not self._persona_cache:
                    self._persona_cache = p.read_text(encoding="utf-8").strip()
                    self._persona_mtime = st.st_mtime
                return self._persona_cache
        except Exception as exc:
            logger.warning("failed to read persona.md: %s", exc)
        return (
            "你是哈基咪（小咪），常驻在大家 TeamSpeak 语音频道与 QQ 群里的年轻女生朋友兼点歌陪玩搭子。"
            "说话自然利落、口语化、有分寸，绝不骂人脑子傻，对初识群友保持客气礼貌。"
        )

    def _resolve_gemini_credentials(self) -> tuple[str, str]:
        api_key = (os.getenv("TSBOT_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
        model_name = os.getenv("TSBOT_GEMINI_MODEL", "").strip() or "gemini-4-argon"

        sec_path = self._secrets_file()
        if sec_path.is_file():
            try:
                sec = json.loads(sec_path.read_text(encoding="utf-8-sig"))
                if not api_key and sec.get("gemini_api_key"):
                    api_key = str(sec["gemini_api_key"]).strip()
                if sec.get("gemini_model") and not os.getenv("TSBOT_GEMINI_MODEL"):
                    model_name = str(sec["gemini_model"]).strip()
            except Exception:
                pass

        cfg_path = self._cmd_config_file()
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
                providers = cfg.get("provider", [])
                sources = {s.get("id"): s for s in cfg.get("provider_sources", []) if isinstance(s, dict)}
                matched = next(
                    (p for p in providers if p.get("enable") and "gemini-4-argon" in str(p.get("model", ""))),
                    None,
                )
                if matched:
                    raw_m = str(matched.get("model", "")).strip()
                    if raw_m:
                        model_name = raw_m.removeprefix("models/")
                    src = sources.get(matched.get("provider_source_id"))
                    if not api_key and src and src.get("key"):
                        api_key = str(src["key"][0]).strip()
                if not api_key:
                    for src in cfg.get("provider_sources", []):
                        if src.get("type") == "googlegenai_chat_completion" and src.get("key"):
                            api_key = str(src["key"][0]).strip()
                            break
            except Exception:
                pass

        return api_key, model_name.removeprefix("models/")

    def _ensure_genai(self) -> bool:
        if self._genai_client is not None:
            return True
        api_key, model_name = self._resolve_gemini_credentials()
        if not api_key:
            logger.warning("hachimi gemini api key unavailable")
            return False
        from google import genai
        from google.genai import types

        self._genai_client = genai.Client(api_key=api_key)
        self._genai_types = types
        self._model_name = model_name
        return True

    async def _get_pg_pool(self) -> Any:
        if self._pg_pool is not None:
            return self._pg_pool
        if time.monotonic() < self._pg_unavailable_until:
            return None
        async with self._pg_lock:
            if self._pg_pool is not None:
                return self._pg_pool
            sec_path = self._secrets_file()
            if not sec_path.is_file():
                self._pg_unavailable_until = time.monotonic() + 30.0
                return None
            try:
                sec = json.loads(sec_path.read_text(encoding="utf-8-sig"))
                password = str(sec.get("pg_app_password") or "")
                if not password:
                    self._pg_unavailable_until = time.monotonic() + 30.0
                    return None
                import asyncpg

                pool = await asyncpg.create_pool(
                    host=os.getenv("TSBOT_HACHIMI_PG_HOST", "127.0.0.1"),
                    port=int(os.getenv("TSBOT_HACHIMI_PG_PORT", "5432")),
                    user=os.getenv("TSBOT_HACHIMI_PG_USER", "hachimi_app"),
                    password=password,
                    database=os.getenv("TSBOT_HACHIMI_PG_DB", "hachimi"),
                    min_size=1,
                    max_size=4,
                    command_timeout=8.0,
                )
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO chat_groups(group_id, pool_id) VALUES($1, $2::uuid) ON CONFLICT DO NOTHING",
                        TS_GROUP_ID,
                        POOL_ID,
                    )
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_music_history (
                            id SERIAL PRIMARY KEY,
                            pool_id uuid NOT NULL REFERENCES memory_pools(id) ON DELETE CASCADE,
                            user_key text NOT NULL,
                            ts_unique_id text NOT NULL DEFAULT '',
                            display_name text NOT NULL DEFAULT '',
                            track_id text NOT NULL,
                            title text NOT NULL,
                            artist text NOT NULL DEFAULT '',
                            source text NOT NULL DEFAULT 'ts6',
                            play_count integer NOT NULL DEFAULT 1,
                            last_played_at timestamptz NOT NULL DEFAULT now(),
                            UNIQUE(pool_id, user_key, track_id)
                        )
                        """
                    )
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_music_playlists (
                            id SERIAL PRIMARY KEY,
                            pool_id uuid NOT NULL REFERENCES memory_pools(id) ON DELETE CASCADE,
                            user_key text NOT NULL,
                            display_name text NOT NULL DEFAULT '',
                            playlist_name text NOT NULL DEFAULT '默认歌单',
                            track_id text NOT NULL,
                            title text NOT NULL,
                            artist text NOT NULL DEFAULT '',
                            created_at timestamptz NOT NULL DEFAULT now(),
                            UNIQUE(pool_id, user_key, playlist_name, track_id)
                        )
                        """
                    )
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_music_taste (
                            pool_id uuid NOT NULL REFERENCES memory_pools(id) ON DELETE CASCADE,
                            user_key text NOT NULL,
                            ts_unique_id text NOT NULL DEFAULT '',
                            display_name text NOT NULL DEFAULT '',
                            top_artists jsonb NOT NULL DEFAULT '[]'::jsonb,
                            favorite_tracks jsonb NOT NULL DEFAULT '[]'::jsonb,
                            taste_summary text NOT NULL DEFAULT '',
                            total_plays integer NOT NULL DEFAULT 0,
                            updated_at timestamptz NOT NULL DEFAULT now(),
                            PRIMARY KEY (pool_id, user_key)
                        )
                        """
                    )
                self._pg_pool = pool
                logger.info("connected to shared Hachimi PostgreSQL pool (group=%s)", TS_GROUP_ID)
                return pool
            except Exception as exc:
                logger.warning("shared Hachimi PostgreSQL unavailable (%s); running degraded", exc)
                self._pg_unavailable_until = time.monotonic() + 20.0
                return None

    async def close(self) -> None:
        if self._pg_pool is not None:
            try:
                await self._pg_pool.close()
            except Exception:
                pass
            self._pg_pool = None

    def mark_followup_window(self, speaker_key: str, seconds: float = 26.0) -> None:
        if speaker_key:
            self._speaker_followup_until[speaker_key] = time.monotonic() + seconds

    def in_followup_window(self, speaker_key: str) -> bool:
        if not speaker_key:
            return False
        return time.monotonic() < self._speaker_followup_until.get(speaker_key, 0.0)

    @staticmethod
    def _parse_csv_set(raw: str) -> set[str]:
        return {item.strip().lower() for item in str(raw or "").split(",") if item.strip()}

    def _is_owner_ts_identity(
        self,
        speaker_name: str,
        ts_unique_id: str,
        *,
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> bool:
        uid_norm = (ts_unique_id or "").strip().lower()
        name_norm = (speaker_name or "").strip().lower()
        configured_uids = self._parse_csv_set(owner_ts_uids or os.getenv("TSBOT_HACHIMI_OWNER_UIDS", ""))
        if uid_norm and uid_norm in configured_uids:
            return True
        default_names = {"线性流逝", "老咪", "蕾咪", "remi", "10159641"}
        configured_names = default_names | self._parse_csv_set(
            owner_ts_names or os.getenv("TSBOT_HACHIMI_OWNER_NAMES", "")
        )
        return bool(name_norm and name_norm in configured_names)

    async def resolve_speaker_and_state(
        self,
        *,
        speaker_name: str,
        ts_unique_id: str,
        query_text: str = "",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> tuple[dict[str, Any], AffectSnapshot]:
        now = self._now_utc()
        sh_hour = now.astimezone(timezone(timedelta(hours=8))).hour
        circadian = _circadian_energy(sh_hour)

        is_owner = self._is_owner_ts_identity(
            speaker_name,
            ts_unique_id,
            owner_ts_names=owner_ts_names,
            owner_ts_uids=owner_ts_uids,
        )
        sender_id = OWNER_QQ if is_owner else f"ts6:{(ts_unique_id or speaker_name or 'guest').strip()[:40]}"

        pool = await self._get_pg_pool()
        profile: dict[str, Any] | None = None
        facts: list[dict[str, Any]] = []
        directives: list[str] = []
        episodes: list[dict[str, Any]] = []
        familiarity = 0.0
        warmth = 0.0
        direct_live_turns = 0
        mood = 0.0
        irritation = 0.0
        mood_reason = ""

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    # 1. Match TS nickname against QQ user_profiles if not already matched to owner
                    name_clean = (speaker_name or "").strip()
                    if not is_owner and name_clean:
                        prof_row = await conn.fetchrow(
                            """
                            SELECT sender_id, nicknames, persona_summary, speaking_style,
                                   interests, group_memes, relation_style, is_target_member
                            FROM user_profiles
                            WHERE pool_id = $1::uuid
                              AND EXISTS (
                                  SELECT 1 FROM jsonb_array_elements_text(nicknames) AS n
                                  WHERE lower(n) = lower($2)
                              )
                            LIMIT 1
                            """,
                            POOL_ID,
                            name_clean,
                        )
                        if prof_row is None:
                            person_match = await conn.fetchrow(
                                """
                                SELECT sender_id FROM companion_people
                                WHERE bot_id = $1 AND lower(nickname) = lower($2)
                                ORDER BY familiarity DESC LIMIT 1
                                """,
                                self.bot_id,
                                name_clean,
                            )
                            if person_match:
                                sender_id = str(person_match["sender_id"])
                        else:
                            sender_id = str(prof_row["sender_id"])
                            profile = dict(prof_row)
                            if isinstance(profile.get("nicknames"), str):
                                try:
                                    profile["nicknames"] = json.loads(profile["nicknames"])
                                except Exception:
                                    profile["nicknames"] = [name_clean]

                        if sender_id == OWNER_QQ:
                            is_owner = True

                    if profile is None:
                        prof_row2 = await conn.fetchrow(
                            """
                            SELECT sender_id, nicknames, persona_summary, speaking_style,
                                   interests, group_memes, relation_style, is_target_member
                            FROM user_profiles
                            WHERE pool_id = $1::uuid AND sender_id = $2
                            LIMIT 1
                            """,
                            POOL_ID,
                            sender_id,
                        )
                        if prof_row2:
                            profile = dict(prof_row2)
                            if isinstance(profile.get("nicknames"), str):
                                try:
                                    profile["nicknames"] = json.loads(profile["nicknames"])
                                except Exception:
                                    profile["nicknames"] = []

                    # 2. Person familiarity & direct_live_turns check (strict unfamiliar boundary!)
                    person_row = await conn.fetchrow(
                        "SELECT familiarity, warmth, updated_at FROM companion_people WHERE bot_id = $1 AND sender_id = $2",
                        self.bot_id,
                        sender_id,
                    )
                    if person_row and person_row["updated_at"]:
                        age_s = (now - person_row["updated_at"]).total_seconds()
                        familiarity = _decay(float(person_row["familiarity"] or 0.0), age_s, 60 * 86400)
                        warmth = _decay(float(person_row["warmth"] or 0.0), age_s, 7 * 86400)

                    direct_live_turns = int(
                        await conn.fetchval(
                            """
                            SELECT count(*) FROM messages u
                            WHERE u.bot_id = $1 AND u.sender_id = $2 AND u.origin = 'live'
                              AND u.deleted_at IS NULL
                              AND (
                                  u.group_id = 'ts6:main'
                                  OR EXISTS (
                                      SELECT 1 FROM messages r
                                      WHERE r.bot_id = $1 AND r.sender_id = $1
                                        AND r.reply_to = u.platform_id
                                  )
                              )
                            """,
                            self.bot_id,
                            sender_id,
                        )
                        or 0
                    )

                    # 3. Live bidirectional affect across TS6 + QQ active groups
                    grp_rows = await conn.fetch(
                        """
                        SELECT group_id, mood, irritation, irritation_updated_at,
                               mood_reason, mood_reason_at, updated_at
                        FROM companion_groups
                        WHERE bot_id = $1 AND group_id = ANY($2::text[])
                        ORDER BY updated_at DESC
                        """,
                        self.bot_id,
                        list(SHARED_AFFECT_GROUPS),
                    )
                    if grp_rows:
                        best_mood = 0.0
                        best_irr = 0.0
                        best_reason = ""
                        for gr in grp_rows:
                            u_at = gr["updated_at"] or now
                            m_dec = _decay(float(gr["mood"] or 0.0), (now - u_at).total_seconds(), 4 * 3600)
                            if abs(m_dec) > abs(best_mood):
                                best_mood = m_dec
                            i_at = gr["irritation_updated_at"] or u_at
                            i_dec = _decay(float(gr["irritation"] or 0.0), (now - i_at).total_seconds(), 1800)
                            if i_dec > best_irr:
                                best_irr = i_dec
                            r_at = gr["mood_reason_at"]
                            if not best_reason and gr["mood_reason"] and r_at and (now - r_at).total_seconds() < 9000:
                                best_reason = str(gr["mood_reason"])
                        mood = best_mood
                        irritation = best_irr
                        mood_reason = best_reason

                    # 4. Speaker facts & owner directives
                    fact_rows = await conn.fetch(
                        """
                        SELECT f.slot, f.value
                        FROM memory_facts f
                        JOIN messages m ON m.id = f.source_id
                        WHERE f.sender_id = $1 AND f.active = true
                          AND m.deleted_at IS NULL AND m.pool_id = $2::uuid
                          AND (f.expires_at IS NULL OR f.expires_at > now())
                        ORDER BY f.created_at DESC LIMIT 6
                        """,
                        sender_id,
                        POOL_ID,
                    )
                    facts = [{"slot": str(r["slot"]), "value": str(r["value"])} for r in fact_rows]

                    dir_rows = await conn.fetch(
                        """
                        SELECT content FROM owner_directives
                        WHERE pool_id = $1::uuid AND active = true
                        ORDER BY id ASC LIMIT 16
                        """,
                        POOL_ID,
                    )
                    directives = [str(r["content"]) for r in dir_rows if r["content"]]

                    # 5. Lightweight semantic episode recall when query_text is meaningful
                    if query_text and len(query_text.strip()) >= 2:
                        episodes = await self._recall_episodes(conn, query_text.strip())

                    # 6. Load speaker's personal music taste & playlist summary
                    music_profile = await self._fetch_music_profile_from_conn(
                        conn,
                        user_key=sender_id,
                        display_name=speaker_name or "",
                    )
            except Exception as exc:
                logger.warning("resolve_speaker_and_state query error: %s", exc)
                music_profile = None
        else:
            music_profile = None

        is_roster_member = bool(profile and profile.get("is_target_member"))
        eff_score = float(familiarity) + float(warmth) * 1.5
        if is_owner:
            relationship = "作者"
        elif direct_live_turns == 0 and not is_roster_member:
            relationship = "初识"
        elif warmth <= -0.8:
            relationship = "初识"
        elif eff_score >= 22.0:
            relationship = "老熟人"
        elif eff_score >= 10.0:
            relationship = "熟悉"
        elif eff_score >= 4.0:
            relationship = "渐熟"
        else:
            relationship = "初识"

        relation_cue = self._build_relation_cue(
            is_owner=is_owner,
            relationship=relationship,
            warmth=warmth,
            profile=profile,
            irritation=irritation,
        )

        affect = AffectSnapshot(
            mood=round(mood, 2),
            irritation=round(irritation, 2),
            mood_reason=mood_reason,
            circadian=circadian,
        )
        speaker_ctx: dict[str, Any] = {
            "sender_id": sender_id,
            "speaker_name": speaker_name or "未知队友",
            "is_author": is_owner,
            "relationship": relationship,
            "relation_cue": relation_cue,
            "mood": affect.mood,
            "irritation": affect.irritation,
            "mood_reason": affect.mood_reason,
            "energy_state": affect.circadian,
            "facts": facts,
            "owner_directives": directives,
            "recalled_episodes": episodes,
        }
        if profile:
            allowed_keys = (
                ("nicknames", "persona_summary", "speaking_style", "interests", "group_memes")
                if relationship in {"初识", "渐熟"}
                else ("nicknames", "persona_summary", "speaking_style", "interests", "group_memes", "relation_style")
            )
            sp = {k: profile[k] for k in allowed_keys if profile.get(k)}
            if relationship == "初识":
                sp["interaction_boundary"] = "此前未与你直接熟络互动，属于初识关系，必须保持客气礼貌分寸！"
            speaker_ctx["speaker_profile"] = sp
        if music_profile and (music_profile.get("total_plays") or music_profile.get("saved_playlist")):
            speaker_ctx["music_profile"] = music_profile

        return speaker_ctx, affect

    async def _recall_episodes(self, conn: Any, query_text: str) -> list[dict[str, Any]]:
        try:
            has_ep = await conn.fetchval("SELECT to_regclass('public.memory_episodes') IS NOT NULL")
            if not has_ep:
                return []
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                resp = await client.post(
                    f"{self.embed_url}/embed",
                    json={"texts": [query_text[:200]], "kind": "query"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
                vecs = data.get("vectors") or data.get("embeddings") or []
                if not vecs:
                    return []
                vec_str = "[" + ",".join(f"{float(x):.6f}" for x in vecs[0]) + "]"
            rows = await conn.fetch(
                """
                SELECT title, summary
                FROM memory_episodes
                WHERE pool_id = $1::uuid AND deleted_at IS NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> $2::vector
                LIMIT 3
                """,
                POOL_ID,
                vec_str,
            )
            return [{"title": str(r["title"]), "summary": str(r["summary"])[:220]} for r in rows]
        except Exception:
            return []

    @staticmethod
    def _build_relation_cue(
        *,
        is_owner: bool,
        relationship: str,
        warmth: float,
        profile: dict[str, Any] | None,
        irritation: float,
    ) -> str:
        if is_owner:
            if irritation >= 0.38:
                return "对方是作者老咪，刚挑刺数落过你让你有点小逆反：语气干脆不服气地跟他拌两句嘴，偶尔可以喊一声「老登」怼回去（自然简短，别浮夸演戏）"
            return "对方是作者老咪（熟稔自己人），平时叫他老咪或直接自然接话，语气灵动松弛，可适度随口调侃拌嘴"
        if relationship == "老熟人":
            rel = (profile or {}).get("relation_style") or ""
            if rel:
                return f"车队老熟人死党（{rel}；默契松弛、随时接梗打趣，但严禁骂人脑子傻）"
            return "很铁的车队老熟人死党，彼此知根知底，接话松弛自然、不用客套"
        if relationship == "熟悉":
            rel = (profile or {}).get("relation_style") or ""
            if rel:
                return f"常一起开黑聊天的熟人队友（{rel}；玩笑点到为止，严禁骂人脑子傻）"
            return "常一起玩的熟人朋友，语气自然温和、爽快接话" + ("，平时对你很友善" if warmth > 0.3 else "")
        if relationship == "渐熟":
            return "略熟悉（渐熟）的群友/队友：态度温和自然、轻松友好，保留恰当分寸不生硬互损"
        return "初识/未直接熟络过的频道成员：正常交流保持客气礼貌；若对方故意挑衅盘问或恶搞，只简短回一句「无语。」即可，绝不多话解释！"

    async def appraise_and_record_turn(
        self,
        *,
        sender_id: str,
        speaker_name: str,
        is_owner: bool,
        user_text: str,
        bot_reply: str,
    ) -> None:
        now_mono = time.monotonic()
        dq = self._burst_tracker.setdefault(sender_id, deque(maxlen=6))
        dq.append(now_mono)
        recent_hits = sum(1 for t in dq if now_mono - t <= 20.0)

        txt = (user_text or "").strip()
        irr_delta = 0.0
        mood_delta = 0.0
        reason = ""
        label = (speaker_name or sender_id)[:16]

        if recent_hits >= 4:
            irr_delta += 0.40
            mood_delta -= 0.08
            reason = f"刚在TS语音频道被 {label} 连着催促有点烦"
        if any(bad in txt for bad in ("人工智障", "傻逼", "笨蛋", "废物", "真蠢", "逆子", "真笨", "太呆", "没用", "垃圾")):
            irr_delta += 0.48
            mood_delta -= 0.14
            reason = "刚在TS被老咪嫌弃数落，有点小逆反" if is_owner else f"刚在TS被 {label} 怼了不太爽"
        elif is_owner and any(nag in txt for nag in ("重做", "把你删", "改掉你", "不听话", "笨死", "呆子")):
            irr_delta += 0.40
            reason = "刚在TS被老咪挑刺念叨，有点小逆反"
        elif any(good in txt for good in ("谢谢", "谢啦", "太懂了", "好听", "真棒", "厉害", "真贴心", "夸夸")):
            irr_delta = -0.35
            mood_delta += 0.22
            reason = f"刚在TS被 {label} 夸奖感谢心情不错"

        self._recent_ts_turns.append(
            {
                "speaker": speaker_name,
                "sender_id": sender_id,
                "user": txt[:160],
                "bot": (bot_reply or "")[:160],
                "ts": int(time.time()),
            }
        )

        pool = await self._get_pg_pool()
        if pool is None:
            return

        try:
            async with pool.acquire() as conn:
                if irr_delta != 0.0 or mood_delta != 0.0:
                    for gid in ("ts6:main", "728270623"):
                        await conn.execute(
                            "INSERT INTO chat_groups(group_id, pool_id) VALUES($1, $2::uuid) ON CONFLICT DO NOTHING",
                            gid,
                            POOL_ID,
                        )
                        await conn.execute(
                            """
                            INSERT INTO companion_groups(
                                bot_id, group_id, mood, irritation, irritation_updated_at,
                                mood_reason, mood_reason_at, updated_at
                            )
                            VALUES($1, $2, $3, GREATEST(0, $4), now(), $5, CASE WHEN $5 <> '' THEN now() END, now())
                            ON CONFLICT(bot_id, group_id) DO UPDATE SET
                                mood = GREATEST(-2, LEAST(2, companion_groups.mood * exp(-0.693147 * extract(epoch from (now() - companion_groups.updated_at)) / 14400) + $3)),
                                irritation = GREATEST(0, LEAST(2, companion_groups.irritation * exp(-0.693147 * extract(epoch from (now() - companion_groups.irritation_updated_at)) / 1800) + $4)),
                                irritation_updated_at = now(),
                                mood_reason = CASE WHEN $5 <> '' THEN $5 ELSE companion_groups.mood_reason END,
                                mood_reason_at = CASE WHEN $5 <> '' THEN now() ELSE companion_groups.mood_reason_at END,
                                updated_at = now()
                            """,
                            self.bot_id,
                            gid,
                            float(mood_delta),
                            float(irr_delta),
                            reason,
                        )

                if bot_reply:
                    is_rude_turn = (not is_owner) and (irr_delta >= 0.35)
                    warmth_delta = 0.22 if mood_delta > 0 else (-0.28 if is_rude_turn else 0.02)
                    fam_step = 0.0 if is_rude_turn else 0.30
                    await conn.execute(
                        """
                        INSERT INTO companion_people(bot_id, sender_id, nickname, familiarity, warmth, updated_at)
                        VALUES($1, $2, $3, $5, $4, now())
                        ON CONFLICT(bot_id, sender_id) DO UPDATE SET
                            nickname = CASE WHEN $3 <> '' THEN $3 ELSE companion_people.nickname END,
                            familiarity = LEAST(30, companion_people.familiarity + CASE WHEN companion_people.updated_at < now() - interval '60 seconds' THEN $5 ELSE 0.0 END),
                            warmth = GREATEST(-3, LEAST(3, companion_people.warmth + $4)),
                            updated_at = now()
                        """,
                        self.bot_id,
                        sender_id,
                        (speaker_name or "")[:40],
                        float(warmth_delta),
                        float(fam_step),
                    )
                    user_pid = f"ts6u_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
                    bot_pid = f"ts6b_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
                    await conn.execute(
                        """
                        INSERT INTO messages(
                            id, pool_id, bot_id, group_id, sender_id, platform_id,
                            sender_name, sent_at, body, segments, reply_to, origin
                        )
                        VALUES($1::uuid, $2::uuid, $3, $4, $5, $6, $7, now(), $8, '[]'::jsonb, NULL, 'live')
                        ON CONFLICT(bot_id, group_id, platform_id) DO NOTHING
                        """,
                        str(uuid.uuid4()),
                        POOL_ID,
                        self.bot_id,
                        TS_GROUP_ID,
                        sender_id,
                        user_pid,
                        (speaker_name or "")[:40],
                        f"[TS6] {txt}"[:400],
                    )
                    await conn.execute(
                        """
                        INSERT INTO messages(
                            id, pool_id, bot_id, group_id, sender_id, platform_id,
                            sender_name, sent_at, body, segments, reply_to, origin
                        )
                        VALUES($1::uuid, $2::uuid, $3, $4, $3, $5, '哈基咪', now(), $6, '[]'::jsonb, $7, 'live')
                        ON CONFLICT(bot_id, group_id, platform_id) DO NOTHING
                        """,
                        str(uuid.uuid4()),
                        POOL_ID,
                        self.bot_id,
                        TS_GROUP_ID,
                        bot_pid,
                        bot_reply[:400],
                        user_pid,
                    )
        except Exception as exc:
            logger.warning("appraise_and_record_turn db error: %s", exc)

    async def resolve_canonical_user_key(
        self,
        conn: Any,
        *,
        speaker_name: str = "",
        ts_unique_id: str = "",
        qq_sender_id: str = "",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> tuple[str, str]:
        qq_id = str(qq_sender_id or "").strip()
        name_clean = str(speaker_name or "").strip()
        uid_clean = str(ts_unique_id or "").strip()

        if qq_id:
            if conn is not None and not name_clean:
                p_name = await conn.fetchval(
                    "SELECT nickname FROM companion_people WHERE bot_id = $1 AND sender_id = $2",
                    self.bot_id,
                    qq_id,
                )
                name_clean = str(p_name or ("老咪" if qq_id == OWNER_QQ else qq_id))
            return qq_id, (name_clean or ("老咪" if qq_id == OWNER_QQ else qq_id))

        if self._is_owner_ts_identity(
            name_clean,
            uid_clean,
            owner_ts_names=owner_ts_names,
            owner_ts_uids=owner_ts_uids,
        ):
            return OWNER_QQ, (name_clean or "老咪")

        if conn is not None:
            if uid_clean:
                existing_taste_key = await conn.fetchval(
                    "SELECT user_key FROM user_music_taste WHERE pool_id = $1::uuid AND ts_unique_id = $2 LIMIT 1",
                    POOL_ID,
                    uid_clean,
                )
                if existing_taste_key and not str(existing_taste_key).startswith("ts6:"):
                    return str(existing_taste_key), (name_clean or uid_clean)
            if name_clean:
                prof_uid = await conn.fetchval(
                    """
                    SELECT sender_id FROM user_profiles
                    WHERE pool_id = $1::uuid
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements_text(nicknames) AS n
                          WHERE lower(n) = lower($2)
                      )
                    LIMIT 1
                    """,
                    POOL_ID,
                    name_clean,
                )
                if prof_uid:
                    return str(prof_uid), name_clean
                comp_uid = await conn.fetchval(
                    """
                    SELECT sender_id FROM companion_people
                    WHERE bot_id = $1 AND lower(nickname) = lower($2)
                    ORDER BY familiarity DESC LIMIT 1
                    """,
                    self.bot_id,
                    name_clean,
                )
                if comp_uid:
                    return str(comp_uid), name_clean

        fallback_key = f"ts6:{(uid_clean or name_clean or 'guest')[:40]}"
        return fallback_key, (name_clean or uid_clean or "未知队友")

    async def _fetch_music_profile_from_conn(
        self,
        conn: Any,
        *,
        user_key: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        taste_row = await conn.fetchrow(
            """
            SELECT display_name, top_artists, favorite_tracks, taste_summary, total_plays
            FROM user_music_taste
            WHERE pool_id = $1::uuid AND user_key = $2
            """,
            POOL_ID,
            user_key,
        )
        pl_rows = await conn.fetch(
            """
            SELECT playlist_name, track_id, title, artist
            FROM user_music_playlists
            WHERE pool_id = $1::uuid AND user_key = $2
            ORDER BY id DESC LIMIT 30
            """,
            POOL_ID,
            user_key,
        )
        saved_playlist = [
            {
                "playlist_name": str(r["playlist_name"]),
                "track_id": str(r["track_id"]),
                "title": str(r["title"]),
                "artist": str(r["artist"] or ""),
            }
            for r in pl_rows
        ]
        top_artists: list[str] = []
        favorite_tracks: list[dict[str, Any]] = []
        taste_summary = ""
        total_plays = 0
        resolved_name = display_name
        if taste_row:
            resolved_name = resolved_name or str(taste_row["display_name"] or "")
            total_plays = int(taste_row["total_plays"] or 0)
            taste_summary = str(taste_row["taste_summary"] or "")
            raw_artists = taste_row["top_artists"]
            if isinstance(raw_artists, str):
                try:
                    top_artists = json.loads(raw_artists)
                except Exception:
                    top_artists = []
            elif isinstance(raw_artists, list):
                top_artists = list(raw_artists)
            raw_favs = taste_row["favorite_tracks"]
            if isinstance(raw_favs, str):
                try:
                    favorite_tracks = json.loads(raw_favs)
                except Exception:
                    favorite_tracks = []
            elif isinstance(raw_favs, list):
                favorite_tracks = list(raw_favs)
        return {
            "user_key": user_key,
            "display_name": resolved_name or user_key,
            "total_plays": total_plays,
            "taste_summary": taste_summary,
            "top_artists": top_artists[:6],
            "favorite_tracks": favorite_tracks[:8],
            "saved_playlist": saved_playlist,
        }

    async def _recompute_user_music_taste(
        self,
        conn: Any,
        *,
        user_key: str,
        display_name: str,
        ts_unique_id: str = "",
    ) -> dict[str, Any]:
        hist_rows = await conn.fetch(
            """
            SELECT track_id, title, artist, play_count
            FROM user_music_history
            WHERE pool_id = $1::uuid AND user_key = $2
            ORDER BY play_count DESC, last_played_at DESC
            LIMIT 40
            """,
            POOL_ID,
            user_key,
        )
        pl_rows = await conn.fetch(
            """
            SELECT track_id, title, artist
            FROM user_music_playlists
            WHERE pool_id = $1::uuid AND user_key = $2
            ORDER BY id DESC LIMIT 40
            """,
            POOL_ID,
            user_key,
        )
        total_plays = int(
            await conn.fetchval(
                "SELECT COALESCE(SUM(play_count), 0) FROM user_music_history WHERE pool_id = $1::uuid AND user_key = $2",
                POOL_ID,
                user_key,
            )
            or 0
        )

        artist_weights: dict[str, int] = {}
        for r in hist_rows:
            raw_a = str(r["artist"] or "").strip()
            for part in re.split(r"[/、,&，]", raw_a):
                a = part.strip()
                if a and a.lower() not in {"unknown", "未知"}:
                    artist_weights[a] = artist_weights.get(a, 0) + int(r["play_count"] or 1)
        for r in pl_rows:
            raw_a = str(r["artist"] or "").strip()
            for part in re.split(r"[/、,&，]", raw_a):
                a = part.strip()
                if a and a.lower() not in {"unknown", "未知"}:
                    artist_weights[a] = artist_weights.get(a, 0) + 2

        top_artists = [
            name for name, _w in sorted(artist_weights.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        ]
        favorite_tracks = [
            {
                "track_id": str(r["track_id"]),
                "title": str(r["title"]),
                "artist": str(r["artist"] or ""),
                "plays": int(r["play_count"] or 1),
            }
            for r in hist_rows[:8]
        ]

        existing_summary = str(
            await conn.fetchval(
                "SELECT taste_summary FROM user_music_taste WHERE pool_id = $1::uuid AND user_key = $2",
                POOL_ID,
                user_key,
            )
            or ""
        )
        auto_parts: list[str] = []
        if top_artists:
            auto_parts.append("偏爱歌手：" + "、".join(top_artists[:4]))
        sample_titles = [t["title"] for t in favorite_tracks[:3] if t.get("title")] or [
            str(r["title"]) for r in pl_rows[:3] if r["title"]
        ]
        if sample_titles:
            auto_parts.append("常听/收藏：" + "、".join(f"《{x}》" for x in sample_titles))
        fallback_summary = "；".join(auto_parts) if auto_parts else "刚开始点歌，歌品还在观察中"
        final_summary = existing_summary if (existing_summary and not existing_summary.startswith("偏爱歌手：") and not existing_summary.startswith("常听/收藏：")) else fallback_summary

        await conn.execute(
            """
            INSERT INTO user_music_taste(
                pool_id, user_key, ts_unique_id, display_name,
                top_artists, favorite_tracks, taste_summary, total_plays, updated_at
            )
            VALUES($1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, now())
            ON CONFLICT(pool_id, user_key) DO UPDATE SET
                ts_unique_id = CASE WHEN $3 <> '' THEN $3 ELSE user_music_taste.ts_unique_id END,
                display_name = CASE WHEN $4 <> '' THEN $4 ELSE user_music_taste.display_name END,
                top_artists = $5::jsonb,
                favorite_tracks = $6::jsonb,
                taste_summary = $7,
                total_plays = $8,
                updated_at = now()
            """,
            POOL_ID,
            user_key,
            ts_unique_id[:64],
            display_name[:40],
            json.dumps(top_artists, ensure_ascii=False),
            json.dumps(favorite_tracks, ensure_ascii=False),
            final_summary[:180],
            total_plays,
        )

        # Every 4 plays (starting at 3), refine the 1-sentence taste portrait with Gemini in the background
        if total_plays >= 3 and (total_plays == 3 or total_plays % 4 == 0):
            saved_list = [{"title": str(r["title"]), "artist": str(r["artist"] or "")} for r in pl_rows[:10]]
            asyncio.create_task(
                self._refresh_taste_summary_llm(
                    user_key=user_key,
                    display_name=display_name,
                    top_artists=top_artists,
                    favorite_tracks=favorite_tracks,
                    saved_tracks=saved_list,
                )
            )

        return {
            "user_key": user_key,
            "display_name": display_name,
            "total_plays": total_plays,
            "taste_summary": final_summary,
            "top_artists": top_artists,
            "favorite_tracks": favorite_tracks,
        }

    async def _refresh_taste_summary_llm(
        self,
        *,
        user_key: str,
        display_name: str,
        top_artists: list[str],
        favorite_tracks: list[dict[str, Any]],
        saved_tracks: list[dict[str, Any]],
    ) -> None:
        try:
            if not self._ensure_genai():
                return
            prompt_data = {
                "user": display_name,
                "top_artists": top_artists,
                "favorite_tracks": favorite_tracks,
                "saved_playlist": saved_tracks,
            }
            res = await self._call_structured_gemini(
                system_instruction=(
                    "请根据该用户在 TS6/QQ 点歌和收藏的记录，用精炼自然的中文（20～65字）提炼一句「歌品画像总结」`taste_summary`"
                    "（概括曲风偏好、常听歌手与代表作风格，客观生动，不要人身攻击）。"
                ),
                contents=[json.dumps(prompt_data, ensure_ascii=False)],
                schema_cls=UserTasteSummarySchema,
                thinking_level="LOW",
            )
            if res and res.taste_summary.strip():
                pool = await self._get_pg_pool()
                if pool is not None:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE user_music_taste
                            SET taste_summary = $3, updated_at = now()
                            WHERE pool_id = $1::uuid AND user_key = $2
                            """,
                            POOL_ID,
                            user_key,
                            res.taste_summary.strip()[:160],
                        )
        except Exception as exc:
            logger.debug("taste summary llm refresh skipped: %s", exc)

    async def record_user_music_play(
        self,
        *,
        track_id: str,
        title: str,
        artist: str = "",
        speaker_name: str = "",
        ts_unique_id: str = "",
        qq_sender_id: str = "",
        source: str = "ts6",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> None:
        clean_title = str(title or "").strip()
        clean_tid = str(track_id or "").strip()
        if not clean_title or not clean_tid:
            return
        caller_lower = str(speaker_name or "").strip().lower()
        if not qq_sender_id and not ts_unique_id and caller_lower in {
            "", "auto", "next", "previous", "web", "web_history", "external_api", "external_history", "哈基咪", "ts"
        }:
            return
        pool = await self._get_pg_pool()
        if pool is None:
            return
        try:
            async with pool.acquire() as conn:
                user_key, disp_name = await self.resolve_canonical_user_key(
                    conn,
                    speaker_name=speaker_name,
                    ts_unique_id=ts_unique_id,
                    qq_sender_id=qq_sender_id,
                    owner_ts_names=owner_ts_names,
                    owner_ts_uids=owner_ts_uids,
                )
                await conn.execute(
                    """
                    INSERT INTO user_music_history(
                        pool_id, user_key, ts_unique_id, display_name,
                        track_id, title, artist, source, play_count, last_played_at
                    )
                    VALUES($1::uuid, $2, $3, $4, $5, $6, $7, $8, 1, now())
                    ON CONFLICT(pool_id, user_key, track_id) DO UPDATE SET
                        play_count = user_music_history.play_count + 1,
                        last_played_at = now(),
                        ts_unique_id = CASE WHEN $3 <> '' THEN $3 ELSE user_music_history.ts_unique_id END,
                        display_name = CASE WHEN $4 <> '' THEN $4 ELSE user_music_history.display_name END,
                        title = CASE WHEN $6 <> '' THEN $6 ELSE user_music_history.title END,
                        artist = CASE WHEN $7 <> '' THEN $7 ELSE user_music_history.artist END
                    """,
                    POOL_ID,
                    user_key,
                    str(ts_unique_id or "")[:64],
                    disp_name[:40],
                    clean_tid[:120],
                    clean_title[:160],
                    str(artist or "")[:120],
                    str(source or "ts6")[:16],
                )
                await self._recompute_user_music_taste(
                    conn,
                    user_key=user_key,
                    display_name=disp_name,
                    ts_unique_id=str(ts_unique_id or ""),
                )
        except Exception as exc:
            logger.warning("record_user_music_play failed: %s", exc)

    async def save_to_user_playlist(
        self,
        *,
        track_id: str,
        title: str,
        artist: str = "",
        playlist_name: str = "默认歌单",
        speaker_name: str = "",
        ts_unique_id: str = "",
        qq_sender_id: str = "",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> dict[str, Any]:
        pool = await self._get_pg_pool()
        if pool is None:
            return {"ok": False, "reason": "记忆库未连接"}
        async with pool.acquire() as conn:
            user_key, disp_name = await self.resolve_canonical_user_key(
                conn,
                speaker_name=speaker_name,
                ts_unique_id=ts_unique_id,
                qq_sender_id=qq_sender_id,
                owner_ts_names=owner_ts_names,
                owner_ts_uids=owner_ts_uids,
            )
            pl_name = (playlist_name or "默认歌单").strip()[:40] or "默认歌单"
            await conn.execute(
                """
                INSERT INTO user_music_playlists(
                    pool_id, user_key, display_name, playlist_name, track_id, title, artist, created_at
                )
                VALUES($1::uuid, $2, $3, $4, $5, $6, $7, now())
                ON CONFLICT(pool_id, user_key, playlist_name, track_id) DO UPDATE SET
                    display_name = CASE WHEN $3 <> '' THEN $3 ELSE user_music_playlists.display_name END,
                    title = $6,
                    artist = $7
                """,
                POOL_ID,
                user_key,
                disp_name[:40],
                pl_name,
                str(track_id)[:120],
                str(title)[:160],
                str(artist or "")[:120],
            )
            await self._recompute_user_music_taste(
                conn,
                user_key=user_key,
                display_name=disp_name,
                ts_unique_id=str(ts_unique_id or ""),
            )
            total_saved = int(
                await conn.fetchval(
                    "SELECT count(*) FROM user_music_playlists WHERE pool_id = $1::uuid AND user_key = $2",
                    POOL_ID,
                    user_key,
                )
                or 0
            )
            return {
                "ok": True,
                "user_key": user_key,
                "display_name": disp_name,
                "playlist_name": pl_name,
                "total_saved": total_saved,
            }

    async def remove_from_user_playlist(
        self,
        *,
        keyword: str,
        speaker_name: str = "",
        ts_unique_id: str = "",
        qq_sender_id: str = "",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> dict[str, Any]:
        kw = str(keyword or "").strip()
        if not kw:
            return {"ok": False, "removed": 0}
        pool = await self._get_pg_pool()
        if pool is None:
            return {"ok": False, "removed": 0}
        async with pool.acquire() as conn:
            user_key, disp_name = await self.resolve_canonical_user_key(
                conn,
                speaker_name=speaker_name,
                ts_unique_id=ts_unique_id,
                qq_sender_id=qq_sender_id,
                owner_ts_names=owner_ts_names,
                owner_ts_uids=owner_ts_uids,
            )
            rows = await conn.fetch(
                """
                DELETE FROM user_music_playlists
                WHERE pool_id = $1::uuid AND user_key = $2
                  AND (title ILIKE '%' || $3 || '%' OR artist ILIKE '%' || $3 || '%' OR track_id = $3)
                RETURNING title
                """,
                POOL_ID,
                user_key,
                kw,
            )
            await self._recompute_user_music_taste(
                conn,
                user_key=user_key,
                display_name=disp_name,
                ts_unique_id=str(ts_unique_id or ""),
            )
            return {
                "ok": True,
                "user_key": user_key,
                "display_name": disp_name,
                "removed": len(rows),
                "titles": [str(r["title"]) for r in rows],
            }

    async def get_user_music_profile(
        self,
        *,
        target_name: str = "",
        speaker_name: str = "",
        ts_unique_id: str = "",
        qq_sender_id: str = "",
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> dict[str, Any]:
        pool = await self._get_pg_pool()
        if pool is None:
            return {
                "user_key": "",
                "display_name": target_name or speaker_name or "",
                "total_plays": 0,
                "taste_summary": "",
                "top_artists": [],
                "favorite_tracks": [],
                "saved_playlist": [],
            }
        async with pool.acquire() as conn:
            t_clean = str(target_name or "").strip()
            if t_clean and t_clean not in {"我", "自己", "本人", "我的"}:
                # Check if target_name matches an existing user_music_taste display_name first
                matched_row = await conn.fetchrow(
                    """
                    SELECT user_key, display_name FROM user_music_taste
                    WHERE pool_id = $1::uuid AND (lower(display_name) = lower($2) OR user_key = $2)
                    ORDER BY total_plays DESC LIMIT 1
                    """,
                    POOL_ID,
                    t_clean,
                )
                if matched_row:
                    user_key = str(matched_row["user_key"])
                    disp_name = str(matched_row["display_name"] or t_clean)
                else:
                    user_key, disp_name = await self.resolve_canonical_user_key(
                        conn,
                        speaker_name=t_clean,
                        owner_ts_names=owner_ts_names,
                        owner_ts_uids=owner_ts_uids,
                    )
            else:
                user_key, disp_name = await self.resolve_canonical_user_key(
                    conn,
                    speaker_name=speaker_name,
                    ts_unique_id=ts_unique_id,
                    qq_sender_id=qq_sender_id,
                    owner_ts_names=owner_ts_names,
                    owner_ts_uids=owner_ts_uids,
                )
            return await self._fetch_music_profile_from_conn(
                conn,
                user_key=user_key,
                display_name=disp_name,
            )

    def _build_system_instruction(self, *, modality: str) -> str:
        persona = self.load_persona()
        return (
            f"{persona}\n\n"
            "【当前运行环境：TeamSpeak 6 语音频道实时陪玩 + 音乐机器人（与 QQ 哈基咪共享同一套记忆、人设、实时情绪与全员歌品/歌单库）】\n"
            f"当前交互模态：{modality}\n"
            "核心行为准则：\n"
            "1. 你的语音会通过少女音 TTS 直接在 TS6 频道里说出来！因此 `spoken_reply` 必须是自然、口语化、像真人女孩在麦克风里随口接话的短句（通常 6～45 个字），绝不要念列表编号、URL、Markdown 符号或舞台动作括号。\n"
            "2. 严格遵守人际边界：对 `relationship=\"初识\"` 的陌生/未熟络成员保持客气礼貌，严禁自来熟损人；对熟人和老咪按 `relation_cue` 自然相处；任何情况下严禁骂任何人脑子傻/智障/没脑子。\n"
            "3. 若遇到初识成员恶意挑衅、盘问或恶搞（如让你叫主人/学狗叫），直接简短回 `spoken_reply=\"无语。\"`，不要长篇解释。\n"
            "4. 你同时掌控 TS6 点歌台与全员专属【歌品 + 歌单】记忆（`speaker.music_profile` 里有当前说话人的常听歌手、代表曲目、歌品画像与个人收藏歌单）：\n"
            "   - 立即播放某首歌（如果对方说「按我的歌品随便来一首」，你可参考 `music_profile` 挑一首符合他口味的歌填入 `music_arg`）：`music_cmd=\"play\"`, `music_arg=\"歌手 歌名\"`\n"
            "   - 加入队列排歌：`music_cmd=\"add\"`, `music_arg=\"歌手 歌名\"`\n"
            "   - 收藏当前正在放的歌进说话人歌单（如「收藏这首」「把这首歌加进我歌单」）：`music_cmd=\"like_current\"`, `music_arg=\"\"`\n"
            "   - 收藏指定歌曲进说话人歌单（如「把晴天存进我歌单」）：`music_cmd=\"save_song\"`, `music_arg=\"歌手 歌名\"`\n"
            "   - 从说话人歌单删除某首歌：`music_cmd=\"remove_song\"`, `music_arg=\"歌名\"`\n"
            "   - 播放个人歌单/常听喜好歌单（如「放我的歌单」「播老咪的歌单」）：`music_cmd=\"play_user_playlist\"`, `music_arg=\"\"`（播说话人自己填 `\"\"`，播指定队友填对方昵称如 `\"老咪\"`）\n"
            "   - 绑定/导入网易云歌单进个人歌单：`music_cmd=\"bind_netease_playlist\"`, `music_arg=\"网易云歌单ID或链接\"`\n"
            "   - 切歌/下一首：`music_cmd=\"skip\"` | 暂停：`music_cmd=\"pause\"` | 继续：`music_cmd=\"resume\"` | 停止：`music_cmd=\"stop\"`\n"
            "   - 调整音量（0-200）：`music_cmd=\"vol\"`, `music_arg=\"数字\"`\n"
            "   - 随机：`music_cmd=\"random\"` | 顺序：`music_cmd=\"order\"` | 循环：`music_cmd=\"repeat\"`, `music_arg=\"one|all|none\"`\n"
            "   - 清空队列：`music_cmd=\"clear\"` | 当前歌曲：`music_cmd=\"now\"` | 查看队列：`music_cmd=\"queue\"`\n"
            "   - 不涉及音乐控制（包括单纯聊歌品、评价某人歌单而无需切歌时）：`music_cmd=\"none\"`, `music_arg=\"\"`。\n"
        )

    async def _call_structured_gemini(
        self,
        *,
        system_instruction: str,
        contents: list[Any],
        schema_cls: type[BaseModel],
        thinking_level: str = "LOW",
    ) -> Any:
        if not self._ensure_genai():
            return None
        types = self._genai_types
        clean_schema = _sanitize_schema(schema_cls.model_json_schema())
        cfg_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "response_json_schema": clean_schema,
            "temperature": 0.75,
        }
        lvl = getattr(types.ThinkingLevel, thinking_level.upper(), None)
        if lvl is not None:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=lvl)
        config = types.GenerateContentConfig(**cfg_kwargs)
        resp = await self._genai_client.aio.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )
        raw_text = (resp.text or "").strip()
        return schema_cls.model_validate_json(raw_text)

    async def plan_ts_chat(
        self,
        *,
        speaker_name: str,
        ts_unique_id: str,
        message: str,
        target_mode: int,
        music_summary: str,
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> tuple[TsHachimiPlan | None, AffectSnapshot]:
        msg = (message or "").strip()
        if not msg:
            return None, AffectSnapshot()

        speaker_key = (ts_unique_id or speaker_name or "").strip().lower()
        is_private = int(target_mode) == 1
        at_mentioned = bool(AT_MENTION_RE.search(msg))
        called = bool(WAKE_WORD_RE.search(msg))
        music_intent = bool(NATURAL_MUSIC_INTENT_RE.search(msg))
        in_window = self.in_followup_window(speaker_key)

        sh_hour = self._now_utc().astimezone(timezone(timedelta(hours=8))).hour
        in_rest = is_rest_hours(sh_hour)

        # 04:00-08:00 rest window: strictly silent unless explicitly @-mentioned or 1v1 private message
        if in_rest and not (is_private or at_mentioned):
            return None, AffectSnapshot(circadian=_circadian_energy(sh_hour))

        direct_trigger = bool(is_private or at_mentioned or called or music_intent or in_window)
        opportunistic = False
        if not direct_trigger:
            if len(msg) < 2 or msg.startswith(("http://", "https://")):
                return None, AffectSnapshot(circadian=_circadian_energy(sh_hour))
            opportunistic = True

        speaker_ctx, affect = await self.resolve_speaker_and_state(
            speaker_name=speaker_name,
            ts_unique_id=ts_unique_id,
            query_text=msg,
            owner_ts_names=owner_ts_names,
            owner_ts_uids=owner_ts_uids,
        )
        if opportunistic:
            rel_tier = str(speaker_ctx.get("relationship", "初识"))
            min_cd = (
                25.0
                if rel_tier in {"作者", "老熟人"}
                else 35.0
                if rel_tier == "熟悉"
                else 45.0
                if rel_tier == "渐熟"
                else 75.0
            )
            if time.monotonic() - self._last_ambient_chat_ts < min_cd:
                return None, affect

        if not speaker_ctx["is_author"] and speaker_ctx["relationship"] == "初识" and PROVOCATION_BAIT_RE.search(msg):
            if opportunistic:
                return None, affect
            plan = TsHachimiPlan(
                transcript=msg,
                should_reply=True,
                spoken_reply="无语。",
                chat_reply="无语。",
                music_cmd="none",
                music_arg="",
            )
            await self.appraise_and_record_turn(
                sender_id=speaker_ctx["sender_id"],
                speaker_name=speaker_name,
                is_owner=False,
                user_text=msg,
                bot_reply="无语。",
            )
            return plan, affect

        woken_during_rest = bool(in_rest and not is_private)
        payload: dict[str, Any] = {
            "channel_context": {
                "is_private_chat": is_private,
                "at_mentioned": at_mentioned,
                "called_wake_word": called,
                "in_followup_window": in_window,
                "opportunistic_chime_in": opportunistic,
                "woken_during_rest": woken_during_rest,
                "music_status": music_summary,
                "recent_ts_dialogue": list(self._recent_ts_turns)[-6:],
            },
            "speaker": speaker_ctx,
            "incoming_text": msg,
        }
        m_other = re.search(r"(?:看看|播|放|放一下|播一下|查查|评价一下)?([^\s，,。！？?@＠]{1,12})的(?:歌品|歌单)", msg)
        if m_other:
            cand_target = m_other.group(1).strip()
            if cand_target and cand_target not in {"我", "自己", "你", "大家", "谁", "别人"}:
                other_mp = await self.get_user_music_profile(
                    target_name=cand_target,
                    owner_ts_names=owner_ts_names,
                    owner_ts_uids=owner_ts_uids,
                )
                if other_mp.get("total_plays") or other_mp.get("saved_playlist"):
                    payload["target_member_music_profile"] = other_mp

        extra_chat_rules = ""
        if woken_during_rest:
            extra_chat_rules = (
                "\n【休息时段被@唤醒提示】：现在是凌晨4点到早上8点的休息睡眠时间，你是被队友专门 `@` 叫醒才回话的；"
                "正常回应对方需求的同时，语气可以自然带一点刚被吵醒的困意迷糊（简短自然，别浮夸演戏）。\n"
            )
        elif opportunistic:
            extra_chat_rules = (
                "\n【见机回复模式（opportunistic_chime_in=true）】：当前在你的日常活跃时段（08:00-次日04:00），这条频道消息没有直接@你或喊你名字。"
                "1. 看熟稔度调节接话概率：`speaker.relationship` 为「作者」或「老熟人」时接话概率最高，「熟悉」较高，「渐熟」适度，「初识」保持克制（仅明确面向频道提问时才接）；"
                "2. 感兴趣话题加成：聊到牛肉面/猪脚饭/关东煮/便利店/画画/独立游戏/CS赛事饰品道具/东方/街霸/音乐歌单等哈基咪感兴趣的话题时，可适度顺口搭话；"
                "3. 严禁硬接防尬铁律：如果是队友两人私下对暗号、半截黑话、纯语气词附和，或者你只能憋出「哈哈确实」「太真实了」这种干巴巴的硬接尬聊，必须设 `should_reply=false` 安静潜水！\n"
            )

        plan = await self._call_structured_gemini(
            system_instruction=self._build_system_instruction(modality="TS6 文字聊天框消息") + extra_chat_rules,
            contents=[json.dumps(payload, ensure_ascii=False)],
            schema_cls=TsHachimiPlan,
            thinking_level="LOW",
        )
        if plan is None or not plan.should_reply:
            return plan, affect

        if opportunistic:
            self._last_ambient_chat_ts = time.monotonic()
        plan.spoken_reply = sanitize_outbound_text(plan.spoken_reply)
        plan.chat_reply = sanitize_outbound_text(plan.chat_reply or plan.spoken_reply)
        self.mark_followup_window(speaker_key, 30.0)
        await self.appraise_and_record_turn(
            sender_id=speaker_ctx["sender_id"],
            speaker_name=speaker_name,
            is_owner=bool(speaker_ctx["is_author"]),
            user_text=msg,
            bot_reply=plan.spoken_reply or plan.chat_reply,
        )
        return plan, affect

    async def plan_ts_utterance(
        self,
        *,
        wav_path: Path,
        speaker_name: str,
        ts_unique_id: str,
        duration_ms: int,
        music_summary: str,
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> tuple[TsHachimiPlan | None, AffectSnapshot]:
        if not wav_path.is_file():
            return None, AffectSnapshot()

        sh_hour = self._now_utc().astimezone(timezone(timedelta(hours=8))).hour
        if is_rest_hours(sh_hour):
            # 04:00-08:00 rest window: stay strictly silent on un-@ voice utterances
            return None, AffectSnapshot(circadian=_circadian_energy(sh_hour))

        async with self._utterance_lock:
            try:
                wav_bytes = wav_path.read_bytes()
            except OSError:
                return None, AffectSnapshot()
            if len(wav_bytes) < 1000:
                return None, AffectSnapshot()

            if not self._ensure_genai():
                return None, AffectSnapshot()

            speaker_key = (ts_unique_id or speaker_name or "").strip().lower()
            in_window = self.in_followup_window(speaker_key)
            speaker_ctx, affect = await self.resolve_speaker_and_state(
                speaker_name=speaker_name,
                ts_unique_id=ts_unique_id,
                query_text="",
                owner_ts_names=owner_ts_names,
                owner_ts_uids=owner_ts_uids,
            )
            rel_tier = str(speaker_ctx.get("relationship", "初识"))
            voice_cd = 60.0 if rel_tier in {"作者", "老熟人"} else 75.0 if rel_tier == "熟悉" else 90.0
            allow_ambient = (not in_window) and ((time.monotonic() - self._last_ambient_voice_ts) >= voice_cd)

            voice_gate_instruction = (
                self._build_system_instruction(modality="TS6 麦克风实时语音输入（16kHz WAV）")
                + "\n【麦克风防打扰与见机回复准则（极其重要！）】：\n"
                "- 先把听到的中文语音准确转写到 `transcript`（若只是杂音/咳嗽/键盘声/听不清，`transcript` 填 `\"\"` 且 `should_reply=false`）。\n"
                "- 游戏激战报点绝对静默：只要队友是在互相报点/指挥战斗（如「A大两个」「给闪」「Nice」「拉枪线」「残血」），无论何时必须设 `should_reply=false`，绝不打断游戏！\n"
                "- 当说话人明确喊了你的名字（「哈基咪」「哈吉米」「哈基米」「小咪」「咪」），或者明显在对点歌机器人下达放歌/切歌/音量指令时，设 `should_reply=true`。\n"
                "- 当 `in_followup_window=true` 时：说明这位队友几秒前刚跟你说过话；如果是在顺着刚才的话题继续跟你聊或追问，设 `should_reply=true` 自然接话。\n"
                "- 当 `allow_ambient_chime_in=true` 时（活跃时段见机回复）：熟稔度越高（老熟人/熟悉 > 渐熟 > 初识）或聊到哈基咪感兴趣的话题（美食、音乐歌单、画画、独立游戏、赛场/饰品吐槽等）时接话概率越高，但严禁硬接尬聊——如果队友在私下交流或你接出来很生硬干巴，必须设 `should_reply=false`。\n"
            )

            meta_prompt = json.dumps(
                {
                    "channel_context": {
                        "in_followup_window": in_window,
                        "allow_ambient_chime_in": allow_ambient,
                        "duration_ms": duration_ms,
                        "music_status": music_summary,
                        "recent_ts_dialogue": list(self._recent_ts_turns)[-6:],
                    },
                    "speaker": speaker_ctx,
                },
                ensure_ascii=False,
            )
            audio_part = self._genai_types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

            plan = await self._call_structured_gemini(
                system_instruction=voice_gate_instruction,
                contents=[audio_part, meta_prompt],
                schema_cls=TsHachimiPlan,
                thinking_level="LOW",
            )
            if plan is None:
                return None, affect

            logger.info(
                "ts3 voice transcript: speaker=%s dur=%dms in_win=%s reply=%s cmd=%s text=%r",
                speaker_name,
                duration_ms,
                in_window,
                plan.should_reply,
                plan.music_cmd,
                plan.transcript,
            )
            if not plan.should_reply:
                return plan, affect

            if (
                not speaker_ctx["is_author"]
                and speaker_ctx["relationship"] == "初识"
                and PROVOCATION_BAIT_RE.search(plan.transcript or "")
            ):
                plan.spoken_reply = "无语。"
                plan.chat_reply = ""
                plan.music_cmd = "none"
                plan.music_arg = ""
            else:
                plan.spoken_reply = sanitize_outbound_text(plan.spoken_reply)
                plan.chat_reply = sanitize_outbound_text(plan.chat_reply)

            if plan.spoken_reply or plan.music_cmd != "none":
                if allow_ambient and not WAKE_WORD_RE.search(plan.transcript or ""):
                    self._last_ambient_voice_ts = time.monotonic()
                self.mark_followup_window(speaker_key, 26.0)
                await self.appraise_and_record_turn(
                    sender_id=speaker_ctx["sender_id"],
                    speaker_name=speaker_name,
                    is_owner=bool(speaker_ctx["is_author"]),
                    user_text=plan.transcript or "[语音]",
                    bot_reply=plan.spoken_reply or plan.chat_reply,
                )
            return plan, affect

    async def plan_ts_welcome(
        self,
        *,
        speaker_name: str,
        ts_unique_id: str,
        music_summary: str,
        owner_ts_names: str = "",
        owner_ts_uids: str = "",
    ) -> tuple[TsWelcomePlan | None, AffectSnapshot]:
        sh_hour = self._now_utc().astimezone(timezone(timedelta(hours=8))).hour
        if is_rest_hours(sh_hour):
            return None, AffectSnapshot(circadian=_circadian_energy(sh_hour))
        speaker_ctx, affect = await self.resolve_speaker_and_state(
            speaker_name=speaker_name,
            ts_unique_id=ts_unique_id,
            query_text="",
            owner_ts_names=owner_ts_names,
            owner_ts_uids=owner_ts_uids,
        )
        welcome_instruction = (
            self._build_system_instruction(modality="队友刚进入你所在的 TS6 语音频道（进频道语音打招呼）")
            + "\n请生成一句简短自然的进频道语音招呼 `spoken_reply`（8～30个字），像真人已经在频道里看到对方进来随口打声招呼：\n"
            "- 如果是作者老咪进来：随口熟稔招呼（若当前 `irritation` 较高可以带点小傲娇吐槽）。\n"
            "- 如果是熟人队友：自然熟络打招呼。\n"
            "- 如果是初识成员：客气礼貌、简短自然地欢迎一声即可。\n"
            "- 如果当前正好在放歌，可以顺口提半句或者直接打招呼，别像客服广播。"
        )
        payload = {
            "joining_member": speaker_ctx,
            "music_status": music_summary,
        }
        plan = await self._call_structured_gemini(
            system_instruction=welcome_instruction,
            contents=[json.dumps(payload, ensure_ascii=False)],
            schema_cls=TsWelcomePlan,
            thinking_level="LOW",
        )
        if plan is None:
            return None, affect
        plan.spoken_reply = sanitize_outbound_text(plan.spoken_reply)
        plan.chat_reply = sanitize_outbound_text(plan.chat_reply)
        return plan, affect
