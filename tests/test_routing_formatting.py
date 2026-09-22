import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from calendar_core.config import Settings
from calendar_core.formatting import digest, display_entries, duration
from calendar_core.models import Contest
from calendar_core.routing import GroupRouter
from calendar_core.storage import Store


def platform(id="qq", groups=None, fail=False, name="aiocqhttp"):
    calls = []

    async def call(action):
        calls.append(action)
        if fail:
            raise TimeoutError()
        return groups if groups is not None else [{"group_id": 123}, {"group_id": 999}]

    return SimpleNamespace(
        meta=lambda: SimpleNamespace(id=id, name=name),
        get_client=lambda: SimpleNamespace(call_action=call),
        calls=calls,
    )


def context(platforms):
    return SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: platforms))


async def test_group_list_enables_without_any_message_or_binding(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    await store.open("hold")
    bot = platform()
    router = GroupRouter(
        context([bot]), Settings({"target_groups": ["123"]}), store, logging.getLogger("test")
    )
    try:
        await router.discover()
        assert await router.targets() == ["qq:GroupMessage:123"]
        await router.discover()
        assert bot.calls == ["get_group_list"]  # 成功结果缓存，避免频繁查询。
        router.config = Settings({"target_groups": [], "target_sessions": ["qq:GroupMessage:123"]})
        assert await router.targets() == []  # 旧会话配置不能绕过群白名单。
    finally:
        await store.close()


async def test_multiple_bots_choose_one_and_removed_membership_clears(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    await store.open("hold")
    router = GroupRouter(
        context([platform("b"), platform("a")]),
        Settings({"target_groups": ["123"]}),
        store,
        logging.getLogger("test"),
    )
    try:
        await router.discover()
        assert await router.targets() == ["a:GroupMessage:123"]
        router.context = context([platform("a", groups=[])])
        router.last_success.clear()
        await router.discover()
        assert await router.targets() == []
    finally:
        await store.close()


async def test_discovery_failure_isolated_and_retried(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    await store.open("hold")
    broken = platform("broken", fail=True)
    router = GroupRouter(
        context([broken, platform("ok")]),
        Settings({"target_groups": ["123"]}),
        store,
        logging.getLogger("test"),
    )
    try:
        await router.discover()
        await router.discover()
        assert len(broken.calls) == 2
        assert await router.targets() == ["ok:GroupMessage:123"]
    finally:
        await store.close()


async def test_observe_uses_group_session_and_ignores_other_groups(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    await store.open("hold")
    router = GroupRouter(
        context([platform()]),
        Settings({"target_groups": ["123"]}),
        store,
        logging.getLogger("test"),
    )
    try:
        for group in ("123", "999", ""):
            event = SimpleNamespace(
                get_group_id=lambda: group,
                get_platform_id=lambda: "qq",
                unified_msg_origin=f"qq:GroupMessage:member_{group}",
            )
            await router.observe(event)
        assert await router.targets() == ["qq:GroupMessage:123"]
        assert len(await store.routes()) == 1
    finally:
        await store.close()


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (3600, "1小时"),
        (6000, "1小时40分"),
        (9000, "2小时30分"),
        (864000, "10天/240h"),
        (90, "1分30秒"),
    ],
)
def test_human_duration(seconds, expected):
    assert duration(seconds) == expected


def cf(id, division, start=1916668800, duration_seconds=7200, round=1200):
    return Contest(
        str(id),
        "codeforces",
        f"Codeforces Round {round} (Div. {division})",
        start,
        duration_seconds,
        f"https://codeforces.com/contest/{id}",
    )


def test_merge_only_matching_cf_divisions():
    rows = [cf(1, 1), cf(2, 2), cf(3, 1, round=1201), cf(4, 2, duration_seconds=9000, round=1201)]
    entries = display_entries(rows)
    assert len(entries) == 3 and len(entries[0]) == 2
    assert len(display_entries([cf(1, 1), cf(2, 2, start=1916668801)])) == 2
    assert len(display_entries([cf(1, 1), cf(2, 1)])) == 2


def test_limit_after_merging_date_filter_and_clear_omission_notice():
    now = datetime.fromtimestamp(1916668700, timezone.utc)
    rows = [cf(1, 1), cf(2, 2), cf(3, 1, round=1201), cf(4, 2, start=1916668800 + 86400 * 8)]
    pages = digest(rows, now, Settings({"digest_days": 7, "digest_max_contests": 1}))
    text = "\n".join(pages)
    assert text.count("•") == 1
    assert "Div.1: https://codeforces.com/contest/1" in text
    assert "Div.2: https://codeforces.com/contest/2" in text
    assert "另有 1 项未展示" in text
    assert "🏆 【近期算法赛事周报】" in text
    assert text.endswith("💡 记得提前报名参赛，祝大家把把上分、轻松 AC！")


def test_limits_validated_and_nondefault_timezone_visible():
    for n in (0, -1, 101, True):
        with pytest.raises(ValueError):
            Settings({"digest_max_contests": n})
    text = "\n".join(digest([], datetime.now(timezone.utc), Settings({"timezone": "UTC"})))
    assert "时区：UTC" in text


def test_pagination_keeps_normal_cards_and_date_headings_together():
    rows = [cf(i, 1, round=1200 + i) for i in range(10)]
    pages = digest(
        rows, datetime.fromtimestamp(1916668700, timezone.utc), Settings({"message_max_chars": 400})
    )
    for page in pages:
        assert len(page) <= 400
        if "•" in page:
            assert "📅" in page
            assert page.count("•") == page.count("⏰") == page.count("🔗")
