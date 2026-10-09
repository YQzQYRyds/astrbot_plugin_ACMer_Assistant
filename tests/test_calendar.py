import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from calendar_core.config import SCHEMA, Settings
from calendar_core.fetchers import atcoder, codeforces, leetcode, luogu, nowcoder
from calendar_core.formatting import digest, reminder, split_message
from calendar_core.models import Contest
from calendar_core.scheduler import Scheduler
from calendar_core.service import CalendarService
from calendar_core.storage import Store

NOW = datetime(2030, 1, 2, 0, 30, tzinfo=timezone.utc)
TARGET = "test:GroupMessage:123"


def contest(minutes=60, id="1", platform="codeforces"):
    return Contest(
        id,
        platform,
        "Test Round",
        int(NOW.timestamp() + minutes * 60),
        7200,
        "https://example.com/contest/1",
    )


class FakeFetchers:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, 0

    async def fetch(self, platform):
        self.calls += 1
        if self.fail:
            raise ValueError("bad source")
        return self.rows


async def setup(tmp_path, overrides=None, sender=None, rows=None):
    config = Settings(
        {
            "target_groups": ["123"],
            "enabled_platforms": ["codeforces"],
            "daily_push_enabled": False,
            **(overrides or {}),
        }
    )
    store = Store(tmp_path / "calendar.sqlite3")
    await store.open(config.uncertain_delivery_policy)
    service = CalendarService(config, store, FakeFetchers(), logging.getLogger("test"))
    await service.load()
    if rows is not None:
        await store.save_snapshot("codeforces", NOW.timestamp(), rows)
        service.snapshots["codeforces"] = (NOW.timestamp(), rows)
    sent = []

    async def send(target, body):
        sent.append((target, body))
        return True

    async def targets():
        return (overrides or {}).get("test_targets", [TARGET])

    scheduler = Scheduler(
        config, store, service, sender or send, logging.getLogger("test"), targets
    )
    return config, store, service, scheduler, sent


@pytest.mark.parametrize(
    "bad",
    [
        {"daily_push_time": "24:00"},
        {"digest_days": 0},
        {"enabled_platforms": ["bad"]},
        {"advance_notice_minutes": [True]},
        {"advance_notice_minutes": [0]},
        {"advance_notice_minutes": [1.5]},
        {"target_groups": [123]},
        {"sync_interval_minutes": True},
        {"daily_push_enabled": "false"},
        {"uncertain_delivery_policy": "magic"},
    ],
)
def test_config_validation(bad):
    with pytest.raises((ValueError, TypeError)):
        Settings(bad)


def test_config_defaults_and_list_normalization():
    config = Settings({"advance_notice_minutes": ["60", 30, 60]})
    assert config.advance_notice_minutes == (30, 60)
    assert config.digest_days == SCHEMA["digest_days"]["default"]
    assert Settings({"enabled_platforms": []}).enabled_platforms == ()


def test_parsers():
    rows = codeforces(
        {
            "status": "OK",
            "result": [
                {
                    "id": 1,
                    "name": "Round",
                    "startTimeSeconds": 1000,
                    "durationSeconds": 7200,
                    "phase": "BEFORE",
                },
                {"phase": "FINISHED"},
            ],
        }
    )
    assert len(rows) == 1 and rows[0].start_time == 1000
    body = """<div id="contest-table-upcoming"><table><tbody><tr>
    <td><time>2030-01-02 21:00:00+0900</time></td>
    <td><a href="/contests/abc999">ABC 999</a></td><td>01:40</td>
    </tr></tbody></table></div>"""
    assert atcoder(body)[0].duration_seconds == 6000
    assert atcoder(body)[0].local_start(Settings({}).zone).hour == 20
    raw = json.dumps(
        {
            "contestId": 1,
            "contestName": "牛客",
            "contestStartTime": 1000000,
            "contestDuration": 7200000,
        }
    ).replace('"', "&amp;quot;")
    row = nowcoder(f'<div class="platform-item" data-json="{raw}"></div>')[0]
    assert row.start_time == 1000 and row.duration_seconds == 7200
    assert (
        leetcode(
            {
                "data": {
                    "contestUpcomingContests": [
                        {
                            "titleSlug": "weekly-1",
                            "title": "周赛",
                            "startTime": 1000,
                            "duration": 5400,
                        }
                    ]
                }
            }
        )[0].id
        == "weekly-1"
    )
    for root in ("currentData", "data"):
        row = luogu(
            {
                root: {
                    "contests": {
                        "result": [{"id": 1, "name": "月赛", "startTime": 1000, "endTime": 8200}]
                    }
                }
            }
        )[0]
        assert row.duration_seconds == 7200


