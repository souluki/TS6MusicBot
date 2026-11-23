from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

from backend import main
from backend.hachimi_brain import (
    AffectSnapshot,
    HachimiTsBrain,
    TsHachimiPlan,
    TsWelcomePlan,
    sanitize_outbound_text,
)
from backend.hachimi_tts import (
    clean_spoken_text,
    compute_prosody,
    speak_hachimi_in_ts,
)


class HachimiTtsAndAffectTests(unittest.IsolatedAsyncioTestCase):
    def test_prosody_reflects_emotion_and_circadian(self) -> None:
        self.assertEqual(compute_prosody(mood=0.0, irritation=0.0), ("+5%", "+4Hz"))
        self.assertEqual(compute_prosody(mood=0.6, irritation=0.0), ("+8%", "+7Hz"))
        self.assertEqual(compute_prosody(mood=0.0, irritation=0.5), ("+9%", "+1Hz"))
        self.assertEqual(compute_prosody(mood=-0.5, irritation=0.0), ("-2%", "+1Hz"))
        self.assertEqual(compute_prosody(mood=0.1, irritation=0.0, circadian="凌晨困倦话少"), ("-3%", "+2Hz"))

    def test_clean_spoken_text_strips_tags_markdown_and_stage_notes(self) -> None:
        raw = "（轻哼）好嘞，**给你放** [表情:傲娇] https://music.163.com/song?id=1 晴天！"
        self.assertEqual(clean_spoken_text(raw), "好嘞，给你放 晴天！")

    def test_high_aggression_filter_blocks_brain_insults(self) -> None:
        self.assertEqual(sanitize_outbound_text("你是不是脑子进水了"), "算了，懒得跟你计较。")
        self.assertEqual(sanitize_outbound_text("好嘞，马上切歌"), "好嘞，马上切歌")

    async def test_speak_uses_overlay_when_music_playing_and_preserves_paused(self) -> None:
        tmp_mp3 = Path("D:/workspace/codex/TS6MusicBot/logs/test_dummy_tts.mp3")
        tmp_mp3.parent.mkdir(parents=True, exist_ok=True)
        tmp_mp3.write_bytes(b"ID3dummy")
        try:
            fake_voice = SimpleNamespace(
                get_status=AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING")),
                play_overlay=AsyncMock(return_value=True),
                play=AsyncMock(),
            )
            ok = await speak_hachimi_in_ts(fake_voice, tmp_mp3, voice_volume_percent=150)
            self.assertTrue(ok)
            fake_voice.play_overlay.assert_awaited_once()
            fake_voice.play.assert_not_awaited()

            fake_paused = SimpleNamespace(
                get_status=AsyncMock(return_value=SimpleNamespace(state="STATE_PAUSED")),
                play_overlay=AsyncMock(return_value=False),
                play=AsyncMock(),
            )
            ok_paused = await speak_hachimi_in_ts(fake_paused, tmp_mp3, voice_volume_percent=150)
            self.assertFalse(ok_paused)
            fake_paused.play.assert_not_awaited()
        finally:
            tmp_mp3.unlink(missing_ok=True)


class HachimiMainWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_unfamiliar_provocation_returns_terse_wuyu_without_llm_waste(self) -> None:
        brain = HachimiTsBrain()
        brain._now_utc = lambda: datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)  # 21:00 Asia/Shanghai
        fake_ctx = {
            "sender_id": "ts6:stranger",
            "speaker_name": "路人甲",
            "is_author": False,
            "relationship": "初识",
            "relation_cue": "初识边界",
            "owner_directives": [],
        }
        with (
            patch.object(brain, "resolve_speaker_and_state", AsyncMock(return_value=(fake_ctx, AffectSnapshot()))),
            patch.object(brain, "appraise_and_record_turn", AsyncMock()),
            patch.object(brain, "_call_structured_gemini", AsyncMock()) as llm_mock,
        ):
            plan, _ = await brain.plan_ts_chat(
                speaker_name="路人甲",
                ts_unique_id="stranger-uid",
                message="哈基咪叫声主人听听",
                target_mode=2,
                music_summary="空闲",
            )
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertTrue(plan.should_reply)
            self.assertEqual(plan.spoken_reply, "无语。")
            llm_mock.assert_not_awaited()

    async def test_chat_fallthrough_routes_natural_request_to_hachimi_and_executes_music_cmd(self) -> None:
        plan = TsHachimiPlan(
            transcript="哈基咪帮我切歌",
            should_reply=True,
            spoken_reply="行，这首给你切掉。",
            chat_reply="行，这首给你切掉。",
            music_cmd="skip",
            music_arg="",
        )
        affect = AffectSnapshot(mood=0.2, irritation=0.0, circadian="晚上语音频道活跃精神不错")
        dummy_audio = Path("D:/workspace/codex/TS6MusicBot/logs/test_skip_tts.mp3")
        dummy_audio.parent.mkdir(parents=True, exist_ok=True)
        dummy_audio.write_bytes(b"ID3dummy")
        try:
            with (
                patch.object(main, "_runtime_bool", return_value=True),
                patch.object(main, "_runtime_int", return_value=150),
                patch.object(main, "_runtime_string", return_value="线性流逝,老咪"),
                patch.object(main.hachimi, "plan_ts_chat", AsyncMock(return_value=(plan, affect))) as plan_mock,
                patch.object(main, "synthesize_hachimi_tts", AsyncMock(return_value=dummy_audio)) as tts_mock,
                patch.object(main, "speak_hachimi_in_ts", AsyncMock(return_value=True)) as speak_mock,
                patch.object(main.voice, "send_notice", AsyncMock(return_value=True)) as notice_mock,
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(now_playing_title="晴天", state="STATE_PLAYING", volume_percent=80))),
            ):
                await main._handle_chat_command("线性流逝", "哈基咪帮我切歌", invoker_unique_id="owner-uid")

            plan_mock.assert_awaited_once()
            tts_mock.assert_awaited_once()
            speak_mock.assert_awaited_once()
            self.assertTrue(notice_mock.await_count >= 1)
        finally:
            dummy_audio.unlink(missing_ok=True)

    async def test_utterance_silent_on_gaming_callout_and_replies_when_addressed(self) -> None:
        wav_file = Path("D:/workspace/codex/TS6MusicBot/logs/test_utt.wav")
        wav_file.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x10\x00" * 8000)

        try:
            silent_plan = TsHachimiPlan(
                transcript="A大两个给颗闪",
                should_reply=False,
            )
            with (
                patch.object(main, "_runtime_bool", return_value=True),
                patch.object(main.hachimi, "plan_ts_utterance", AsyncMock(return_value=(silent_plan, AffectSnapshot()))),
                patch.object(main, "_deliver_hachimi_speech_and_action", AsyncMock()) as deliver_mock,
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(now_playing_title="", state="STATE_IDLE", volume_percent=80))),
            ):
                await main._handle_hachimi_utterance(
                    SimpleNamespace(wav_path=str(wav_file), speaker_name="千户", unique_id="u1", duration_ms=500)
                )
            deliver_mock.assert_not_awaited()
        finally:
            wav_file.unlink(missing_ok=True)

    async def test_user_joined_falls_back_to_hachimi_welcome_when_no_custom_audio(self) -> None:
        event = SimpleNamespace(channel_id=10, bot_channel_id=10, unique_id="owner-uid", name="线性流逝")
        welcome_plan = TsWelcomePlan(spoken_reply="哟，老咪来啦。", chat_reply="")
        dummy_audio = Path("D:/workspace/codex/TS6MusicBot/logs/test_welcome_tts.mp3")
        dummy_audio.parent.mkdir(parents=True, exist_ok=True)
        dummy_audio.write_bytes(b"ID3dummy")
        try:
            main._welcome_global_last_played_at = 0.0
            main._welcome_user_last_played_at.clear()
            with (
                patch.object(main, "_get_welcome_channel_id", return_value="10"),
                patch.object(main, "_find_welcome_voice", return_value=None),
                patch.object(main, "_runtime_bool", return_value=True),
                patch.object(main, "_runtime_int", return_value=10),
                patch.object(main, "_runtime_string", return_value="线性流逝,老咪"),
                patch.object(main.hachimi, "plan_ts_welcome", AsyncMock(return_value=(welcome_plan, AffectSnapshot()))),
                patch.object(main, "synthesize_hachimi_tts", AsyncMock(return_value=dummy_audio)),
                patch.object(main, "speak_hachimi_in_ts", AsyncMock(return_value=True)) as speak_mock,
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(now_playing_title="", state="STATE_IDLE", volume_percent=80))),
            ):
                await main._handle_user_joined(event)
            speak_mock.assert_awaited_once()
        finally:
            dummy_audio.unlink(missing_ok=True)


