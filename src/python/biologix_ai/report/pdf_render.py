"""
Markdown to PDF with fpdf2's drawing API instead of its HTML importer.

``FPDF.write_html`` does not scale images to the page, and the core fonts cannot
print an em dash, a minus sign, or an angstrom sign. This renderer lays the
document out itself, so:

- every image is scaled to fit the text width and a maximum height, and stays on
  one page with its caption;
- an image that appeared once in the document is not drawn again;
- text uses bundled DejaVu (Unicode), so no character turns into "?";
- long unbroken strings (PSMILES, sequences) wrap instead of running off the page;
- tables are real tables that break across pages.

Supported Markdown: headings, paragraphs, bullet and numbered lists (nested),
tables, fenced code, block quotes, horizontal rules, images, and inline bold,
italic, code, and links.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

INK = (28, 28, 30)
HEADING = (24, 52, 92)
MUTED = (96, 100, 108)
RULE = (190, 194, 200)
CODE_BG = (243, 244, 246)

MAX_FIGURE_HEIGHT_MM = 92.0
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_INLINE = re.compile(
    r"(\*\*[^*\n]+?\*\*|`[^`\n]+`|(?<![\w*\[])\*[^*\s\[\]][^*\n\[\]]*?\*(?![\w\]])|(?<![\w])_[^_\s][^_\n]*?_(?![\w])"
    r"|\[[^\]\n]+\]\([^)\n]+\))"
)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_IMAGE_LINE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_NON_BMP = re.compile("[\U00010000-\U0010ffff​-‏⁠﻿]")


def _font_dir() -> Path:
    import matplotlib

    return Path(matplotlib.__file__).resolve().parent / "mpl-data" / "fonts" / "ttf"


def _clean(text: str) -> str:
    return _NON_BMP.sub("", text)


def _plain(text: str) -> str:
    """Inline Markdown reduced to plain text (for table cells and captions)."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?<![\w*\[])\*([^*\n\[\]]+)\*(?![\w\]])", r"\1", text)
    return _clean(text).strip()


@dataclass
class RenderResult:
    pages: int = 0
    figures: int = 0
    duplicate_figures_skipped: List[str] = field(default_factory=list)
    missing_images: List[str] = field(default_factory=list)