@pytest.mark.parametrize(
    "parser,data",
    [
        (codeforces, {"status": "FAILED"}),
        (atcoder, "<html>captcha</html>"),
        (nowcoder, "<html>captcha</html>"),
        (leetcode, {"errors": ["oops"]}),
        (luogu, {}),
    ],
)
def test_invalid_sources_do_not_mean_empty_calendar(parser, data):
    with pytest.raises((ValueError, KeyError)):
        parser(data)


def test_formatting_date_boundaries():
    config = Settings({"digest_days": 2})
    rows = [contest(60), contest(24 * 60, "tomorrow"), contest(48 * 60, "excluded")]
    body = "\n".join(digest(rows, NOW, config))
    assert "📅 01-02（周三）" in body and "\n\n📅 01-03（周四）" in body
    assert body.count("Test Round") == 2
    assert "09:30" in reminder(rows[0], NOW, config)
    assert all(len(p) <= 400 for p in split_message("a" * 1900 + "\nb", 400))


async def test_restart_dedup_and_two_thresholds(tmp_path):
    options = {"advance_notice_minutes": [60, 30]}
    _, store, _, scheduler, sent = await setup(tmp_path, options, rows=[contest()])
    await scheduler.tick(NOW)
    await scheduler.tick(NOW + timedelta(seconds=20))
    assert len(sent) == 1
    await store.close()
    _, store, _, scheduler, sent = await setup(tmp_path, options)
    try:
        await scheduler.tick(NOW + timedelta(seconds=40))
        assert not sent
        await scheduler.tick(NOW + timedelta(minutes=30))
        assert len(sent) == 1
        await scheduler.tick(NOW + timedelta(minutes=60))
        assert len(sent) == 1
    finally:
        await store.close()


async def test_catchup_coalesces_and_no_after_start(tmp_path):
    _, store, _, scheduler, sent = await setup(tmp_path, rows=[contest(20)])
    try:
        await scheduler.tick(NOW)
        await scheduler.tick(NOW + timedelta(minutes=1))
        assert len(sent) == 1
        assert "约 20 分钟后" in sent[0][1]
        await scheduler.tick(NOW + timedelta(minutes=21))
        assert len(sent) == 1
    finally:
        await store.close()


async def test_targets_independent_and_retry_false(tmp_path):
    received = []

    async def send(target, body):
        if target == TARGET and not received:
            received.append("failure")
            return False
        received.append(target)
        return True

    _, store, _, scheduler, _ = await setup(
        tmp_path, {"test_targets": [TARGET, "z:GroupMessage:2"]}, sender=send, rows=[contest()]
    )
    try:
        await scheduler.tick(NOW)
        await scheduler.tick(NOW + timedelta(seconds=61))
        assert received == ["failure", "z:GroupMessage:2", TARGET]
    finally:
        await store.close()


@pytest.mark.parametrize("policy,expected", [("hold", "uncertain"), ("retry", "pending")])
async def test_crash_during_send_recovery(tmp_path, policy, expected):
    store = Store(tmp_path / "db.sqlite3")
    await store.open(policy)
    await store.enqueue([("one", TARGET, "body", NOW.timestamp() + 100)])
    assert await store.claim("one", NOW.timestamp(), 5)
    await store.close()
    await store.open(policy)
    try:
        assert (await store.jobs("one"))[0]["status"] == expected
    finally:
        await store.close()


