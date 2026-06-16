"""Generic code locator for large-file read discipline.

The locator returns ranked line ranges, not file contents.  It is intentionally
boring and dependency-free: prefer ripgrep when available, fall back to a small
Python scanner, then expand hits to nearby function/class-ish boundaries.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_RESULTS = 8
DEFAULT_CONTEXT_LINES = 80
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_SYMBOL_RE = re.compile(
    r"^\s*(def|class|async\s+def|function|const|let|var|export\s+function|"
    r"export\s+class|pub\s+fn|fn|func|type|interface)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
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


@dataclass(frozen=True)
class Location:
    path: Path
    start: int
    end: int
    confidence: float
    reason: str


@dataclass(frozen=True)
class _Hit:
    path: Path
    line: int
    text: str
    exact: bool


def locate(
    query: str,
    root: Path | str = ".",
    *,
    glob: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    symbol: bool = False,
) -> list[Location]:
    """Return ranked candidate ranges for `query`.

    Fail-open for the CLI use case: unexpected errors return an empty result
    rather than raising.  `root` is allowed to be any readable directory.
    """
    try:
        root_path = Path(root).resolve()
        tokens = _tokens(query)
        if not tokens or not root_path.exists():
            return []
        max_results = max(1, int(max_results))
        context_lines = max(10, int(context_lines))
        hits = _rg_hits(root_path, tokens, glob)
        if not hits:
            hits = _scan_hits(root_path, tokens, glob)
        grouped: dict[Path, list[_Hit]] = {}
        for hit in hits:
            grouped.setdefault(hit.path, []).append(hit)

        locations: list[Location] = []
        for path, path_hits in grouped.items():
            locations.extend(_locations_for_path(path, path_hits, tokens, context_lines, symbol))

        locations.sort(key=lambda loc: (-loc.confidence, str(loc.path), loc.start))
        return _dedupe(locations)[:max_results]
    except Exception:
        return []


def format_locations(locations: list[Location], root: Path | str = ".") -> str:
    """Render locations as human-readable ranked ranges plus read commands."""
    try:
        root_path = Path(root).resolve()
    except Exception:
        root_path = Path.cwd()
    try:
        cwd = Path.cwd().resolve()
    except Exception:
        cwd = Path.cwd()
    if not locations:
        return "No likely targets found."

    lines = ["Likely targets:"]
    for i, loc in enumerate(locations, 1):
        display = _display_path(loc.path, root_path)
        command_path = _display_path(loc.path, cwd)
        pct = int(round(loc.confidence * 100))
        lines.append(f"{i}. {display}:{loc.start}-{loc.end} - {loc.reason} ({pct}% confidence)")
        lines.append(f"   sed -n '{loc.start},{loc.end}p' {sh_quote(str(command_path))}")
    return "\n".join(lines)


def sh_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _tokens(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN_RE.findall(str(query)):
        token = raw.lower()
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _rg_hits(root: Path, tokens: list[str], glob: str | None) -> list[_Hit]:
    rg = shutil.which("rg")
    if not rg:
        return []
    pattern = "|".join(re.escape(t) for t in tokens)
    cmd = [rg, "--json", "--color", "never", "--max-filesize", "2M", "--max-count", "20", "-i", pattern, str(root)]
    if glob:
        cmd[1:1] = ["--glob", glob]
    for name in sorted(_SKIP_DIRS):
        cmd[1:1] = ["--glob", f"!{name}/**"]
    try:
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        return []
    if proc.returncode not in (0, 1):
        return []
    hits: list[_Hit] = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
            if obj.get("type") != "match":
                continue
            data = obj.get("data")
            if not isinstance(data, dict):
                continue
            path_obj = data.get("path")
            lines_obj = data.get("lines")
            path_s = path_obj.get("text") if isinstance(path_obj, dict) else ""
            text = lines_obj.get("text") if isinstance(lines_obj, dict) else ""
            line_no = int(data.get("line_number"))
        except Exception:
            continue
        if not isinstance(path_s, str) or not isinstance(text, str):
            continue
        path = Path(path_s)
        if not path.is_absolute():
            path = root / path
        exact = any(t in text.lower() for t in tokens)
        hits.append(_Hit(path.resolve(), line_no, text, exact))
    return hits


def _scan_hits(root: Path, tokens: list[str], glob: str | None) -> list[_Hit]:
    hits: list[_Hit] = []
    for path in _iter_files(root, glob):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for i, text in enumerate(fh, 1):
                    lower = text.lower()
                    matched = [t for t in tokens if t in lower]
                    if matched:
                        hits.append(_Hit(path.resolve(), i, text.rstrip("\n"), len(matched) == len(tokens)))
        except Exception:
            continue
    return hits


def _iter_files(root: Path, glob: str | None):
    if root.is_file():
        if glob and not fnmatch.fnmatch(root.name, glob):
            return
        try:
            if root.stat().st_size <= 2_000_000:
                yield root
        except OSError:
            return
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if glob and not fnmatch.fnmatch(str(path.relative_to(root)), glob):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            yield path


def _locations_for_path(
    path: Path,
    hits: list[_Hit],
    tokens: list[str],
    context_lines: int,
    symbol: bool,
) -> list[Location]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    if not lines:
        return []

    max_gap = 1 if symbol else max(6, context_lines // 4)
    clusters = _cluster_hits(sorted(hits, key=lambda h: h.line), max_gap=max_gap)
    out: list[Location] = []
    for cluster in clusters:
        first = min(h.line for h in cluster)
        last = max(h.line for h in cluster)
        start, end, boundary_reason = _expand_range(lines, first, last, context_lines)
        score, reason = _score(path, cluster, tokens, start, end, boundary_reason, symbol, lines)
        out.append(Location(path=path, start=start, end=end, confidence=score, reason=reason))
    return out


def _cluster_hits(hits: list[_Hit], max_gap: int) -> list[list[_Hit]]:
    clusters: list[list[_Hit]] = []
    for hit in hits:
        if not clusters or hit.line - clusters[-1][-1].line > max_gap:
            clusters.append([hit])
        else:
            clusters[-1].append(hit)
    return clusters


def _expand_range(lines: list[str], first: int, last: int, context_lines: int) -> tuple[int, int, str]:
    start = max(1, first - 10)
    end = min(len(lines), last + 10)
    boundary = "nearby matches"

    base_idx = max(0, min(first - 1, len(lines) - 1))
    base_indent = _indent(lines[base_idx])
    for i in range(base_idx, -1, -1):
        text = lines[i]
        if _SYMBOL_RE.match(text) or (text.strip().endswith(":") and _indent(text) <= base_indent):
            start = i + 1
            boundary = "enclosing block"
            break

    max_end = min(len(lines), start + context_lines - 1)
    if boundary == "enclosing block":
        start_indent = _indent(lines[start - 1])
        for i in range(max(first, start + 1), min(len(lines), start + context_lines) + 1):
            text = lines[i - 1]
            if text.strip() and _indent(text) <= start_indent and i > last:
                max_end = i - 1
                break
    else:
        max_end = min(len(lines), start + context_lines - 1)

    end = max(end, last)
    end = min(max_end, max(start, end))
    return start, end, boundary


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" \t"))


def _score(
    path: Path,
    hits: list[_Hit],
    tokens: list[str],
    start: int,
    end: int,
    boundary_reason: str,
    symbol: bool,
    lines: list[str],
) -> tuple[float, str]:
    text = "\n".join(h.text for h in hits).lower()
    path_text = str(path).lower()
    token_hits = sum(1 for t in tokens if t in text)
    path_hits = sum(1 for t in tokens if t in path_text)
    exact_bonus = 0.18 if any(h.exact for h in hits) else 0.0
    density = min(0.18, len(hits) / max(1, end - start + 1))
    boundary_bonus = 0.12 if boundary_reason == "enclosing block" else 0.0
    symbol_bonus = 0.0
    if symbol:
        wanted = set(tokens)
        for line in lines[max(0, start - 1):min(len(lines), end)]:
            m = _SYMBOL_RE.match(line)
            if m and m.group(2).lower() in wanted:
                symbol_bonus = 0.25
                break
    score = 0.25 + (0.28 * token_hits / max(1, len(tokens))) + min(0.15, path_hits * 0.05)
    score += exact_bonus + density + boundary_bonus + symbol_bonus
    if symbol and not symbol_bonus:
        score -= 0.30
    score = max(0.05, min(0.99, score))
    reason_parts = []
    if symbol_bonus:
        reason_parts.append("symbol definition")
    elif boundary_reason == "enclosing block":
        reason_parts.append("enclosing block")
    else:
        reason_parts.append("text matches")
    if path_hits:
        reason_parts.append("path match")
    if len(hits) > 1:
        reason_parts.append(f"{len(hits)} nearby hits")
    return score, ", ".join(reason_parts)


def _dedupe(locations: list[Location]) -> list[Location]:
    out: list[Location] = []
    seen: set[tuple[Path, int, int]] = set()
    for loc in locations:
        key = (loc.path, loc.start, loc.end)
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def _display_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path
