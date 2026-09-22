import asyncio
import os
import sqlite3


async def migrate_database(data_path, destination):
    """首次改名安装时复制旧数据库；使用 SQLite backup 保留 WAL 中已提交记录。"""
    old = data_path / "plugin_data" / "astrbot_plugin_acmer_calendar" / "calendar.sqlite3"

    def copy():
        if destination.exists() or not old.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".migrating")
        source = sqlite3.connect(old.as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.replace(temporary, destination)

    await asyncio.to_thread(copy)
