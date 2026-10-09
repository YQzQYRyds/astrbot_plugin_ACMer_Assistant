import asyncio
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .calendar_core.config import Settings
from .calendar_core.fetchers import Fetchers
from .calendar_core.formatting import digest, split_message
from .calendar_core.http import HTTPClient
from .calendar_core.migration import migrate_database
from .calendar_core.preferences import PLATFORM_ALIASES, SCOPES, event_session
from .calendar_core.preferences import load as load_preferences
from .calendar_core.routing import GroupRouter
from .calendar_core.scheduler import Scheduler
from .calendar_core.service import CalendarService
from .calendar_core.storage import Store

DENIED = "只有群主、群管理员或机器人主人指定的用户可以修改本群设置。"
HELP = """【ACMer 赛历助手 使用说明】
自动推送：
· 每日日报：只推本群还没见过的新比赛和今天开赛的比赛，没有就不发
· 赛前提醒：开赛前按设定时间提醒，开赛时间接近的比赛合并为一条；长期赛不提醒

所有人可用：
/赛历 查看本群范围内近期比赛
/赛历 全部 查看所有平台全部比赛
/赛历设置 查看本群当前设置
/赛历帮助 显示本说明

群主、群管理员、指定用户可用：
/赛历范围 新手 只推新手友好比赛（CF Div.2/3/4、ABC、牛客小白月赛/周赛、力扣周赛等）
/赛历范围 全部 推送全部比赛
/赛历平台 cf atc nc lc lg 只推这些平台（/赛历平台 全部 恢复）
/赛历屏蔽 AHC 屏蔽标题含关键词的比赛
/赛历取消屏蔽 AHC 取消屏蔽
/赛历重置 恢复后台默认设置

机器人主人可用：/刷新赛历、/赛历状态、/处理赛历通知"""


def arguments(event):
    return str(getattr(event, "message_str", "") or "").split()[1:]


