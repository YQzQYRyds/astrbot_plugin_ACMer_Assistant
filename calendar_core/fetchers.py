"""平台协议集中于此；解析器为纯函数，可独立测试及替换。"""

import asyncio
import html
import json
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .models import Contest


def codeforces(data):
    if data.get("status") != "OK" or not isinstance(data.get("result"), list):
        raise ValueError("Codeforces API 结构异常")
    return [
        Contest(
            str(c["id"]),
            "codeforces",
            c["name"],
            int(c["startTimeSeconds"]),
            int(c["durationSeconds"]),
            f"https://codeforces.com/contest/{c['id']}",
        )
        for c in data["result"]
        if c["phase"] == "BEFORE"
    ]


def atcoder(body):
    soup = BeautifulSoup(body, "html.parser")
    section = soup.select_one("#contest-table-upcoming")
    if section is None:
        raise ValueError("AtCoder upcoming 区域缺失")
    result = []
    for row in section.select("tbody tr"):
        cells = row.select("td")
        link = cells[1].select_one('a[href^="/contests/"]')
        start = datetime.strptime(cells[0].get_text(strip=True), "%Y-%m-%d %H:%M:%S%z")
        hours, minutes = map(int, cells[2].get_text(strip=True).split(":"))
        contest_id = link["href"].strip("/").split("/")[-1]
        result.append(
            Contest(
                contest_id,
                "atcoder",
                link.get_text(strip=True),
                int(start.timestamp()),
                hours * 3600 + minutes * 60,
                f"https://atcoder.jp/contests/{contest_id}",
            )
        )
    return result


def nowcoder(body):
    soup = BeautifulSoup(body, "html.parser")
    nodes = soup.select(".platform-item[data-json]")
    if not nodes:
        raise ValueError("牛客赛历 data-json 区域缺失；保留旧缓存")
    result = []
    for node in nodes:
        c = json.loads(html.unescape(node["data-json"]))
        result.append(
            Contest(
                str(c["contestId"]),
                "nowcoder",
                c["contestName"],
                int(c["contestStartTime"]) // 1000,
                int(c["contestDuration"]) // 1000,
                f"https://ac.nowcoder.com/acm/contest/{c['contestId']}",
            )
        )
    return result


def leetcode(data):
    if data.get("errors"):
        raise ValueError("LeetCode GraphQL 返回错误")
    rows = data["data"]["contestUpcomingContests"]
    if not isinstance(rows, list):
        raise ValueError("LeetCode 赛历结构异常")
    return [
        Contest(
            c["titleSlug"],
            "leetcode",
            c["title"],
            int(c["startTime"]),
            int(c["duration"]),
            f"https://leetcode.cn/contest/{c['titleSlug']}/",
        )
        for c in rows
    ]


def luogu(data):
    # 新版 Lentille content-only 与旧版 currentData 均支持。
    container = data.get("currentData", data.get("data", data))
    rows = container["contests"]["result"]
    if not isinstance(rows, list):
        raise ValueError("洛谷比赛列表结构异常")
    return [
        Contest(
            str(c["id"]),
            "luogu",
            c["name"],
            int(c["startTime"]),
            int(c["endTime"]) - int(c["startTime"]),
            f"https://www.luogu.com.cn/contest/{c['id']}",
        )
        for c in rows
    ]


class Fetchers:
    def __init__(self, http, config):
        self.http, self.config = http, config

    async def fetch(self, platform):
        url = getattr(self.config, f"{platform}_url")
        if platform == "leetcode":
            data = await self.http.json(
                url,
                payload={
                    "query": "{ contestUpcomingContests { title titleSlug startTime duration } }"
                },
            )
            return leetcode(data)
        if platform == "codeforces":
            return codeforces(await self.http.json(url))
        if platform == "luogu":
            results = []
            for page in range(1, self.config.luogu_max_pages + 1):
                page_url = url + ("&" if "?" in url else "?") + urlencode({"page": page})
                data = await self.http.json(
                    page_url, headers={"x-lentille-request": "content-only"}
                )
                rows = luogu(data)
                results.extend(rows)
                container = data.get("currentData", data.get("data", data))
                count = container["contests"].get("count")
                if not rows or (count is not None and len(results) >= int(count)):
                    break
            return results
        body = await self.http.request(url)
        parser = {"atcoder": atcoder, "nowcoder": nowcoder}[platform]
        return await asyncio.to_thread(parser, body)
