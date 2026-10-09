import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

from .formatting import daily, reminders
from .preferences import is_long, session_key
from .preferences import load as load_preferences

TODAY_UNTIL_HOUR = 6


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
            for target in targets:
                session = session_key(target)
                prefs = await load_preferences(self.store, self.config, session)
                await self.baseline(session, stamp)
                await self.remind(target, prefs.filter(contests), now)
                await self.daily(now, target, session, prefs.filter(contests))
            await self.store.cleanup(stamp - self.config.state_retention_days * 86400)

    async def baseline(self, session, stamp):
        """会话首次出现（新启用或从旧版升级）时静默记下窗口内已有比赛，不补推旧赛历。"""
        done = await self.store.baselined(session)
        for platform in self.config.enabled_platforms:
            snapshot = self.service.snapshots.get(platform)
            if platform in done or snapshot is None:
                continue
            rows = [c for c in snapshot[1] if c.start_time > stamp]
            await self.store.baseline(session, platform, self.window(rows, stamp), stamp)

    def window(self, contests, stamp):
        today = datetime.fromtimestamp(stamp, self.config.zone).date()
        end = today + timedelta(days=self.config.digest_days)
        return [c for c in contests if c.local_start(self.config.zone).date() < end]

    async def remind(self, target, contests, now):
        stamp = now.timestamp()
        tk = target_key(target)
        eligible = [c for c in contests if not is_long(c, self.config)]
        due = []
        for contest in eligible:
            remaining = (contest.start_time - stamp) / 60
            minutes = [
                m
                for m in self.config.advance_notice_minutes
                if 0 < remaining <= m and m - remaining <= self.config.reminder_catchup_minutes
            ]
            # 多个节点均已错过时只处理最近节点，旧节点永远不会随后补刷。
            if minutes:
                due.append((contest, min(minutes)))
        handled = set()
        window = self.config.reminder_merge_minutes * 60
        for contest, minute in due:
            if contest.key in handled:
                continue
            key = f"notice|{contest.key}|{minute}|{tk}"
            if key not in await self.store.existing([key]):
                # 同一节点下开赛时间接近、尚未提醒的比赛并入这一条，其余各自记占位防重。
                group = [
                    c
                    for c in eligible
                    if 0 <= c.start_time - contest.start_time <= window and c.key not in handled
                ]
                others = {f"notice|{c.key}|{minute}|{tk}": c for c in group if c is not contest}
                taken = await self.store.existing(list(others))
                group = [c for c in group if f"notice|{c.key}|{minute}|{tk}" not in taken]
                body = reminders(group, now, self.config)
                await self.store.enqueue([(key, target, body, contest.start_time)])
                await self.store.mark(
                    [(k, target, c.start_time) for k, c in others.items() if k not in taken]
                )
                handled.update(c.key for c in group)
            handled.add(contest.key)
            for row in await self.store.jobs(key):
                await self.deliver(row, stamp)

    async def daily(self, now, target, session, contests):
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
        prefix = f"daily|{local.date()}|{target_key(target)}|"
        if not await self.store.exists_prefix(prefix):
            seen = await self.store.seen(session)
            new = [
                c
                for c in self.window(contests, now.timestamp())
                if seen.get(f"{c.platform}:{c.id}") != c.start_time
            ]
            changed = {f"{c.platform}:{c.id}" for c in new if f"{c.platform}:{c.id}" in seen}
            # “今天”延长到次日 06:00，深夜开赛的场次也算进当天早上的日报。
            until = (scheduled + timedelta(days=1)).replace(hour=TODAY_UNTIL_HOUR, minute=0)
            fresh = {c.key for c in new}
            today = [
                c
                for c in contests
                if c.start_time <= until.timestamp()
                and c.key not in fresh
                and not is_long(c, self.config)
            ]
            pages = daily(
                new, today, now, self.config, self.service.notes(now.timestamp()), changed
            )
            # 没有内容也写一条已完成记录，当天不再反复计算或迟到补发。
            jobs = [
                (prefix + str(i).zfill(4), target, body, expires) for i, body in enumerate(pages)
            ]
            await self.store.enqueue_daily(
                jobs or [(prefix + "empty", target, "", expires)], session, new
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
