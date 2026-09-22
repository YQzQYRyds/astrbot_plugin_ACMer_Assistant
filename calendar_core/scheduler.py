import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

from .formatting import digest, reminder


class DeliveryRejected(Exception):
    """适配器明确没有找到目标平台，确定没有发送。"""


def target_key(target):
    return hashlib.sha256(target.encode()).hexdigest()[:20]


class Scheduler:
    def __init__(self, config, store, service, sender, logger, targets):
        self.config, self.store, self.service = config, store, service
        self.sender, self.logger = sender, logger
        self.tick_lock = asyncio.Lock()
        self.targets = targets

    async def deliver(self, row, now, payload=None):
        # 多目标发送可能耗时较长，每条发送前重新检查有效期。
        now = max(now, time.time())
        if not await self.store.claim(row["key"], now, self.config.send_max_attempts):
            return
        try:
            result = await asyncio.wait_for(
                self.sender(row["target"], payload or row["payload"]),
                min(self.config.send_timeout_seconds, row["expires"] - now),
            )
            if result is False:
                raise DeliveryRejected()
        except DeliveryRejected:
            state = "failed" if row["attempts"] + 1 >= self.config.send_max_attempts else "pending"
            await self.store.finish(row["key"], state, now + self.config.send_retry_seconds)
        except Exception as error:
            # 超时/网络错误可能已产生外部副作用，不能当作确定未发送。
            state = "uncertain"
            if self.config.uncertain_delivery_policy == "retry":
                state = (
                    "failed" if row["attempts"] + 1 >= self.config.send_max_attempts else "pending"
                )
            await self.store.finish(row["key"], state, now + self.config.send_retry_seconds)
            self.logger.warning("赛历消息发送结果不确定 %s: %s", row["key"], type(error).__name__)
        else:
            # CancelledError 或落盘失败会保留 sending，供重启恢复处理。
            await self.store.finish(row["key"], "sent")

    async def tick(self, now=None):
        async with self.tick_lock:
            now = now or datetime.now(timezone.utc)
            stamp = now.timestamp()
            targets = await self.targets()
            contests = self.service.contests(stamp, trusted_only=True)
            for contest in contests:
                remaining = (contest.start_time - stamp) / 60
                due = [
                    m
                    for m in self.config.advance_notice_minutes
                    if 0 < remaining <= m and m - remaining <= self.config.reminder_catchup_minutes
                ]
                if not due:
                    continue
                # 多个节点均已错过时只处理最近节点，旧节点永远不会随后补刷。
                minute = min(due)
                body = reminder(contest, now, self.config)
                for target in targets:
                    key = f"notice|{contest.key}|{minute}|{target_key(target)}"
                    await self.store.enqueue([(key, target, body, contest.start_time)])
                    rows = await self.store.jobs(key)
                    for row in rows:
                        await self.deliver(row, stamp, body)
            await self.daily(now, targets, contests)
            await self.store.cleanup(stamp - self.config.state_retention_days * 86400)

    async def daily(self, now, targets, contests):
        if not self.config.daily_push_enabled or not self.service.has_trusted_data(now.timestamp()):
            return
        local = now.astimezone(self.config.zone)
        hour, minute = map(int, self.config.daily_push_time.split(":"))
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elapsed = now.timestamp() - scheduled.timestamp()
        if not 0 <= elapsed <= self.config.daily_catchup_minutes * 60:
            return
        expires = min(
            scheduled.timestamp() + self.config.daily_catchup_minutes * 60,
            (scheduled + timedelta(days=1)).replace(hour=0, minute=0).timestamp(),
        )
        for target in targets:
            prefix = f"daily|{local.date()}|{target_key(target)}|"
            if not await self.store.exists_prefix(prefix):
                pages = digest(contests, now, self.config, self.service.notes(now.timestamp()))
                await self.store.enqueue(
                    [
                        (prefix + str(i).zfill(4), target, body, expires)
                        for i, body in enumerate(pages)
                    ]
                )
            for row in await self.store.jobs(prefix):
                await self.deliver(row, now.timestamp())

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                self.logger.exception("赛历调度异常，将在下一轮继续")
            await asyncio.sleep(self.config.scheduler_tick_seconds)
