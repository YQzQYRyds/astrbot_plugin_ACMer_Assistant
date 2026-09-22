import math
import re
from datetime import timedelta

from .models import PLATFORMS


def clean(text):
    return " ".join(text.split())


def duration(seconds):
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds_left = divmod(rest, 60)
    text = "".join(
        f"{n}{unit}"
        for n, unit in ((days, "天"), (hours, "小时"), (minutes, "分"), (seconds_left, "秒"))
        if n
    )
    return f"{text}/{seconds / 3600:g}h" if days else text


def short_title(contest):
    title = clean(contest.title)
    if contest.platform == "atcoder":
        match = re.fullmatch(r"(abc|arc|agc|ahc)(\d+)", contest.id, re.I)
        if match:
            return f"{match[1].upper()} {match[2]}"
    if contest.platform == "codeforces":
        title = re.sub(r"Codeforces\s+Round\s*#?", "Round ", title, flags=re.I)
    return clean(title)


def display_entries(contests):
    """仅合并同名、同时间、同时长、明确带轮次的 CF Div.1/2。"""
    buckets = {}
    for contest in contests:
        match = re.fullmatch(r"(.+?)\s*\(Div\.?\s*([12])\)", clean(contest.title), re.I)
        if (
            contest.platform == "codeforces"
            and match
            and re.search(r"Round\s*#?\d+", match[1], re.I)
        ):
            key = (match[1].strip().casefold(), contest.start_time, contest.duration_seconds)
            buckets.setdefault(key, []).append((contest, match[2]))
    merged = {}
    for pairs in buckets.values():
        if len(pairs) == 2 and {division for _, division in pairs} == {"1", "2"}:
            pair = sorted(pairs, key=lambda item: item[1])
            for contest, _ in pair:
                merged[contest.key] = pair
    entries, seen = [], set()
    for contest in contests:
        if contest.key in seen:
            continue
        pairs = merged.get(contest.key, [(contest, None)])
        seen.update(c.key for c, _ in pairs)
        entries.append(pairs)
    return entries


def reminder(contest, now, config):
    start = contest.local_start(config.zone)
    minutes = max(1, math.ceil((contest.start_time - now.timestamp()) / 60))
    return (
        "【赛事提醒】\n"
        f"🏆 比赛：{clean(contest.title)}\n"
        f"🏷️ 平台：{PLATFORMS[contest.platform]}\n"
        f"⏱️ 开赛时间：{start:%Y-%m-%d %H:%M}（{config.timezone}，约 {minutes} 分钟后）\n"
        f"⏳ 比赛时长：{duration(contest.duration_seconds)}\n"
        f"🔗 比赛链接：{contest.url}"
    )


def digest(contests, now, config, notes=""):
    today = now.astimezone(config.zone).date()
    end_date = today + timedelta(days=config.digest_days)
    selected = sorted(
        (
            c
            for c in contests
            if c.start_time > now.timestamp()
            and today <= c.local_start(config.zone).date() < end_date
        ),
        key=lambda c: (c.start_time, c.platform, c.id),
    )
    entries = display_entries(selected)
    shown = entries[: config.digest_max_contests]
    header = "🏆 【近期算法赛事周报】"
    if config.timezone != "Asia/Shanghai":
        header += f"\n时区：{config.timezone}"
    pages, current, previous = [], header, None
    if notes:
        current += f"\n{notes}"
    for pairs in shown:
        contest = pairs[0][0]
        start = contest.local_start(config.zone)
        day = start.date()
        date_line = f"📅 {start:%m-%d}（周{'一二三四五六日'[start.weekday()]}）"
        title = short_title(contest)
        if len(pairs) > 1:
            title = re.sub(r"\s*\(Div\.?\s*[12]\)$", "", title, flags=re.I)
        links = "\n".join(
            f"🔗 Div.{division}: {c.url}" if division else f"🔗 {c.url}" for c, division in pairs
        )
        card = (
            f"• [{PLATFORMS[contest.platform]}] {title}\n"
            f"⏰ {start:%H:%M}（时长 {duration(contest.duration_seconds)}）\n{links}"
        )
        addition = f"\n\n{date_line}\n{card}" if day != previous else f"\n\n{card}"
        if len(current + addition) > config.message_max_chars:
            pages.extend(split_message(current, config.message_max_chars))
            current = f"{header}\n\n{date_line}\n{card}"
        else:
            current += addition
        previous = day
    if not shown:
        current += f"\n\n未来 {config.digest_days} 个日历日暂无已获取的未开赛赛事。"
    footer = "💡 记得提前报名参赛，祝大家把把上分、轻松 AC！"
    if len(entries) > len(shown):
        footer = (
            f"已显示最近 {len(shown)} 项，另有 {len(entries) - len(shown)} 项未展示。\n" + footer
        )
    if len(current + "\n\n" + footer) > config.message_max_chars:
        pages.extend(split_message(current, config.message_max_chars))
        current = footer
    else:
        current += "\n\n" + footer
    pages.extend(split_message(current, config.message_max_chars))
    return pages


def split_message(text, maximum):
    """按行分段；极长单行也受长度上限约束。"""
    pages, current = [], ""
    for line in text.splitlines():
        while len(line) > maximum:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:maximum])
            line = line[maximum:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > maximum:
            pages.append(current)
            current = line
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages
