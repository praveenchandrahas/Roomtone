#!/usr/bin/env python3
"""Draws docs/img/autocal-five-position-20261028.png from data/autocal/<run>/results.json (a real five-position autocal run).
Run: python3 tools/make-autocal-figure.py [RUN_ID]. Needs numpy and matplotlib. Shows what the run MEASURED; it is not a before/after."""
import json, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
run = sys.argv[1] if len(sys.argv) > 1 else "20261028-151158"
r = json.load(open(os.path.join(HERE, "..", "data", "autocal", run, "results.json")))
f = np.array(r["freq"]); tgt = np.array(r["target_db"])
BLUE, GREY, INK, RED = "#2a6fb0", "#b5b5b5", "#222222", "#b3402a"

fig, axs = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.35, 1]})
ax = axs[0]
for i in range(1, 6):
    ax.semilogx(f, r["response_db"]["FL"][f"pos{i}"], color=GREY, lw=0.9, label="one position (5 shown)" if i == 1 else None)
ax.semilogx(f, r["response_db"]["FL"]["avg"], color=BLUE, lw=2.0, label="front left, average of 5 positions")
ax.semilogx(f, tgt, color=INK, lw=1.4, ls="--", label="target (the author's bass hump)")
ax.set_xlim(20, 20000); ax.set_ylim(-30, 45)
ax.set_xticks([20, 50, 100, 300, 1000, 3000, 10000]); ax.set_xticklabels(["20", "50", "100", "300", "1k", "3k", "10k"])
ax.set_xlabel("Hz"); ax.set_ylabel("dB (re the mid-band level)")
ax.set_title("Front left at the seat, through the live CamillaDSP profile", fontsize=10.5, loc="left")
ax.grid(alpha=0.2, lw=0.5); ax.legend(loc="upper right", fontsize=8, frameon=False)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)

ax = axs[1]
chs = [c for c in r["channels"] if c != "LFE"]
for i, c in enumerate(chs):
    v = np.array(r["arrival_scatter"][c])
    ax.scatter(v, [i] * len(v), s=22, color=BLUE, alpha=0.85, zorder=3)
    ax.plot([v.min(), v.max()], [i, i], color=GREY, lw=1.2, zorder=2)
ax.axvline(0, color=INK, lw=0.8)
ax.set_yticks(range(len(chs))); ax.set_yticklabels(chs); ax.invert_yaxis()
ax.set_xlabel("arrival time relative to front left, ms (one dot per microphone position)")
ax.set_title("Arrival time at 5 microphone positions\n(LFE not shown: about 7.2 ms after FL, left unchanged, the Z906 sub lag is internal)", fontsize=9.5, loc="left")
ax.grid(axis="x", alpha=0.2, lw=0.5)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)

fig.suptitle(f"Roomtone autocal, five-position run {run[:8]}: what it measured. Verdict: {r['verdict']}", x=0.01, ha="left", fontsize=12, weight="bold", color=RED if r["verdict"] != "PASS" else INK)
fig.text(0.01, 0.005, "Warnings from the run: " + "; ".join(r["warnings"]) + ".  This is the state BEFORE any change; no measurement after applying exists in this repository.",
         fontsize=7.2, color="#444", wrap=True, ha="left", va="bottom")
fig.tight_layout(rect=(0, 0.06, 1, 0.94))
out = os.path.join(HERE, "..", "docs", "img", "autocal-five-position-20261028.png")
fig.savefig(out, dpi=140)
print("wrote", os.path.normpath(out))
