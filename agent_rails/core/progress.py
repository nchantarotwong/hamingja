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
to catch — green-while-wrong is the most expensive failure class. So we credit
only outcomes the agent cannot fake from narration: a test/build going
red->green, or a genuine error streak breaking on a real success.

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
                if e.arg_kind == newest.arg_kind:
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
