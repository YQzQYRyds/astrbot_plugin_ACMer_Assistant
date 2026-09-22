"""仅打包运行文件与说明，排除虚拟环境、测试数据和开发缓存。"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
output = root / "dist" / "astrbot_plugin_acmer_calendar.zip"
output.parent.mkdir(exist_ok=True)
files = [
    root / name
    for name in (
        "main.py",
        "metadata.yaml",
        "_conf_schema.json",
        "requirements.txt",
        "README.md",
        "LICENSE",
    )
]
files.extend((root / "calendar_core").glob("*.py"))
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(root).as_posix())
print(output)