class ACMerCalendar(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = Settings(config)
        self.tasks = []
        self.http = None
        self.store = Store(
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_acmer_assistant"
            / "calendar.sqlite3"
        )

    async def initialize(self):
        try:
            await migrate_database(Path(get_astrbot_data_path()), self.store.path)
            await self.store.open(self.settings.uncertain_delivery_policy)
            self.http = HTTPClient(self.settings)
            self.service = CalendarService(
                self.settings, self.store, Fetchers(self.http, self.settings), logger
            )
            await self.service.load()
            self.router = GroupRouter(self.context, self.settings, self.store, logger)
            self.scheduler = Scheduler(
                self.settings, self.store, self.service, self.send, logger, self.router.targets
            )
            self.tasks = [
                asyncio.create_task(self.router.run(), name="acmer-groups"),
                asyncio.create_task(self.sync_loop(), name="acmer-sync"),
                asyncio.create_task(self.scheduler.run(), name="acmer-notify"),
            ]
        except BaseException:
            await self.terminate()
            raise

    async def sync_loop(self):
        while True:
            try:
                await self.service.refresh()
            except Exception:
                logger.exception("赛历同步循环异常")
            await asyncio.sleep(self.settings.sync_interval_minutes * 60)

    async def send(self, target, text):
        return await self.context.send_message(target, MessageChain().message(text))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_group(self, event: AstrMessageEvent):
        """自动识别白名单群聊，无需发送绑定指令。"""
        await self.router.observe(event)

    def enabled(self, event):
        return self.settings.allows(event)

    @filter.command("赛历", alias={"近期比赛", "比赛"})
    async def calendar(self, event: AstrMessageEvent):
        """查询未来数日赛事；/赛历 全部 不受本群推送范围限制。"""
        if not self.enabled(event):
            return
        await self.service.refresh()
        now = datetime.now(timezone.utc)
        contests = self.service.contests(now.timestamp())
        if "全部" not in arguments(event):
            prefs = await load_preferences(self.store, self.settings, event_session(event))
            contests = prefs.filter(contests)
        for page in digest(contests, now, self.settings, self.service.notes(now.timestamp())):
            yield event.plain_result(page)

    @filter.command("赛历帮助")
    async def help(self, event: AstrMessageEvent):
        """插件使用说明。"""
        if not self.enabled(event):
            return
        yield event.plain_result(HELP)

    @filter.command("赛历设置")
    async def show_settings(self, event: AstrMessageEvent):
        """查看本群推送设置及来源。"""
        if not self.enabled(event):
            return
        prefs = await load_preferences(self.store, self.settings, event_session(event))
        yield event.plain_result(
            "【本群赛历设置】\n" + prefs.describe() + "\n发送 /赛历帮助 查看用法"
        )

    @filter.command("赛历范围")
    async def set_scope(self, event: AstrMessageEvent):
        """/赛历范围 新手|全部"""
        async for result in self.update(event, "scope", self.parse_scope):
            yield result

    @filter.command("赛历平台")
    async def set_platforms(self, event: AstrMessageEvent):
        """/赛历平台 cf atc …；/赛历平台 全部"""
        async for result in self.update(event, "platforms", self.parse_platforms):
            yield result

    @filter.command("赛历屏蔽")
    async def block(self, event: AstrMessageEvent):
        """/赛历屏蔽 关键词 …"""
        async for result in self.update(event, "blocked", self.parse_block(True)):
            yield result

    @filter.command("赛历取消屏蔽")
    async def unblock(self, event: AstrMessageEvent):
        """/赛历取消屏蔽 关键词 …"""
        async for result in self.update(event, "blocked", self.parse_block(False)):
            yield result

    @filter.command("赛历重置")
    async def reset(self, event: AstrMessageEvent):
        """本群设置恢复为后台默认值。"""
        if not self.enabled(event):
            return
        if not self.can_manage(event):
            yield event.plain_result(DENIED)
            return
        session = event_session(event)
        await self.store.reset_settings(session)
        prefs = await load_preferences(self.store, self.settings, session)
        yield event.plain_result("已恢复为后台默认设置。\n" + prefs.describe())

    async def update(self, event, key, parse):
        if not self.enabled(event):
            return
        if not self.can_manage(event):
            yield event.plain_result(DENIED)
            return
        session = event_session(event)
        prefs = await load_preferences(self.store, self.settings, session)
        value, error = parse(arguments(event), prefs)
        if error:
            yield event.plain_result(error)
            return
        await self.store.set_setting(session, key, value)
        prefs = await load_preferences(self.store, self.settings, session)
        yield event.plain_result("已更新本群设置。\n" + prefs.describe())

    @staticmethod
    def parse_scope(words, prefs):
        scope = {name: key for key, name in SCOPES.items()}.get(words[0] if words else "")
        if scope is None:
            return None, "用法：/赛历范围 新手 或 /赛历范围 全部"
        return scope, None

    def parse_platforms(self, words, prefs):
        if words == ["全部"]:
            return list(self.settings.enabled_platforms), None
        chosen = [PLATFORM_ALIASES.get(w.casefold()) for w in words]
        if not words or None in chosen:
            names = "cf、atc、nc（牛客）、lc（力扣）、lg（洛谷）"
            return None, f"用法：/赛历平台 cf atc …，可用平台：{names}；或 /赛历平台 全部"
        return list(dict.fromkeys(chosen)), None

    @staticmethod
    def parse_block(add):
        def parse(words, prefs):
            if not words:
                return None, f"用法：/赛历{'' if add else '取消'}屏蔽 关键词，例如 AHC"
            if add:
                return list(dict.fromkeys([*prefs.blocked, *words])), None
            return [w for w in prefs.blocked if w not in words], None

        return parse

    def can_manage(self, event):
        """群主/群管理员、后台指定用户和 AstrBot 管理员可改设置；私聊用户可改自己的设置。"""
        try:
            if event.is_admin():
                return True
        except AttributeError:
            pass
        if str(event.get_sender_id()) in self.settings.settings_admin_ids:
            return True
        if not event.get_group_id():
            return True
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        sender = raw.get("sender") if isinstance(raw, dict) else getattr(raw, "sender", None)
        role = sender.get("role") if isinstance(sender, dict) else getattr(sender, "role", None)
        return role in {"owner", "admin"}

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新赛历")
    async def refresh(self, event: AstrMessageEvent):
        """管理员强制刷新，失败的平台继续保留缓存。"""
        if not self.enabled(event):
            return
        await self.service.refresh(force=True)
        now = datetime.now(timezone.utc).timestamp()
        failed = len(self.service.errors)
        yield event.plain_result(
            f"同步完成，失败平台 {failed} 个。\n"
            + (self.service.notes(now) or "已启用的平台均同步成功。")
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("赛历状态")
    async def status(self, event: AstrMessageEvent):
        """查看数据源、目标数量和待人工处置的发送记录。"""
        if not self.enabled(event):
            return
        now = datetime.now(timezone.utc).timestamp()
        lines = [
            f"启用会话数：{len(self.settings.enabled_session_ids)}；"
            f"已识别推送目标数：{len(await self.router.targets())}"
        ]
        for platform in self.settings.enabled_platforms:
            snapshot = self.service.snapshots.get(platform)
            if snapshot:
                updated = datetime.fromtimestamp(snapshot[0], self.settings.zone)
                lines.append(f"{platform}：{len(snapshot[1])} 场，上次成功 {updated:%m-%d %H:%M}")
        lines.append(self.service.notes(now))
        for row in await self.store.unresolved():
            lines.append(f"{row['status']} ({row['attempts']} 次)：{row['key']}")
        for page in split_message("\n".join(lines), self.settings.message_max_chars):
            yield event.plain_result(page)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("处理赛历通知")
    async def resolve(self, event: AstrMessageEvent, key: str, action: str):
        """/处理赛历通知 记录ID retry 或 sent；重试仍受有效窗口约束。"""
        if not self.enabled(event):
            return
        if action not in {"retry", "sent"}:
            yield event.plain_result("操作仅支持 retry（重试）或 sent（确认已发送）。")
            return
        changed = await self.store.resolve(key, action == "retry")
        yield event.plain_result("状态已更新。" if changed else "未找到可处理的异常记录。")

    async def terminate(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        if self.http:
            await self.http.close()
            self.http = None
        await self.store.close()
