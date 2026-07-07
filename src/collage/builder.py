"""Compile a CollageSpec into one self-contained deterministic HTML document.

``build_collage_html`` (frozen signature, docs/collage/CONTRACTS.md §5) resolves
every TimeRef against the REAL audio duration, resolves ``$palette.*`` colour
tokens and font families against the scene's style pack, verifies referenced
assets exist, then emits a single HTML file that inlines:

- the static runtime CSS + the pack's runtime.css + generated @font-face rules
  (fonts base64-embedded — no file references),
- the compiled scene object at ``window.__COLLAGE__``,
- the self-contained runtime JS implementing the ``window.seek(t)`` contract.

No fallbacks (CONTRACTS §3/§4/§5 "No-fallback rules"):
- a spec using ``$palette.*`` requires a resolvable style pack; unknown token →
  ValueError listing the valid tokens,
- every font family a text element needs must exist as a woff2 in the pack —
  missing families are ValueErrors (no system-stack substitution),
- a missing asset file is a ValueError listing every missing asset at once.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from src.assets.generate import asset_path
from src.collage.spec import (
    CollageSpec,
    LabelElement,
    LayerElement,
    MaskElement,
    NodeGraphElement,
    ParticlesElement,
    SplitElement,
    TypewriterElement,
    default_asset_seed,
)
from src.collage.timing import resolve_time
from src.studio.style_packs import StylePack, load_style_pack

_RUNTIME_DIR = Path(__file__).parent / "runtime"


def _norm_family(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# The CollageSpec font vocabulary is serif/sans/mono. Style packs may name their
# type roles either in that vocabulary or with the semantic display/label/body
# naming; map spec roles onto whichever keys the pack provides.
_ROLE_ALIASES = {
    "serif": ("serif", "display", "heading"),
    "sans": ("sans", "label", "body", "text"),
    "mono": ("mono", "code"),
}


def _family_of(type_tokens: dict, role: str) -> str | None:
    """Family name for a spec font role (serif/sans/mono). Accepts either a
    matching key or a semantic alias (display/label/body), and either the
    string shape ``{"mono": "IBM Plex Mono"}`` or the nested
    ``{"mono": {"family": "IBM Plex Mono", ...}}`` shape."""
    for key in _ROLE_ALIASES.get(role, (role,)):
        value = type_tokens.get(key)
        if isinstance(value, dict):
            fam = value.get("family")
            if fam:
                return str(fam)
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return None


class _PaletteResolver:
    def __init__(self, spec: CollageSpec, pack: StylePack | None) -> None:
        self._pack = pack
        self._palette: dict[str, Any] = pack.palette if pack else {}
        self._spec_id = spec.segment_id

    def color(self, value: str) -> str:
        if not isinstance(value, str) or not value.startswith("$palette."):
            return value
        name = value[len("$palette.") :]
        if self._pack is None:
            raise ValueError(
                f"segment {self._spec_id!r}: colour token {value!r} needs a style pack "
                "but none is set — add `style_pack` to the spec (or the script)."
            )
        if name not in self._palette:
            valid = ", ".join(sorted(self._palette)) or "(none)"
            raise ValueError(
                f"segment {self._spec_id!r}: unknown palette token {value!r}; "
                f"valid tokens: {valid}"
            )
        return str(self._palette[name])


def _used_font_roles(spec: CollageSpec) -> set[str]:
    roles: set[str] = set()
    for el in spec.elements:
        if isinstance(el, LabelElement):
            roles.add("mono" if el.style == "mono" else "sans")
        elif isinstance(el, TypewriterElement):
            roles.add(el.font)
        elif isinstance(el, NodeGraphElement):
            roles.add("sans")
        elif isinstance(el, SplitElement):
            if any(p.label for p in el.panels):
                roles.add("sans")
    return roles


def _resolve_fonts(spec: CollageSpec, pack: StylePack | None) -> tuple[dict[str, str], str, list[str]]:
    """Return (role->family map, generated @font-face+role CSS, family list).

    Every used role must map to a family named in the pack's ``type`` tokens, and
    every such family must have a woff2 in the pack's ``fonts/`` dir.
    """
    used = _used_font_roles(spec)
    if not used:
        return {}, "", []

    if pack is None:
        raise ValueError(
            f"segment {spec.segment_id!r}: text elements need bundled fonts but no "
            "style pack is set — add `style_pack` to the spec (or the script)."
        )

    type_tokens = pack.tokens.get("type", {}) if isinstance(pack.tokens, dict) else {}
    role_family: dict[str, str] = {}
    missing_roles: list[str] = []
    for role in sorted(used):
        fam = _family_of(type_tokens, role)
        if fam is None:
            missing_roles.append(role)
        else:
            role_family[role] = fam
    if missing_roles:
        raise ValueError(
            f"segment {spec.segment_id!r}: style pack {pack.name!r} type tokens are "
            f"missing font roles {missing_roles} (needed by this scene)."
        )

    # Match each family to a woff2 file in the pack.
    file_for: dict[str, Path] = {}
    missing_families: list[str] = []
    for fam in sorted(set(role_family.values())):
        fam_norm = _norm_family(fam)
        matches = [p for p in pack.fonts if _norm_family(p.stem).startswith(fam_norm)]
        if not matches:
            missing_families.append(fam)
            continue
        regular = [p for p in matches if "regular" in _norm_family(p.stem)]
        file_for[fam] = regular[0] if regular else matches[0]
    if missing_families:
        raise ValueError(
            f"segment {spec.segment_id!r}: style pack {pack.name!r} is missing woff2 "
            f"fonts for families {missing_families} in {pack.dir / 'fonts'} — "
            "determinism requires bundled fonts (no system-stack fallback)."
        )

    # @font-face rules (base64) + role class mappings.
    css_parts: list[str] = []
    for fam in sorted(file_for):
        data = base64.b64encode(file_for[fam].read_bytes()).decode("ascii")
        css_parts.append(
            "@font-face{font-family:'%s';font-style:normal;font-weight:normal;"
            "src:url(data:font/woff2;base64,%s) format('woff2');}" % (fam, data)
        )
    role_class = {"serif": "collage-font-serif", "sans": "collage-font-sans", "mono": "collage-font-mono"}
    for role, fam in sorted(role_family.items()):
        if role in role_class:
            css_parts.append(".%s{font-family:'%s';}" % (role_class[role], fam))
    if "sans" in role_family:
        sans = role_family["sans"]
        css_parts.append(
            ".collage-label,.collage-panel-label,.collage-ng-label{font-family:'%s';}" % sans
        )
        css_parts.append("body,.collage-frame{font-family:'%s';}" % sans)

    families = sorted(set(role_family.values()))
    return role_family, "".join(css_parts), families


def _asset_urls(spec: CollageSpec, run_dir: Path) -> dict[str, str]:
    """Map asset id -> HTML-relative URL, raising for every missing file at once."""
    urls: dict[str, str] = {}
    missing: list[str] = []
    for asset in spec.assets:
        if asset.src is not None:
            path = run_dir / asset.src
            url = "../../" + asset.src.lstrip("./")
        else:
            path = asset_path(run_dir, spec.segment_id, asset.id)
            url = f"../../assets/{spec.segment_id}/{asset.id}.png"
        if not path.exists():
            missing.append(f"{asset.id} (expected at {path})")
        urls[asset.id] = url
    if missing:
        raise ValueError(
            f"segment {spec.segment_id!r}: missing asset files:\n  - "
            + "\n  - ".join(missing)
            + "\nRun: uv run python -m src.pipeline assets <script.json> <run_dir>"
        )
    return urls


def build_collage_html(
    spec: CollageSpec,
    run_dir: Path,
    narration_text: str,
    duration_seconds: float,
    words: list[dict] | None,
) -> str:
    """Compile *spec* into a complete, self-contained HTML document (see module docstring)."""
    run_dir = Path(run_dir)

    pack: StylePack | None = None
    if spec.style_pack:
        pack = load_style_pack(spec.style_pack)

    palette = _PaletteResolver(spec, pack)
    asset_urls = _asset_urls(spec, run_dir)

    def rt(ref) -> float:
        return resolve_time(
            ref,
            narration_text=narration_text,
            duration_seconds=duration_seconds,
            words=words,
        )

    def opt_rt(ref) -> float | None:
        return None if ref is None else rt(ref)

    # ---- camera ----
    camera = [
        {"t": rt(k.time), "x": k.x, "y": k.y, "scale": k.scale}
        for k in spec.camera
    ]

    # ---- elements ----
    compiled: list[dict[str, Any]] = []
    for el in spec.elements:
        base = {
            "id": el.id,
            "type": el.type,
            "enter": opt_rt(el.enter),
            "exit": opt_rt(el.exit),
        }
        if isinstance(el, LayerElement):
            base.update(
                {
                    "assetUrl": asset_urls[el.asset_id],
                    "x": el.x,
                    "y": el.y,
                    "width": el.width,
                    "depth": el.depth,
                    "scale": el.scale,
                    "rotate": el.rotate,
                    "opacity": el.opacity,
                    "z": el.z,
                }
            )
        elif isinstance(el, LabelElement):
            base.update(
                {
                    "text": el.text,
                    "attach": el.attach,
                    "x": el.x,
                    "y": el.y,
                    "style": el.style,
                    "color": palette.color(el.color),
                    "seed": default_asset_seed(spec.segment_id, el.id),
                }
            )
        elif isinstance(el, MaskElement):
            base.update(
                {
                    "target": el.target,
                    "shape": el.shape,
                    "reveal": rt(el.reveal),
                    "duration": el.duration,
                }
            )
        elif isinstance(el, ParticlesElement):
            seed = el.seed if el.seed is not None else default_asset_seed(spec.segment_id, el.id)
            base.update(
                {
                    "style": el.style,
                    "count": el.count,
                    "color": palette.color(el.color),
                    "area": {"x": el.area.x, "y": el.area.y, "w": el.area.w, "h": el.area.h},
                    "depth": el.depth,
                    "seed": seed,
                }
            )
        elif isinstance(el, SplitElement):
            base.update(
                {
                    "panels": [
                        {"assetUrl": asset_urls[p.asset_id], "label": p.label}
                        for p in el.panels
                    ],
                    "direction": el.direction,
                    "gap": el.gap,
                }
            )
        elif isinstance(el, TypewriterElement):
            base.update(
                {
                    "text": el.text,
                    "x": el.x,
                    "y": el.y,
                    "speed_cps": el.speed_cps,
                    "font": el.font,
                    "color": palette.color(el.color),
                }
            )
        elif isinstance(el, NodeGraphElement):
            base.update(
                {
                    "nodes": [
                        {"id": n.id, "label": n.label, "x": n.x, "y": n.y} for n in el.nodes
                    ],
                    "edges": [list(e) for e in el.edges],
                    "reveal": rt(el.reveal),
                    "color": palette.color(el.color),
                    "accent": palette.color(el.accent),
                }
            )
        compiled.append(base)

    _role_family, font_css, families = _resolve_fonts(spec, pack)

    scene = {
        "duration": duration_seconds,
        "fps": spec.fps,
        "background": palette.color(spec.background),
        "fonts": families,
        "camera": camera,
        "elements": compiled,
    }

    runtime_css = (_RUNTIME_DIR / "collage-runtime.css").read_text()
    runtime_js = (_RUNTIME_DIR / "collage-runtime.js").read_text()
    pack_css = pack.css if pack else ""

    scene_json = json.dumps(scene, ensure_ascii=False).replace("</", "<\\/")

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>collage {spec.segment_id}</title>\n"
        "<style>\n"
        f"{runtime_css}\n{pack_css}\n{font_css}\n"
        "</style>\n</head>\n<body>\n"
        f"<script>window.__COLLAGE__ = {scene_json};</script>\n"
        f"<script>\n{runtime_js}\n</script>\n"
        "</body>\n</html>\n"
    )