async def test_timeout_uncertain_and_manual_resolution(tmp_path):
    async def send(*args):
        raise TimeoutError()

    _, store, _, scheduler, _ = await setup(tmp_path, sender=send, rows=[contest()])
    try:
        await scheduler.tick(NOW)
        row = (await store.unresolved())[0]
        assert row["status"] == "uncertain"
        assert await store.resolve(row["key"], retry=False)
        await scheduler.tick(NOW + timedelta(minutes=1))
        assert not await store.unresolved()
    finally:
        await store.close()


async def test_daily_restart_and_pagination(tmp_path):
    options = {"daily_push_enabled": True, "message_max_chars": 400, "advance_notice_minutes": []}
    _, store, _, scheduler, sent = await setup(
        tmp_path, options, rows=[contest(120, str(i)) for i in range(12)]
    )
    await scheduler.tick(NOW)
    pages = len(sent)
    assert pages > 1
    await store.close()
    _, store, _, scheduler, sent = await setup(tmp_path, options)
    try:
        await scheduler.tick(NOW + timedelta(minutes=10))
        assert not sent
    finally:
        await store.close()


async def test_daily_outside_window(tmp_path):
    _, store, _, scheduler, sent = await setup(
        tmp_path, {"daily_push_enabled": True, "advance_notice_minutes": []}, rows=[contest(900)]
    )
    try:
        await scheduler.tick(NOW - timedelta(minutes=1))
        await scheduler.tick(NOW + timedelta(hours=4))
        assert not sent
    finally:
        await store.close()


async def test_stale_cache_and_disabled_platform_not_sent(tmp_path):
    _, store, service, scheduler, sent = await setup(tmp_path, rows=[contest()])
    try:
        service.snapshots["codeforces"] = (NOW.timestamp() - 25 * 3600, [contest()])
        service.snapshots["atcoder"] = (NOW.timestamp(), [contest(platform="atcoder")])
        await scheduler.tick(NOW)
        assert not sent
        assert len(service.contests(NOW.timestamp())) == 1
        assert "缓存过期" in service.notes(NOW.timestamp())
    finally:
        await store.close()


async def test_reschedule_changes_dedup_key(tmp_path):
    _, store, service, scheduler, sent = await setup(tmp_path, rows=[contest()])
    try:
        await scheduler.tick(NOW)
        service.snapshots["codeforces"] = (NOW.timestamp(), [contest(61)])
        await scheduler.tick(NOW + timedelta(minutes=1))
        assert len(sent) == 2
    finally:
        await store.close()


async def test_refresh_failure_preserves_cache_success_replaces(tmp_path):
    _, store, service, _, _ = await setup(tmp_path, rows=[contest()])
    try:
        fake = FakeFetchers(fail=True)
        service.fetchers = fake
        await service.refresh(force=True)
        assert len(service.snapshots["codeforces"][1]) == 1
        fake.fail = False
        await service.refresh(force=True)
        assert service.snapshots["codeforces"][1] == []
        assert not service.errors
    finally:
        await store.close()


async def test_concurrent_query_refresh_coalesced(tmp_path):
    _, store, service, _, _ = await setup(tmp_path)
    try:
        await asyncio.gather(service.refresh(), service.refresh(), service.refresh())
        assert service.fetchers.calls == 1
    finally:
        await store.close()


async def test_atomic_claim(tmp_path):
    _, store, _, _, _ = await setup(tmp_path)
    try:
        await store.enqueue([("one", TARGET, "body", NOW.timestamp() + 100)])
        claims = await asyncio.gather(*(store.claim("one", NOW.timestamp(), 5) for _ in range(10)))
        assert claims.count(True) == 1
    finally:
        await store.close()


