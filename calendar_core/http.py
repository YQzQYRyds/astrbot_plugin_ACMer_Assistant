import asyncio
import json
import random

import aiohttp


class HTTPClient:
    def __init__(self, config):
        self.config = config
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=config.request_timeout_seconds),
            headers={"User-Agent": "ACMerCalendar/1.0 (+AstrBot contest calendar)"},
            trust_env=config.trust_env_proxy,
        )

    async def request(self, url, *, payload=None, headers=None):
        for attempt in range(self.config.http_retries + 1):
            try:
                async with self.session.request(
                    "POST" if payload is not None else "GET",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > self.config.max_response_bytes:
                            raise ValueError("数据源响应超过大小限制")
                    return body.decode("utf-8")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == self.config.http_retries:
                    raise
                await asyncio.sleep(self.config.retry_base_seconds * 2**attempt + random.random())

    async def json(self, url, **kwargs):
        return json.loads(await self.request(url, **kwargs))

    async def close(self):
        await self.session.close()
