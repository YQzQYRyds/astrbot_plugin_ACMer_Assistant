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
from .calendar_core.scheduler import Scheduler
from .calendar_core.service import CalendarService
from .calendar_core.storage import Store


class ACMerCalendar(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = Settings(config)
        self.tasks = []
        self.http = None
        self.store = Store(
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_acmer_calendar"
            / "calendar.sqlite3"
        )

    async def initialize(self):
        try:
            await self.store.open(self.settings.uncertain_delivery_policy)
            self.http = HTTPClient(self.settings)
            self.service = CalendarService(
                self.settings, self.store, Fetchers(self.http, self.settings), logger
            )
            await self.service.load()
            self.scheduler = Scheduler(self.settings, self.store, self.service, self.send, logger)
            self.tasks = [
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

    @filter.command("赛历", alias={"近期比赛"})
    async def calendar(self, event: AstrMessageEvent):
        """查询未来数日赛事；缓存到期时刷新。"""
        await self.service.refresh()
        now = datetime.now(timezone.utc)
        for page in digest(
            self.service.contests(now.timestamp()),
            now,
            self.settings,
            self.service.notes(now.timestamp()),
        ):
            yield event.plain_result(page)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新赛历")
    async def refresh(self, event: AstrMessageEvent):
        """管理员强制刷新，失败的平台继续保留缓存。"""
        await self.service.refresh(force=True)
        now = datetime.now(timezone.utc).timestamp()
        failed = len(self.service.errors)
        yield event.plain_result(
            f"同步完成，失败平台 {failed} 个。\n"
            + (self.service.notes(now) or "已启用的平台均同步成功。")
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("绑定赛历")
    async def bind(self, event: AstrMessageEvent):
        """将白名单内群号绑定到当前真实会话；一个群号绑定一个会话。"""
        group = str(event.get_group_id() or "")
        if not group or group not in self.settings.target_groups:
            yield event.plain_result("请先在 target_groups 中添加当前群号，重载插件后在群内绑定。")
            return
        await self.store.bind(group, event.unified_msg_origin)
        yield event.plain_result("赛历已绑定当前群会话。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("赛历会话")
    async def session(self, event: AstrMessageEvent):
        """查看可填入 target_sessions 的当前会话标识。"""
        yield event.plain_result(event.unified_msg_origin)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("赛历状态")
    async def status(self, event: AstrMessageEvent):
        """查看数据源、目标数量和待人工处置的发送记录。"""
        now = datetime.now(timezone.utc).timestamp()
        lines = [f"已绑定目标数：{len(await self.store.targets(self.settings))}"]
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
