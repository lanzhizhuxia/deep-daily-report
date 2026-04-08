from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


class FilePublisher:

    def publish(
        self,
        date_str: str,
        html_path: str,
        one_liner: str,
        topic_titles: list[str],
        panorama_md: str,
        **kwargs: Any,
    ) -> bool:
        path = Path(html_path)
        if not path.exists():
            print(f"[FilePublisher] HTML not found: {html_path}", file=sys.stderr)
            return False
        size_kb = path.stat().st_size / 1024
        print(f"[FilePublisher] {date_str} -> {html_path} ({size_kb:.1f} KB)")
        print(f"  one_liner: {one_liner}")
        print(f"  topics: {', '.join(topic_titles)}")
        return True
