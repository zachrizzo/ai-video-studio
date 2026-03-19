"""PDF parser for research papers.

Extracts structured content (sections, equations, figures, tables) from
research paper PDFs using PyMuPDF (fitz). Produces a PaperContent model
ready for downstream analysis and video generation.
"""

import fitz  # PyMuPDF
import re
import tempfile
from pathlib import Path
from collections import Counter

from rich.console import Console

from .models import PaperContent, Section, Equation, Figure, Table

console = Console()

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Display-math environments
_DISPLAY_MATH_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}"
    r"(.*?)"
    r"\\end\{\1\}",
    re.DOTALL,
)

# Labeled equations  \label{eq:foo}
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")

# Dollar-delimited math: $$...$$ or $...$  (non-greedy, no newlines for inline)
_BLOCK_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_DOLLAR_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")

# Figure / table captions
_FIGURE_CAPTION_RE = re.compile(
    r"(?:Figure|Fig\.?)\s*(\d+)[.:]\s*(.*?)(?:\n\n|\Z)", re.IGNORECASE | re.DOTALL
)
_TABLE_CAPTION_RE = re.compile(
    r"Table\s*(\d+)[.:]\s*(.*?)(?:\n\n|\Z)", re.IGNORECASE | re.DOTALL
)

# Abstract heading
_ABSTRACT_RE = re.compile(r"^\s*abstract\s*$", re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_font_stats(page: fitz.Page) -> list[dict]:
    """Return a list of text spans with font metadata from a page.

    Each entry has keys: text, size, flags, origin_y, bbox.
    """
    spans: list[dict] = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for block in blocks:
        if block["type"] != 0:  # text block
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                spans.append(
                    {
                        "text": text,
                        "size": round(span["size"], 1),
                        "flags": span["flags"],
                        "origin_y": span["origin"][1],
                        "bbox": span["bbox"],
                    }
                )
    return spans


def _detect_heading_sizes(all_spans: list[dict], body_size: float) -> set[float]:
    """Return font sizes that are meaningfully larger than the body text."""
    return {s["size"] for s in all_spans if s["size"] > body_size + 0.5}


def _most_common_font_size(all_spans: list[dict]) -> float:
    """Determine the most common (body) font size across all spans."""
    sizes = [s["size"] for s in all_spans]
    if not sizes:
        return 10.0
    counter = Counter(sizes)
    return counter.most_common(1)[0][0]


def _extract_title_and_authors(
    first_page_spans: list[dict], body_size: float
) -> tuple[str, list[str]]:
    """Heuristically extract title and authors from the first page.

    Strategy:
    - Title = the largest-font text on page 1.
    - Authors = text immediately below the title that is larger than body
      but smaller than the title, or same-size text following the title
      before body text begins.
    """
    if not first_page_spans:
        return ("Untitled", [])

    max_size = max(s["size"] for s in first_page_spans)

    # Collect title fragments (largest font)
    title_parts: list[str] = []
    title_bottom_y = 0.0
    for span in first_page_spans:
        if abs(span["size"] - max_size) < 0.3:
            title_parts.append(span["text"])
            title_bottom_y = max(title_bottom_y, span["origin_y"])

    title = " ".join(title_parts).strip()
    # Clean up excessive whitespace
    title = re.sub(r"\s+", " ", title)

    # Authors: spans between title bottom and either abstract or body text.
    # Typically slightly smaller than title but larger than body.
    author_parts: list[str] = []
    for span in first_page_spans:
        if span["origin_y"] <= title_bottom_y:
            continue
        # Stop once we reach body-sized text that looks like a paragraph
        if abs(span["size"] - body_size) < 0.3 and len(span["text"]) > 60:
            break
        # Accept text that is above body size (author names, affiliations)
        if span["size"] > body_size + 0.3:
            author_parts.append(span["text"])

    # Try splitting on commas, "and", newlines
    raw_authors = " ".join(author_parts).strip()
    if raw_authors:
        # Split by comma or " and "
        authors = re.split(r",|\band\b", raw_authors)
        authors = [a.strip() for a in authors if a.strip()]
        # Filter out things that are clearly not names (affiliations, emails)
        authors = [
            a
            for a in authors
            if not re.search(r"@|university|department|institute|lab\b", a, re.I)
            and len(a) < 60
        ]
    else:
        authors = []

    return (title if title else "Untitled", authors)


def _extract_abstract(full_text: str) -> str:
    """Pull out the abstract from the full text."""
    match = _ABSTRACT_RE.search(full_text)
    if not match:
        return ""

    start = match.end()
    # The abstract typically ends at the next section heading or double newline
    # after a reasonable amount of text.
    rest = full_text[start:].lstrip()

    # Take text until we hit something that looks like a heading
    # (a short line in title-case followed by a newline) or a hard limit.
    lines: list[str] = []
    for line in rest.split("\n"):
        stripped = line.strip()
        # Heuristic: a new section heading is a short title-case line
        if (
            lines
            and stripped
            and len(stripped) < 80
            and stripped[0].isupper()
            and stripped.endswith(stripped.rstrip(".:"))
            and not any(c.islower() for c in stripped[:3])
            and len(lines) > 2
        ):
            # Check if it looks like a heading: short, title-cased, standalone
            if re.match(r"^[A-Z0-9]", stripped) and len(stripped.split()) <= 8:
                break
        lines.append(stripped)

    abstract = " ".join(lines).strip()
    # Cap at a reasonable length (abstracts are rarely > 2000 chars)
    if len(abstract) > 3000:
        abstract = abstract[:3000].rsplit(".", 1)[0] + "."
    return abstract


def _build_sections(
    pages: list[fitz.Page],
    body_size: float,
    heading_sizes: set[float],
) -> list[Section]:
    """Walk every page and group text into Section objects by heading detection."""

    sorted_heading_sizes = sorted(heading_sizes, reverse=True)

    def _heading_level(size: float) -> int:
        """Map a font size to a heading level (1 = biggest)."""
        for i, hs in enumerate(sorted_heading_sizes):
            if abs(size - hs) < 0.3:
                return min(i + 1, 4)
        return 0  # not a heading

    sections: list[Section] = []
    current_title = "Introduction"
    current_level = 1
    current_lines: list[str] = []

    for page in pages:
        spans = _get_font_stats(page)
        for span in spans:
            level = _heading_level(span["size"])
            if level > 0 and len(span["text"].split()) <= 12:
                # Flush previous section
                if current_lines:
                    content = "\n".join(current_lines).strip()
                    if content:
                        sections.append(
                            Section(
                                title=current_title,
                                content=content,
                                level=current_level,
                            )
                        )
                current_title = span["text"].strip()
                current_level = level
                current_lines = []
            else:
                current_lines.append(span["text"])

    # Flush last section
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(
                Section(title=current_title, content=content, level=current_level)
            )

    return sections


def _extract_equations(text: str) -> list[Equation]:
    """Find LaTeX equations in the raw text."""
    equations: list[Equation] = []
    seen: set[str] = set()

    def _context_around(full: str, start: int, end: int, chars: int = 120) -> str:
        ctx_start = max(0, start - chars)
        ctx_end = min(len(full), end + chars)
        return full[ctx_start:ctx_end].strip()

    # Display math environments
    for m in _DISPLAY_MATH_RE.finditer(text):
        latex = m.group(2).strip()
        if latex and latex not in seen:
            seen.add(latex)
            label_m = _LABEL_RE.search(latex)
            label = label_m.group(1) if label_m else None
            equations.append(
                Equation(
                    latex=latex,
                    context=_context_around(text, m.start(), m.end()),
                    label=label,
                )
            )

    # $$...$$ blocks
    for m in _BLOCK_DOLLAR_RE.finditer(text):
        latex = m.group(1).strip()
        if latex and latex not in seen:
            seen.add(latex)
            equations.append(
                Equation(
                    latex=latex,
                    context=_context_around(text, m.start(), m.end()),
                )
            )

    # $...$ inline math (skip very short or likely dollar-amount matches)
    for m in _INLINE_DOLLAR_RE.finditer(text):
        latex = m.group(1).strip()
        if (
            latex
            and latex not in seen
            and len(latex) > 2
            and not re.match(r"^\d+[.,]?\d*$", latex)
        ):
            seen.add(latex)
            equations.append(
                Equation(
                    latex=latex,
                    context=_context_around(text, m.start(), m.end()),
                )
            )

    return equations


def _extract_figures(
    doc: fitz.Document, output_dir: Path
) -> list[Figure]:
    """Extract embedded images and match them with nearby captions."""
    figures: list[Figure] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_text = page.get_text("text")
        images = page.get_images(full=True)

        # Extract captions from the page text
        captions: dict[int, str] = {}
        for m in _FIGURE_CAPTION_RE.finditer(page_text):
            fig_num = int(m.group(1))
            caption_text = m.group(2).strip()
            # Clean up the caption
            caption_text = re.sub(r"\s+", " ", caption_text)
            captions[fig_num] = caption_text

        for img_idx, img_info in enumerate(images):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            if not base_image or not base_image.get("image"):
                continue

            image_bytes = base_image["image"]
            ext = base_image.get("ext", "png")

            # Skip very small images (likely icons or artifacts)
            if len(image_bytes) < 2000:
                continue

            img_filename = f"figure_p{page_idx + 1}_{img_idx}.{ext}"
            img_path = output_dir / img_filename

            img_path.write_bytes(image_bytes)

            # Try to match with a caption; fall back to a generic one
            matched_caption = ""
            if captions:
                # Use the lowest-numbered unmatched caption on this page
                for fig_num in sorted(captions.keys()):
                    matched_caption = f"Figure {fig_num}: {captions.pop(fig_num)}"
                    break

            if not matched_caption:
                matched_caption = f"Figure on page {page_idx + 1}"

            figures.append(
                Figure(
                    image_path=str(img_path),
                    caption=matched_caption,
                    page_number=page_idx + 1,
                )
            )

    return figures


def _extract_tables(full_text: str) -> list[Table]:
    """Detect table captions in the text.

    Full table-content reconstruction from PDF is notoriously difficult.
    Here we capture captions and any immediately-following lines that look
    like tabular data (rows of values separated by whitespace/tabs).
    """
    tables: list[Table] = []

    for m in _TABLE_CAPTION_RE.finditer(full_text):
        caption = f"Table {m.group(1)}: {m.group(2).strip()}"
        caption = re.sub(r"\s+", " ", caption)

        # Try to grab tabular content after the caption
        rest = full_text[m.end(): m.end() + 1500]
        tab_lines: list[str] = []
        for line in rest.split("\n"):
            stripped = line.strip()
            if not stripped:
                if tab_lines:
                    break
                continue
            # Heuristic: table rows often have multiple whitespace-separated
            # columns or contain pipe characters
            if re.search(r"\t|  {2,}|\|", stripped):
                tab_lines.append(stripped)
            elif tab_lines:
                break

        if tab_lines:
            # Convert to rough markdown table
            md_rows: list[str] = []
            for i, row in enumerate(tab_lines):
                cells = re.split(r"\t|  {2,}|\|", row)
                cells = [c.strip() for c in cells if c.strip()]
                md_row = "| " + " | ".join(cells) + " |"
                md_rows.append(md_row)
                if i == 0:
                    md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
            content = "\n".join(md_rows)
        else:
            content = "(table content not extracted)"

        tables.append(Table(content=content, caption=caption))

    return tables


def _assign_artifacts_to_sections(
    sections: list[Section],
    equations: list[Equation],
    figures: list[Figure],
    tables: list[Table],
) -> None:
    """Assign extracted equations, figures, and tables to the most relevant section.

    Uses a simple heuristic: check if the equation/figure/table context or
    caption text appears within the section content.
    """
    for eq in equations:
        best_section = _find_best_section(sections, eq.context)
        if best_section is not None:
            best_section.equations.append(eq)
        elif sections:
            sections[-1].equations.append(eq)

    for fig in figures:
        best_section = _find_best_section(sections, fig.caption)
        if best_section is not None:
            best_section.figures.append(fig)
        elif sections:
            sections[-1].figures.append(fig)

    for tbl in tables:
        best_section = _find_best_section(sections, tbl.caption or "")
        if best_section is not None:
            best_section.tables.append(tbl)
        elif sections:
            sections[-1].tables.append(tbl)


def _find_best_section(sections: list[Section], query: str) -> Section | None:
    """Return the section whose content has the best overlap with the query."""
    if not query or not sections:
        return None

    query_words = set(query.lower().split())
    best: Section | None = None
    best_score = 0

    for section in sections:
        section_words = set(section.content.lower().split())
        overlap = len(query_words & section_words)
        if overlap > best_score:
            best_score = overlap
            best = section

    return best if best_score > 2 else None


def _build_markdown(
    title: str, authors: list[str], abstract: str, sections: list[Section]
) -> str:
    """Assemble a markdown representation of the paper."""
    parts: list[str] = []
    parts.append(f"# {title}\n")

    if authors:
        parts.append("**Authors:** " + ", ".join(authors) + "\n")

    if abstract:
        parts.append("## Abstract\n")
        parts.append(abstract + "\n")

    for section in sections:
        heading_prefix = "#" * min(section.level + 1, 6)
        parts.append(f"{heading_prefix} {section.title}\n")
        parts.append(section.content + "\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_paper(pdf_path: Path, output_dir: Path | None = None) -> PaperContent:
    """Parse a research-paper PDF and return structured content.

    Parameters
    ----------
    pdf_path : Path
        Path to the PDF file.
    output_dir : Path | None
        Directory for extracted images. Defaults to a temp directory.

    Returns
    -------
    PaperContent
        Structured representation of the paper.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="paper_figures_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold blue]Parsing:[/bold blue] {pdf_path.name}")

    doc = fitz.open(str(pdf_path))
    console.print(f"  Pages: {len(doc)}")

    # ------------------------------------------------------------------
    # 1. Collect all spans for font analysis
    # ------------------------------------------------------------------
    console.print("  [dim]Analysing fonts...[/dim]")
    all_spans: list[dict] = []
    for page in doc:
        all_spans.extend(_get_font_stats(page))

    body_size = _most_common_font_size(all_spans)
    heading_sizes = _detect_heading_sizes(all_spans, body_size)
    console.print(
        f"  Body font size: {body_size}  |  Heading sizes: {sorted(heading_sizes, reverse=True)}"
    )

    # ------------------------------------------------------------------
    # 2. Extract plain text per page and concatenate
    # ------------------------------------------------------------------
    console.print("  [dim]Extracting text...[/dim]")
    page_texts: list[str] = []
    for page in doc:
        page_texts.append(page.get_text("text"))
    full_text = "\n".join(page_texts)

    # ------------------------------------------------------------------
    # 3. Title & authors from page 1
    # ------------------------------------------------------------------
    console.print("  [dim]Detecting title & authors...[/dim]")
    first_page_spans = _get_font_stats(doc[0]) if len(doc) > 0 else []
    title, authors = _extract_title_and_authors(first_page_spans, body_size)
    console.print(f"  Title: [green]{title}[/green]")
    if authors:
        console.print(f"  Authors: {', '.join(authors)}")

    # ------------------------------------------------------------------
    # 4. Abstract
    # ------------------------------------------------------------------
    console.print("  [dim]Extracting abstract...[/dim]")
    abstract = _extract_abstract(full_text)

    # ------------------------------------------------------------------
    # 5. Sections
    # ------------------------------------------------------------------
    console.print("  [dim]Building section hierarchy...[/dim]")
    pages = [doc[i] for i in range(len(doc))]
    sections = _build_sections(pages, body_size, heading_sizes)
    console.print(f"  Sections found: {len(sections)}")

    # ------------------------------------------------------------------
    # 6. Equations
    # ------------------------------------------------------------------
    console.print("  [dim]Extracting equations...[/dim]")
    equations = _extract_equations(full_text)
    console.print(f"  Equations found: {len(equations)}")

    # ------------------------------------------------------------------
    # 7. Figures
    # ------------------------------------------------------------------
    console.print("  [dim]Extracting figures...[/dim]")
    figures = _extract_figures(doc, output_dir)
    console.print(f"  Figures extracted: {len(figures)}")

    # ------------------------------------------------------------------
    # 8. Tables
    # ------------------------------------------------------------------
    console.print("  [dim]Detecting tables...[/dim]")
    tables = _extract_tables(full_text)
    console.print(f"  Tables found: {len(tables)}")

    # ------------------------------------------------------------------
    # 9. Assign artifacts to sections
    # ------------------------------------------------------------------
    _assign_artifacts_to_sections(sections, equations, figures, tables)

    # ------------------------------------------------------------------
    # 10. Build markdown
    # ------------------------------------------------------------------
    raw_text = _build_markdown(title, authors, abstract, sections)

    doc.close()

    console.print("[bold green]Parsing complete.[/bold green]")

    return PaperContent(
        title=title,
        authors=authors,
        abstract=abstract,
        sections=sections,
        raw_text=raw_text,
        source_path=str(pdf_path),
    )