async def test_daily_partial_failure_only_retries_unsent_pages(tmp_path):
    delivered = []
    failed = False

    async def send(target, body):
        nonlocal failed
        if len(delivered) == 1 and not failed:
            failed = True
            return False
        delivered.append(body)
        return True

    options = {"daily_push_enabled": True, "message_max_chars": 400, "advance_notice_minutes": []}
    _, store, _, scheduler, _ = await setup(
        tmp_path, options, sender=send, rows=[contest(120, str(i)) for i in range(12)]
    )
    try:
        await scheduler.tick(NOW)
        jobs = await store.jobs("daily|")
        assert sum(r["status"] == "pending" for r in jobs) == 1
        successful = len(delivered)
        await scheduler.tick(NOW + timedelta(seconds=61))
        assert len(delivered) == successful + 1
        assert all(r["status"] == "sent" for r in await store.jobs("daily|"))
    finally:
        await store.close()


async def test_cancellation_preserves_inflight_state(tmp_path):
    entered = asyncio.Event()

    async def send(*args):
        entered.set()
        await asyncio.Future()

    _, store, _, scheduler, _ = await setup(tmp_path, sender=send, rows=[contest()])
    try:
        task = asyncio.create_task(scheduler.tick(NOW))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await store.jobs("notice|"))[0]["status"] == "sending"
    finally:
        await store.close()


async def test_expiry_rechecked_before_sending(tmp_path, monkeypatch):
    _, store, _, scheduler, sent = await setup(tmp_path, rows=[contest(20)])
    try:
        monkeypatch.setattr("calendar_core.scheduler.time.time", lambda: NOW.timestamp() + 3600)
        await scheduler.tick(NOW)
        assert not sent
    finally:
        await store.close()


async def test_recovered_attempt_budget_becomes_actionable(tmp_path):
    _, store, _, _, _ = await setup(tmp_path)
    await store.enqueue([("one", TARGET, "body", NOW.timestamp() + 1000)])
    assert await store.claim("one", NOW.timestamp(), 1)
    await store.close()
    await store.open("retry")
    try:
        assert not await store.claim("one", NOW.timestamp(), 1)
        assert (await store.unresolved())[0]["status"] == "failed"
    finally:
        await store.close()


def named(minutes, id, title, platform="codeforces", hours=2):
    return Contest(
        id,
        platform,
        title,
        int(NOW.timestamp() + minutes * 60),
        hours * 3600,
        f"https://example.com/{platform}/{id}",
    )


async def daily_setup(tmp_path, rows, overrides=None):
    options = {"daily_push_enabled": True, "advance_notice_minutes": [], **(overrides or {})}
    return await setup(tmp_path, options, rows=rows)


# NOW 是 Asia/Shanghai 08:30，即默认日报时间。
async def test_upgrade_is_silent_and_daily_only_pushes_new_and_today(tmp_path):
    existing = named(3 * 24 * 60, "1", "Codeforces Round 1 (Div. 2)")
    tonight = named(14 * 60, "2", "Codeforces Round 2 (Div. 2)")
    _, store, service, scheduler, sent = await daily_setup(tmp_path, [existing, tonight])
    try:
        await scheduler.tick(NOW)
        # 升级/新启用：已有比赛静默记录，只有今天开赛的出现在“今日开赛”。
        assert len(sent) == 1
        assert "🆕" not in sent[0][1] and "🔥 今日开赛" in sent[0][1]
        assert "Round 2" in sent[0][1] and "Round 1 " not in sent[0][1]
        fresh = named(2 * 24 * 60, "3", "Codeforces Round 3 (Div. 2)")
        far = named(20 * 24 * 60, "4", "Codeforces Round 4 (Div. 2)")
        rows = [existing, fresh, far]
        service.snapshots["codeforces"] = (NOW.timestamp() + 86400, rows)
        await scheduler.tick(NOW + timedelta(days=1))
        assert len(sent) == 2
        body = sent[1][1]
        assert "🆕 新上架比赛" in body and "Round 3" in body
        assert "Round 1 " not in body and "Round 4" not in body
        # 第三天没有新比赛也没有当天比赛：不发。
        service.snapshots["codeforces"] = (NOW.timestamp() + 2 * 86400, rows)
        await scheduler.tick(NOW + timedelta(days=2))
        assert len(sent) == 2
    finally:
        await store.close()


