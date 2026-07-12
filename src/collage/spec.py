"""CollageSpec — the frozen, agent-authored contract for collage scenes.

A collage scene is a declarative JSON file at ``scenes/{segment_id}.collage.json``
inside a run directory. The builder (src/collage/builder.py) compiles it plus the
self-contained JS runtime into one HTML file that implements the deterministic
``window.seek(t)`` render contract (docs/collage/CONTRACTS.md).

Conventions frozen here:
- Coordinates are normalized 0-1 with origin at the TOP-LEFT of the 16:9 frame.
- ``depth`` is 0 (locked to camera, no parallax) .. 1 (full parallax):
  ``screen_offset = camera_offset * depth``.
- Colors may be raw CSS values or style-pack tokens namespaced ``$palette.<name>``;
  an unknown token is a builder error listing the valid tokens.
- Every model forbids unknown fields so agent typos fail loudly with a path.
- Time is expressed as a TimeRef: exactly one of ``at`` (absolute seconds,
  discouraged), ``at_word`` (narration word, resolved via alignment.json), or
  ``at_frac`` (fraction of the segment's REAL audio duration). Resolution happens
  after the audio-manifest duration override.
"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRef(_Frozen):
    """A point in scene time. Exactly one of at / at_word / at_frac."""

    at: float | None = Field(default=None, ge=0)
    at_word: str | None = None
    occurrence: int = Field(default=1, ge=1)  # nth occurrence of at_word
    at_frac: float | None = Field(default=None, ge=0, le=1)
    offset: float = 0.0  # seconds, added after resolution (may be negative)

    @model_validator(mode="after")
    def _exactly_one(self) -> "TimeRef":
        set_fields = [f for f in ("at", "at_word", "at_frac") if getattr(self, f) is not None]
        if len(set_fields) != 1:
            raise ValueError(
                f"TimeRef needs exactly one of at/at_word/at_frac, got {set_fields or 'none'}"
            )
        return self


class AssetGenerate(_Frozen):
    prompt: str
    width: int = 1088  # mflux requires multiples of 16
    height: int = 1088
    cutout: bool = False  # rembg -> RGBA cutout with alpha gate + fallback
    seed: int | None = None  # default: deterministic hash of segment_id+asset_id

    @model_validator(mode="after")
    def _multiple_of_16(self) -> "AssetGenerate":
        for name in ("width", "height"):
            if getattr(self, name) % 16 != 0:
                raise ValueError(f"generate.{name} must be a multiple of 16 (mflux constraint)")
        return self


class CollageAsset(_Frozen):
    id: str
    role: Literal["background", "midground", "subject", "texture", "panel"] = "subject"
    # Exactly one of src (existing file, path relative to the run dir — manual
    # override) or generate (FLUX generation request).
    src: str | None = None
    generate: AssetGenerate | None = None

    @model_validator(mode="after")
    def _src_xor_generate(self) -> "CollageAsset":
        if not _ID_RE.match(self.id):
            raise ValueError(f"asset id {self.id!r} must match {_ID_RE.pattern}")
        if (self.src is None) == (self.generate is None):
            raise ValueError(f"asset {self.id!r}: exactly one of src/generate required")
        return self


def default_asset_seed(segment_id: str, asset_id: str) -> int:
    """Deterministic per-asset seed so rebuilds reproduce identical imagery."""
    digest = hashlib.sha256(f"{segment_id}:{asset_id}".encode()).hexdigest()
    return int(digest[:8], 16)


class _Element(_Frozen):
    id: str
    enter: TimeRef | None = None  # None = visible from t=0
    exit: TimeRef | None = None  # None = visible to end

    @model_validator(mode="after")
    def _valid_id(self) -> "_Element":
        if not _ID_RE.match(self.id):
            raise ValueError(f"element id {self.id!r} must match {_ID_RE.pattern}")
        return self


class MoveKey(_Frozen):
    """One keyframe of a layer's motion path (positions may go off-frame so
    subjects can march/sail in and out of shot). Fields left None inherit the
    previous keyframe's value (the first keyframe inherits the layer's base
    pose). Author keys in chronological order."""

    time: TimeRef
    x: float | None = Field(default=None, ge=-0.5, le=1.5)
    y: float | None = Field(default=None, ge=-0.5, le=1.5)
    scale: float | None = Field(default=None, gt=0)
    rotate: float | None = None  # degrees


class Oscillation(_Frozen):
    """Continuous closed-form wobble layered on top of the pose — bobbing
    marchers, rocking ships, undulating wave strips. amplitude is in
    normalized frame units for x/y, degrees for rotate, scale delta for
    scale."""

    axis: Literal["x", "y", "rotate", "scale"]
    amplitude: float = Field(gt=0)
    period: float = Field(gt=0.2, le=60)  # seconds per full cycle
    phase: float = Field(default=0.0, ge=0, le=1)  # cycle offset, for phased waves

    @model_validator(mode="after")
    def _sane_amplitude(self) -> "Oscillation":
        limits = {"x": 0.5, "y": 0.5, "rotate": 45.0, "scale": 0.5}
        if self.amplitude > limits[self.axis]:
            raise ValueError(
                f"oscillate.amplitude {self.amplitude} too large for axis "
                f"{self.axis!r} (max {limits[self.axis]})"
            )
        return self


class LayerElement(_Element):
    """A cutout/painting layer positioned in the parallax stack."""

    type: Literal["layer"]
    asset_id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=2)  # normalized to frame width; height keeps aspect
    depth: float = Field(default=0.5, ge=0, le=1)
    scale: float = Field(default=1.0, gt=0)
    rotate: float = 0.0  # degrees
    opacity: float = Field(default=1.0, ge=0, le=1)
    z: int = 0  # explicit stacking; ties break by list order
    # Subject motion: a keyframed path across the scene and/or a continuous
    # oscillation. Both are closed-form functions of t (seek-contract safe).
    move: list[MoveKey] = Field(default_factory=list)
    oscillate: Oscillation | None = None


class LabelElement(_Element):
    """A torn-paper word label, optionally pinned to another element."""

    type: Literal["label"]
    text: str
    attach: str | None = None  # element id to pin to (offset by x/y) or None = absolute
    x: float = 0.0
    y: float = 0.0
    style: Literal["torn", "plain", "mono"] = "torn"
    color: str = "$palette.ink"


class MaskElement(_Element):
    """An animated SVG/CSS mask reveal over a target element."""

    type: Literal["mask"]
    target: str  # element id being revealed
    shape: Literal["circle", "rect", "head_silhouette"]
    reveal: TimeRef
    duration: float = Field(default=1.2, gt=0)  # seconds of reveal animation


class ParticleArea(_Frozen):
    x: float = Field(default=0.0, ge=0, le=1)
    y: float = Field(default=0.0, ge=0, le=1)
    w: float = Field(default=1.0, gt=0, le=1)
    h: float = Field(default=1.0, gt=0, le=1)


class ParticlesElement(_Element):
    """Deterministic canvas particle field; positions are closed-form f(t, seed)."""

    type: Literal["particles"]
    style: Literal["dust", "biolume", "sparks"] = "dust"
    count: int = Field(default=60, ge=1, le=400)
    color: str = "$palette.accent"
    area: ParticleArea = ParticleArea()
    depth: float = Field(default=0.8, ge=0, le=1)
    seed: int | None = None  # default: deterministic hash of segment_id+element id


class SplitPanel(_Frozen):
    asset_id: str
    label: str | None = None


class SplitElement(_Element):
    """Side-by-side 'experiment' panels (non-recursive)."""

    type: Literal["split"]
    panels: list[SplitPanel] = Field(min_length=2, max_length=3)
    direction: Literal["horizontal", "vertical"] = "horizontal"
    gap: float = Field(default=0.02, ge=0, le=0.2)


class TypewriterElement(_Element):
    type: Literal["typewriter"]
    text: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    speed_cps: float = Field(default=18.0, gt=0)  # characters per second
    font: Literal["serif", "sans", "mono"] = "mono"
    color: str = "$palette.ink"


class NodeGraphNode(_Frozen):
    id: str
    label: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class NodeGraphElement(_Element):
    """A node-graph visualization revealed edge by edge."""

    type: Literal["nodegraph"]
    nodes: list[NodeGraphNode] = Field(min_length=1)
    edges: list[tuple[str, str]] = []
    reveal: TimeRef
    color: str = "$palette.ink"
    accent: str = "$palette.accent"

    @model_validator(mode="after")
    def _edges_ref_nodes(self) -> "NodeGraphElement":
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError(f"nodegraph {self.id!r}: duplicate node ids")
        for a, b in self.edges:
            for end in (a, b):
                if end not in node_ids:
                    raise ValueError(f"nodegraph {self.id!r}: edge endpoint {end!r} is not a node id")
        return self


CollageElement = Annotated[
    Union[
        LayerElement,
        LabelElement,
        MaskElement,
        ParticlesElement,
        SplitElement,
        TypewriterElement,
        NodeGraphElement,
    ],
    Field(discriminator="type"),
]


class CameraKeyframe(_Frozen):
    time: TimeRef
    x: float = Field(default=0.5, ge=0, le=1)  # camera center, normalized
    y: float = Field(default=0.5, ge=0, le=1)
    scale: float = Field(default=1.0, gt=0, le=2)


class CollageSpec(_Frozen):
    spec_version: Literal[1]
    segment_id: str
    # Estimate only — the builder overrides it with the real narration duration
    # from audio_manifest.json BEFORE resolving any TimeRef.
    duration_seconds: float = Field(gt=0)
    fps: Literal[24, 30, 60] = 30
    background: str = "$palette.paper"
    style_pack: str | None = None  # defaults to the script-level style_pack
    camera: list[CameraKeyframe] = []
    assets: list[CollageAsset] = []
    elements: list[CollageElement] = Field(min_length=1)

    @model_validator(mode="after")
    def _referential_integrity(self) -> "CollageSpec":
        asset_ids = [a.id for a in self.assets]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("duplicate asset ids")
        element_ids = [e.id for e in self.elements]
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("duplicate element ids")
        assets = set(asset_ids)
        elements = set(element_ids)
        for el in self.elements:
            if isinstance(el, LayerElement) and el.asset_id not in assets:
                raise ValueError(f"layer {el.id!r}: unknown asset_id {el.asset_id!r}")
            if isinstance(el, LabelElement) and el.attach is not None and el.attach not in elements:
                raise ValueError(f"label {el.id!r}: attach target {el.attach!r} is not an element id")
            if isinstance(el, MaskElement):
                if el.target not in elements:
                    raise ValueError(f"mask {el.id!r}: target {el.target!r} is not an element id")
                if el.target == el.id:
                    raise ValueError(f"mask {el.id!r}: cannot target itself")
            if isinstance(el, SplitElement):
                for panel in el.panels:
                    if panel.asset_id not in assets:
                        raise ValueError(f"split {el.id!r}: unknown asset_id {panel.asset_id!r}")
        return self


def load_collage_spec(path) -> CollageSpec:
    """Load and validate a CollageSpec, raising pydantic errors with field paths."""
    from pathlib import Path

    return CollageSpec.model_validate_json(Path(path).read_text())
