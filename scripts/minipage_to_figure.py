#!/usr/bin/env python3
"""
Convert fake figures to proper \\begin{figure}[H].

Patterns: (1) minipage{\\textwidth} with captionof + images; (2) standalone center with
\\caption + images. Layout: max 2 per row. Single image: sized by aspect ratio.
Requires PyMuPDF and Pillow for dimension detection.

Usage:
  python minipage_to_figure.py [--dry-run] [--file FILE] [--backup-dir DIR] [--img-dir DIR] [FILES...]
"""

import argparse
import re
import shutil
from pathlib import Path

# Optional: read image dimensions for aspect-ratio-based sizing
try:
    import fitz as _fitz
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
try:
    from PIL import Image as _PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# Regex for \includegraphics - captures optional args and path
INCLUDEGRAPHICS_RE = re.compile(
    r'\\includegraphics\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}'
)

# Regex for \captionof{figure}{...} - need to handle nested braces
def _extract_braced_content(content: str, start: int) -> str | None:
    """Extract content of {...} starting at start (after the opening brace)."""
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
        i += 1
    return content[start:i-1] if depth == 0 else None

def find_captionof(content: str) -> str | None:
    """Extract \\captionof{figure}{...} content. Returns None if not found."""
    m = re.search(r'\\captionof\{figure\}\s*\{', content)
    return _extract_braced_content(content, m.end()) if m else None

def find_caption_plain(content: str) -> str | None:
    """Extract \\caption{...} content (plain, not captionof)."""
    m = re.search(r'\\caption\s*\{', content)
    return _extract_braced_content(content, m.end()) if m else None

def find_label(content: str) -> str | None:
    """Extract \\label{...} content."""
    m = re.search(r'\\label\{([^}]+)\}', content)
    return m.group(1) if m else None

def extract_includegraphics_paths(content: str) -> list[str]:
    """Extract all includegraphics file paths from content."""
    return [m.group(2) for m in INCLUDEGRAPHICS_RE.finditer(content)]

def has_fuentepropia(content: str) -> bool:
    return '\\fuentepropia' in content

def find_matching_end_minipage(text: str, start: int) -> int:
    """Find the position of \\end{minipage} that matches the \\begin{minipage} at start."""
    depth = 1  # We are inside the opening \begin{minipage}
    i = start
    begin_pat = re.compile(r'\\begin\{minipage\}')
    end_pat = re.compile(r'\\end\{minipage\}')
    while i < len(text):
        m_begin = begin_pat.search(text, i)
        m_end = end_pat.search(text, i)
        if m_begin is None and m_end is None:
            break
        if m_begin is None:
            pos_end = m_end.start()
            depth -= 1
            if depth == 0:
                return pos_end
            i = m_end.end()
            continue
        if m_end is None:
            depth += 1
            i = m_begin.end()
            continue
        if m_begin.start() < m_end.start():
            depth += 1
            i = m_begin.end()
        else:
            depth -= 1
            if depth == 0:
                return m_end.start()
            i = m_end.end()
    return -1

def get_image_dimensions(path: Path) -> tuple[float, float] | None:
    """Return (width, height) in pts, or None if unreadable."""
    if not path.exists():
        return None
    ext = path.suffix.lower()
    if ext == '.pdf' and HAS_PYMUPDF:
        try:
            doc = _fitz.open(path)
            page = doc[0]
            r = page.rect
            doc.close()
            return (r.width, r.height)
        except Exception:
            return None
    if ext in ('.png', '.jpg', '.jpeg') and HAS_PIL:
        try:
            img = _PILImage.open(path)
            w, h = img.size
            img.close()
            return (float(w), float(h))
        except Exception:
            return None
    return None

def img_opts_single(path: str, img_dir: Path) -> str:
    """Return \\includegraphics opts for single image based on aspect ratio."""
    full = img_dir / path
    dims = get_image_dimensions(full)
    if dims is None:
        return r'width=0.5\textwidth'  # fallback
    w, h = dims
    ratio = w / h
    if ratio > 1.15:
        return r'width=0.8\textwidth'  # landscape
    if ratio < 0.87:
        return r'height=4.5cm'  # portrait
    return r'width=0.5\textwidth'  # square

def img_opts_for_two() -> str:
    """Max 2 per row: width=0.4\\textwidth each."""
    return r'width=0.4\textwidth'

def layout_rows(paths: list[str]) -> list[list[str]]:
    """Return rows with max 2 images per row."""
    return [paths[i : i + 2] for i in range(0, len(paths), 2)]

