#!/usr/bin/env python3
"""
Busca archivos del proyecto LaTeX (tex, imágenes, bib) que no están referenciados desde el documento principal.
Respeta .gitignore: los archivos y directorios ignorados por git se excluyen del conjunto de archivos.
Ejecutar desde la raíz del proyecto: python3 scripts/find-unused-latex-files.py [main.tex]
  --zip            crea un archivo zip con los archivos no usados
  --zip-output     ruta del archivo zip (por defecto: _archivos-sin-usar-YYYYMMDD-HHMM.zip)
  --borrar-despues borra los archivos encontrados después de comprimir (requiere --zip)
"""

import argparse
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def get_git_ignored_paths(root: Path, paths: set[Path]) -> set[Path]:
    """Devuelve el subconjunto de rutas que git ignora (según .gitignore)."""
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return set()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    lines = [p.as_posix() for p in paths]
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            cwd=root,
            input="\n".join(lines),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode not in (0, 1):
            return set()
        ignored = set()
        for line in proc.stdout.strip().splitlines():
            line = line.strip()
            if line:
                p = (root / line).resolve().relative_to(root.resolve())
                ignored.add(p)
        return ignored
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()


def normalize_tex_path(root: Path, path: str, base_dir: Path) -> Path:
    """Resuelve la ruta relativa y añade .tex si no tiene extensión."""
    path = path.strip()
    if not path:
        return None
    if not path.endswith(".tex"):
        path = path + ".tex"
    p = (base_dir / path).resolve()
    try:
        return p.relative_to(root)
    except ValueError:
        return p


def extract_brace_content(s: str, start: int) -> tuple[str | None, int]:
    """Extrae el contenido del primer {...} desde start. Devuelve (contenido, pos_fin)."""
    if start >= len(s) or s[start] != "{":
        return None, start
    depth = 0
    i = start
    while i < len(s):
        if s[i] == "{":
            depth += 1
            if depth == 1:
                begin = i + 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[begin:i], i + 1
        elif s[i] == "\\" and i + 1 < len(s):
            i += 1  # saltar carácter escapado
        i += 1
    return None, start


def find_used_tex(root: Path, main_file: Path, seen: set[Path]) -> None:
    r"""Busca recursivamente todas las referencias \input y \include."""
    if main_file in seen:
        return
    seen.add(main_file)
    try:
        text = main_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    base = main_file.parent
    for m in re.finditer(r"\\(?:input|include)\s*\{", text, re.IGNORECASE):
        path, _ = extract_brace_content(text, m.end() - 1)
        if path:
            # normalizar ruta (añadir .tex si hace falta)
            p = normalize_tex_path(root, path, base)
            if p and (root / p).exists():
                find_used_tex(root, root / p, seen)
            else:
                for ext in ("", ".tex"):
                    cand = root / base / (path + ext)
                    if cand.exists():
                        find_used_tex(root, cand, seen)
                        break


