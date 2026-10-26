"""Shared helpers for the autocal tests: PASS/FAIL printing in the style of the other tests, a path hook, and a counter."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

_n = 0
_fail = 0


def check(name, cond, detail=""):
    global _n, _fail
    _n += 1
    if not cond:
        _fail += 1
    print(("PASS" if cond else "FAIL"), name, detail, flush=True)
    return bool(cond)


def finish(label):
    print(f"{label}: {_n - _fail}/{_n} checks passed", flush=True)
    sys.exit(1 if _fail else 0)


def fast_cfg(runs_dir, mode="single", **kw):
    """RunConfig with short sweeps (3 s + 1.5 s tail) so the simulator runs in seconds; prompts answered automatically."""
    from autocal import runner as RN
    import io
    return RN.RunConfig(mode=mode, runs_dir=runs_dir, sweep_s=3.0, tail_s=1.5, noise_s=3.0, auto_continue=True, out=io.StringIO(), **kw)