def build_figure(inner: str, img_dir: Path, use_plain_caption: bool = False) -> str:
    """Build \\begin{figure}[H]...\\end{figure} from inner content."""
    caption_content = (find_caption_plain(inner) or find_captionof(inner)) if use_plain_caption else find_captionof(inner)
    label_content = find_label(inner)
    paths = extract_includegraphics_paths(inner)
    fuentepropia = has_fuentepropia(inner)

    if not paths:
        return None  # No images, skip

    rows = layout_rows(paths)
    sep = ' \\hspace{5mm}\n'
    row_lines = []
    for row in rows:
        if len(paths) == 1:
            opts = img_opts_single(paths[0], img_dir)
        else:
            opts = img_opts_for_two()
        row_lines.append(sep.join(f'\\includegraphics[{opts}]{{{p}}}' for p in row))
    img_line = '\n\n'.join(row_lines)

    parts = ['\\begin{figure}[H]', '\\centering']
    if caption_content:
        parts.append(f'\\caption{{{caption_content}}}')
    if label_content:
        parts.append(f'\\label{{{label_content}}}')
    parts.append(img_line)
    if fuentepropia:
        parts.append('\\fuentepropia')
    parts.append('\\end{figure}')

    return '\n\n' + '\n'.join(parts) + '\n\n'

def process_content(text: str, img_dir: Path) -> tuple[str, int]:
    """Process text, replacing fake figures (minipage + center). Returns (new_text, count)."""
    text, count_mini = _process_minipage(text, img_dir)
    text, count_center = process_center(text, img_dir)
    return (text, count_mini + count_center)

def _process_minipage(text: str, img_dir: Path) -> tuple[str, int]:
    """Process minipage{\\textwidth} fake figures."""
    result = []
    count = 0
    i = 0
    begin_pat = re.compile(r'\\begin\{minipage\}\{\\textwidth\}')

    while i < len(text):
        m = begin_pat.search(text, i)
        if not m:
            result.append(text[i:])
            break

        # Include everything from current position up to (but not including) the match
        result.append(text[i:m.start()])

        # Check for preceding \vskip and \noindent to remove
        chunk_before = result[-1]
        # Remove trailing \vskip... and \noindent from the chunk we're about to output
        lines = chunk_before.split('\n')
        strip_count = 0
        for j in range(len(lines) - 1, -1, -1):
            line = lines[j]
            stripped = line.strip()
            if not stripped:
                strip_count += 1
                continue
            if stripped == r'\noindent':
                strip_count += 1
                continue
            if r'\vskip' in stripped and re.match(r'%?\s*\\vskip\s*[\d.]*\s*(cm|mm|em|ex)?\s*$', stripped):
                strip_count += 1
                continue
            if re.match(r'\\vspace\s*\{[^}]+\}\s*$', stripped):
                strip_count += 1
                continue
            break
        if strip_count > 0:
            result[-1] = '\n'.join(lines[:-strip_count])
            if result[-1] and not result[-1].endswith('\n'):
                result[-1] += '\n'

        start = m.start()
        end_outer = find_matching_end_minipage(text, m.end())
        if end_outer < 0:
            result.append(text[m.start():m.end()])
            i = m.end()
            continue

        block = text[start:end_outer + len('\\end{minipage}')]
        inner = text[m.end():end_outer]

        replacement = build_figure(block, img_dir)
        if replacement:
            result.append(replacement)
            # Remove trailing \vskip, \vspace and blank lines after \end{minipage}
            after = text[end_outer + len('\\end{minipage}'):]
            skip_len = 0
            for line in after.split('\n'):
                stripped = line.strip()
                if not stripped or stripped.startswith('%'):
                    skip_len += len(line) + 1  # +1 for newline
                    continue
                if r'\vskip' in stripped and re.match(r'%?\s*\\vskip\s*[\d.]*\s*(cm|mm|em|ex)?\s*$', stripped):
                    skip_len += len(line) + 1
                    continue
                if re.match(r'\\vspace\s*\{[^}]+\}\s*$', stripped):
                    skip_len += len(line) + 1
                    continue
                break
            i = end_outer + len('\\end{minipage}') + skip_len
            count += 1
        else:
            result.append(text[m.start():m.end()])
            i = m.end()

    return (''.join(result), count)

