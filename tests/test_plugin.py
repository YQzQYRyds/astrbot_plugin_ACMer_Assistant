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

        @staticmethod
        def command(name, **kwargs):
            def decorate(fn):
                fn.command = name
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
    context = types.SimpleNamespace()
    plugin = plugin_class(context, {"enabled_platforms": []})
    await plugin.initialize()
    tasks = plugin.tasks[:]
    assert len(tasks) == 2
    assert plugin.http.session.closed is False
    await plugin.store.bind("123", "test:GroupMessage:123")
    await plugin.terminate()
    assert all(t.done() for t in tasks)
    assert plugin.store.db is None and plugin.http is None
    await plugin.terminate()  # 清理可重复调用。
    plugin = plugin_class(context, {"enabled_platforms": [], "target_groups": ["123"]})
    await plugin.initialize()
    try:
        assert await plugin.store.targets(plugin.settings) == ["test:GroupMessage:123"]
    finally:
        await plugin.terminate()


def test_admin_command_permissions(plugin_class):
    for handler in ("refresh", "bind", "session", "status", "resolve"):
        assert getattr(plugin_class, handler).permission == "admin"
    assert plugin_class.calendar.command == "赛历"


async def test_sender_preserves_false(plugin_class):
    calls = []

    async def send(target, chain):
        calls.append((target, chain.text))
        return False

    plugin = plugin_class(types.SimpleNamespace(send_message=send), {})
    assert await plugin.send("test:GroupMessage:1", "hello") is False
    assert calls == [("test:GroupMessage:1", "hello")]
