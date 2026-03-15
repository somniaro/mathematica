#!/usr/bin/env python3
"""
Convert fake figures (minipage{\textwidth} with captionof + images) to proper \\begin{figure}[H].

Usage:
  python minipage_to_figure.py [--dry-run] [--file FILE] [--backup-dir DIR] [--img-opts OPTS] [FILES...]

  --dry-run    Show changes without modifying files
  --file FILE  Process only this file
  --backup-dir DIR  Directory for backups (default: same as source, .bak suffix)
  --img-opts OPTS   Options for \\includegraphics, e.g. "height=4.5cm" or "width=\\linewidth" (default: height=4.5cm)
"""

import argparse
import re
import shutil
from pathlib import Path


# Regex for \includegraphics - captures optional args and path
INCLUDEGRAPHICS_RE = re.compile(
    r'\\includegraphics\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}'
)

# Regex for \captionof{figure}{...} - need to handle nested braces
def find_captionof(content: str) -> str | None:
    """Extract \\captionof{figure}{...} content. Returns None if not found."""
    m = re.search(r'\\captionof\{figure\}\s*\{', content)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
        i += 1
    return content[start:i-1] if depth == 0 else None

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

def build_figure(inner: str, img_opts: str = 'height=4.5cm') -> str:
    """Build \\begin{figure}[H]...\\end{figure} from inner minipage content."""
    caption_content = find_captionof(inner)
    label_content = find_label(inner)
    paths = extract_includegraphics_paths(inner)
    fuentepropia = has_fuentepropia(inner)

    if not paths:
        return None  # No images, skip

    # Build image line(s): each path with normalized options, separated by \hspace{5mm}
    img_parts = [f'\\includegraphics[{img_opts}]{{{p}}}' for p in paths]
    img_line = ' \\hspace{5mm}\n'.join(img_parts)

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

def process_content(text: str, img_opts: str = 'height=4.5cm') -> tuple[str, int]:
    """Process text, replacing fake figures. Returns (new_text, count_replacements)."""
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

        replacement = build_figure(block, img_opts)
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

def process_file(path: Path, dry_run: bool, backup_dir: Path | None, img_opts: str = 'height=4.5cm') -> int:
    """Process a single file. Returns number of replacements."""
    content = path.read_text(encoding='utf-8')
    new_content, count = process_content(content, img_opts)

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
    ap.add_argument('--img-opts', default='height=4.5cm', metavar='OPTS',
                    help='Options for \\includegraphics (default: height=4.5cm)')
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

    total = 0
    for f in files:
        if not f.exists():
            print(f"Skip (not found): {f}")
            continue
        total += process_file(f, args.dry_run, args.backup_dir, args.img_opts)

    print(f"\nTotal: {total} replacement(s)")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