class MarkdownPdf:
    """Render one Markdown document to one PDF (see the module docstring)."""

    def __init__(self, base_dir: Path, *, footer_label: str = "", cache_dir: Optional[Path] = None):
        from fpdf import FPDF

        outer = self

        class _Pdf(FPDF):
            def footer(self) -> None:  # noqa: D401 - fpdf hook
                self.set_y(-12)
                self.set_font("Sans", "", 8)
                self.set_text_color(*MUTED)
                label = outer.footer_label
                self.cell(0, 6, f"{label}    Page {self.page_no()}" if label else f"Page {self.page_no()}", align="C")

        self.base_dir = Path(base_dir)
        self.footer_label = footer_label
        self.cache_dir = cache_dir or (self.base_dir / ".discovery_pdf_cache")
        self.pdf = _Pdf(format="A4", unit="mm")
        self.pdf.set_margins(20, 18, 20)
        self.pdf.set_auto_page_break(auto=True, margin=18)
        self._add_fonts()
        self.result = RenderResult()
        self._seen_images: set[str] = set()
        self._figure_no = 0

    # -- setup ---------------------------------------------------------------

    def _add_fonts(self) -> None:
        d = _font_dir()
        for fam, stem in (("Serif", "DejaVuSerif"), ("Sans", "DejaVuSans"), ("Mono", "DejaVuSansMono")):
            for style, suffix in (("", ""), ("B", "-Bold"), ("I", "-Oblique" if fam != "Serif" else "-Italic"),
                                  ("BI", "-BoldOblique" if fam != "Serif" else "-BoldItalic")):
                path = d / f"{stem}{suffix}.ttf"
                if path.is_file():
                    self.pdf.add_font(fam, style, str(path))

    @property
    def width(self) -> float:
        return self.pdf.w - self.pdf.l_margin - self.pdf.r_margin

    def _room(self) -> float:
        return self.pdf.h - self.pdf.b_margin - self.pdf.get_y()

    def _ensure_room(self, needed: float) -> None:
        if self._room() < needed:
            self.pdf.add_page()

    # -- inline text ---------------------------------------------------------

    def _write_inline(self, text: str, *, size: float = 10.5, line: float = 5.6,
                      family: str = "Serif", color: Tuple[int, int, int] = INK, style: str = "") -> None:
        pdf = self.pdf
        text = _clean(text)
        for part in _INLINE.split(text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                pdf.set_font(family, "B" + style.replace("B", ""), size)
                pdf.set_text_color(*color)
                pdf.write(line, part[2:-2])
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                pdf.set_font("Mono", "", size - 1.2)
                pdf.set_text_color(*HEADING)
                pdf.write(line, part[1:-1])
            elif (part.startswith("*") and part.endswith("*") and len(part) > 2) or (
                part.startswith("_") and part.endswith("_") and len(part) > 2
            ):
                pdf.set_font(family, "I" + style.replace("I", ""), size)
                pdf.set_text_color(*color)
                pdf.write(line, part[1:-1])
            elif part.startswith("[") and "](" in part:
                label, _, url = part[1:-1].partition("](")
                pdf.set_font(family, style, size)
                pdf.set_text_color(*HEADING)
                pdf.write(line, label)
                if url.startswith("http") and url.strip() != label.strip():
                    pdf.set_text_color(*MUTED)
                    pdf.set_font(family, style, size - 1.5)
                    pdf.write(line, f" ({url})")
            else:
                pdf.set_font(family, style, size)
                pdf.set_text_color(*color)
                pdf.write(line, part)
        pdf.set_text_color(*INK)

    # -- blocks --------------------------------------------------------------

    def _heading(self, level: int, text: str, follow_mm: float = 32.0) -> None:
        pdf = self.pdf
        size, gap_before, gap_after = {1: (20, 0, 4), 2: (14.5, 6, 2), 3: (12, 4, 1.5)}.get(level, (10.8, 3, 1))
        # Never leave a heading alone at the foot of a page: keep it with what follows,
        # which is a whole figure when a figure comes next.
        self._ensure_room(size * 0.9 + follow_mm)
        if pdf.get_y() > pdf.t_margin + 1:
            pdf.ln(gap_before)
        pdf.set_font("Sans", "B", size)
        pdf.set_text_color(*HEADING)
        pdf.multi_cell(0, size * 0.52, _plain(text), align="L", new_x="LMARGIN", new_y="NEXT")
        if level == 2:
            y = pdf.get_y() + 0.6
            pdf.set_draw_color(*RULE)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(2.4)
        pdf.set_text_color(*INK)
        pdf.ln(gap_after)

    def _paragraph(self, text: str, *, indent: float = 0.0) -> None:
        pdf = self.pdf
        pdf.set_x(pdf.l_margin + indent)
        pdf.l_margin += indent
        try:
            for i, chunk in enumerate(text.split("\n")):
                if i:
                    pdf.ln(5.6)
                    pdf.set_x(pdf.l_margin)
                self._write_inline(chunk)
        finally:
            pdf.l_margin -= indent
        pdf.ln(5.6 + 1.6)

    def _list(self, items: Sequence[Tuple[int, str, str]]) -> None:
        pdf = self.pdf
        counters: Dict[int, int] = {}
        for depth, marker, text in items:
            indent = 5.0 + depth * 6.0
            if marker[0].isdigit():
                counters[depth] = counters.get(depth, 0) + 1
                bullet = f"{counters[depth]}."
            else:
                counters[depth] = 0
                bullet = "•" if depth == 0 else "–"
            self._ensure_room(12)
            x0 = pdf.l_margin + indent - 4.2
            pdf.set_xy(x0, pdf.get_y())
            pdf.set_font("Serif", "", 10.5)
            pdf.set_text_color(*HEADING)
            pdf.cell(4.2, 5.6, bullet)
            pdf.l_margin += indent
            pdf.set_x(pdf.l_margin)
            try:
                self._write_inline(text)
            finally:
                pdf.l_margin -= indent
            pdf.ln(5.6 + 0.9)
        pdf.ln(1.2)

    def _code(self, lines: Sequence[str]) -> None:
        pdf = self.pdf
        pdf.set_font("Mono", "", 8.4)
        pdf.set_fill_color(*CODE_BG)
        pdf.set_text_color(*INK)
        for raw in lines or [""]:
            self._ensure_room(6)
            pdf.multi_cell(0, 4.4, _clean(raw) or " ", align="L", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2.5)

    def _quote(self, text: str) -> None:
        pdf = self.pdf
        y0 = pdf.get_y()
        pdf.l_margin += 6
        pdf.set_x(pdf.l_margin)
        try:
            self._write_inline(text, color=MUTED, style="I")
        finally:
            pdf.l_margin -= 6
        pdf.ln(5.6)
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.6)
        pdf.line(pdf.l_margin + 1.5, y0, pdf.l_margin + 1.5, pdf.get_y() - 1)
        pdf.set_line_width(0.2)
        pdf.ln(2)

    def _rule(self) -> None:
        pdf = self.pdf
        pdf.ln(2)
        y = pdf.get_y()
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(4)

    def _table(self, rows: List[List[str]]) -> None:
        pdf = self.pdf
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        # Column widths follow content length, bounded so no column is unreadably narrow.
        weight = [
            min(max(len(_plain(r[c])) for r in rows[:12]), 46) + 9 for c in range(ncol)
        ]
        total = sum(weight)
        widths = [max(0.13, w / total) for w in weight]
        scale = sum(widths)
        widths = tuple(round(self.width * w / scale, 2) for w in widths)
        self._ensure_room(28)
        pdf.set_font("Sans", "", 8.4)
        pdf.set_text_color(*INK)
        with pdf.table(
            col_widths=widths,
            text_align="LEFT",
            line_height=4.6,
            borders_layout="HORIZONTAL_LINES",
            first_row_as_headings=True,
            padding=1.4,
            headings_style=__import__("fpdf").fonts.FontFace(emphasis="BOLD", color=HEADING, fill_color=(236, 240, 246)),
            width=self.width,
        ) as table:
            for r in rows:
                row = table.row()
                for cell in r:
                    row.cell(_plain(cell))
        pdf.set_font("Serif", "", 10.5)
        pdf.ln(3)

    # -- figures -------------------------------------------------------------

    def _resolve(self, src: str) -> Optional[Path]:
        if src.startswith(("http://", "https://", "data:")):
            return None
        path = Path(src)
        if not path.is_absolute():
            path = (self.base_dir / path).resolve()
        return path if path.is_file() and path.suffix.lower() in _IMG_EXT else None

    def _normalized(self, path: Path) -> Path:
        """8-bit RGB PNG on white, so palette and alpha images embed reliably."""
        from PIL import Image

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        st = path.stat()
        key = hashlib.sha256(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:24]
        out = self.cache_dir / f"fig_{key}.png"
        if out.is_file():
            return out
        im = Image.open(path)
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (255, 255, 255))
            flat.paste(im, mask=im.split()[3])
            im = flat
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(out, format="PNG", optimize=True)
        return out

    def _figure_extent(self, src: str) -> float:
        """Vertical space (mm) the figure at *src* will take, or 0 if it will not be drawn."""
        path = self._resolve(src)
        if path is None:
            return 0.0
        try:
            from PIL import Image

            with Image.open(path) as im:
                px_w, px_h = im.size
        except Exception:
            return 0.0
        scale = min(self.width / px_w, MAX_FIGURE_HEIGHT_MM / px_h, 1 / 4.2)
        return px_h * scale + 22.0

    def _figure(self, alt: str, src: str) -> None:
        pdf = self.pdf
        path = self._resolve(src)
        if path is None:
            self.result.missing_images.append(src)
            return
        try:
            norm = self._normalized(path)
            digest = hashlib.sha256(norm.read_bytes()).hexdigest()
        except Exception:
            self.result.missing_images.append(src)
            return
        if digest in self._seen_images:
            self.result.duplicate_figures_skipped.append(src)
            return
        self._seen_images.add(digest)
        from PIL import Image

        with Image.open(norm) as im:
            px_w, px_h = im.size
        # Fit inside the text width and a maximum height; never enlarge past ~6 px/mm.
        scale = min(self.width / px_w, MAX_FIGURE_HEIGHT_MM / px_h, 1 / 4.2)
        w, h = px_w * scale, px_h * scale
        self._figure_no += 1
        caption = f"Figure {self._figure_no}. {_plain(alt)}" if _plain(alt) else f"Figure {self._figure_no}."
        pdf.set_font("Sans", "I", 8.6)
        caption_lines = max(1, int(len(caption) * 1.75 / self.width) + 1)
        needed = h + caption_lines * 4.4 + 8
        if needed > self._room():
            pdf.add_page()
        pdf.ln(1)
        pdf.image(str(norm), x=pdf.l_margin + (self.width - w) / 2, y=pdf.get_y(), w=w, h=h)
        pdf.set_y(pdf.get_y() + h + 1.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(0, 4.4, caption, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*INK)
        pdf.ln(4)
        self.result.figures += 1

    # -- document ------------------------------------------------------------

    def render(self, markdown_text: str, output: Path) -> RenderResult:
        pdf = self.pdf
        pdf.add_page()
        pdf.set_font("Serif", "", 10.5)
        text = re.sub(r"<!--.*?-->", "", markdown_text, flags=re.S)
        lines = text.replace("\r\n", "\n").split("\n")
        i, n = 0, len(lines)
        para: List[str] = []  # (text, hard_break_after) pairs are joined the Markdown way

        def flush() -> None:
            if para:
                joined = ""
                for k, piece in enumerate(para):
                    hard = piece.endswith("  ")
                    joined += piece.strip() + ("\n" if hard and k < len(para) - 1 else " ")
                self._paragraph(joined.strip())
                para.clear()

        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                flush()
                i += 1
                continue
            if stripped.startswith("```"):
                flush()
                i += 1
                block: List[str] = []
                while i < n and not lines[i].strip().startswith("```"):
                    block.append(lines[i])
                    i += 1
                i += 1
                self._code(block)
                continue
            heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
            if heading:
                flush()
                follow = 32.0
                nxt = i + 1
                while nxt < n and not lines[nxt].strip():
                    nxt += 1
                if nxt < n and _IMAGE_LINE.match(lines[nxt]):
                    follow = self._figure_extent(_IMAGE_LINE.match(lines[nxt]).group(2)) or follow
                self._heading(len(heading.group(1)), heading.group(2), follow)
                i += 1
                continue
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                flush()
                self._rule()
                i += 1
                continue
            image = _IMAGE_LINE.match(line)
            if image:
                flush()
                self._figure(image.group(1), image.group(2))
                i += 1
                continue
            if "|" in stripped and i + 1 < n and _TABLE_SEP.match(lines[i + 1] or "") and "-" in lines[i + 1]:
                flush()
                rows: List[List[str]] = []
                header = [c.strip() for c in stripped.strip("|").split("|")]
                rows.append(header)
                i += 2
                while i < n and "|" in lines[i] and lines[i].strip():
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                self._table(rows)
                continue
            if stripped.startswith(">"):
                flush()
                quote: List[str] = []
                while i < n and lines[i].strip().startswith(">"):
                    quote.append(lines[i].strip().lstrip(">").strip())
                    i += 1
                self._quote(" ".join(quote))
                continue
            item = _LIST_ITEM.match(line)
            if item:
                flush()
                items: List[Tuple[int, str, str]] = []
                while i < n:
                    m = _LIST_ITEM.match(lines[i])
                    if m:
                        depth = min(len(m.group(1).replace("\t", "    ")) // 2, 3)
                        items.append((depth, m.group(2), m.group(3)))
                        i += 1
                    elif lines[i].startswith("  ") and lines[i].strip() and items and not _IMAGE_LINE.match(lines[i]):
                        d, mk, tx = items[-1]
                        items[-1] = (d, mk, tx + " " + lines[i].strip())  # a wrapped list item
                        i += 1
                    else:
                        break
                self._list(items)
                continue
            para.append(line)
            i += 1
        flush()
        self.result.pages = pdf.page_no()
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(output))
        return self.result


def render_markdown_to_pdf(
    markdown_text: str,
    output_pdf: Path,
    base_dir: Path,
    *,
    footer_label: str = "",
) -> RenderResult:
    """Render *markdown_text* to *output_pdf*; relative images resolve against *base_dir*."""
    return MarkdownPdf(base_dir, footer_label=footer_label).render(markdown_text, output_pdf)
