"""AstrBot 边界契约替身测试；真实适配器发信仍需部署后联调。"""

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plugin_class(monkeypatch, tmp_path):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    class Star:
        def __init__(self, context):
            self.context = context

    class Chain:
        def message(self, text):
            self.text = text
            return self

    class Filters:
        PermissionType = types.SimpleNamespace(ADMIN="admin")
        EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group", ALL="all")

        @staticmethod
        def event_message_type(kind):
            return lambda fn: fn

        @staticmethod
        def command(name, **kwargs):
            def decorate(fn):
                fn.command = name
                fn.aliases = kwargs.get("alias", set())
                return fn

            return decorate

        @staticmethod
        def permission_type(permission):
            def decorate(fn):
                fn.permission = permission
                return fn

            return decorate

    module("astrbot")
    module("astrbot.api", AstrBotConfig=dict, logger=logging.getLogger("plugin-test"))
    module("astrbot.api.star", Star=Star, Context=object)
    module("astrbot.api.event", AstrMessageEvent=object, MessageChain=Chain, filter=Filters)
    module("astrbot.core")
    module("astrbot.core.utils")
    module("astrbot.core.utils.astrbot_path", get_astrbot_data_path=lambda: str(tmp_path))
    package = module("plugin_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location("plugin_test.main", ROOT / "main.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    yield loaded.ACMerCalendar
    for name in list(sys.modules):
        if name.startswith("plugin_test."):
            sys.modules.pop(name)


async def test_initialize_terminate_reload(plugin_class, tmp_path):
    context = types.SimpleNamespace(
        platform_manager=types.SimpleNamespace(
            get_insts=lambda: [
                types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="test", name="other"))
            ]
        )
    )
    plugin = plugin_class(context, {"enabled_platforms": []})
    await plugin.initialize()
    tasks = plugin.tasks[:]
    assert len(tasks) == 3
    assert plugin.http.session.closed is False
    await plugin.store.remember_route("123", "test", "test:GroupMessage:123")
    await plugin.terminate()
    assert all(t.done() for t in tasks)
    assert plugin.store.db is None and plugin.http is None
    await plugin.terminate()  # 清理可重复调用。
    plugin = plugin_class(context, {"enabled_platforms": [], "target_groups": ["123"]})
    await plugin.initialize()
    try:
        assert await plugin.router.targets() == ["test:GroupMessage:123"]
    finally:
        await plugin.terminate()


def test_admin_command_permissions(plugin_class):
    for handler in ("refresh", "status", "resolve"):
        assert getattr(plugin_class, handler).permission == "admin"
    assert plugin_class.calendar.command == "赛历"
    assert "比赛" in plugin_class.calendar.aliases
    assert not hasattr(plugin_class, "bind") and not hasattr(plugin_class, "session")


async def test_sender_preserves_false(plugin_class):
    calls = []

    async def send(target, chain):
        calls.append((target, chain.text))
        return False

    plugin = plugin_class(types.SimpleNamespace(send_message=send), {})
    assert await plugin.send("test:GroupMessage:1", "hello") is False
    assert calls == [("test:GroupMessage:1", "hello")]


@pytest.mark.parametrize("group", ["999", ""])
async def test_unlisted_groups_and_private_chats_are_silent(plugin_class, group):
    # 不初始化任何业务依赖：如漏掉白名单检查，这些 handler 会立即失败。
    plugin = plugin_class(object(), {"target_groups": ["123"]})
    event = types.SimpleNamespace(get_group_id=lambda: group)
    for handler, args in (
        ("calendar", ()),
        ("help", ()),
        ("show_settings", ()),
        ("set_scope", ()),
        ("set_platforms", ()),
        ("block", ()),
        ("unblock", ()),
        ("reset", ()),
        ("refresh", ()),
        ("status", ()),
        ("resolve", ("key", "retry")),
    ):
        assert [r async for r in getattr(plugin, handler)(event, *args)] == []


async def test_listed_group_can_query_without_binding(plugin_class):
    calls = []

    async def refresh():
        calls.append("refresh")

    async def settings(session):
        return {}

    plugin = plugin_class(object(), {"target_groups": ["123"]})
    plugin.service = types.SimpleNamespace(
        refresh=refresh, contests=lambda _: [], notes=lambda _: ""
    )
    plugin.store = types.SimpleNamespace(settings=settings)
    event = types.SimpleNamespace(
        get_group_id=lambda: "123", message_str="赛历", plain_result=lambda text: text
    )
    result = [r async for r in plugin.calendar(event)]
    assert calls == ["refresh"] and "近期算法赛事周报" in result[0]


def member(role="member", sender="1", text="", group="123", admin=False):
    raw = {"sender": {"user_id": sender, "role": role}}
    return types.SimpleNamespace(
        get_group_id=lambda: group,
        get_sender_id=lambda: sender,
        is_admin=lambda: admin,
        message_str=text,
        message_obj=types.SimpleNamespace(raw_message=raw),
        plain_result=lambda text: text,
    )


async def test_group_settings_permissions_and_overrides(plugin_class, tmp_path):
    from plugin_test.calendar_core.storage import Store

    plugin = plugin_class(object(), {"target_groups": ["123"], "settings_admin_ids": ["42"]})
    plugin.store = Store(tmp_path / "settings.sqlite3")
    await plugin.store.open("hold")
    try:
        refused = [r async for r in plugin.set_scope(member(text="赛历范围 新手"))]
        assert "只有群主" in refused[0]
        for event in (
            member("admin", text="赛历范围 新手"),
            member("owner", text="赛历范围 新手"),
            member(sender="42", text="赛历范围 新手"),
            member(admin=True, text="赛历范围 新手"),
        ):
            result = [r async for r in plugin.set_scope(event)]
            assert "推送范围：新手（本群）" in result[0]
        bad = [r async for r in plugin.set_scope(member("admin", text="赛历范围 随便"))]
        assert "用法" in bad[0]
        result = [r async for r in plugin.set_platforms(member("admin", text="赛历平台 CF atc"))]
        assert "平台：Codeforces、AtCoder（本群）" in result[0]
        result = [r async for r in plugin.block(member("admin", text="赛历屏蔽 AHC ARC"))]
        assert "屏蔽关键词：AHC、ARC（本群）" in result[0]
        result = [r async for r in plugin.unblock(member("admin", text="赛历取消屏蔽 ARC"))]
        assert "屏蔽关键词：AHC（本群）" in result[0]
        other = [r async for r in plugin.show_settings(member(group="999"))]
        assert other == []  # 未启用的群不响应
        result = [r async for r in plugin.reset(member("admin"))]
        assert "推送范围：全部（后台默认）" in result[0]
        assert "/赛历范围" in [r async for r in plugin.help(member())][0]
    finally:
        await plugin.store.close()
