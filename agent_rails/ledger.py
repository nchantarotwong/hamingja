"""Repo-local ruled-out ledger.

Stage 1 is intentionally plain: markdown files under ``.ledger/`` with a
small frontmatter block, pinned to file content hashes. All readers skip bad
records instead of raising so hook/advisory callers can fail open.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


LEDGER_DIR = ".ledger"
INDEX_NAME = "LEDGER.md"
VALID_KINDS = {"ruled-out", "dead-end", "constraint"}


@dataclass
class Pin:
    path: str
    blob: str
    current: Optional[str] = None

    @property
    def stale(self) -> bool:
        return self.current is not None and self.current != self.blob


@dataclass
class Record:
    slug: str
    path: Path
    kind: str
    claim: str
    evidence: str
    falsifier: str
    scope: list[str]
    pins: list[Pin]
    date: str
    cost: str
    body: str = ""

    @property
    def stale(self) -> bool:
        return not self.pins or any(pin.stale for pin in self.pins)


@dataclass
class LedgerResult:
    ok: bool
    message: str
    record: Optional[Record] = None


def discover_root(start: Path) -> Path:
    try:
        path = start.resolve()
    except OSError:
        path = start
    for candidate in (path, *path.parents):
        try:
            if (candidate / ".git").exists() or (candidate / LEDGER_DIR).exists():
                return candidate
        except OSError:
            continue
    return path


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].strip("-") or "ledger-record"


def add_record(
    root: Path,
    *,
    kind: str,
    claim: str,
    evidence: str,
    scope: Iterable[str],
    falsifier: str = "",
    cost: str = "",
    body: str = "",
    slug: str = "",
    today: Optional[_dt.date] = None,
) -> LedgerResult:
    try:
        kind = (kind or "").strip()
        claim = (claim or "").strip()
        evidence = (evidence or "").strip()
        falsifier = (falsifier or "").strip()
        cost = (cost or "").strip()
        body = (body or "").strip()
        paths = [_clean_rel_path(p) for p in scope if _clean_rel_path(p)]
        if kind not in VALID_KINDS:
            return LedgerResult(False, "kind must be one of: constraint, dead-end, ruled-out")
        if not claim:
            return LedgerResult(False, "claim is required")
        if not evidence:
            return LedgerResult(False, "evidence is required")
        if kind == "ruled-out" and not falsifier:
            return LedgerResult(False, "falsifier is required for ruled-out records")
        if not paths:
            return LedgerResult(False, "at least one --scope path is required")

        root = root.resolve()
        ledger_dir = root / LEDGER_DIR
        ledger_dir.mkdir(parents=True, exist_ok=True)
        base_slug = slugify(slug or claim)
        record_path = _unique_record_path(ledger_dir, base_slug)
        pins = [Pin(path=p, blob=_blob_hash(_repo_path(root, p))) for p in paths]
        record = Record(
            slug=record_path.stem,
            path=record_path,
            kind=kind,
            claim=claim,
            evidence=evidence,
            falsifier=falsifier,
            scope=paths,
            pins=pins,
            date=(today or _dt.date.today()).isoformat(),
            cost=cost,
            body=body,
        )
        record_path.write_text(format_record(record), encoding="utf-8")
        write_index(root, check=True)
        return LedgerResult(True, f"added {record.slug}", record)
    except Exception as e:
        return LedgerResult(False, f"could not add ledger record: {e}")


def records(root: Path, *, check: bool = False) -> list[Record]:
    try:
        ledger_dir = root / LEDGER_DIR
        out: list[Record] = []
        for path in sorted(ledger_dir.glob("*.md")):
            if path.name == INDEX_NAME:
                continue
            rec = read_record(path)
            if rec is None:
                continue
            if check:
                rec.pins = [
                    Pin(path=pin.path, blob=pin.blob, current=_blob_hash(_repo_path(root, pin.path)))
                    for pin in rec.pins
                ]
            out.append(rec)
        return out
    except Exception:
        return []


def read_record(path: Path) -> Optional[Record]:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        end = text.find("\n---", 4)
        if end < 0:
            return None
        front = text[4:end].splitlines()
        body = text[end + 4 :].strip()
        data = _parse_frontmatter(front)
        kind = str(data.get("kind") or "").strip()
        claim = str(data.get("claim") or "").strip()
        evidence = str(data.get("evidence") or "").strip()
        falsifier = str(data.get("falsifier") or "").strip()
        scope = [str(p).strip() for p in data.get("scope", []) if str(p).strip()]
        pins = []
        for item in data.get("valid-while", []):
            if isinstance(item, dict):
                p = str(item.get("path") or "").strip()
                b = str(item.get("blob") or "").strip()
                if p and b:
                    pins.append(Pin(path=p, blob=b))
        if kind not in VALID_KINDS or not claim or not evidence:
            return None
        if not pins:
            pins = [Pin(path=p, blob="missing") for p in scope]
        return Record(
            slug=path.stem,
            path=path,
            kind=kind,
            claim=claim,
            evidence=evidence,
            falsifier=falsifier,
            scope=scope,
            pins=pins,
            date=str(data.get("date") or "").strip(),
            cost=str(data.get("cost") or "").strip(),
            body=body,
        )
    except Exception:
        return None


def check_records(root: Path) -> tuple[list[Record], int]:
    recs = records(root, check=True)
    try:
        write_index(root, records_=recs)
    except Exception:
        pass
    return recs, sum(1 for rec in recs if rec.stale)


def relevant_records(root: Path, paths: Iterable[str]) -> list[Record]:
    try:
        targets = {_clean_rel_path(p) for p in paths if _clean_rel_path(p)}
        if not targets:
            return []
        out: list[Record] = []
        for rec in records(root, check=True):
            scopes = {_clean_rel_path(p) for p in rec.scope}
            if any(_intersects(target, scope) for target in targets for scope in scopes):
                out.append(rec)
        return out
    except Exception:
        return []


def advisory_for_tool(root: Path, tool: str, tool_input: object) -> str:
    try:
        paths = _target_paths(root, tool, tool_input)
        if not paths:
            return ""
        recs = relevant_records(root, paths)
        if not recs:
            return ""
        lines = ["Ruled-out ledger records touch this path:"]
        for rec in recs[:5]:
            state = " [STALE]" if rec.stale else ""
            lines.append(f"- {rec.slug}{state} ({rec.kind}): {rec.claim}")
        if len(recs) > 5:
            lines.append(f"- ... {len(recs) - 5} more; run `agent-rails ledger relevant {' '.join(paths)}`")
        return "\n".join(lines)
    except Exception:
        return ""


def reverify(root: Path, slug: str, *, timeout: float = 60.0) -> LedgerResult:
    try:
        rec = _record_by_slug(root, slug)
        if rec is None:
            return LedgerResult(False, f"unknown ledger record: {slug}")
        if not rec.falsifier:
            return LedgerResult(False, f"record has no falsifier: {slug}")
        completed = subprocess.run(
            rec.falsifier,
            shell=True,
            cwd=str(root),
            check=False,
            timeout=max(1.0, float(timeout)),
        )
        if completed.returncode == 0:
            return retire(root, slug, reason="falsifier now passes")
        rec.pins = [Pin(path=p, blob=_blob_hash(_repo_path(root, p))) for p in rec.scope]
        rec.path.write_text(format_record(rec), encoding="utf-8")
        write_index(root, check=True)
        return LedgerResult(True, f"re-pinned {rec.slug}; falsifier still fails", rec)
    except subprocess.TimeoutExpired:
        return LedgerResult(False, f"falsifier timed out for ledger record: {slug}")
    except Exception as e:
        return LedgerResult(False, f"could not reverify ledger record: {e}")


def retire(root: Path, slug: str, *, reason: str = "") -> LedgerResult:
    try:
        rec = _record_by_slug(root, slug)
        if rec is None:
            return LedgerResult(False, f"unknown ledger record: {slug}")
        rec.path.unlink()
        _append_tombstone(root, rec, reason=reason)
        write_index(root, check=True)
        return LedgerResult(True, f"retired {rec.slug}", rec)
    except Exception as e:
        return LedgerResult(False, f"could not retire ledger record: {e}")


def write_index(root: Path, *, check: bool = False, records_: Optional[list[Record]] = None) -> None:
    ledger_dir = root / LEDGER_DIR
    ledger_dir.mkdir(parents=True, exist_ok=True)
    recs = records_ if records_ is not None else records(root, check=check)
    tombstones = _read_tombstones(root)
    lines = [
        "# Ruled-Out Ledger",
        "",
        "Live records:",
    ]
    if not recs:
        lines.append("- None")
    for rec in recs:
        stale = " [STALE]" if rec.stale else ""
        scope = ", ".join(rec.scope) or "no scope"
        lines.append(f"- {rec.slug}{stale} ({rec.kind}; {scope}): {rec.claim}")
    if tombstones:
        lines.extend(["", "Retired records:", *tombstones])
    lines.append("")
    (ledger_dir / INDEX_NAME).write_text("\n".join(lines), encoding="utf-8")


def format_record(rec: Record) -> str:
    lines = [
        "---",
        f"kind: {rec.kind}",
        "claim: >",
        *_indented(rec.claim),
        "evidence: >",
        *_indented(rec.evidence),
    ]
    if rec.falsifier:
        lines.extend(["falsifier: |", *_indented(rec.falsifier)])
    lines.append("scope:")
    lines.extend(f"  - {p}" for p in rec.scope)
    lines.append("valid-while:")
    for pin in rec.pins:
        lines.append(f"  - path: {pin.path}")
        lines.append(f"    blob: {pin.blob}")
    lines.append(f"date: {rec.date}")
    if rec.cost:
        lines.append(f"cost: {rec.cost}")
    lines.append("---")
    if rec.body:
        lines.extend(["", rec.body])
    lines.append("")
    return "\n".join(lines)


def _parse_frontmatter(lines: list[str]) -> dict:
    data: dict = {}
    key: Optional[str] = None
    block: list[str] = []
    list_key: Optional[str] = None
    current_map: Optional[dict] = None

    def flush_block() -> None:
        nonlocal key, block
        if key is not None:
            data[key] = "\n".join(line.strip() for line in block).strip()
        key = None
        block = []

    for raw in lines:
        if key is not None and (raw.startswith("  ") or raw.strip() == ""):
            block.append(raw)
            continue
        flush_block()
        line = raw.rstrip()
        if not line:
            continue
        if line.endswith(": >") or line.endswith(": |"):
            key = line.split(":", 1)[0]
            block = []
            list_key = None
            current_map = None
            continue
        if line.endswith(":"):
            list_key = line[:-1]
            data[list_key] = []
            current_map = None
            continue
        if list_key and line.startswith("  - "):
            item = line[4:]
            if ":" in item:
                k, v = item.split(":", 1)
                current_map = {k.strip(): v.strip()}
                data[list_key].append(current_map)
            else:
                current_map = None
                data[list_key].append(item.strip())
            continue
        if list_key and current_map is not None and line.startswith("    ") and ":" in line:
            k, v = line.strip().split(":", 1)
            current_map[k.strip()] = v.strip()
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()
            list_key = None
            current_map = None
    flush_block()
    return data


def _blob_hash(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "hash-object", str(path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()[:12]
    except Exception:
        pass
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except Exception:
        return "missing"


def _clean_rel_path(path: str) -> str:
    try:
        text = str(path or "").strip()
        if not text:
            return ""
        if os.path.isabs(text):
            return ""
        cleaned = os.path.normpath(text).replace("\\", "/")
        if cleaned in {"", "."}:
            return ""
        if cleaned == ".." or cleaned.startswith("../") or "/../" in cleaned:
            return ""
        return cleaned
    except Exception:
        return ""


def _repo_path(root: Path, rel: str) -> Path:
    cleaned = _clean_rel_path(rel)
    if not cleaned:
        return root / "__agent_rails_invalid_ledger_path__"
    return root / cleaned


def _target_paths(root: Path, tool: str, tool_input: object) -> list[str]:
    name = str(tool or "").lower()
    if not isinstance(tool_input, dict):
        return []
    if name not in {"edit", "write", "multiedit", "notebookedit"}:
        return []
    out: list[str] = []
    for key in ("file_path", "path"):
        val = tool_input.get(key)
        if isinstance(val, str):
            cleaned = _clean_target_path(root, val)
            if cleaned:
                out.append(cleaned)
    return out


def _clean_target_path(root: Path, path: str) -> str:
    try:
        text = str(path or "").strip()
        if not text:
            return ""
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                return _clean_rel_path(str(candidate.resolve().relative_to(root.resolve())))
            except (OSError, ValueError):
                return ""
        return _clean_rel_path(text)
    except Exception:
        return ""


def _intersects(left: str, right: str) -> bool:
    return left == right or left.startswith(right.rstrip("/") + "/") or right.startswith(left.rstrip("/") + "/")


def _unique_record_path(ledger_dir: Path, slug: str) -> Path:
    path = ledger_dir / f"{slug}.md"
    if not path.exists():
        return path
    for i in range(2, 10_000):
        candidate = ledger_dir / f"{slug}-{i}.md"
        if not candidate.exists():
            return candidate
    return ledger_dir / f"{slug}-{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}.md"


def _record_by_slug(root: Path, slug: str) -> Optional[Record]:
    safe = slugify(slug)
    path = root / LEDGER_DIR / f"{safe}.md"
    if path.exists():
        return read_record(path)
    for rec in records(root):
        if rec.slug == slug:
            return rec
    return None


def _append_tombstone(root: Path, rec: Record, *, reason: str = "") -> None:
    path = root / LEDGER_DIR / ".tombstones"
    why = f" - {reason}" if reason else ""
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {rec.slug} ({_dt.date.today().isoformat()}): {rec.claim}{why}\n")


def _read_tombstones(root: Path) -> list[str]:
    try:
        path = root / LEDGER_DIR / ".tombstones"
        return [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return []


def _indented(text: str) -> list[str]:
    return [f"  {line}" if line else "  " for line in text.splitlines()]
