#!/usr/bin/env python3
"""Writes delaytest.wav: 8 channels, 48 kHz, 16-bit, hardware order FL FR RL RR FC LFE SL SR.

Part 1 (about 8 s): a 1 kHz beep on each speaker in turn, so you can hear the order is right:
  front-left, front-right, left height, right height, centre, (nothing for the sub), left surround, right surround.
Part 2 (about 24 s): a short chirp on each speaker in turn, repeated 12 times. This is what gets analysed.

Standard library only. Usage:  python3 make-delaytest.py [outfile.wav]
"""
import array, math, sys, wave

FS = 48000
NCH = 8
LFE = 5                     # silent: the sub is not timed here
SLOT = 0.25                 # seconds per speaker in the chirp part
CYCLES = 12
AMP_CHIRP = 0.10            # -20 dBFS peak
AMP_BEEP = 0.10
CHIRP_MS = 30
F0, F1 = 400.0, 8000.0


def chirp():
    n = int(FS * CHIRP_MS / 1000)
    k = math.log(F1 / F0)
    T = n / FS
    out = []
    for i in range(n):
        t = i / FS
        ph = 2 * math.pi * F0 * T / k * (math.exp(t / T * k) - 1)
        w = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))      # Hann taper
        out.append(AMP_CHIRP * w * math.sin(ph))
    return out


def beep(ms=400):
    n = int(FS * ms / 1000)
    return [AMP_BEEP * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) * math.sin(2 * math.pi * 1000 * i / FS) for i in range(n)]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "delaytest.wav"
    lead, gap = 1.0, 1.5
    walk_step = 0.8
    t_walk = lead
    t_meas = lead + NCH * walk_step + gap
    total = t_meas + CYCLES * NCH * SLOT + 1.0
    nfr = int(total * FS)
    buf = array.array("h", bytes(nfr * NCH * 2))

    def put(sig, ch, start_s):
        s0 = int(round(start_s * FS))
        for i, v in enumerate(sig):
            buf[(s0 + i) * NCH + ch] = int(round(v * 32767))

    b = beep()
    for ch in range(NCH):
        if ch != LFE:
            put(b, ch, t_walk + ch * walk_step)
    c = chirp()
    for cyc in range(CYCLES):
        for ch in range(NCH):
            if ch != LFE:
                put(c, ch, t_meas + (cyc * NCH + ch) * SLOT)

    with wave.open(path, "wb") as w:
        w.setnchannels(NCH)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(buf.tobytes() if sys.byteorder == "little" else (buf.byteswap() or buf).tobytes())
    print(f"wrote {path}: {total:.1f} s, {NCH} ch, {FS} Hz, 16-bit. Chirp part starts at {t_meas:.2f} s.")


if __name__ == "__main__":
    main()
