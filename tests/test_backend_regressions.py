import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import main
from backend.db import Base
from backend.managed_voice import validate_voice_audio
from backend.models import AppSetting, CustomVoice


class BilibiliAudioCacheTests(unittest.TestCase):
    def test_cache_lookup_removes_expired_audio_and_stale_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            audio_path = cache_dir / "BVEXPIRED.m4a"
            partial_path = cache_dir / "BVFAILED.m4s.part"
            audio_path.write_bytes(b"audio")
            partial_path.write_bytes(b"partial")

            old_timestamp = time.time() - 7200
            os.utime(audio_path, (old_timestamp, old_timestamp))
            os.utime(partial_path, (old_timestamp, old_timestamp))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 3600, create=True),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 0, create=True),
                patch.object(main, "BILIBILI_AUDIO_PARTIAL_TTL_SECONDS", 3600, create=True),
            ):
                cached = main._find_cached_bilibili_audio("BVEXPIRED")

            self.assertEqual("", cached)
            self.assertFalse(audio_path.exists())
            self.assertFalse(partial_path.exists())

    def test_cache_lookup_evicts_oldest_audio_when_size_limit_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            oldest_path = cache_dir / "BVOLDEST.m4a"
            newest_path = cache_dir / "BVNEWEST.m4a"
            oldest_path.write_bytes(b"a" * 700_000)
            newest_path.write_bytes(b"b" * 700_000)

            now = time.time()
            os.utime(oldest_path, (now - 120, now - 120))
            os.utime(newest_path, (now - 60, now - 60))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 0, create=True),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 1_000_000, create=True),
            ):
                main._find_cached_bilibili_audio("BVMISSING")

            self.assertFalse(oldest_path.exists())
            self.assertTrue(newest_path.exists())

    def test_cache_lookup_does_not_evict_the_requested_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            requested_path = cache_dir / "BVREQUESTED.m4a"
            other_path = cache_dir / "BVOTHER.m4a"
            requested_path.write_bytes(b"a" * 700_000)
            other_path.write_bytes(b"b" * 700_000)

            now = time.time()
            os.utime(requested_path, (now - 120, now - 120))
            os.utime(other_path, (now - 60, now - 60))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 0),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 1_000_000),
            ):
                cached = main._find_cached_bilibili_audio("BVREQUESTED")

            self.assertEqual(str(requested_path.resolve()), cached)
            self.assertTrue(requested_path.exists())
            self.assertFalse(other_path.exists())


class AdminCookieStatusTests(unittest.TestCase):
    def test_encrypted_empty_cookie_is_not_reported_as_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-empty-cookie")

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "decrypt_text", return_value=""),
        ):
            result = main.admin_status(object(), session)

        self.assertFalse(result["admin_cookie_set"])

    def test_encrypted_empty_cookie_is_treated_as_not_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-empty-cookie")

        with patch.object(main, "decrypt_text", return_value=""):
            with self.assertRaises(HTTPException) as raised:
                main._get_admin_cookie(session)

        self.assertEqual(400, raised.exception.status_code)

    def test_metadata_only_cookie_is_treated_as_not_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-metadata-cookie")
        metadata_cookie = "NMTID=device-id; __csrf=csrf-token"

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "decrypt_text", return_value=metadata_cookie),
        ):
            status = main.admin_status(object(), session)
            with self.assertRaises(HTTPException) as raised:
                main._get_admin_cookie(session)

        self.assertFalse(status["admin_cookie_set"])
        self.assertEqual(400, raised.exception.status_code)

    def test_metadata_only_cookie_is_not_saved_manually(self) -> None:
        session = unittest.mock.Mock()
        metadata_cookie = "NMTID=device-id; __csrf=csrf-token"

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            self.assertRaises(HTTPException) as raised,
        ):
            main.admin_set_cookie(main.AdminCookieSetRequest(cookie=metadata_cookie), object(), session)

        self.assertEqual(400, raised.exception.status_code)
        set_secret.assert_not_called()


class NeteaseQrCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_success_without_core_auth_cookie_is_rejected(self) -> None:
        qr_response = {
            "code": 803,
            "cookie": "NMTID=device-id; __csrf=csrf-token; MUSIC_SNS=",
        }

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            patch.object(main.netease, "qr_check", AsyncMock(return_value=qr_response)),
        ):
            result = await main.admin_qr_check("qr-key", object(), object())

        self.assertEqual(803, result["code"])
        self.assertFalse(result["admin_cookie_set"])
        set_secret.assert_not_called()

    async def test_qr_cookie_keeps_valid_auth_value_when_later_duplicate_is_empty(self) -> None:
        qr_response = {
            "code": 803,
            "cookie": (
                "MUSIC_U=valid-token; Path=/;;"
                "MUSIC_U=; Max-Age=0; Path=/; __csrf=csrf-token"
            ),
        }

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            patch.object(main.netease, "qr_check", AsyncMock(return_value=qr_response)),
        ):
            result = await main.admin_qr_check("qr-key", object(), object())

        self.assertTrue(result["admin_cookie_set"])
        set_secret.assert_called_once_with(
            unittest.mock.ANY,
            "netease_cookie",
            "MUSIC_U=valid-token; __csrf=csrf-token",
        )


class TsChatCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main._ts_playlist_results.clear()

    async def test_playlist_search_then_select_enqueues_netease_tracks(self) -> None:
        search_result = {
            "result": {
                "playlists": [
                    {
                        "id": 123,
                        "name": "测试歌单",
                        "creator": {"nickname": "创建者"},
                        "trackCount": 2,
                    }
                ]
            }
        }
        tracks = [
            {"id": 1, "name": "歌曲一", "ar": [{"name": "歌手一"}], "al": {"name": "专辑一"}},
            {"id": 2, "name": "歌曲二", "ar": [{"name": "歌手二"}], "al": {"name": "专辑二"}},
        ]

        with (
            patch.object(main.netease, "search", AsyncMock(return_value=search_result)) as search,
            patch.object(main.netease, "playlist_detail", AsyncMock(return_value={"playlist": {"name": "测试歌单"}})),
            patch.object(main, "_load_netease_playlist_tracks", AsyncMock(return_value=("测试歌单", tracks))),
            patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(1, False))) as enqueue,
            patch.object(main, "_get_admin_cookie_or_none", return_value="cookie"),
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Alice", "playlist 测试", invoker_unique_id="alice-uid")
            await main._handle_chat_command("Alice", "select 1", invoker_unique_id="alice-uid")

        search.assert_awaited_once_with(keywords="测试", limit=5, type_=1000)
        self.assertEqual(2, enqueue.await_count)
        self.assertEqual("1", enqueue.await_args_list[0].kwargs["song_id"])
        self.assertEqual("2", enqueue.await_args_list[1].kwargs["song_id"])
        self.assertIn("使用 select <编号>", notice.await_args_list[0].args[0])
        self.assertIn("已从歌单《测试歌单》加入 2 首歌曲", notice.await_args_list[1].args[0])

    async def test_playlist_id_command_accepts_numeric_id_and_batches_tracks(self) -> None:
        tracks = [
            {"id": 11, "name": "歌曲一", "ar": [{"name": "歌手一"}]},
            {"id": 12, "name": "歌曲二", "ar": [{"name": "歌手二"}]},
        ]

        with (
            patch.object(main.netease, "playlist_detail", AsyncMock(return_value={"playlist": {"name": "直达歌单"}})) as detail,
            patch.object(main, "_load_netease_playlist_tracks", AsyncMock(return_value=("直达歌单", tracks))),
            patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(1, False))) as enqueue,
            patch.object(main, "_get_admin_cookie_or_none", return_value="cookie"),
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Alice", "!歌单ID 123456", invoker_unique_id="alice-uid")

        detail.assert_awaited_once_with(playlist_id="123456", cookie="cookie")
        self.assertEqual(2, enqueue.await_count)
        self.assertEqual(["11", "12"], [call.kwargs["song_id"] for call in enqueue.await_args_list])
        self.assertIn("已从歌单《直达歌单》加入 2 首歌曲", notice.await_args.args[0])

    async def test_playlist_id_command_accepts_standard_netease_url(self) -> None:
        tracks = [{"id": 21, "name": "链接歌曲", "ar": []}]

        with (
            patch.object(main.netease, "playlist_detail", AsyncMock(return_value={"playlist": {"name": "链接歌单"}})) as detail,
            patch.object(main, "_load_netease_playlist_tracks", AsyncMock(return_value=("链接歌单", tracks))),
            patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(1, False))),
            patch.object(main, "_get_admin_cookie_or_none", return_value=None),
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()),
        ):
            await main._handle_chat_command(
                "Alice",
                "歌单ID https://music.163.com/#/playlist?id=654321",
                invoker_unique_id="alice-uid",
            )

        detail.assert_awaited_once_with(playlist_id="654321", cookie=None)

    async def test_playlist_id_command_rejects_invalid_input_without_api_call(self) -> None:
        with (
            patch.object(main.netease, "playlist_detail", AsyncMock()) as detail,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Alice", "歌单ID https://example.com/playlist?id=123")

        detail.assert_not_awaited()
        self.assertIn("歌单 ID 或链接无效", notice.await_args.args[0])

    def test_netease_playlist_id_parser_supports_id_and_standard_urls_only(self) -> None:
        self.assertEqual("123456", main._try_parse_netease_playlist_id("123456"))
        self.assertEqual(
            "123456",
            main._try_parse_netease_playlist_id("https://music.163.com/playlist?id=123456"),
        )
        self.assertEqual(
            "123456",
            main._try_parse_netease_playlist_id("https://music.163.com/#/playlist?id=123456"),
        )
        self.assertIsNone(main._try_parse_netease_playlist_id("https://example.com/playlist?id=123456"))
        self.assertIsNone(main._try_parse_netease_playlist_id("abc123"))

    async def test_playlist_resolve_endpoint_returns_tracks_for_web_input(self) -> None:
        raw_detail = {"playlist": {"id": 7788, "name": "网页歌单", "trackIds": [{"id": 1}]}}
        tracks = [{"id": 1, "name": "网页歌曲"}]

        with (
            patch.object(main.netease, "playlist_detail", AsyncMock(return_value=raw_detail)) as detail,
            patch.object(main, "_load_netease_playlist_tracks", AsyncMock(return_value=("网页歌单", tracks))),
            patch.object(main, "_get_admin_cookie_or_none", return_value="cookie"),
        ):
            result = await main.netease_playlist_resolve("7788")

        detail.assert_awaited_once_with("7788", cookie="cookie")
        self.assertEqual("7788", result["playlist_id"])
        self.assertEqual(tracks, result["playlist"]["tracks"])

    async def test_playlist_resolve_endpoint_rejects_invalid_input(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await main.netease_playlist_resolve("https://example.com/playlist?id=7788")

        self.assertEqual(400, raised.exception.status_code)


class TsLyricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ts_lyrics_command_can_interrupt_while_playing(self) -> None:
        with (
            patch.object(main, "update_settings") as update,
            patch.object(main, "_schedule_ts_lyrics_update") as schedule,
            patch.object(
                main.voice,
                "get_status",
                AsyncMock(return_value=SimpleNamespace(
                    state="STATE_PLAYING",
                    now_playing_title="测试歌曲",
                )),
            ) as status,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Alice", "歌词 中断")

        update.assert_called_once_with(
            unittest.mock.ANY,
            {"voice.ts_lyrics_enabled": False},
            apply=True,
        )
        schedule.assert_called_once_with(None)
        status.assert_awaited_once_with()
        self.assertIn("歌词已中断", notice.await_args.args[0])
        self.assertIn("当前正在播放：测试歌曲", notice.await_args.args[0])

    async def test_ts_lyrics_command_can_resume_when_stopped(self) -> None:
        with (
            patch.object(main, "update_settings") as update,
            patch.object(main, "_reschedule_ts_lyrics_from_current", AsyncMock()) as reschedule,
            patch.object(
                main.voice,
                "get_status",
                AsyncMock(return_value=SimpleNamespace(
                    state="STATE_IDLE",
                    now_playing_title="",
                )),
            ),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Bob", "!歌词 播放")

        update.assert_called_once_with(
            unittest.mock.ANY,
            {"voice.ts_lyrics_enabled": True},
            apply=True,
        )
        reschedule.assert_awaited_once_with()
        self.assertIn("歌词已开启", notice.await_args.args[0])
        self.assertIn("当前没有正在播放的歌曲", notice.await_args.args[0])

    async def test_ts_lyrics_command_reports_usage_for_unknown_action(self) -> None:
        with patch.object(main.voice, "send_notice", AsyncMock()) as notice:
            await main._handle_chat_command("Alice", "歌词 状态")

        self.assertEqual("用法: 歌词 播放 / 歌词 中断", notice.await_args.args[0])

    async def test_setting_now_playing_starts_and_clearing_it_cancels_lyrics_generation(self) -> None:
        with (
            patch.object(main, "_schedule_ts_description_update"),
            patch.object(main, "_schedule_ts_profile_update"),
            patch.object(main, "_schedule_ts_lyrics_update") as schedule,
        ):
            await main._set_now_playing_queue_item(42, "netease://song", track_id="netease:42", title="测试")
            await main._set_now_playing_queue_item(None)

        self.assertEqual([call(42), call(None)], schedule.call_args_list)

    def test_seek_position_uses_last_line_at_or_before_new_position(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="零"),
            main.LyricLine(time=1.0, text="一"),
            main.LyricLine(time=2.0, text="二"),
        ]
        self.assertEqual(1, main._find_ts_lyrics_index(lines, 1.25))
        self.assertEqual(0, main._find_ts_lyrics_index(lines, 0.0))
        self.assertEqual(2, main._find_ts_lyrics_index(lines, 5.0))

    def test_paused_playback_position_does_not_advance(self) -> None:
        self.assertAlmostEqual(
            10.0,
            main._resolve_playback_position_s(
                now_s=20.0,
                started_at=0.0,
                paused_at=12.0,
                paused_total_s=2.0,
            ),
        )

    async def test_lyrics_clock_stays_frozen_while_playback_is_paused(self) -> None:
        old_values = (
            main._current_queue_item_id,
            main._current_source_url,
            main._play_started_at,
            main._paused_at,
            main._paused_total_s,
        )
        try:
            main._current_queue_item_id = 42
            main._current_source_url = "netease://song"
            main._play_started_at = 100.0
            main._paused_at = 110.0
            main._paused_total_s = 5.0
            with patch.object(main.time, "monotonic", return_value=999.0):
                position = await main._get_ts_lyrics_playback_position(42)
        finally:
            (
                main._current_queue_item_id,
                main._current_source_url,
                main._play_started_at,
                main._paused_at,
                main._paused_total_s,
            ) = old_values

        self.assertAlmostEqual(5.0, position or 0.0)

    async def test_pause_failure_restores_lyrics_clock_state(self) -> None:
        old_values = (
            main._current_queue_item_id,
            main._current_source_url,
            main._play_started_at,
            main._pending_playback_position_s,
            main._paused_at,
            main._paused_total_s,
        )
        try:
            main._current_queue_item_id = 42
            main._current_source_url = "netease://song"
            main._play_started_at = 100.0
            main._pending_playback_position_s = None
            main._paused_at = None
            main._paused_total_s = 0.0
            with (
                patch.object(main.time, "monotonic", return_value=110.0),
                patch.object(main, "_schedule_ts_description_update"),
                patch.object(main, "_schedule_ts_lyrics_update"),
                patch.object(main.voice, "pause", AsyncMock(side_effect=RuntimeError("voice offline"))),
            ):
                with self.assertRaisesRegex(RuntimeError, "voice offline"):
                    await main.voice_pause()

            self.assertEqual(100.0, main._play_started_at)
            self.assertIsNone(main._paused_at)
            self.assertEqual(0.0, main._paused_total_s)
        finally:
            (
                main._current_queue_item_id,
                main._current_source_url,
                main._play_started_at,
                main._pending_playback_position_s,
                main._paused_at,
                main._paused_total_s,
            ) = old_values

    async def test_resume_failure_restores_lyrics_clock_state(self) -> None:
        old_values = (
            main._current_queue_item_id,
            main._current_source_url,
            main._play_started_at,
            main._pending_playback_position_s,
            main._paused_at,
            main._paused_total_s,
        )
        try:
            main._current_queue_item_id = 42
            main._current_source_url = "netease://song"
            main._play_started_at = 100.0
            main._pending_playback_position_s = None
            main._paused_at = 110.0
            main._paused_total_s = 3.0
            with (
                patch.object(main.time, "monotonic", return_value=115.0),
                patch.object(main, "_schedule_ts_description_update"),
                patch.object(main, "_schedule_ts_lyrics_update"),
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PAUSED"))),
                patch.object(main.voice, "resume", AsyncMock(side_effect=RuntimeError("voice offline"))),
            ):
                with self.assertRaisesRegex(RuntimeError, "voice offline"):
                    await main.voice_play()

            self.assertEqual(100.0, main._play_started_at)
            self.assertEqual(110.0, main._paused_at)
            self.assertEqual(3.0, main._paused_total_s)
        finally:
            (
                main._current_queue_item_id,
                main._current_source_url,
                main._play_started_at,
                main._pending_playback_position_s,
                main._paused_at,
                main._paused_total_s,
            ) = old_values

    async def test_timestamped_lyrics_are_sent_to_current_channel_at_playback_position(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="第一句"),
            main.LyricLine(time=1.0, text="第二句"),
        ]

        with (
            patch.object(main, "_ts_lyrics_generation", 7),
            patch.object(main, "_get_ts_lyrics_playback_position", AsyncMock(side_effect=[0.0, 0.25, 0.5, 0.75, 1.0])),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._run_ts_timestamped_lyrics(42, 7, lines)

        self.assertEqual(
            [
                (("♪ 第一句",), {"target_mode": 2}),
                (("♪ 第二句",), {"target_mode": 2}),
            ],
            [(call.args, call.kwargs) for call in notice.await_args_list],
        )

    async def test_timestamp_trigger_lateness_stays_within_300ms_in_clock_fixture(self) -> None:
        target_time = 1.0
        observed_positions = [0.75, 1.2]
        with (
            patch.object(main, "_ts_lyrics_generation", 71),
            patch.object(main, "_get_ts_lyrics_playback_position", AsyncMock(side_effect=observed_positions)),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._run_ts_timestamped_lyrics(
                42,
                71,
                [main.LyricLine(time=target_time, text="时间点歌词")],
            )

        self.assertEqual(1, notice.await_count)
        self.assertLessEqual(observed_positions[-1] - target_time, 0.3)

    async def test_duplicate_adjacent_lyrics_are_not_sent_twice(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="重复"),
            main.LyricLine(time=0.0, text="重复"),
            main.LyricLine(time=1.0, text="下一句"),
        ]
        with (
            patch.object(main, "_ts_lyrics_generation", 8),
            patch.object(main, "_get_ts_lyrics_playback_position", AsyncMock(side_effect=[0.0, 0.0, 0.25, 0.5, 0.75, 1.1])),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._run_ts_timestamped_lyrics(42, 8, lines)

        self.assertEqual(["♪ 重复", "♪ 下一句"], [call.args[0] for call in notice.await_args_list])

    async def test_late_lyrics_load_does_not_burst_send_past_lines(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="已过去"),
            main.LyricLine(time=1.0, text="也已过去"),
            main.LyricLine(time=2.0, text="未来一句"),
        ]
        with (
            patch.object(main, "_ts_lyrics_generation", 10),
            patch.object(main, "_get_ts_lyrics_playback_position", AsyncMock(side_effect=[1.2, 2.0, 2.0])),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._run_ts_timestamped_lyrics(42, 10, lines)

        self.assertEqual(["♪ 也已过去", "♪ 未来一句"], [call.args[0] for call in notice.await_args_list])

    async def test_seek_replays_same_text_after_reposition(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="副歌重复"),
            main.LyricLine(time=1.0, text="下一句"),
        ]
        with (
            patch.object(main, "_ts_lyrics_generation", 101),
            patch.object(
                main,
                "_get_ts_lyrics_playback_position",
                AsyncMock(side_effect=[0.0, 0.3, 0.3, 0.0, 0.0, 1.0]),
            ),
            patch.object(main.asyncio, "sleep", AsyncMock()),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._run_ts_timestamped_lyrics(42, 101, lines)

        self.assertEqual(
            ["♪ 副歌重复", "♪ 副歌重复", "♪ 下一句"],
            [call.args[0] for call in notice.await_args_list],
        )

    async def test_playback_started_event_starts_clock_and_lyrics_worker(self) -> None:
        old_values = (
            main._current_queue_item_id,
            main._current_source_url,
            main._play_started_at,
            main._pending_playback_position_s,
            main._paused_at,
            main._paused_total_s,
        )
        try:
            main._current_queue_item_id = 42
            main._current_source_url = "netease://song"
            main._play_started_at = None
            main._pending_playback_position_s = 0.0
            main._paused_at = None
            main._paused_total_s = 0.0
            with (
                patch.object(main.time, "monotonic", return_value=123.0),
                patch.object(main, "_schedule_ts_description_update"),
                patch.object(main, "_schedule_ts_lyrics_update") as schedule,
            ):
                item_id = await main._mark_playback_started("netease://song")
        finally:
            (
                main._current_queue_item_id,
                main._current_source_url,
                main._play_started_at,
                main._pending_playback_position_s,
                main._paused_at,
                main._paused_total_s,
            ) = old_values

        self.assertEqual(42, item_id)
        self.assertEqual([call(42)], schedule.call_args_list)

    async def test_seek_waits_for_next_real_start_event_before_advancing_clock(self) -> None:
        old_values = (
            main._current_queue_item_id,
            main._current_source_url,
            main._play_started_at,
            main._pending_playback_position_s,
            main._paused_at,
            main._paused_total_s,
        )
        try:
            main._current_queue_item_id = 42
            main._current_source_url = "netease://song"
            main._play_started_at = 500.0
            main._pending_playback_position_s = None
            main._paused_at = None
            main._paused_total_s = 0.0
            with patch.object(main, "_schedule_ts_description_update"):
                await main._mark_playback_seeked(37.5)

            self.assertIsNone(main._play_started_at)
            self.assertEqual(37.5, main._pending_playback_position_s)
            with patch.object(main.time, "monotonic", return_value=1000.0), patch.object(main, "_schedule_ts_lyrics_update"):
                await main._mark_playback_started("netease://song")
            self.assertAlmostEqual(962.5, main._play_started_at or 0.0)
        finally:
            (
                main._current_queue_item_id,
                main._current_source_url,
                main._play_started_at,
                main._pending_playback_position_s,
                main._paused_at,
                main._paused_total_s,
            ) = old_values

    async def test_lyrics_send_retries_transient_voice_failure(self) -> None:
        lines = [main.LyricLine(time=0.0, text="重试")]
        with (
            patch.object(main, "_ts_lyrics_generation", 9),
            patch.object(main, "_get_ts_lyrics_playback_position", AsyncMock(side_effect=[0.0, 0.0, 0.0])),
            patch.object(main.voice, "send_notice", AsyncMock(side_effect=[RuntimeError("offline"), RuntimeError("offline"), None])) as notice,
        ):
            await main._run_ts_timestamped_lyrics(42, 9, lines)

        self.assertEqual(3, notice.await_count)

    async def test_plain_lyrics_are_sent_as_one_message(self) -> None:
        response = main.LyricsResponse(lyrics=[], plain_lyrics="第一行\n第二行")

        with (
            patch.object(main, "_ts_lyrics_generation", 11),
            patch.object(main, "_load_queue_item_lyrics", AsyncMock(return_value=response)),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._ts_lyrics_worker(42, 11)

        notice.assert_awaited_once_with("♪ 第一行\n第二行", target_mode=2)

    async def test_plain_lyrics_retry_until_ts_recovers(self) -> None:
        response = main.LyricsResponse(lyrics=[], plain_lyrics="断线歌词")
        with (
            patch.object(main, "_ts_lyrics_generation", 13),
            patch.object(main, "_load_queue_item_lyrics", AsyncMock(return_value=response)),
            patch.object(
                main.voice,
                "send_notice",
                AsyncMock(side_effect=[RuntimeError("offline")] * 3 + [None]),
            ) as notice,
            patch.object(main.asyncio, "sleep", AsyncMock()),
        ):
            await main._ts_lyrics_worker(42, 13)

        self.assertEqual(4, notice.await_count)
        self.assertEqual("♪ 断线歌词", notice.await_args.args[0])

    async def test_lyrics_schedule_honors_web_toggle(self) -> None:
        old_task = main._ts_lyrics_task
        old_generation = main._ts_lyrics_generation
        try:
            main._ts_lyrics_task = None
            with (
                patch.object(main, "_runtime_bool", return_value=False),
                patch.object(main.asyncio, "create_task") as create_task,
            ):
                main._schedule_ts_lyrics_update(42)
            create_task.assert_not_called()
        finally:
            main._ts_lyrics_task = old_task
            main._ts_lyrics_generation = old_generation

    async def test_stale_lyrics_worker_does_not_send_after_track_change(self) -> None:
        response = main.LyricsResponse(
            lyrics=[main.LyricLine(time=0.0, text="旧歌曲歌词")],
        )

        with (
            patch.object(main, "_ts_lyrics_generation", 12),
            patch.object(main, "_load_queue_item_lyrics", AsyncMock(return_value=response)),
            patch.object(main, "_run_ts_timestamped_lyrics", AsyncMock()) as run_lines,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._ts_lyrics_worker(42, 11)

        run_lines.assert_not_awaited()
        notice.assert_not_awaited()

    def test_lrc_parser_prefers_timestamped_primary_lyrics_and_keeps_plain_fallback(self) -> None:
        timestamped, plain = main._parse_lrc_content("[00:01.20]主歌词\n[tlyric:翻译]")
        self.assertEqual([main.LyricLine(time=1.2, text="主歌词")], timestamped)
        self.assertEqual("", plain)

        timestamped, plain = main._parse_lrc_content("第一行\n第二行")
        self.assertEqual([], timestamped)
        self.assertEqual("第一行\n第二行", plain)

    def test_timestamped_primary_and_translation_are_merged_on_primary_clock(self) -> None:
        primary, _ = main._parse_lrc_content("[00:01.20]I love you\n[00:03.00]Goodbye")
        translated, _ = main._parse_lrc_content("[00:01.24]我爱你\n[00:03.40]再见")

        merged = main._merge_timestamped_lyrics(primary, translated)

        self.assertEqual(
            [
                main.LyricLine(time=1.2, text="I love you\n译：我爱你"),
                main.LyricLine(time=3.0, text="Goodbye\n译：再见"),
            ],
            merged,
        )

    def test_plain_primary_and_translation_are_merged_line_by_line(self) -> None:
        self.assertEqual(
            "I love you\n译：我爱你\nGoodbye\n译：再见",
            main._merge_plain_lyrics("I love you\nGoodbye", "我爱你\n再见"),
        )

    def test_plain_lyrics_fallback_is_shared_by_timestamped_sources(self) -> None:
        timestamped, plain = main._parse_lrc_content("[00:01.20]主歌词")
        self.assertEqual([main.LyricLine(time=1.2, text="主歌词")], timestamped)
        self.assertEqual("", plain)

        timestamped, plain = main._parse_lrc_content("QQ 主歌词第一行\nQQ 主歌词第二行")
        self.assertEqual([], timestamped)
        self.assertEqual("QQ 主歌词第一行\nQQ 主歌词第二行", plain)

    def test_lrc_metadata_and_translation_only_response_is_silent(self) -> None:
        timestamped, plain = main._parse_lrc_content(
            "[ar:歌手]\n[ti:歌曲]\n[tlyric:翻译]\n[romalrc:拼音]",
        )
        self.assertEqual([], timestamped)
        self.assertEqual("", plain)

    async def test_qq_lyrics_endpoint_returns_plain_fallback(self) -> None:
        queue_session = unittest.mock.Mock()
        queue_session.get.return_value = SimpleNamespace(
            track_id="qqmusic:mid-123",
            title="QQ 歌曲",
            artist="歌手",
        )
        cookie_session = unittest.mock.Mock()
        with (
            patch.object(main, "new_session", side_effect=[queue_session, cookie_session]),
            patch.object(main, "_get_admin_qqmusic_cookie", return_value=""),
            patch.object(main.qqmusic, "get_song_lyric", AsyncMock(return_value={"lyric": "第一行\n第二行"})),
        ):
            result = await main.lyrics(42)

        self.assertEqual([], result.lyrics)
        self.assertEqual("第一行\n第二行", result.plain_lyrics)

    def test_ts_lyrics_are_truncated_to_one_safe_message(self) -> None:
        text = main._truncate_ts_lyrics("歌词" * 400)
        self.assertLessEqual(len(text), 700)
        self.assertLessEqual(len(main._TS_LYRICS_PREFIX + text), main._TS_LYRICS_MAX_MESSAGE_CHARS)
        self.assertTrue(text.endswith("..."))


class CustomVoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_voice_gain_uses_independent_runtime_volume(self) -> None:
        with patch.object(main, "_runtime_int", return_value=180):
            self.assertEqual(1.8, main._custom_voice_gain())
        with patch.object(main, "_runtime_int", return_value=999):
            self.assertEqual(3.0, main._custom_voice_gain())
        with patch.object(main, "_runtime_int", return_value=-10):
            self.assertEqual(0.0, main._custom_voice_gain())

    def test_custom_voice_command_rejects_builtins_and_spaces(self) -> None:
        self.assertEqual("鼓掌", main._normalize_custom_voice_command("!鼓掌"))
        with self.assertRaises(ValueError):
            main._normalize_custom_voice_command("播放")
        with self.assertRaises(ValueError):
            main._normalize_custom_voice_command("欢迎 语音")

    def test_custom_voice_audio_validation_accepts_supported_formats(self) -> None:
        self.assertEqual(".mp3", validate_voice_audio(b"audio", filename="welcome.mp3", content_type="audio/mpeg"))
        self.assertEqual(".wav", validate_voice_audio(b"audio", filename="welcome.wav", content_type="audio/wav"))
        with self.assertRaises(ValueError):
            validate_voice_audio(b"audio", filename="welcome.txt", content_type="text/plain")

    async def test_custom_voice_mixes_over_active_music(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "welcome.mp3"
            audio.write_bytes(b"audio")
            row = SimpleNamespace(stored_path=str(audio), label="欢迎语", command="欢迎")
            with (
                patch.object(main, "_find_custom_voice", return_value=row),
                patch.object(main, "_custom_voice_gain", return_value=1.5),
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
                patch.object(main.voice, "play_overlay", AsyncMock(return_value=True)) as overlay,
            ):
                result = await main._trigger_custom_voice("欢迎", invoker_name="Alice")

        self.assertIn("背景音量降低 12 dB", result)
        overlay.assert_awaited_once_with(
            source_url=str(audio),
            title="欢迎语",
            requested_by="Alice",
            duck_db=12.0,
            voice_gain=1.5,
        )

    async def test_custom_voice_plays_directly_when_music_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "beep.wav"
            audio.write_bytes(b"audio")
            row = SimpleNamespace(stored_path=str(audio), label="提示音", command="提示")
            with (
                patch.object(main, "_find_custom_voice", return_value=row),
                patch.object(main, "_custom_voice_gain", return_value=1.5),
                patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_IDLE"))),
                patch.object(main.voice, "play", AsyncMock()) as play,
            ):
                result = await main._trigger_custom_voice("提示", invoker_name="Bob")

        self.assertIn("正在播放内置语音", result)
        play.assert_awaited_once_with(
            source_url=str(audio),
            title="提示音",
            requested_by="Bob",
            voice_gain=1.5,
            independent_volume=True,
        )

    async def test_disabled_custom_voice_is_not_played(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "disabled.wav"
            audio.write_bytes(b"audio")
            row = SimpleNamespace(stored_path=str(audio), label="停用提示", command="提示")
            with (
                patch.object(main, "_find_custom_voice", return_value=row),
                patch.object(main, "_custom_voice_is_enabled", return_value=False),
                patch.object(main.voice, "get_status", AsyncMock()) as status,
                patch.object(main.voice, "play", AsyncMock()) as play,
            ):
                result = await main._trigger_custom_voice("提示", invoker_name="Bob")

        self.assertIn("内置语音已停用", result)
        status.assert_not_awaited()
        play.assert_not_awaited()

    def test_custom_voice_enabled_setting_round_trips(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            row = CustomVoice(command="提示", label="提示音", stored_path="/tmp/prompt.wav")
            session.add(row)
            session.commit()
            self.assertTrue(main._custom_voice_is_enabled(row, session=session))

            main._set_custom_voice_enabled(session, row.id, False)
            session.commit()
            self.assertFalse(main._custom_voice_is_enabled(row, session=session))
            self.assertEqual("0", session.get(AppSetting, main._custom_voice_setting_key(row.id)).value)

            main._set_custom_voice_enabled(session, row.id, True)
            session.commit()
            self.assertTrue(main._custom_voice_is_enabled(row, session=session))
        finally:
            session.close()
            engine.dispose()

    def test_custom_voice_volume_setting_is_per_voice_and_clamped(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            first = CustomVoice(command="提示", label="提示音", stored_path="/tmp/prompt.wav")
            second = CustomVoice(command="欢迎", label="欢迎音", stored_path="/tmp/welcome.wav")
            session.add_all([first, second])
            session.commit()

            with patch.object(main, "_runtime_int", return_value=150):
                self.assertEqual(150, main._custom_voice_volume_percent(first, session=session))
            main._set_custom_voice_volume(session, first.id, 240)
            main._set_custom_voice_volume(session, second.id, -20)
            session.commit()

            self.assertEqual(240, main._custom_voice_volume_percent(first, session=session))
            self.assertEqual(0, main._custom_voice_volume_percent(second, session=session))
            self.assertEqual("240", session.get(AppSetting, main._custom_voice_volume_setting_key(first.id)).value)
            with patch.object(main, "_custom_voice_volume_percent", return_value=240):
                self.assertEqual(2.4, main._custom_voice_gain(first))
        finally:
            session.close()
            engine.dispose()

    async def test_ordinary_user_can_trigger_custom_voice_from_ts_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clap.ogg"
            audio.write_bytes(b"audio")
            row = SimpleNamespace(stored_path=str(audio), label="掌声", command="掌声")
            with (
                patch.object(main, "_find_custom_voice", return_value=row),
                patch.object(main, "_trigger_custom_voice", AsyncMock(return_value="正在播放内置语音：掌声")) as trigger,
                patch.object(main.voice, "send_notice", AsyncMock()) as notice,
            ):
                await main._handle_chat_command("普通用户", "!掌声")

        trigger.assert_awaited_once_with("掌声", invoker_name="普通用户")
        self.assertIn("正在播放内置语音：掌声", notice.await_args.args[0])


class WelcomeVoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_welcome_event_requires_user_and_bot_to_be_in_configured_channel(self) -> None:
        self.assertTrue(main._welcome_event_matches_target(channel_id=123, bot_channel_id=123, target_channel_id="123"))
        self.assertFalse(main._welcome_event_matches_target(channel_id=456, bot_channel_id=123, target_channel_id="123"))
        self.assertFalse(main._welcome_event_matches_target(channel_id=123, bot_channel_id=456, target_channel_id="123"))
        self.assertTrue(main._welcome_event_matches_target(channel_id=123, bot_channel_id=123, target_channel_id=""))
        self.assertFalse(main._welcome_event_matches_target(channel_id=0, bot_channel_id=0, target_channel_id=""))

    def test_welcome_voice_has_global_and_per_user_cooldowns(self) -> None:
        with patch.object(main, "_runtime_int", return_value=10):
            main._welcome_global_last_played_at = 0.0
            main._welcome_user_last_played_at.clear()
            self.assertTrue(main._welcome_cooldown_allows("uid-a"))
            self.assertFalse(main._welcome_cooldown_allows("uid-a"))
            self.assertFalse(main._welcome_cooldown_allows("uid-b"))

    async def test_user_joined_in_target_channel_triggers_global_welcome_voice(self) -> None:
        row = SimpleNamespace(label="全局欢迎", command="welcome")
        event = SimpleNamespace(channel_id=123, bot_channel_id=123, name="Alice")
        with (
            patch.object(main, "_get_welcome_channel_id", return_value="123"),
            patch.object(main, "_find_welcome_voice", return_value=row),
            patch.object(main, "_trigger_custom_voice_row", AsyncMock(return_value="正在播放内置语音：全局欢迎")) as trigger,
        ):
            await main._handle_user_joined(event)

        trigger.assert_awaited_once_with(row, invoker_name="welcome:Alice")

    async def test_user_joined_is_skipped_when_bot_is_not_in_target_channel(self) -> None:
        event = SimpleNamespace(channel_id=123, bot_channel_id=456, name="Bob")
        with (
            patch.object(main, "_get_welcome_channel_id", return_value="123"),
            patch.object(main, "_find_welcome_voice") as find_voice,
        ):
            await main._handle_user_joined(event)

        find_voice.assert_not_called()


class DynamicNeteaseProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_ts_reconnect_refreshes_current_dynamic_profile(self) -> None:
        with patch.object(main, "_refresh_ts_profile_from_current", AsyncMock()) as refresh:
            await main._handle_voice_log_event("ts3 connected")
            await main._handle_voice_log_event("ts3 disconnected")

        refresh.assert_awaited_once_with()

    async def test_netease_dynamic_nickname_is_optional_and_only_runs_for_netease(self) -> None:
        main._ts_dynamic_nickname_active = False
        with (
            patch.object(main, "_runtime_bool", return_value=True),
            patch.object(main.voice, "set_client_nickname", AsyncMock()) as nickname,
        ):
            main._schedule_ts_profile_update(track_id="netease:123", title="歌曲名", artwork_url="")
            await asyncio.sleep(0.25)
        nickname.assert_awaited_once_with("歌曲名")

        with (
            patch.object(main.voice, "set_client_nickname", AsyncMock()) as non_netease_nickname,
            patch.object(main, "_runtime_string", return_value="tsbot"),
        ):
            main._schedule_ts_profile_update(track_id="bilibili:BV1", title="视频", artwork_url="")
            await asyncio.sleep(0.25)
        non_netease_nickname.assert_awaited_once_with("tsbot")

    async def test_short_netease_title_adds_artist_for_ts_nickname_minimum(self) -> None:
        main._ts_dynamic_nickname_active = False
        with (
            patch.object(main, "_runtime_bool", return_value=True),
            patch.object(main.voice, "set_client_nickname", AsyncMock()) as nickname,
        ):
            main._schedule_ts_profile_update(
                track_id="netease:190381",
                title="水手",
                artist="郑智化",
                artwork_url="",
            )
            await asyncio.sleep(0.25)

        nickname.assert_awaited_once_with("水手 - 郑智化")

    async def test_dynamic_avatar_downloads_supported_image_and_queues_voice_upload(self) -> None:
        class FakeResponse:
            content = b"\x89PNG\r\n\x1a\nimage"

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(main, "TS_DYNAMIC_AVATAR_DIR", Path(tmp)),
                patch.object(main.httpx, "AsyncClient", return_value=FakeClient()),
                patch.object(main.voice, "set_client_avatar", AsyncMock(return_value=True)) as avatar,
            ):
                main._ts_profile_generation += 1
                await main._update_ts_dynamic_avatar("https://example.test/cover.jpg", generation=main._ts_profile_generation)

            avatar.assert_awaited_once()
            uploaded_path = Path(avatar.await_args.args[0])
            self.assertTrue(uploaded_path.is_file())
            self.assertEqual(b"\x89PNG\r\n\x1a\nimage", uploaded_path.read_bytes())

    async def test_play_without_argument_plays_first_queue_item(self) -> None:
        row = SimpleNamespace(id=42, title="队首歌曲", artist="歌手")
        session = unittest.mock.Mock()
        session.execute.return_value.scalars.return_value.first.return_value = row

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_play_queue_item_internal", AsyncMock(return_value=True)) as play_item,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Bob", "play")

        session.close.assert_called_once_with()
        play_item.assert_awaited_once_with(42, requested_by="Bob")
        self.assertIn("已播放队列第一首: 队首歌曲 - 歌手", notice.await_args.args[0])

    async def test_random_and_order_commands_switch_shuffle_mode(self) -> None:
        with (
            patch.object(main, "_set_shuffle_enabled", AsyncMock(return_value={"ok": True})) as shuffle,
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Carol", "随机播放")
            await main._handle_chat_command("Carol", "顺序播放")

        self.assertEqual([True, False], [call.args[0] for call in shuffle.await_args_list])
        self.assertIn("已切换为随机播放", notice.await_args_list[0].args[0])
        self.assertIn("已切换为顺序播放", notice.await_args_list[1].args[0])

    async def test_repeat_commands_switch_list_single_and_off_modes(self) -> None:
        previous_mode = main._repeat_mode
        try:
            with (
                patch.object(main, "_schedule_ts_description_update"),
                patch.object(main.voice, "send_notice", AsyncMock()) as notice,
            ):
                await main._handle_chat_command("Carol", "循环 列表")
                self.assertEqual("all", main._repeat_mode)
                self.assertIn("已切换为列表循环", notice.await_args_list[-1].args[0])

                await main._handle_chat_command("Carol", "循环 单曲")
                self.assertEqual("one", main._repeat_mode)
                self.assertIn("已切换为单曲循环", notice.await_args_list[-1].args[0])

                await main._handle_chat_command("Carol", "循环 关闭")
                self.assertEqual("none", main._repeat_mode)
                self.assertIn("已切换为关闭循环", notice.await_args_list[-1].args[0])
        finally:
            main._repeat_mode = previous_mode

    async def test_clear_command_clears_queue_and_playback_state(self) -> None:
        count_result = unittest.mock.Mock()
        count_result.scalar.return_value = 3
        session = unittest.mock.Mock()
        session.execute.side_effect = [count_result, unittest.mock.Mock()]

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_shuffle_queue", [1, 2, 3]),
            patch.object(main, "_current_shuffle_index", 1),
            patch.object(main, "_invalidate_play_requests", AsyncMock()) as invalidate,
            patch.object(main, "_set_now_playing_queue_item", AsyncMock()) as clear_now_playing,
            patch.object(main, "_schedule_ts_description_update"),
            patch.object(main.voice, "stop", AsyncMock()) as stop,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Dave", "清空")

            self.assertEqual([], main._shuffle_queue)
            self.assertEqual(-1, main._current_shuffle_index)

        session.commit.assert_called_once_with()
        session.close.assert_called_once_with()
        invalidate.assert_awaited_once_with()
        clear_now_playing.assert_awaited_once_with(None)
        stop.assert_awaited_once_with()
        self.assertIn("已清空播放队列（3 首）", notice.await_args.args[0])

    def test_playlist_results_are_isolated_by_unique_id_and_expire(self) -> None:
        alice_key = main._ts_playlist_result_key(invoker_unique_id="alice-uid", invoker_name="SameName")
        bob_key = main._ts_playlist_result_key(invoker_unique_id="bob-uid", invoker_name="SameName")
        playlists = [{"id": "123", "name": "测试", "creator": "", "track_count": "1"}]

        with patch.object(main.time, "monotonic", return_value=100.0):
            main._remember_ts_playlist_results(alice_key, playlists)

        with patch.object(main.time, "monotonic", return_value=101.0):
            self.assertEqual(playlists, main._get_ts_playlist_results(alice_key))
            self.assertEqual([], main._get_ts_playlist_results(bob_key))

        with patch.object(main.time, "monotonic", return_value=100.0 + main._TS_PLAYLIST_RESULTS_TTL_S):
            self.assertEqual([], main._get_ts_playlist_results(alice_key))

    def test_netease_search_metadata_accepts_artists_field(self) -> None:
        raw = {
            "result": {
                "songs": [
                    {
                        "id": 123,
                        "name": "歌曲",
                        "ar": [],
                        "artists": [{"name": "歌手一"}, {"name": "歌手二"}],
                    }
                ]
            }
        }

        self.assertEqual(("123", "歌曲", "歌手一, 歌手二"), main._extract_song_meta_from_search_first(raw))


class PlaybackCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_one_replays_finished_item_without_deleting_it(self) -> None:
        with (
            patch.object(main, "_repeat_mode", "one"),
            patch.object(main, "_take_now_playing_if_match", AsyncMock(return_value=7)),
            patch.object(main, "_play_queue_item_internal", AsyncMock(return_value=True)) as replay,
            patch.object(main, "_delete_queue_item", AsyncMock()) as delete_item,
            patch.object(main, "_auto_play_next_from_queue", AsyncMock()) as play_next,
        ):
            await main._handle_playback_finished("source")

        replay.assert_awaited_once_with(7, requested_by="auto")
        delete_item.assert_not_awaited()
        play_next.assert_not_awaited()

    async def test_normal_completion_deletes_item_and_plays_next(self) -> None:
        with (
            patch.object(main, "_repeat_mode", "none"),
            patch.object(main, "_take_now_playing_if_match", AsyncMock(return_value=8)),
            patch.object(main, "_play_queue_item_internal", AsyncMock()) as replay,
            patch.object(main, "_delete_queue_item", AsyncMock()) as delete_item,
            patch.object(main, "_auto_play_next_from_queue", AsyncMock()) as play_next,
        ):
            await main._handle_playback_finished("source")

        replay.assert_not_awaited()
        delete_item.assert_awaited_once_with(8)
        play_next.assert_awaited_once_with()


class VoiceStatusFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_voice_service_returns_offline_status(self) -> None:
        with (
            patch.object(main.voice, "get_status", AsyncMock(side_effect=RuntimeError("offline"))),
            patch.object(main, "_current_queue_item_id", None),
        ):
            result = await main.voice_status()

        self.assertFalse(result["voice_connected"])
        self.assertEqual("idle", result["state"])
        self.assertEqual("", result["now_playing_title"])


if __name__ == "__main__":
    unittest.main()
