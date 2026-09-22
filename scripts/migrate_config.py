"""显式迁移旧配置到新插件名；保留原文件，不覆盖已经存在的目标文件。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calendar_core.config import SCHEMA, Settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    config = Settings(json.loads(args.source.read_text("utf-8-sig")))
    output = {key: getattr(config, key) for key in SCHEMA}
    with args.destination.open("x", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
