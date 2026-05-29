"""Shared standalone test runner.

Used by each test file's __main__ so `python tests/test_x.py` works without
pytest. Unlike a bare try/except AssertionError, this also catches ANY other
exception and reports it as ERROR — a test that raises (ImportError, TypeError,
...) must not silently vanish from the count.
"""
from __future__ import annotations

import traceback


def run_module_tests(namespace: dict) -> int:
    fns = {
        k: v for k, v in sorted(namespace.items())
        if k.startswith("test_") and callable(v)
    }
    failed = 0
    for name, fn in fns.items():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # a crashing test is a failure, not a skip
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0
