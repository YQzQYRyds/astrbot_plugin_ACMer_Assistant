from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

PLATFORMS = {
    "codeforces": "Codeforces",
    "atcoder": "AtCoder",
    "nowcoder": "牛客",
    "leetcode": "LeetCode",
    "luogu": "洛谷",
}


@dataclass(frozen=True)
class Contest:
    id: str
    platform: str
    title: str
    start_time: int  # UTC Unix 秒；展示时才转换时区。
    duration_seconds: int
    url: str

    def __post_init__(self):
        if self.platform not in PLATFORMS or not self.id or not self.title.strip():
            raise ValueError("比赛缺少标识、平台或标题")
        if self.start_time <= 0 or self.duration_seconds <= 0:
            raise ValueError("比赛时间或时长无效")
        if urlparse(self.url).scheme not in {"http", "https"} or not urlparse(self.url).netloc:
            raise ValueError("比赛链接必须为 HTTP(S) URL")

    @property
    def key(self):
        return f"{self.platform}:{self.id}:{self.start_time}"

    def local_start(self, zone):
        return datetime.fromtimestamp(self.start_time, timezone.utc).astimezone(zone)

    def to_dict(self):
        return asdict(self)
