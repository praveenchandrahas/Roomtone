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
