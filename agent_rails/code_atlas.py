"""Deterministic source map for large-file navigation.

The atlas is intentionally shallow: it emits file/line maps, not content.  It
is a cheap navigation layer between repo-specific semantic tools and reading a
whole file.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MIN_LINES = 200
DEFAULT_MAX_FILES = 50
DEFAULT_MAX_ENTRIES_PER_FILE = 80
DEFAULT_HEALTH_MIN_LINES = 1000
_MAX_FILE_BYTES = 2_000_000
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
}
_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
_SYMBOL_RE = re.compile(
    r"^\s*(?P<kind>def|class|async\s+def|function|const|let|var|export\s+function|"
    r"export\s+class|pub\s+fn|fn|func|type|interface)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_MD_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<name>.+?)\s*$")


@dataclass(frozen=True)
class AtlasEntry:
    name: str
    kind: str
    start: int
    end: int
    depth: int = 0


@dataclass(frozen=True)
class AtlasFile:
    path: Path
    line_count: int
    entries: list[AtlasEntry]


@dataclass(frozen=True)
class HealthFile:
    path: Path
    line_count: int
    estimated_tokens: int
    suggestions: list[str]


def build_code_atlas(
    root: Path | str = ".",
    *,
    glob: str | None = None,
    min_lines: int = DEFAULT_MIN_LINES,
    max_files: int = DEFAULT_MAX_FILES,
    max_entries_per_file: int = DEFAULT_MAX_ENTRIES_PER_FILE,
) -> list[AtlasFile]:
    """Return source files with definition-ish line maps.

    Fails open for CLI use: malformed inputs, unreadable files, or traversal
    errors produce an empty or partial atlas rather than raising.
    """
    try:
        root_path = Path(root).resolve()
        min_lines = max(0, int(min_lines))
        max_files = max(1, int(max_files))
        max_entries_per_file = max(1, int(max_entries_per_file))
    except Exception:
        return []
    try:
        if not root_path.exists():
            return []
    except Exception:
        return []

    out: list[AtlasFile] = []
    for path in _iter_source_files(root_path, glob):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
        except Exception:
            continue
        line_count = len(lines)
        if line_count < min_lines:
            continue
        entries = _entries_for_lines(lines, max_entries_per_file)
        if not entries:
            continue
        out.append(AtlasFile(path=path.resolve(), line_count=line_count, entries=entries))
        if len(out) >= max_files:
            break
    return out


def format_code_atlas(atlas: list[AtlasFile], root: Path | str = ".") -> str:
    """Render a compact atlas with runnable bounded-read commands."""
    try:
        root_path = Path(root).resolve()
    except Exception:
        root_path = Path.cwd()
    try:
        cwd = Path.cwd().resolve()
    except Exception:
        cwd = Path.cwd()
    if not atlas:
        return "No code atlas entries found."

    lines = ["Code Atlas:"]
    for item in atlas:
        display = _display_path(item.path, root_path)
        command_path = _display_path(item.path, cwd)
        lines.append("")
        lines.append(f"{display} ({item.line_count} lines)")
        for entry in item.entries:
            indent = "  " + ("  " * entry.depth)
            lines.append(f"{indent}{entry.name}  lines {entry.start}-{entry.end}")
        lines.append(f"  read: sed -n '<start>,<end>p' {sh_quote(str(command_path))}")
    return "\n".join(lines)


def repo_health(
    root: Path | str = ".",
    *,
    glob: str | None = None,
    min_lines: int = DEFAULT_HEALTH_MIN_LINES,
    max_files: int = DEFAULT_MAX_FILES,
    max_suggestions: int = 8,
) -> list[HealthFile]:
    """Return large files with retrieval-cost and decomposition hints."""
    try:
        root_path = Path(root).resolve()
        min_lines = max(0, int(min_lines))
        max_files = max(1, int(max_files))
        max_suggestions = max(1, int(max_suggestions))
    except Exception:
        return []
    try:
        if not root_path.exists():
            return []
    except Exception:
        return []

    out: list[HealthFile] = []
    for path in _iter_source_files(root_path, glob):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
        except Exception:
            continue
        line_count = len(lines)
        if line_count < min_lines:
            continue
        entries = _entries_for_lines(lines, max_suggestions * 4)
        out.append(
            HealthFile(
                path=path.resolve(),
                line_count=line_count,
                estimated_tokens=_estimate_tokens(line_count),
                suggestions=_split_suggestions(entries, max_suggestions),
            )
        )
        if len(out) >= max_files:
            break
    return out


def format_repo_health(health: list[HealthFile], root: Path | str = ".") -> str:
    """Render large-file retrieval-cost visibility."""
    try:
        root_path = Path(root).resolve()
    except Exception:
        root_path = Path.cwd()
    if not health:
        return "No large source files found."

    lines = ["Repo Health:"]
    for item in health:
        display = _display_path(item.path, root_path)
        lines.append("")
        lines.append(f"{display}")
        lines.append(f"  {item.line_count} lines")
        lines.append(f"  ~{item.estimated_tokens:,} token unscoped read")
        if item.suggestions:
            lines.append("  Suggested splits:")
            for suggestion in item.suggestions:
                lines.append(f"    {suggestion}")
    return "\n".join(lines)


def _iter_source_files(root: Path, glob: str | None):
    if root.is_file():
        if _include_file(root, root.parent, glob):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if _include_file(path, root, glob):
                yield path


def _include_file(path: Path, root: Path, glob: str | None) -> bool:
    try:
        if glob and not fnmatch.fnmatch(str(path.relative_to(root)), glob):
            return False
    except Exception:
        return False
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return path.suffix.lower() in _SOURCE_EXTENSIONS


def _entries_for_lines(lines: list[str], max_entries: int) -> list[AtlasEntry]:
    starts: list[tuple[int, int, str, str]] = []
    for i, text in enumerate(lines, 1):
        md = _MD_HEADING_RE.match(text)
        if md:
            starts.append((i, len(md.group("level")) - 1, "heading", md.group("name")))
            continue
        sym = _SYMBOL_RE.match(text)
        if sym:
            starts.append((i, _depth(text), sym.group("kind"), sym.group("name")))
    if not starts:
        return []

    out: list[AtlasEntry] = []
    total_lines = len(lines)
    for idx, (start, depth, kind, name) in enumerate(starts):
        end = total_lines
        for next_start, next_depth, _, _ in starts[idx + 1:]:
            if next_depth <= depth:
                end = next_start - 1
                break
        out.append(AtlasEntry(name=name, kind=kind, start=start, end=max(start, end), depth=depth))
        if len(out) >= max_entries:
            break
    return out


def _depth(text: str) -> int:
    stripped = text.lstrip(" \t")
    indent = len(text) - len(stripped)
    return min(6, indent // 4)


def _display_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def _estimate_tokens(line_count: int) -> int:
    return max(1, int(line_count) * 12)


def _split_suggestions(entries: list[AtlasEntry], max_suggestions: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.depth > 1:
            continue
        name = _module_name(entry.name)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max_suggestions:
            break
    return out


def _module_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    if not cleaned:
        return ""
    if not cleaned.endswith(".py"):
        cleaned += ".py"
    return cleaned


def sh_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
