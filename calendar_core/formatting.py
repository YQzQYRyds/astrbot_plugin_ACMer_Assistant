import math
from datetime import timedelta

from .models import PLATFORMS


def clean(text):
    return " ".join(text.split())


def reminder(contest, now, config):
    start = contest.local_start(config.zone)
    minutes = max(1, math.ceil((contest.start_time - now.timestamp()) / 60))
    return (
        "【赛事提醒】\n"
        f"🏆 比赛：{clean(contest.title)}\n"
        f"🏷️ 平台：{PLATFORMS[contest.platform]}\n"
        f"⏱️ 开赛时间：{start:%Y-%m-%d %H:%M}（{config.timezone}，约 {minutes} 分钟后）\n"
        f"⏳ 比赛时长：{contest.duration_seconds / 3600:g} 小时\n"
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
    lines = [f"📅【近期算法赛事汇总】\n时区：{config.timezone}"]
    if notes:
        lines.append(notes)
    previous = None
    for contest in selected:
        start = contest.local_start(config.zone)
        day = start.date()
        if day != previous:
            delta = (day - today).days
            label = "今天" if delta == 0 else "明天" if delta == 1 else f"{day:%m-%d}"
            lines.append(f"--- 📌 {label}（{day:%Y-%m-%d}）---")
            previous = day
        lines.append(
            f"• [{PLATFORMS[contest.platform]}] {clean(contest.title)} | {start:%H:%M}\n"
            f"  ⏳ {contest.duration_seconds / 3600:g} 小时\n🔗 {contest.url}"
        )
    if not selected:
        lines.append(f"已获取的数据中，未来 {config.digest_days} 个日历日暂无未开赛赛事。")
    return split_message("\n".join(lines), config.message_max_chars)


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
