"""Progress assessment — the positive-signal counterpart to the detectors.

Detectors (engine + detectors/) fire on NEGATIVE signals (repetition, error
streaks, oscillation) and BLOCK. This module fires on OBSERVED PROGRESS and
returns a credit that *relieves* budget pressure (see
``core.budget.credit_progress``). It is the symmetric other half: detectors
gate on absence-of-correctness; progress credits presence-of-verification.

THE DEFINITION OF PROGRESS IS THE SECURITY BOUNDARY of this whole mechanism.
Progress is an *observed verification transition* — never:

  * a tool exiting 0 (a successful Edit is not an advanced task),
  * a novel argument hash (varied flailing is still flailing),
  * anything the agent asserts about itself.

Crediting any of those would refuel exactly the doom loops the budget exists
to catch — green-while-wrong is the most expensive failure class. Automatic
credit therefore requires the same normalized test/build identity going
red->green, or a genuine error streak breaking on a real success. Structured
adapter/workflow claims additionally require a recent observed event anchor.

Defense in depth: a credit can only ever LOWER budget pressure, and the
repetition / oscillation / error_streak detectors still BLOCK independently of
the budget, so even a false-positive credit cannot carry a doom loop past them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .events import ToolEvent, OK, ERROR

# progress kinds, strongest first
TEST_RECOVERY = "test_recovery"        # a failing test/build went green
STREAK_BROKEN = "streak_broken"        # a real error streak ended on a success
CLEAN_VALIDATION = "clean_validation"  # a test/build passed with no prior failure
FAILURE_REPRODUCED = "failure_reproduced"
HYPOTHESIS_ELIMINATED = "hypothesis_eliminated"
FAILURE_SET_SHRANK = "failure_set_shrank"
DIFF_REDUCED = "diff_reduced"
WORKFLOW_TRANSITION = "workflow_transition"

_TEST_KINDS = ("shell:test", "shell:build")

_DEFAULT_CREDITS = {
    TEST_RECOVERY: 12.0,
    STREAK_BROKEN: 6.0,
    CLEAN_VALIDATION: 3.0,
}
# Minimum consecutive errors before a non-test success counts as "getting
# un-stuck". A single failed command followed by an unrelated success is
# routine and must not pay — only a real streak breaking is progress.
_DEFAULT_STREAK_MIN = 2


@dataclass
class ProgressSignal:
    kind: str
    credit: float
    reason: str
    evidence: Optional[dict] = None


def _num(prog: dict, key: str, default: float) -> float:
    try:
        v = float(prog.get(key, default))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _credits(prog: dict) -> dict:
    return {
        TEST_RECOVERY: _num(prog, "test_recovery_credit", _DEFAULT_CREDITS[TEST_RECOVERY]),
        STREAK_BROKEN: _num(prog, "streak_broken_credit", _DEFAULT_CREDITS[STREAK_BROKEN]),
        CLEAN_VALIDATION: _num(prog, "clean_validation_credit", _DEFAULT_CREDITS[CLEAN_VALIDATION]),
    }


def assess_progress(events: list[ToolEvent], budget_cfg: dict) -> Optional[ProgressSignal]:
    """Inspect the recent window (newest event LAST) and return one credit, or None.

    ``events`` MUST include the just-recorded event as the newest entry.
    ``budget_cfg`` is the resolved ``budget`` config dict; progress settings
    live under ``budget_cfg["progress"]``. Returns at most one signal — the
    strongest applicable. Total: never raises.
    """
    try:
        if not events:
            return None
        prog = budget_cfg.get("progress", {}) if isinstance(budget_cfg, dict) else {}
        if not isinstance(prog, dict):
            prog = {}
        if not prog.get("enabled", True):
            return None

        newest = events[-1]
        # Only a successful call can be progress. A failure never credits.
        if newest.status != OK:
            return None

        credits = _credits(prog)

        if newest.arg_kind in _TEST_KINDS:
            # The strongest signal: a previously-failing test/build of the same
            # kind that is now green.
            for e in reversed(events[:-1]):
                if e.arg_hash == newest.arg_hash:
                    if e.status == ERROR:
                        return ProgressSignal(
                            TEST_RECOVERY, credits[TEST_RECOVERY],
                            f"{newest.arg_kind} recovered (red->green)",
                        )
                    break  # most recent same-kind run was not a failure
            # Passed with nothing failing before it — a clean validation run.
            # Dedupe: if this exact check already passed in the window, don't
            # pay twice (re-running a green suite is not new progress).
            for e in events[:-1]:
                if e.arg_hash == newest.arg_hash and e.status == OK:
                    return None
            if any(e.arg_kind in _TEST_KINDS and e.status == ERROR for e in events[:-1]):
                return None  # another known-red validation makes this not clean
            return ProgressSignal(
                CLEAN_VALIDATION, credits[CLEAN_VALIDATION], f"{newest.arg_kind} passed",
            )

        # Non-test success: the only credited signal is breaking a real error
        # streak. A success following a success is steady-state, not recovery,
        # and earns nothing — we pay for getting un-stuck, not forward motion.
        streak_min = int(_num(prog, "streak_broken_min", _DEFAULT_STREAK_MIN)) or _DEFAULT_STREAK_MIN
        streak = 0
        for e in reversed(events[:-1]):
            if e.status == ERROR:
                streak += 1
            else:
                break
        if streak >= streak_min:
            return ProgressSignal(
                STREAK_BROKEN, credits[STREAK_BROKEN],
                f"recovered after {streak} consecutive tool errors",
            )
        return None
    except Exception:
        return None


def admit_structured_progress(
    evidence: object,
    events: list[ToolEvent],
    budget_cfg: dict,
) -> Optional[ProgressSignal]:
    """Validate adapter/workflow evidence against an observed event anchor.

    Narration is never accepted. ``anchor`` must match a recent argument or
    output hash, and each evidence kind must carry the fields that make its
    claimed transition mechanically checkable. Invalid input earns nothing.
    """
    try:
        if not isinstance(evidence, dict) or not events:
            return None
        kind = evidence.get("kind")
        anchor = evidence.get("anchor")
        validation_id = evidence.get("validation_id")
        if not isinstance(anchor, str) or not anchor:
            return None
        anchored = [
            event for event in events
            if anchor in {event.arg_hash, event.output_hash}
        ]
        if not anchored:
            return None
        if not isinstance(validation_id, str) or not validation_id.strip():
            return None

        prog = budget_cfg.get("progress", {}) if isinstance(budget_cfg, dict) else {}
        if not isinstance(prog, dict) or not prog.get("enabled", True):
            return None
        defaults = {
            FAILURE_REPRODUCED: 4.0,
            HYPOTHESIS_ELIMINATED: 4.0,
            FAILURE_SET_SHRANK: 12.0,
            DIFF_REDUCED: 4.0,
            WORKFLOW_TRANSITION: 12.0,
        }
        if kind not in defaults:
            return None

        clean = {
            "kind": kind,
            "anchor": anchor,
            "validation_id": validation_id.strip(),
        }
        if kind == FAILURE_SET_SHRANK:
            before = evidence.get("failure_count_before")
            after = evidence.get("failure_count_after")
            if (isinstance(before, bool) or isinstance(after, bool)
                    or not isinstance(before, int) or not isinstance(after, int)
                    or before <= 0 or after < 0 or after >= before):
                return None
            clean.update(failure_count_before=before, failure_count_after=after)
        elif kind == FAILURE_REPRODUCED:
            if not any(event.status == ERROR for event in anchored):
                return None
        elif kind == HYPOTHESIS_ELIMINATED:
            hypothesis_id = evidence.get("hypothesis_id")
            if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
                return None
            clean["hypothesis_id"] = hypothesis_id.strip()
        elif kind == DIFF_REDUCED:
            if not any(event.status == OK for event in anchored):
                return None
        elif kind == WORKFLOW_TRANSITION:
            before = evidence.get("state_before")
            after = evidence.get("state_after")
            if (not isinstance(before, str) or not isinstance(after, str)
                    or not before or not after or before == after):
                return None
            if not any(event.status == OK for event in anchored):
                return None
            clean.update(state_before=before, state_after=after)

        credit = _num(prog, f"{kind}_credit", defaults[kind])
        if credit <= 0:
            return None
        return ProgressSignal(kind, credit, kind.replace("_", " "), clean)
    except Exception:
        return None
