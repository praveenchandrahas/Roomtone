# Sweep test (heights + sub, with CamillaDSP in the path)

Keep the mic exactly where it was for the pan test (the seat, ear height). Same mic gain as before (`./rec.sh gain 60%`),
do not touch it between runs. EQ must be ON (`eq status`). Each speaker plays a 7 s sweep at -20 dBFS (the sub is the loud
one, roughly like a loud film bass scene), one after another: 138 s in all. Please keep the room quiet.

Run 1 = the current state (do NOT apply --heights yet):

    # terminal 1
    ./rec.sh sweep1 155
    # terminal 2, about 8 s later
    ./play-through-camilla.sh sweeptest-hw.flac
    # after both finish
    ./rec.sh check recordings/sweep1.wav          # OK, not TOO HOT, not SILENT
    python3 -I analyze-sweeptest.py recordings/sweep1.wav

Send me recordings/sweep1.wav (or the report text if you prefer). The report ends with the crossover frequency, height-bass
gain, delay and polarity that sum best, as a ready `./set-eq.py ...` command.

Run 2 (after applying that command): same steps with the name sweep2, and analyse with `--heights-on`.