def find_matching_end_center(text: str, start: int) -> int:
    """Find \\end{center} that matches \\begin{center} at start."""
    end_m = re.search(r'\\end\{center\}', text[start:])
    return start + end_m.start() if end_m else -1

def is_inside_figure(text: str, pos: int) -> bool:
    """True if pos is inside \\begin{figure}...\\end{figure} (unclosed figure before pos)."""
    before = text[:pos]
    last_begin = before.rfind(r'\begin{figure}')
    last_end = before.rfind(r'\end{figure}')
    return last_begin > last_end

def process_center(text: str, img_dir: Path) -> tuple[str, int]:
    """Convert standalone \\begin{center}...\\end{center} with caption+images to figure."""
    result = []
    count = 0
    i = 0
    center_pat = re.compile(r'\\begin\{center\}')

    while i < len(text):
        m = center_pat.search(text, i)
        if not m:
            result.append(text[i:])
            break

        if is_inside_figure(text, m.start()):
            result.append(text[i:m.end()])
            i = m.end()
            continue

        result.append(text[i:m.start()])
        start = m.start()
        end_pos = find_matching_end_center(text, m.end())
        if end_pos < 0:
            result.append(text[m.start():m.end()])
            i = m.end()
            continue

        inner = text[m.end():end_pos]
        caption_match = re.search(r'\\caption\s*\{', inner) or re.search(r'\\captionof\{figure\}\s*\{', inner)
        if not caption_match or not INCLUDEGRAPHICS_RE.search(inner):
            result.append(text[m.start():m.end()])
            i = m.end()
            continue

        replacement = build_figure(inner, img_dir, use_plain_caption=True)
        if replacement:
            result.append(replacement)
            end_len = len(r'\end{center}')
            after = text[end_pos + end_len:]
            skip_len = 0
            for line in after.split('\n'):
                stripped = line.strip()
                if not stripped or stripped.startswith('%'):
                    skip_len += len(line) + 1
                    continue
                if r'\vskip' in stripped and re.match(r'%?\s*\\vskip\s*[\d.]*\s*(cm|mm|em|ex)?\s*$', stripped):
                    skip_len += len(line) + 1
                    continue
                if re.match(r'\\vspace\s*\{[^}]+\}\s*$', stripped):
                    skip_len += len(line) + 1
                    continue
                break
            i = end_pos + end_len + skip_len
            count += 1
        else:
            result.append(text[m.start():m.end()])
            i = m.end()

    return (''.join(result), count)

def process_file(path: Path, dry_run: bool, backup_dir: Path | None, img_dir: Path) -> int:
    """Process a single file. Returns number of replacements."""
    content = path.read_text(encoding='utf-8')
    new_content, count = process_content(content, img_dir)

    if count == 0:
        return 0

    if dry_run:
        print(f"[dry-run] {path}: {count} replacement(s)")
        return count

    if backup_dir:
        backup_path = backup_dir / f"{path.name}.bak"
    else:
        backup_path = path.with_suffix(path.suffix + '.bak')

    shutil.copy2(path, backup_path)
    path.write_text(new_content, encoding='utf-8')
    print(f"{path}: {count} replacement(s), backup at {backup_path}")
    return count

def main():
    ap = argparse.ArgumentParser(description="Convert minipage fake figures to \\begin{figure}[H]")
    ap.add_argument('--dry-run', action='store_true', help='Show changes without modifying')
    ap.add_argument('--file', type=Path, help='Process only this file')
    ap.add_argument('--backup-dir', type=Path, help='Directory for .bak files (default: same dir)')
    ap.add_argument('--img-dir', type=Path, default=None,
                    help='Base dir for image paths (default: project root)')
    ap.add_argument('files', nargs='*', type=Path, help='Files to process (default: Originales/*.tex)')
    args = ap.parse_args()

    if args.file:
        files = [args.file]
    elif args.files:
        files = args.files
    else:
        orig = Path(__file__).resolve().parent.parent / 'Originales'
        files = sorted(orig.glob('*.tex'))

    if not files:
        print("No .tex files found")
        return 1

    img_dir = args.img_dir or Path(__file__).resolve().parent.parent
    if not HAS_PYMUPDF or not HAS_PIL:
        print("Note: Install pymupdf and pillow for aspect-ratio sizing: pip install pymupdf pillow")

    total = 0
    for f in files:
        if not f.exists():
            print(f"Skip (not found): {f}")
            continue
        total += process_file(f, args.dry_run, args.backup_dir, img_dir)

    print(f"\nTotal: {total} replacement(s)")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
