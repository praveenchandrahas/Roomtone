#!/usr/bin/env python3
"""Regenerate docs/img/control-dark.png and control-light.png.

Opens gui/index.html in headless Chromium (Playwright) with the status and level streams replaced by made-up data,
so no CamillaDSP, PipeWire or audio hardware is involved. The numbers and the "now playing" text are simulated for
the picture; they are not a recording of anything playing on the rig.

    python3 tools/make-control-screenshots.py [outdir]      # default: docs/img
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img"

STATUS = {
    "eq": {"switch": "on", "router": "active", "camilla": "active", "camilla_summary": "drone2 film, 8ch"},
    "dsp": {"profile": "film", "pmode": "auto", "flavours": {"stereo": "balanced", "bed": "balanced"},
            "engine": {"state": "on", "service": "active", "ports_up": True, "linked": True}},
    "mpv": {"playing": True, "verdict": "cam",
            "source": {"codec": "TrueHD Atmos", "channels": 8, "kind": "5.1.2 objects", "why": "7.1.2 bed + objects, rendered to 5.1.2"},
            "route": {"via": 8, "channels": 8, "direct": 0, "to_usb": 8}},
    "apps": [], "pipewire": True, "underruns_60s": 0,
    "live": {"ok": True, "state": "Running", "capture_rate": 48000, "load_percent": 9.1, "buffer_level": 1024,
             "clipped_samples": 0, "capture_peak_db": [-9, -12], "playback_peak_db": [-9, -12]},
}
# order of the level stream: FL FR TFL TFR FC LFE SL SR
RMS = [-36, -18, -26, -25, -22, -16, -29, -30]

INIT = """
(() => {
  const status = %s, rms = %s;
  const realFetch = window.fetch.bind(window);
  window.fetch = (u, o) => String(u).includes('/v1/status')
    ? Promise.resolve(new Response(JSON.stringify(status), { headers: { 'Content-Type': 'application/json' } })) : realFetch(u, o);
  window.EventSource = class { constructor(u) { this.u = u; this.t = setInterval(() => {
      if (!String(u).includes('levels') || !this.onmessage) return;
      this.onmessage({ data: JSON.stringify({ ok: true, rms: rms, peak: rms.map(x => x + 7) }) }); }, 50); }
    close() { clearInterval(this.t); } };
})();
"""

import json
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("dark", "light"):
        pg = b.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
        pg.add_init_script(INIT % (json.dumps(STATUS), json.dumps(RMS)))
        pg.goto((ROOT / "gui" / "index.html").as_uri())
        pg.wait_for_timeout(2500)
        pg.screenshot(path=str(OUT / f"control-{scheme}.png"), full_page=True)
        print("wrote", OUT / f"control-{scheme}.png")
    b.close()