def find_used_graphics(root: Path, tex_files: set[Path], graphicspath: list[str]) -> set[Path]:
    r"""Busca todas las referencias \includegraphics; resuelve con graphicspath y extensiones."""
    used = set()
    exts = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
    # En LaTeX, graphicspath es relativo a la raíz del proyecto (cwd)
    prefixes = [root] + [root / p.strip().strip("./") for p in graphicspath if p.strip()]
    for tf in tex_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        base = tf.parent
        for m in re.finditer(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{", text):
            path, _ = extract_brace_content(text, m.end() - 1)
            if not path or path.strip() == "":
                continue
            path = path.strip().replace(" ", "").strip("./")
            for prefix in prefixes:
                for ext in ("", *exts):
                    cand = (prefix / (path + ext)).resolve()
                    if cand.exists():
                        try:
                            used.add(cand.relative_to(root))
                        except ValueError:
                            used.add(cand)
            for ext in ("", *exts):
                cand = (base / (path + ext)).resolve()
                if cand.exists():
                    try:
                        used.add(cand.relative_to(root))
                    except ValueError:
                        used.add(cand)
    return used


def find_used_bib(root: Path, tex_files: set[Path]) -> set[Path]:
    """Busca referencias \addbibresource en los .tex y devuelve los .bib usados."""
    used = set()
    for tf in tex_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        base = tf.parent
        for m in re.finditer(r"\\addbibresource\s*\{", text):
            path, _ = extract_brace_content(text, m.end() - 1)
            if path:
                path = path.strip()
                if not path.endswith(".bib"):
                    path += ".bib"
                cand = (base / path).resolve()
                try:
                    rel = cand.relative_to(root)
                    if cand.exists():
                        used.add(rel)
                except ValueError:
                    if cand.exists():
                        used.add(cand)
    return used


def parse_graphicspath(tex_files: set[Path]) -> list[str]:
    """Extrae las rutas de \\graphicspath en los .tex; si no hay ninguna, devuelve Fig/Img/Imgn por defecto."""
    path_list = []
    for tf in tex_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"\\graphicspath\s*\{", text):
            rest = text[m.end() - 1 :]
            pos = 0
            while pos < len(rest) and rest[pos] == "{":
                path, end = extract_brace_content(rest, pos)
                if path:
                    path_list.append(path.strip())
                pos = end
            break
    return path_list if path_list else ["./Fig", "./Img", "./Imgn"]


def collect_project_files(
    root: Path,
    exclude_dirs: set[str],
    tex_ext: set[str],
    img_ext: set[str],
) -> tuple[set[Path], set[Path], set[Path]]:
    """Recorre el proyecto y recoge todos los .tex, imágenes y .bib (excluyendo exclude_dirs)."""
    all_tex = set()
    all_img = set()
    all_bib = set()
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if any(p in exclude_dirs for p in f.parts):
            continue
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        suf = f.suffix.lower()
        if suf == ".tex":
            all_tex.add(rel)
        elif suf in img_ext:
            all_img.add(rel)
        elif suf == ".bib":
            all_bib.add(rel)
    return all_tex, all_img, all_bib


def main(args):
    root = Path.cwd()
    main_name = args.main_tex
    main_file = root / main_name
    if not main_file.exists():
        print(f"Archivo principal no encontrado: {main_file}", file=sys.stderr)
        sys.exit(1)

    exclude_dirs = {".git", "__pycache__", ".cursor", "out", ".aux", "build"}
    # excluir directorios típicos de compilación
    for d in root.iterdir():
        if d.is_dir() and (d.name.startswith("_minted") or d.name.endswith(".toc") or d.name == "main"):
            exclude_dirs.add(d.name)

    used_tex_abs = set()
    find_used_tex(root, main_file, used_tex_abs)
    used_tex_rel = set()
    for p in used_tex_abs:
        try:
            r = p.relative_to(root) if p.is_absolute() else p
            if str(r).endswith(".tex"):
                used_tex_rel.add(r)
        except (ValueError, AttributeError, TypeError):
            pass
    used_tex_rel.add(Path(main_name))

    graphicspath = parse_graphicspath({root / p for p in used_tex_rel})
    used_img = find_used_graphics(root, {root / p for p in used_tex_rel}, graphicspath)
    used_bib = find_used_bib(root, {root / p for p in used_tex_rel})

    all_tex, all_img, all_bib = collect_project_files(
        root, exclude_dirs, {".tex"}, {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
    )

    # Excluir rutas ignoradas por git
    all_candidates = all_tex | all_img | all_bib
    git_ignored = get_git_ignored_paths(root, all_candidates)
    if git_ignored:
        all_tex -= git_ignored
        all_img -= git_ignored
        all_bib -= git_ignored

    # Excluir el PDF de salida del main (p. ej. main.pdf) de "imágenes no usadas"
    all_img.discard(Path(main_file.stem + ".pdf"))

    # Normalizar used_img a rutas relativas para comparar
    used_img_rel = set()
    for p in used_img:
        try:
            used_img_rel.add(p.relative_to(root) if p.is_absolute() else p)
        except (ValueError, AttributeError):
            used_img_rel.add(p)
    used_img = used_img_rel

    unused_tex = sorted(all_tex - used_tex_rel)
    unused_img = sorted(all_img - used_img)
    unused_bib = sorted(all_bib - used_bib)

    if unused_tex or unused_img or unused_bib:
        if unused_tex:
            print(".tex no usados (o fuera de la cadena de include desde el main):")
            for p in unused_tex:
                print(f"  {p}")
        if unused_img:
            print("Imágenes posiblemente no usadas:")
            for p in unused_img:
                print(f"  {p}")
        if unused_bib:
            print(".bib posiblemente no usados:")
            for p in unused_bib:
                print(f"  {p}")

        if args.zip:
            if args.zip_output:
                zip_path = Path(args.zip_output)
                if not zip_path.is_absolute():
                    zip_path = root / zip_path
            else:
                zip_path = root / f"_archivos-sin-usar-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
            all_unused = [root / p for p in unused_tex + unused_img + unused_bib]
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in all_unused:
                    if f.exists():
                        print(f"Comprimiendo archivo: {f}")
                        zf.write(f, f.relative_to(root).as_posix())
            print(f"\nZip escrito: {zip_path} ({len(all_unused)} archivos)")
            if args.borrar_despues:
                borrados = 0
                for f in all_unused:
                    if f.exists():
                        try:
                            f.unlink()
                            print(f"Borrado: {f}")
                            borrados += 1
                        except OSError as e:
                            print(f"Error al borrar {f}: {e}", file=sys.stderr)
                print(f"Se borraron {borrados} archivos.")
    else:
        print("No se encontraron archivos claramente no usados (todos los recogidos están referenciados).")
    print(f"\n(Usados: {len(used_tex_rel)} tex, {len(used_img)} imágenes, {len(used_bib)} bib desde el main)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Busca archivos del proyecto LaTeX no referenciados desde el documento principal."
    )
    parser.add_argument(
        "main_tex",
        nargs="?",
        default="main.tex",
        help="Archivo .tex principal (por defecto: main.tex)",
    )
    parser.add_argument(
        "--zip", "-z",
        action="store_true",
        help="Crear un archivo zip con los archivos no usados",
    )
    parser.add_argument(
        "--zip-output", "-o",
        metavar="RUTA",
        default=None,
        help="Ruta del archivo zip (por defecto: _archivos-sin-usar-YYYYMMDD-HHMM.zip)",
    )
    parser.add_argument(
        "--borrar-despues", "-d",
        action="store_true",
        dest="borrar_despues",
        help="Borrar los archivos no usados después de comprimirlos (requiere --zip)",
    )
    args = parser.parse_args()
    if args.borrar_despues and not args.zip:
        parser.error("--borrar-despues requiere --zip (-z)")
    main(args)
