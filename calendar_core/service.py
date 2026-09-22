import asyncio
import time

from .models import PLATFORMS, Contest


class CalendarService:
    def __init__(self, config, store, fetchers, logger):
        self.config, self.store, self.fetchers, self.logger = config, store, fetchers, logger
        self.snapshots = {}
        self.errors = {}
        self.lock = asyncio.Lock()
        self.last_attempt = 0

    async def load(self):
        for platform, (updated, rows) in (await self.store.snapshots()).items():
            self.snapshots[platform] = (updated, [Contest(**c) for c in rows])

    async def refresh(self, force=False):
        async with self.lock:
            now = time.time()
            if not force and now - self.last_attempt < self.config.sync_interval_minutes * 60:
                return
            self.last_attempt = now

            async def fetch_one(platform):
                try:
                    rows = await self.fetchers.fetch(platform)
                    unique = {c.key: c for c in rows if c.start_time > now}
                    rows = list(unique.values())
                    await self.store.save_snapshot(platform, now, rows)
                    self.snapshots[platform] = (now, rows)
                    self.errors.pop(platform, None)
                except Exception as error:
                    self.errors[platform] = type(error).__name__
                    # 不在日志泄漏自定义源 URL 中的凭据。
                    self.logger.warning("赛历同步失败 %s: %s", platform, type(error).__name__)

            await asyncio.gather(*(fetch_one(p) for p in self.config.enabled_platforms))

    def contests(self, now, trusted_only=False):
        rows = []
        for platform in self.config.enabled_platforms:
            snapshot = self.snapshots.get(platform)
            if snapshot is None:
                continue
            updated, contests = snapshot
            if trusted_only and now - updated > self.config.cache_max_age_hours * 3600:
                continue
            rows.extend(c for c in contests if c.start_time > now)
        return sorted(rows, key=lambda c: (c.start_time, c.platform, c.id))

    def notes(self, now):
        result = []
        for platform in self.config.enabled_platforms:
            name = PLATFORMS[platform]
            snapshot = self.snapshots.get(platform)
            if snapshot is None:
                result.append(f"{name}：尚未取得数据")
            elif now - snapshot[0] > self.config.cache_max_age_hours * 3600:
                result.append(f"{name}：缓存过期，自动推送已排除此平台")
            elif platform in self.errors:
                result.append(f"{name}：同步失败，使用缓存")
        return "\n".join(result)

    def has_trusted_data(self, now):
        return any(
            p in self.snapshots
            and now - self.snapshots[p][0] <= self.config.cache_max_age_hours * 3600
            for p in self.config.enabled_platforms
        )
