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
    _, store, _, scheduler, sent = await setup(tmp_path, rows=[contest()])
    await scheduler.tick(NOW)
    await scheduler.tick(NOW + timedelta(seconds=20))
    assert len(sent) == 1
    await store.close()
    _, store, _, scheduler, sent = await setup(tmp_path)
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
