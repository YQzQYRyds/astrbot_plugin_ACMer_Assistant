import logging
from types import SimpleNamespace

import pytest

from calendar_core.config import SCHEMA, Settings
from calendar_core.migration import migrate_database
from calendar_core.routing import GroupRouter
from calendar_core.service import CalendarService
from calendar_core.storage import Store


def event(group="", user="456", platform="qq"):
    return SimpleNamespace(
        get_group_id=lambda: group, get_sender_id=lambda: user, get_platform_id=lambda: platform
    )


def test_session_types_and_explicit_instance_isolation():
    config = Settings(
        {"enabled_session_ids": ["group:123", "private:456", "other:FriendMessage:789"]}
    )
    assert config.allows(event(group="123"))
    assert config.allows(event(user="456"))
    assert config.allows(event(user="789", platform="other"))
    assert not config.allows(event(group="456"))
    assert not config.allows(event(user="123"))
    assert not config.allows(event(user="789", platform="qq"))
    assert not Settings({"enabled_session_ids": []}).allows(event(group="123"))


@pytest.mark.parametrize(
    "bad",
    [
        "unknown:123",
        "qq:PrivateMessage:1",
        "qq:FriendMessage:",
        ":FriendMessage:1",
        "private:",
        "random",
        123,
    ],
)
def test_invalid_session_rejected(bad):
    with pytest.raises(ValueError):
        Settings({"enabled_session_ids": [bad]})


def test_legacy_migration_and_explicit_new_settings_take_priority():
    config = Settings({"target_groups": ["123"], "enabled_platforms": ["atcoder"]})
    assert config.enabled_session_ids == ("123",)
    assert config.enabled_platforms == ("atcoder",)
    updated = Settings(
        {
            "target_groups": ["123"],
            "enabled_session_ids": [],
            "enabled_platforms": [],
            "enable_codeforces": True,
        }
    )
    assert updated.enabled_session_ids == ()
    assert updated.enabled_platforms == ("codeforces",)
    assert "target_groups" not in SCHEMA and "enabled_platforms" not in SCHEMA
    assert all(
        SCHEMA[f"enable_{p}"]["type"] == "bool"
        for p in ("codeforces", "atcoder", "nowcoder", "leetcode", "luogu")
    )


async def test_friend_discovery_no_binding_and_dedup(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    await store.open("hold")
    calls = []

    async def call(action):
        calls.append(action)
        return [{"user_id": 456}]

    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="qq", name="aiocqhttp"),
        get_client=lambda: SimpleNamespace(call_action=call),
    )
    context = SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: [platform]))
    config = Settings({"enabled_session_ids": ["private:456", "qq:FriendMessage:456"]})
    router = GroupRouter(context, config, store, logging.getLogger("test"))
    try:
        await router.discover()
        assert calls == ["get_friend_list"]
        assert await router.targets() == ["qq:FriendMessage:456"]
        await router.observe(event(user="456"))
        assert await router.targets() == ["qq:FriendMessage:456"]
        router.config = Settings({"enabled_session_ids": []})
        assert await router.targets() == []
    finally:
        await store.close()


async def test_explicit_session_push_needs_no_observation(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    await store.open("hold")
    platform = SimpleNamespace(meta=lambda: SimpleNamespace(id="telegram", name="telegram"))
    context = SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: [platform]))
    router = GroupRouter(
        context,
        Settings({"enabled_session_ids": ["telegram:FriendMessage:123"]}),
        store,
        logging.getLogger("test"),
    )
    try:
        assert await router.targets() == ["telegram:FriendMessage:123"]
    finally:
        await store.close()


async def test_renamed_database_copies_without_losing_notifications(tmp_path):
    old = tmp_path / "plugin_data" / "astrbot_plugin_acmer_calendar" / "calendar.sqlite3"
    new = tmp_path / "plugin_data" / "astrbot_plugin_acmer_assistant" / "calendar.sqlite3"
    store = Store(old)
    await store.open("hold")
    try:
        await store.enqueue([("sent-event", "qq:GroupMessage:123", "hello", 9999999999)])
        await store.finish("sent-event", "sent")
        await migrate_database(tmp_path, new)
        migrated = Store(new)
        await migrated.open("hold")
        try:
            assert (await migrated.jobs("sent-event"))[0]["status"] == "sent"
            await migrated.finish("sent-event", "uncertain")
            await migrate_database(tmp_path, new)  # 已存在的新数据库不覆盖。
            assert (await migrated.jobs("sent-event"))[0]["status"] == "uncertain"
        finally:
            await migrated.close()
        assert old.exists()
    finally:
        await store.close()


async def test_platform_switch_disables_fetch_and_cached_contests():
    calls = []

    async def fetch(platform):
        calls.append(platform)
        return []

    async def save(*args):
        pass

    config = Settings(
        {
            f"enable_{p}": p == "atcoder"
            for p in ("codeforces", "atcoder", "nowcoder", "leetcode", "luogu")
        }
    )
    service = CalendarService(
        config,
        SimpleNamespace(save_snapshot=save),
        SimpleNamespace(fetch=fetch),
        logging.getLogger("test"),
    )
    service.snapshots["codeforces"] = (9999999999, [object()])
    await service.refresh(force=True)
    assert calls == ["atcoder"]
    assert service.contests(0) == []