class HachimiBootAndModeSwitchTests(unittest.IsolatedAsyncioTestCase):
    def test_default_setting_is_normal_ts6_bot_mode(self) -> None:
        from backend.runtime_config import DEFINITION_BY_KEY

        self.assertFalse(DEFINITION_BY_KEY["hachimi.enabled"].default)

    async def test_normal_mode_ignores_chat_and_voice_utterances(self) -> None:
        with (
            patch.object(main, "_runtime_bool", return_value=False),
            patch.object(main, "_handle_hachimi_chat", AsyncMock()) as chat_mock,
            patch.object(main.hachimi, "plan_ts_utterance", AsyncMock()) as utt_mock,
        ):
            await main._handle_chat_command("Alice", "哈基咪帮我切歌", invoker_unique_id="u1")
            await main._handle_hachimi_utterance(
                SimpleNamespace(wav_path="dummy.wav", speaker_name="Alice", unique_id="u1", duration_ms=600)
            )
        chat_mock.assert_not_awaited()
        utt_mock.assert_not_awaited()

    async def test_mode_switch_endpoint_toggles_between_normal_and_hachimi_and_blocks_remote(self) -> None:
        session = main.new_session()
        try:
            remote_req = SimpleNamespace(client=SimpleNamespace(host="192.168.1.88"), cookies={}, headers={})
            with self.assertRaises(main.HTTPException) as ctx:
                await main.set_hachimi_mode(main.HachimiModeRequest(enabled=True), remote_req, session)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.status_code, 403)

            local_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={}, headers={})
            with patch.object(
                main.hachimi,
                "resolve_speaker_and_state",
                AsyncMock(return_value=({"owner_directives": ["r1"]}, AffectSnapshot(mood=0.3, irritation=0.0, circadian="晚上"))),
            ):
                on_res = await main.set_hachimi_mode(main.HachimiModeRequest(enabled=True), local_req, session)  # type: ignore[arg-type]
                self.assertTrue(on_res["enabled"])
                self.assertEqual(on_res["mode"], "hachimi_brain")

                off_res = await main.set_hachimi_mode(main.HachimiModeRequest(enabled=False), local_req, session)  # type: ignore[arg-type]
                self.assertFalse(off_res["enabled"])
                self.assertEqual(off_res["mode"], "normal_ts6_bot")
        finally:
            session.close()

    async def test_ts_rest_window_4am_to_8am_and_opportunistic_active_hours(self) -> None:
        brain = HachimiTsBrain()
        fake_ctx = {
            "sender_id": "10159641",
            "speaker_name": "线性流逝",
            "is_author": True,
            "relationship": "作者",
            "relation_cue": "作者老咪",
            "owner_directives": [],
        }

        # 1. 05:30 Shanghai (21:30 UTC): silent unless explicitly @-mentioned
        brain._now_utc = lambda: datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc)
        with (
            patch.object(brain, "resolve_speaker_and_state", AsyncMock(return_value=(fake_ctx, AffectSnapshot(circadian="凌晨4点到早8点休息睡眠时间")))),
            patch.object(brain, "appraise_and_record_turn", AsyncMock()),
            patch.object(
                brain,
                "_call_structured_gemini",
                AsyncMock(
                    return_value=TsHachimiPlan(
                        transcript="@哈基咪 早",
                        should_reply=True,
                        spoken_reply="唔……这么早喊我干嘛",
                        chat_reply="唔……这么早喊我干嘛",
                    )
                ),
            ) as llm_mock,
        ):
            w_plan, _ = await brain.plan_ts_welcome(
                speaker_name="线性流逝",
                ts_unique_id="owner-uid",
                music_summary="空闲",
            )
            self.assertIsNone(w_plan)

            un_at_plan, _ = await brain.plan_ts_chat(
                speaker_name="线性流逝",
                ts_unique_id="owner-uid",
                message="哈基咪帮我切歌",
                target_mode=2,
                music_summary="空闲",
            )
            self.assertIsNone(un_at_plan)
            llm_mock.assert_not_awaited()

            at_plan, _ = await brain.plan_ts_chat(
                speaker_name="线性流逝",
                ts_unique_id="owner-uid",
                message="@哈基咪 早啊",
                target_mode=2,
                music_summary="空闲",
            )
            self.assertIsNotNone(at_plan)
            assert at_plan is not None
            self.assertTrue(at_plan.should_reply)
            llm_mock.assert_awaited_once()
            self.assertIn("休息时段被@唤醒提示", llm_mock.await_args.kwargs["system_instruction"])

        # 2. 02:30 Shanghai (18:30 UTC): outside 04:00-08:00, opportunistic chime-in works with cooldown!
        brain._now_utc = lambda: datetime(2026, 11, 11, 18, 30, tzinfo=timezone.utc)
        brain._speaker_followup_until.clear()
        with (
            patch.object(brain, "resolve_speaker_and_state", AsyncMock(return_value=(fake_ctx, AffectSnapshot(circadian="凌晨修仙略困但还能接茬")))),
            patch.object(brain, "appraise_and_record_turn", AsyncMock()),
            patch.object(
                brain,
                "_call_structured_gemini",
                AsyncMock(
                    return_value=TsHachimiPlan(
                        transcript="大家夜宵吃啥",
                        should_reply=True,
                        spoken_reply="大半夜别放毒了，搞碗牛肉面。",
                        chat_reply="大半夜别放毒了，搞碗牛肉面。",
                    )
                ),
            ) as opp_mock,
        ):
            opp_plan, _ = await brain.plan_ts_chat(
                speaker_name="其他队友",
                ts_unique_id="teammate-uid",
                message="大家夜宵吃啥",
                target_mode=2,
                music_summary="空闲",
            )
            self.assertIsNotNone(opp_plan)
            assert opp_plan is not None
            self.assertTrue(opp_plan.should_reply)
            opp_mock.assert_awaited_once()
            self.assertIn("见机回复模式", opp_mock.await_args.kwargs["system_instruction"])

            # Immediate second un-@ message from another user hits 45s ambient cooldown
            opp_plan_2, _ = await brain.plan_ts_chat(
                speaker_name="路人乙",
                ts_unique_id="teammate-2",
                message="或者点个烧烤",
                target_mode=2,
                music_summary="空闲",
            )
            self.assertIsNone(opp_plan_2)
            self.assertEqual(opp_mock.await_count, 1)

    async def test_qq_music_bridge_plays_records_taste_and_plays_personal_playlist(self) -> None:
        session = main.new_session()
        try:
            local_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={}, headers={})

            # 1. QQ -> TS6 play song bridges playback, announces in TS, and records user music history/taste
            with (
                patch.object(main, "_resolve_netease_query_to_song", AsyncMock(return_value=("186016", "晴天", "周杰伦"))),
                patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(1, False))) as enq_mock,
                patch.object(main.hachimi, "record_user_music_play", AsyncMock()) as rec_mock,
                patch.object(main.voice, "send_notice", AsyncMock(return_value=True)) as notice_mock,
            ):
                res = await main.hachimi_music_bridge(
                    main.HachimiMusicBridgeRequest(
                        action="play",
                        query="周杰伦 晴天",
                        qq_sender_id="10159641",
                        sender_name="线性流逝",
                    ),
                    local_req,  # type: ignore[arg-type]
                    session,
                )
                self.assertTrue(res["ok"])
                self.assertIn("晴天", res["status_text"])
                enq_mock.assert_awaited_once()
                rec_mock.assert_awaited_once()
                self.assertEqual(rec_mock.await_args.kwargs["qq_sender_id"], "10159641")
                self.assertEqual(rec_mock.await_args.kwargs["source"], "qq")
                notice_mock.assert_awaited_once()

            # 2. Personal playlist playback enqueues user's saved tracks into TS6
            fake_profile = {
                "user_key": "10159641",
                "display_name": "老咪",
                "total_plays": 6,
                "taste_summary": "偏爱周杰伦华语流行",
                "top_artists": ["周杰伦"],
                "favorite_tracks": [],
                "saved_playlist": [
                    {"playlist_name": "默认歌单", "track_id": "netease:186016", "title": "晴天", "artist": "周杰伦"},
                    {"playlist_name": "默认歌单", "track_id": "netease:185811", "title": "夜曲", "artist": "周杰伦"},
                ],
            }
            with (
                patch.object(main.hachimi, "get_user_music_profile", AsyncMock(return_value=fake_profile)),
                patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(2, False))) as pl_enq_mock,
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_IDLE"))),
                patch.object(main.voice, "send_notice", AsyncMock(return_value=True)),
            ):
                pl_res = await main.hachimi_music_bridge(
                    main.HachimiMusicBridgeRequest(
                        action="play_user_playlist",
                        query="",
                        qq_sender_id="10159641",
                        sender_name="线性流逝",
                    ),
                    local_req,  # type: ignore[arg-type]
                    session,
                )
                self.assertTrue(pl_res["ok"])
                self.assertEqual(pl_res["added_count"], 2)
                self.assertEqual(pl_enq_mock.await_count, 2)
                # First song plays immediately since TS6 was STATE_IDLE, second queues
                self.assertTrue(pl_enq_mock.await_args_list[0].kwargs["play_now"])
                self.assertFalse(pl_enq_mock.await_args_list[1].kwargs["play_now"])
        finally:
            session.close()

    async def test_ts_auth_chain_multi_sa_and_hybrid_failover(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td)
            rt_dir = fake_home / "runtime"
            fb_dir = rt_dir / "gemini_key"
            fb_dir.mkdir(parents=True, exist_ok=True)
            sa1 = rt_dir / "gemini-service-account.json"
            sa1_data = {
                "type": "service_account",
                "project_id": "proj-primary",
                "private_key": "-----BEGIN PRIVATE KEY-----\nd1\n-----END PRIVATE KEY-----\n",
                "client_email": "bot@proj-primary.iam.gserviceaccount.com",
            }
            sa1.write_text(json.dumps(sa1_data), encoding="utf-8")
            # Duplicate first file + second distinct SA in fallback dir
            (fb_dir / "00_dup.json").write_text(json.dumps(sa1_data), encoding="utf-8")
            (fb_dir / "01_secondary.json").write_text(
                json.dumps(
                    {
                        "type": "service_account",
                        "project_id": "proj-secondary",
                        "private_key": "-----BEGIN PRIVATE KEY-----\nd2\n-----END PRIVATE KEY-----\n",
                        "client_email": "bot@proj-secondary.iam.gserviceaccount.com",
                    }
                ),
                encoding="utf-8",
            )
            (rt_dir / "local-secrets.json").write_text(
                json.dumps(
                    {
                        "gemini_service_account_file": str(sa1),
                        "gemini_service_account_fallback_dir": str(fb_dir),
                        "gemini_backup_api_keys": ["AQ.BackupExpressKey999999999999", "sk-never-include-openai"],
                    }
                ),
                encoding="utf-8",
            )
            brain = HachimiTsBrain(hachimi_home=fake_home)
            chain, mdl = brain._resolve_gemini_auth_chain()
            self.assertEqual(mdl, "gemini-4-argon")
            self.assertEqual(
                [c["label"] for c in chain],
                ["vertex_sa:proj-primary", "vertex_sa:proj-secondary", "backup_key_1"],
            )

            class _QuotaErr(Exception):
                code = 429
                status = "RESOURCE_EXHAUSTED"

            calls: list[tuple[str, str]] = []

            async def p_gen(*, model, contents, config=None):
                calls.append(("primary", model))
                if model.startswith("gemini-4"):
                    raise _QuotaErr("429 RESOURCE_EXHAUSTED")
                return SimpleNamespace(text='{"taste_summary":"华语流行"}')

            async def b_gen(*, model, contents, config=None):
                calls.append(("backup", model))
                return SimpleNamespace(
                    text='{"transcript":"切歌","should_reply":true,"spoken_reply":"好嘞","chat_reply":"好嘞","music_cmd":"skip","music_arg":""}'
                )

            p_cli = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=p_gen)))
            b_cli = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=b_gen)))
            brain._genai_types = SimpleNamespace(
                GenerateContentConfig=lambda **kw: kw,
                ThinkingConfig=lambda **kw: kw,
                ThinkingLevel=SimpleNamespace(LOW="LOW"),
            )
            brain._backends = [
                {
                    "label": "vertex_sa:proj-primary",
                    "kind": "vertex_sa",
                    "client": p_cli,
                    "cooldown_until": 0.0,
                    "model_cooldowns": {},
                    "model_strikes": {},
                    "probing_models": set(),
                },
                {
                    "label": "backup_key_1",
                    "kind": "api_key",
                    "client": b_cli,
                    "cooldown_until": 0.0,
                    "model_cooldowns": {},
                    "model_strikes": {},
                    "probing_models": set(),
                },
            ]
            brain._genai_client = p_cli
            brain._initial_genai_client = p_cli
            brain._warmup_started = True

            await brain.warmup_probe()
            calls.clear()

            from backend.hachimi_brain import UserTasteSummarySchema

            plan = await brain._call_structured_gemini(
                system_instruction="sys",
                contents=["切歌"],
                schema_cls=TsHachimiPlan,
            )
            self.assertEqual(plan.music_cmd, "skip")
            taste = await brain._call_structured_gemini(
                system_instruction="sys",
                contents=["taste"],
                schema_cls=UserTasteSummarySchema,
                model=brain._lite_model,
            )
            self.assertEqual(taste.taste_summary, "华语流行")
            self.assertEqual(
                calls,
                [("backup", "gemini-4-argon"), ("primary", "gemini-3.5-flash-lite")],
            )
            await brain.close()
