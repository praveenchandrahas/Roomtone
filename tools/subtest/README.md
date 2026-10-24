# Sub test: how low it goes, and where it rattles

Mic at the seat, same gain as the sweep runs (don't touch it), EQ ON (`eq status`), room quiet.
Everything plays on the LFE channel only, through CamillaDSP. The loud tone pass is -14 dBFS (about as loud as the
REW sweeps at -12 dBFS were, but steady): start with your master volume where you normally listen. If it is too much for the
room, regenerate the file with `python3 make-subtest.py --loud=-18` and use the new flac (the analyser reads the new cues).

    # terminal 1
    ./rec.sh sub1 310
    # terminal 2, about 8 s later
    ./play-through-camilla.sh subtest-hw.flac
    ./rec.sh check recordings/sub1.wav
    python3 -I analyze-subtest.py recordings/sub1.wav

Takes 5 minutes. WHILE IT PLAYS, listen during the second half (the stepped tones from about 3 minutes in): 19 tones, 5 s apart,
25 28 31.5 35.5 40 45 50 56 63 71 80 90 100 112 125 140 160 180 200 Hz; the loud pass is the second set of 19.
A tiny tick on the centre speaker comes just before each tone, so you can count. If anything buzzes or rattles, write down
which tone number (counting from the first tone of that pass) and then also look at the object: touch it while that tone plays.

## Hot run: hunting a cabinet rattle at film-level amplitude

The default loud pass (-14 dBFS) is 6-12 dB quieter than the loudest film bass. To reach it:

    python3 make-subtest.py --quiet=-8 --loud=-3     # same file layout, tones at -8 then -3 dBFS

Start with the volume where the rattle happens in the film, and stop (Ctrl-C) if the sub distorts audibly or the amplifier
limits. During the loud pass keep a hand on the sub: lightly on the top, then the side, then the rear panel and the port, and
note the tone number where the buzz appears and whether pressing a panel stops it (a panel/brace), a shake of the object on top
(loose item), or no change (port chuffing, driver, or the room). For a recording, put the mic 30 cm from the cabinet instead of
at the seat so the room matters less; the analyser's 'rise' column (non-tone energy growing faster than the tone) flags it.

### Close-mic recordings: keep them below the clip rail

The first hot run (23 Oct 2026, mic a few cm from the cabinet) clipped the recording chain: every tone from 31.5 to 71 Hz in the loud pass
(and 35.5, 40 and 56 Hz in the quiet pass) was flat-topped at exactly the same level, -6.1 dBFS. Their distortion is the recording's, not the sub's.
The analyser now marks such tones `CLIPPED` and gives no rattle verdict for them. Aim for the loudest tone peaking below about -12 dBFS
(move the mic back 3x for -10 dB, or lower the recording level). The sync-chirp search is also normalised by local energy, because loud
broadband buzz used to out-score the real chirps and break the placement.

Trying EQ against a buzz: `./set-eq.py --rattle` (or `--rattle=0.5`) adds -3 dB cuts at 38 and 58 Hz and a 28 Hz high-pass on top of the standing profile.
It is a trial, not a proven fix; repeat the hot run with the mic further back to see whether the faint 35-40 Hz and 55-66 Hz onset moves.

### Second hot run: two recorders, a little louder (24 Oct 2026)

    python3 make-subtest.py --quiet=-8 --loud=-3 --boost=2     # sweeps -28/-20/-12, tones -6 then -1 dBFS (+2 dB on everything)

The loud tones now peak at -1 dBFS, the most `--boost` allows. Keep the same standing profile and the same master volume as the first
hot run (no `--rattle`) so the two runs compare. Stop if the sub distorts audibly or the amplifier limits.

- **iMM-6S** through the USB interface, about 1 m from the sub (not 3 cm): `./rec.sh gain`, then `./rec.sh hot2 330` and
  `./rec.sh check recordings/hot2.wav` (peaks should be below -12 dBFS; if not, move it back or lower the level and repeat).
- **iPhone** on a different side of the sub, 1-2 m away, not on the cabinet. Voice Memos, audio quality set to Lossless (or any recorder
  that writes WAV/ALAC). A phone is poor below about 40 Hz, so read it for the buzz (harmonics and non-harmonic noise), not for the 25-35 Hz
  fundamentals. Convert and check: `./phone-to-wav.sh Recording.m4a recordings/hot2-phone.wav`.
- Start both recorders about 8 s before playback and record at least 330 s. Analyse each separately:
  `python3 -I analyze-subtest.py recordings/hot2.wav` and `python3 -I analyze-subtest.py recordings/hot2-phone.wav`.
  A rattle that is really in the cabinet shows in the 'other' and 'rise' columns of both recordings at the same tones; one that appears
  in only one is a mic, room or phone artefact. The sync-chirp placement works on lossy phone audio (self-tested through AAC at 44.1 kHz).
- A flat, linear test recording makes every loud tone peak at the same level and trips the clip guard; a real recording does not.