async def test_daily_marks_reschedule(tmp_path):
    first = named(3 * 24 * 60, "1", "Codeforces Round 1 (Div. 2)")
    _, store, service, scheduler, sent = await daily_setup(tmp_path, [first])
    try:
        await scheduler.tick(NOW)
        assert not sent
        moved = named(4 * 24 * 60, "1", "Codeforces Round 1 (Div. 2)")
        service.snapshots["codeforces"] = (NOW.timestamp() + 86400, [moved])
        await scheduler.tick(NOW + timedelta(days=1))
        assert "⚠️ 时间变更" in sent[0][1]
    finally:
        await store.close()


async def test_long_contests_labelled_not_reminded(tmp_path):
    ahc = named(60, "ahc072", "AtCoder Heuristic Contest 072", "codeforces", hours=240)
    _, store, service, scheduler, sent = await setup(tmp_path, rows=[ahc])
    try:
        await scheduler.tick(NOW)
        assert not sent
    finally:
        await store.close()
    rows = [ahc, named(3 * 24 * 60, "9", "Round 9")]
    _, store, _, scheduler, sent = await daily_setup(tmp_path / "b", rows)
    try:
        await store.baseline("group:123", "codeforces", [], NOW.timestamp())
        await scheduler.tick(NOW)
        assert "（长期赛）" in sent[0][1] and "🔥" not in sent[0][1]
    finally:
        await store.close()


async def test_close_reminders_merge_and_never_repeat(tmp_path):
    rows = [
        named(60, "1", "Codeforces Round 7 (Div. 1)"),
        named(60, "2", "Codeforces Round 7 (Div. 2)"),
        named(70, "3", "Other Round"),
        named(120, "4", "Late Round"),
    ]
    _, store, _, scheduler, sent = await setup(tmp_path, rows=rows)
    try:
        await scheduler.tick(NOW)
        assert len(sent) == 1
        body = sent[0][1]
        assert "约 60 分钟后开赛" in body and body.count("•") == 2
        assert "Div.1: https://example.com/codeforces/1" in body and "Other Round" in body
        await scheduler.tick(NOW + timedelta(minutes=10))
        assert len(sent) == 1  # Other Round 已并入，不再单独提醒
        await scheduler.tick(NOW + timedelta(minutes=60))
        assert len(sent) == 2 and "Late Round" in sent[1][1] and "【赛事提醒】\n" in sent[1][1]
    finally:
        await store.close()


async def test_beginner_scope_and_group_overrides(tmp_path):
    rows = [
        named(60, "1", "Codeforces Round 1 (Div. 1)"),
        named(60, "2", "Codeforces Round 2 (Div. 1 + Div. 2)"),
        named(60, "3", "Educational Codeforces Round 3 (Rated for Div. 2)"),
        named(60, "abc477", "AtCoder Beginner Contest 477", "atcoder"),
        named(60, "agc070", "AtCoder Grand Contest 070", "atcoder"),
    ]
    config, store, service, scheduler, sent = await setup(
        tmp_path, {"enabled_platforms": ["codeforces", "atcoder"], "default_scope": "beginner"}
    )
    try:
        service.snapshots["codeforces"] = (NOW.timestamp(), rows[:3])
        service.snapshots["atcoder"] = (NOW.timestamp(), rows[3:])
        await scheduler.tick(NOW)
        body = sent[0][1]
        assert "Educational" in body and "ABC 477" in body
        assert "Round 1 " not in body and "Round 2" not in body and "AGC" not in body
        await store.set_setting("group:123", "scope", "all")
        await store.set_setting("group:123", "blocked", ["AGC"])
        from calendar_core.preferences import load

        prefs = await load(store, config, "group:123")
        assert [c.id for c in prefs.filter(rows)] == ["1", "2", "3", "abc477"]
        assert "（本群）" in prefs.describe()
    finally:
        await store.close()
