"""Shared discovery of collage work inside a run directory.

Used by the align/assets/collage commands to implement the frozen no-op
contract: when a run has no collage work, each command prints
``{"skipped": true}`` and exits 0 so the Studio producer (which raises on any
non-zero exit) can include the steps unconditionally.
"""

from __future__ import annotations

import json
from pathlib import Path


def collage_segment_ids(script_path: Path, run_dir: Path, only: str = "") -> list[str]:
    """Segment ids with collage work: visual_engine == "collage" in the script,
    or an existing scenes/{id}.collage.json spec file.

    ``only`` is an optional comma-separated filter (same convention as
    imagegen/videogen segment_ids arguments).
    """
    ids: list[str] = []
    seen: set[str] = set()

    if script_path.exists():
        try:
            script = json.loads(script_path.read_text())
        except json.JSONDecodeError:
            script = {}
        for seg in script.get("segments", []):
            seg_id = seg.get("segment_id", "")
            if seg.get("visual_engine") == "collage" and seg_id and seg_id not in seen:
                ids.append(seg_id)
                seen.add(seg_id)

    scenes_dir = run_dir / "scenes"
    if scenes_dir.is_dir():
        for spec_file in sorted(scenes_dir.glob("*.collage.json")):
            seg_id = spec_file.name[: -len(".collage.json")]
            if seg_id not in seen:
                ids.append(seg_id)
                seen.add(seg_id)

    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        ids = [i for i in ids if i in wanted]
    return ids


def print_skipped(reason: str) -> None:
    print(json.dumps({"skipped": True, "reason": reason}))
