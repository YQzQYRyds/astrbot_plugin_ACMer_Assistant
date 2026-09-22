import json
import re
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .models import PLATFORMS

SCHEMA = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text("utf-8"))


class Settings:
    """配置面板 schema 是默认值的唯一来源，启动时严格校验。"""

    def __init__(self, raw):
        values = {k: raw.get(k, item["default"]) for k, item in SCHEMA.items()}
        for key, item in SCHEMA.items():
            value = values[key]
            kind = item["type"]
            if kind == "int":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{key} 必须为整数")
                low, high = item["range"]
                if not low <= value <= high:
                    raise ValueError(f"{key} 必须在 {low}..{high} 之间")
            elif kind == "bool" and not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            elif kind in {"string", "text"} and not isinstance(value, str):
                raise ValueError(f"{key} 必须为字符串")
            elif kind == "list" and not isinstance(value, list):
                raise ValueError(f"{key} 必须为数组")
        for key in ("target_groups", "target_sessions", "enabled_platforms"):
            if any(not isinstance(v, str) or not v.strip() for v in values[key]):
                raise ValueError(f"{key} 的成员必须为非空字符串")
            values[key] = tuple(dict.fromkeys(v.strip() for v in values[key]))
        if set(values["enabled_platforms"]) - PLATFORMS.keys():
            raise ValueError("enabled_platforms 含有未知平台")
        notices = values["advance_notice_minutes"]
        # AstrBot list 编辑器可能保存字符串，显式接受整数字符串。
        if any(isinstance(v, bool) or not re.fullmatch(r"\d+", str(v)) for v in notices):
            raise ValueError("提前提醒节点必须为正整数分钟")
        values["advance_notice_minutes"] = tuple(sorted({int(v) for v in notices}))
        if any(not 1 <= v <= 10080 for v in values["advance_notice_minutes"]):
            raise ValueError("提前提醒节点范围为 1..10080 分钟")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", values["daily_push_time"]):
            raise ValueError("daily_push_time 必须为 HH:MM")
        if values["uncertain_delivery_policy"] not in {"hold", "retry"}:
            raise ValueError("uncertain_delivery_policy 必须为 hold 或 retry")
        values["zone"] = ZoneInfo(values["timezone"])
        for platform in PLATFORMS:
            parsed = urlparse(values[f"{platform}_url"])
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{platform}_url 必须是 HTTP(S) URL")
        for key in ("target_sessions",):
            if any(len(v.split(":", 2)) != 3 for v in values[key]):
                raise ValueError("target_sessions 需要完整 unified_msg_origin")
        self._values = MappingProxyType(values)

    def __getattr__(self, key):
        return self._values[key]
