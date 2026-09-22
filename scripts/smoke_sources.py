"""手动联网检查五个源；不发送消息，也不修改生产缓存。"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calendar_core.config import Settings  # noqa: E402
from calendar_core.fetchers import Fetchers  # noqa: E402
from calendar_core.http import HTTPClient  # noqa: E402


async def main():
    settings = Settings({"http_retries": 0, "trust_env_proxy": "--proxy-env" in sys.argv})
    http = HTTPClient(settings)
    fetchers = Fetchers(http, settings)

    async def check(platform):
        try:
            contests = await fetchers.fetch(platform)
            future = [c for c in contests if c.start_time > datetime.now(timezone.utc).timestamp()]
            return {
                "platform": platform,
                "ok": True,
                "fetched": len(contests),
                "upcoming": len(future),
                "sample": future[0].to_dict() if future else None,
            }
        except Exception as error:
            return {"platform": platform, "ok": False, "error": type(error).__name__}

    try:
        results = await asyncio.gather(*(check(p) for p in settings.enabled_platforms))
        print(
            json.dumps(
                {"checked_at": datetime.now(timezone.utc).isoformat(), "results": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if all(r["ok"] for r in results) else 1
    finally:
        await http.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
