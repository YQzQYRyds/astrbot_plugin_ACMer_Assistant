"""每个会话的推送范围：后台配置是默认值，群内指令改过的项只覆盖本会话。"""

from .config import normalize
from .models import PLATFORMS

SCOPES = {"all": "全部", "beginner": "新手"}
PLATFORM_ALIASES = {
    **{p: p for p in PLATFORMS},
    **{normalize(name): p for p, name in PLATFORMS.items()},
    "cf": "codeforces",
    "at": "atcoder",
    "atc": "atcoder",
    "nc": "nowcoder",
    "nowcoder": "nowcoder",
    "lc": "leetcode",
    "力扣": "leetcode",
    "lg": "luogu",
}
KEYS = ("scope", "platforms", "blocked")


def session_key(target):
    """推送目标或简写会话 → 与机器人实例无关的会话标识，多机器人下设置共享。"""
    parts = target.split(":", 2)
    if len(parts) == 3:
        return f"{'group' if parts[1] == 'GroupMessage' else 'private'}:{parts[2]}"
    return target


def event_session(event):
    group = str(event.get_group_id() or "")
    return f"group:{group}" if group else f"private:{event.get_sender_id()}"


def is_long(contest, config):
    return contest.duration_seconds > config.long_contest_hours * 3600


class Preferences:
    def __init__(self, config, overrides=None):
        overrides = overrides or {}
        self.config = config
        self.scope = overrides.get("scope", config.default_scope)
        self.platforms = tuple(
            p
            for p in overrides.get("platforms", config.enabled_platforms)
            if p in config.enabled_platforms
        )
        self.blocked = tuple(overrides.get("blocked", config.default_blocked_keywords))
        self.sources = {k: "本群" if k in overrides else "后台默认" for k in KEYS}

    def allows(self, contest):
        if contest.platform not in self.platforms:
            return False
        text = normalize(f"{contest.title} {contest.id}")
        if any(normalize(word) in text for word in self.blocked):
            return False
        if self.scope == "beginner":
            exclude = self.config.beginner_exclude.get(contest.platform, ())
            include = self.config.beginner_include.get(contest.platform, ())
            return not any(k in text for k in exclude) and any(k in text for k in include)
        return True

    def filter(self, contests):
        return [c for c in contests if self.allows(c)]

    def describe(self):
        names = "、".join(PLATFORMS[p] for p in self.platforms) or "无"
        return "\n".join(
            [
                f"推送范围：{SCOPES[self.scope]}（{self.sources['scope']}）",
                f"平台：{names}（{self.sources['platforms']}）",
                f"屏蔽关键词：{'、'.join(self.blocked) or '无'}（{self.sources['blocked']}）",
            ]
        )


async def load(store, config, session):
    return Preferences(config, await store.settings(session))
