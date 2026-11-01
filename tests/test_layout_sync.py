"""autocal/layout.py is a copy of linux-theater-dsp's scripts/layout.py (the canonical one). When a sibling checkout is found (SET_EQ points into it, or ../../linux-theater-dsp), they must be identical.
Run: python3 tests/test_layout_sync.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
mine = os.path.join(HERE, "..", "autocal", "layout.py")
cands = []
if os.environ.get("SET_EQ"):
    cands.append(os.path.join(os.path.dirname(os.environ["SET_EQ"]), "layout.py"))
cands += [os.path.join(HERE, "..", "..", "linux-theater-dsp", "scripts", "layout.py"), os.path.join(HERE, "..", "deps", "linux-theater-dsp", "scripts", "layout.py")]
other = next((c for c in cands if os.path.isfile(c)), None)
if other is None:
    print("SKIP layout.py sync: no linux-theater-dsp checkout found"); sys.exit(0)
same = open(mine, "rb").read() == open(other, "rb").read()
print(("PASS" if same else "FAIL") + " autocal/layout.py is identical to " + os.path.relpath(other, HERE) + ("" if same else "  (copy scripts/layout.py from linux-theater-dsp over autocal/layout.py, or the other way round if you changed the Roomtone one; then run both test suites)"))
sys.exit(0 if same else 1)
