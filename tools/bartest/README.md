# Soundbar comparison kit (Samsung HW-Q930F vs the rig)

The same tests the rig has had (sweeps, dialogue, sub), plus a pan ring, rebuilt as 5.1 files for a soundbar. Same mic, same laptop,
same analysers, so the numbers can be read side by side. Heights are a separate, second stage (below).

    python3 make-bartest.py --outdir=.          # needs numpy and ffmpeg; about 30 s; self-tests every lossy file

Writes, for sweeptest / dialoguetest / subtest / pantest: `bar-NAME-5.1.flac` (6-ch PCM, order FL FR FC LFE SL SR), `.ac3`
(Dolby Digital 5.1), `.eac3` (Dolby Digital Plus 5.1) and `bar-NAME-cues.json`. The self-test decodes each file back and checks
every slot lands on the channel it should (the weakest slot is at least 66 dB above its silent channels).
AC-3 / E-AC-3 band-limit the LFE channel to about 120 Hz; use the FLAC when the bar accepts it.

## 0. Can the laptop feed the bar at all?

Samsung soundbars often accept only stereo LPCM over HDMI and take 5.1 as a Dolby bitstream (Samsung forum threads on the HW-Q950T and
Q60R report exactly this; I have not found the Q930F's answer). So first:

    ./probe-hdmi.sh          # with the bar on, HDMI from the laptop into the bar's HDMI IN

If an LPCM line shows 6 or 8 channels, use `.flac` (`./play-bar.sh pcm ...`). If it shows only 2, use `.ac3` (`./play-bar.sh ac3 ...`).
The bar's display should read the format it is decoding. `play-bar.sh` is untested on the real hardware.

## 1. Settings on the bar (Samsung user guide pp. 5, 6, 15)

Sound mode **Standard** ("the original sound will be played back"); Adaptive Sound off; SpaceFit Sound Pro off; Voice Enhance off;
Night mode off; Q-Symphony off; Virtual off; Woofer level 0; Tone 0; channel levels (Center, Side, Wide, Front Top, Rear) all 0;
Moderate Bass off (it mutes the sub). Record what you set. The bar may still apply processing the guide does not list; the sweeps will show it.
Output by mode (user guide p. 7): Standard turns 5.1 into 5.1 (no heights), Atmos into 7.1.4; Surround, Game Pro and Adaptive turn everything into 9.1.4.

## 2. Same room, same seat

Comparing the bar in the family room with the rig in the 10x11x10 ft room measures two rooms, not two systems. Best: put the bar,
its sub and rears in the theatre room at your tuning seat for the session (bar under the screen; rears as the manual's rear layout).
Second best: measure the bar where it lives and treat room effects as part of its score. Mic at the same seat and ear height,
same `./rec.sh gain` as the rig runs. Keep the loudest recording peak under about -12 dBFS (a close-mic run clipped at -6.1 dBFS on 23 Oct).
Match loudness first: play bar-dialoguetest's pink burst (FC) and set the bar's volume so the dB(A) at the seat equals the rig's.

## 3. Sequence (bar in Standard mode, 5.1 file)

| Step | Play | Record | Analyse |
|---|---|---|---|
| a. Channel map and tone | bar-sweeptest | `./rec.sh barsweep 155` | `python3 -I ../sweeptest/analyze-sweeptest.py recordings/barsweep.wav --cues=bar-sweeptest-cues.json` |
| b. Dialogue | bar-dialoguetest | `./rec.sh bardlg 165` | `python3 -I ../dialoguetest/analyze-dialoguetest.py recordings/bardlg.wav --cues=bar-dialoguetest-cues.json` |
| c. Pan | bar-pantest | `./rec.sh barpan 80` | `python3 -I analyze-bar-pan.py recordings/barpan.wav` |
| d. Sub | bar-subtest | `./rec.sh barsub 310` | `python3 -I ../subtest/analyze-subtest.py recordings/barsub.wav --cues=bar-subtest-cues.json` |

Start each recording about 8 s before the playback. Step a also answers "does the bar reproduce the file's channels faithfully": each speaker's
arrival, level and tone are reported; a bar that blends channels, adds its own delays or room-corrects will show it. The rig's numbers for the same
steps are in docs/tuning-log.md and the figures PDF.

## 4. What the 9.1.4 processing does with ordinary content

Repeat a and c with the bar in **Surround** (then Adaptive Sound) mode. Differences from the Standard run are the upmixer's work: extra
energy in the arrival profile, changed level per speaker, a smoother or lumpier pan ring. Heights cannot be isolated this way because the
5.1 file has no height content; it shows how much the bar adds, not whether the added height is placed well.

## 5. Real heights: Atmos test content

The bar's four height drivers can only be fed one at a time by Dolby Atmos content that addresses height channels. None of the kit's
files can do that (the Atmos encoder is Dolby's, not ffmpeg's). Two legitimate sources:

- 2L Immersive Channel Identification 7.1.4 test files (NativeDSD, listed at about $5): a voice guide, then sine tones from 30 Hz to
  15.36 kHz in octave steps on each channel including Ltf, Rtf, Ltr, Rtr; Dolby Atmos TrueHD in MKV.
- thothsong's Atmos sweep and pink-noise files (a community set encoded with the AWS encoder, linked in the AVNirvana thread "Dolby Atmos and REW").
  They are 9.1.6 channel-based Dolby Digital Plus; the thread recommends REW at -12 dBFS and notes the files play about 13.5 dB louder than REW's own sweeps.

Play them in **Standard** mode (Atmos becomes 7.1.4 there). TrueHD needs an HD-audio HDMI path from the laptop; Dolby Digital Plus
(Atmos JOC) is the easier route. Per height: record, find the arrival time (ceiling bounce delay), level re the bed (same file's L/R/C),
and the response shape. Compare with the rig's heights measured the same way. This part has not been tried: expect to work out the
routing and the REW timing reference on the first recording.

## What this can and cannot say

It compares measured behaviour at one seat in one room. It does not say which sounds better, and the bar's own processing,
room correction and bass management will move the numbers. Preference (music, dialogue, immersion) stays with your ears.
