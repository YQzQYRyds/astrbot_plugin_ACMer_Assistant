"""由已连接机器人自动发现群聊，不要求用户手动绑定会话。"""

import asyncio
import time


class GroupRouter:
    def __init__(self, context, config, store, logger):
        self.context, self.config, self.store, self.logger = context, config, store, logger
        self.last_success = {}

    def platforms(self):
        return self.context.platform_manager.get_insts()

    def enabled(self, event):
        return self.config.allows(event)

    async def observe(self, event):
        if not self.enabled(event):
            return
        group, platform_id = str(event.get_group_id() or ""), event.get_platform_id()
        if not group:
            user = str(event.get_sender_id())
            await self.store.remember_route(
                f"private:{user}", platform_id, f"{platform_id}:FriendMessage:{user}"
            )
            return
        # 使用群会话，不能持久化 unique_session 下包含成员 ID 的个人会话。
        target = f"{platform_id}:GroupMessage:{group}"
        await self.store.remember_route(group, platform_id, target)

    async def discover(self):
        if not self.config.target_groups and not self.config.target_users:
            return

        async def one(platform):
            meta = platform.meta()
            if meta.name != "aiocqhttp":
                return
            if time.monotonic() - self.last_success.get(meta.id, float("-inf")) < (
                self.config.sync_interval_minutes * 60
            ):
                return
            try:
                routes = {}
                for action, field, wanted, prefix, kind in (
                    ("get_group_list", "group_id", self.config.target_groups, "", "GroupMessage"),
                    (
                        "get_friend_list",
                        "user_id",
                        self.config.target_users,
                        "private:",
                        "FriendMessage",
                    ),
                ):
                    if not wanted:
                        continue
                    rows = await asyncio.wait_for(
                        platform.get_client().call_action(action),
                        self.config.request_timeout_seconds,
                    )
                    if not isinstance(rows, list):
                        raise ValueError("会话列表返回结构异常")
                    routes.update(
                        {
                            prefix + str(r[field]): f"{meta.id}:{kind}:{r[field]}"
                            for r in rows
                            if str(r[field]) in wanted
                        }
                    )
                await self.store.replace_routes(meta.id, routes)
                self.last_success[meta.id] = time.monotonic()
            except Exception as error:
                self.logger.warning("赛历群列表暂不可用 %s: %s", meta.id, type(error).__name__)

        await asyncio.gather(*(one(p) for p in self.platforms()))

    async def targets(self):
        active = {p.meta().id for p in self.platforms()}
        # 同一群在多个机器人下可达时，按稳定的平台 ID 选一个，避免重复推送。
        selected = {}
        wanted = (*self.config.target_groups, *(f"private:{u}" for u in self.config.target_users))
        for row in await self.store.routes():
            if row["group_id"] in wanted and row["platform_id"] in active:
                selected.setdefault(row["group_id"], row["target"])
        explicit = [s for s in self.config.explicit_sessions if s.split(":", 1)[0] in active]
        return list(dict.fromkeys([*explicit, *(selected[g] for g in wanted if g in selected)]))

    async def run(self):
        while True:
            try:
                await self.discover()
            except Exception:
                self.logger.exception("赛历自动发现群聊失败，将自动重试")
            await asyncio.sleep(self.config.scheduler_tick_seconds)
