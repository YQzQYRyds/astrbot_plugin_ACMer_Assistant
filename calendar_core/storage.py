import asyncio
import json

import aiosqlite


class Store:
    """SQLite 事务持久化；aiosqlite 在工作线程处理磁盘 I/O。"""

    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()
        self.db = None

    async def open(self, recovery_policy):
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS snapshots (
                platform TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bindings (
                group_id TEXT PRIMARY KEY, target TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notified_events (
                key TEXT PRIMARY KEY, target TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, expires REAL NOT NULL
            );
        """)
        state = "pending" if recovery_policy == "retry" else "uncertain"
        await self.db.execute(
            "UPDATE notified_events SET status=? WHERE status='sending'", (state,)
        )
        await self.db.commit()

    async def snapshots(self):
        async with self.lock:
            async with self.db.execute("SELECT * FROM snapshots") as cursor:
                return {
                    r["platform"]: (r["updated"], json.loads(r["data"]))
                    for r in await cursor.fetchall()
                }

    async def save_snapshot(self, platform, now, contests):
        async with self.lock:
            await self.db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                (platform, now, json.dumps([c.to_dict() for c in contests])),
            )
            await self.db.commit()

    async def bind(self, group, target):
        async with self.lock:
            await self.db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?)", (group, target))
            await self.db.commit()

    async def targets(self, config):
        async with self.lock:
            async with self.db.execute("SELECT * FROM bindings") as cursor:
                bound = {r["group_id"]: r["target"] for r in await cursor.fetchall()}
        targets = set(config.target_sessions)
        targets.update(bound[g] for g in config.target_groups if g in bound)
        return sorted(targets)

    async def enqueue(self, jobs):
        # 一次提交整个日报，重启后保持原始分页，避免数据变化引起重复或漏页。
        async with self.lock:
            await self.db.executemany(
                "INSERT OR IGNORE INTO notified_events(key,target,payload,expires) "
                "VALUES (?,?,?,?)",
                jobs,
            )
            await self.db.commit()

    async def exists_prefix(self, prefix):
        async with self.lock:
            async with self.db.execute(
                "SELECT 1 FROM notified_events WHERE substr(key,1,?)=? LIMIT 1",
                (len(prefix), prefix),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def claim(self, key, now, maximum):
        async with self.lock:
            await self.db.execute(
                "UPDATE notified_events SET status='failed' "
                "WHERE key=? AND status='pending' AND attempts>=?",
                (key, maximum),
            )
            cursor = await self.db.execute(
                "UPDATE notified_events SET status='sending',attempts=attempts+1 "
                "WHERE key=? AND status='pending' AND retry_at<=? AND expires>? AND attempts<?",
                (key, now, now, maximum),
            )
            await self.db.commit()
            return cursor.rowcount == 1

    async def finish(self, key, status, retry_at=0):
        async with self.lock:
            await self.db.execute(
                "UPDATE notified_events SET status=?,retry_at=? WHERE key=?",
                (status, retry_at, key),
            )
            await self.db.commit()

    async def jobs(self, prefix):
        async with self.lock:
            async with self.db.execute(
                "SELECT * FROM notified_events WHERE substr(key,1,?)=? ORDER BY key",
                (len(prefix), prefix),
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def cleanup(self, cutoff):
        async with self.lock:
            await self.db.execute("DELETE FROM notified_events WHERE expires<?", (cutoff,))
            await self.db.commit()

    async def unresolved(self):
        async with self.lock:
            async with self.db.execute(
                "SELECT key,status,attempts FROM notified_events "
                "WHERE status IN ('uncertain','failed') ORDER BY expires DESC LIMIT 20"
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def resolve(self, key, retry):
        async with self.lock:
            cursor = await self.db.execute(
                "UPDATE notified_events SET status=?,attempts=0,retry_at=0 "
                "WHERE key=? AND status IN ('uncertain','failed')",
                ("pending" if retry else "sent", key),
            )
            await self.db.commit()
            return cursor.rowcount == 1

    async def close(self):
        if self.db is not None:
            await self.db.close()
            self.db = None
