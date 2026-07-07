"""Style pack loading — tokens, FLUX prompt prefixes, bundled fonts, CSS.

A style pack is a directory under ``style_packs/`` (config.style_packs_dir):

    style_packs/<name>/
        tokens.json      required — {"palette": {...}, "type": {...}, "motion": {...}}
        flux_style.txt   optional — line 1: prefix, line 2 (optional): suffix
        fonts/*.woff2    optional — bundled OFL fonts, base64-embedded at build
        runtime.css      optional — CSS overrides appended to the collage runtime

Frozen signatures (docs/collage/CONTRACTS.md): ``load_style_pack`` and
``list_style_packs``. The assets/style-packs workstream extends the returned
data; the shape below is the minimum every consumer can rely on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StylePack:
    name: str
    dir: Path
    tokens: dict = field(default_factory=dict)
    flux_prefix: str = ""
    flux_suffix: str = ""
    fonts: list[Path] = field(default_factory=list)
    css: str = ""

    @property
    def palette(self) -> dict:
        return self.tokens.get("palette", {})


def _style_packs_dir(style_packs_dir: Path | None) -> Path:
    if style_packs_dir is not None:
        return Path(style_packs_dir)
    from src.config import PipelineConfig

    return Path(PipelineConfig().style_packs_dir)


def load_style_pack(name: str, style_packs_dir: Path | None = None) -> StylePack:
    """Load a style pack by directory name. Raises FileNotFoundError with the
    list of available packs when the name is unknown."""
    root = _style_packs_dir(style_packs_dir)
    pack_dir = root / name
    tokens_path = pack_dir / "tokens.json"
    if not tokens_path.exists():
        available = sorted(p.parent.name for p in root.glob("*/tokens.json"))
        raise FileNotFoundError(
            f"Style pack {name!r} not found in {root} (available: {available or 'none'})"
        )
    tokens = json.loads(tokens_path.read_text())

    flux_prefix = ""
    flux_suffix = ""
    flux_path = pack_dir / "flux_style.txt"
    if flux_path.exists():
        lines = [line.strip() for line in flux_path.read_text().splitlines() if line.strip()]
        if lines:
            flux_prefix = lines[0]
        if len(lines) > 1:
            flux_suffix = lines[1]

    fonts = sorted((pack_dir / "fonts").glob("*.woff2")) if (pack_dir / "fonts").is_dir() else []
    css_path = pack_dir / "runtime.css"
    css = css_path.read_text() if css_path.exists() else ""

    return StylePack(
        name=name,
        dir=pack_dir,
        tokens=tokens,
        flux_prefix=flux_prefix,
        flux_suffix=flux_suffix,
        fonts=fonts,
        css=css,
    )


def list_style_packs(style_packs_dir: Path | None = None) -> list[dict]:
    """Summaries for the API/UI: [{"id", "name", "description", "palette"}]."""
    root = _style_packs_dir(style_packs_dir)
    packs: list[dict] = []
    if not root.is_dir():
        return packs
    for tokens_path in sorted(root.glob("*/tokens.json")):
        try:
            tokens = json.loads(tokens_path.read_text())
        except json.JSONDecodeError:
            continue
        pack_id = tokens_path.parent.name
        packs.append(
            {
                "id": pack_id,
                "name": tokens.get("name", pack_id),
                "description": tokens.get("description", ""),
                "palette": tokens.get("palette", {}),
            }
        )
    return packs
