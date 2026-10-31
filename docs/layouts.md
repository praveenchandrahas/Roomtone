# Speaker layouts

Roomtone and [linux-theater-dsp](https://github.com/praveenchandrahas/linux-theater-dsp) used to be wired for one rig: the author's 5.1.2 system (a Z906 for five satellites and the sub, a USB 7.1 interface whose RL and RR outputs feed two ceiling speakers). Any other layout meant editing channel tables in several files. This note describes the one layout file that replaces those tables, what it controls, what it does not, how existing installs migrate, and where the limits are.

*Status: design note, written before the code (phase 1 of 5). The sections "what is configurable" and "limits" are updated as each phase lands; the tables below match what is implemented at the time of the last edit to this file.*

## The layout file

One file, `~/.config/roomtone/layout.yml` (override with `$ROOMTONE_LAYOUT`). YAML or JSON. It is parsed as data and validated; nothing in it is executed. A bad file is refused with a message that says which field is wrong, and nothing is changed. If the file does not exist, the built-in 5.1.2 layout (the author's rig) is used, which is exactly the behaviour from before layouts existed.

```yaml
version: 1
name: "5.1.2"
preset: "5.1.2"              # which preset it started from (informational)
room_defaults: author         # author: the author's measured delays, trims and ceiling tone filters; neutral: all zero
amp_bass_management: true     # true: the amplifier sends bass to the sub itself (Z906-like); false: Roomtone does it where asked
crossover_hz: 140             # Roomtone's own crossover, used only for channels with bass: sub
output:
  device: alsa_output.usb-0d8c_USB_Sound_Device-00.analog-surround-71   # PipeWire sink node name of the interface
  channels: 8                 # number of output ports on the interface
  port_names: [FL, FR, RL, RR, FC, LFE, SL, SR]                         # PipeWire port suffix of each output port, in port order
microphone:
  device: null                # PipeWire source node name; null keeps autocal's own detection
channels:
  - {name: FL,  role: bed,    port: 0, azimuth: -30,  elevation: 0,  bass: full}
  - {name: RL,  role: height, port: 2, azimuth: -135, elevation: 50, bass: sub}
  # one line per speaker
```

| Field | Meaning |
|---|---|
| `name` | Label used everywhere (results, report, status page): 1 to 8 letters or digits, unique. |
| `role` | `bed` (ear-height speaker), `height` (ceiling or top speaker), `sub` (the subwoofer / LFE output; at most one). |
| `port` | Output port index on the interface (0-based). Each speaker needs its own port; unused ports stay silent. |
| `azimuth`, `elevation` | Degrees from the listening position (0 = straight ahead, negative = left; elevation above ear height). They only draw the plan view; they do not change any processing. |
| `bass` | `full`: the speaker's low end is left alone. `sub`: Roomtone high-passes it at `crossover_hz` (Linkwitz-Riley 4th order) and sends the low end, summed, to the sub. Needs a `sub` channel. |
| `amp_bass_management` | `true` means the amplifier already splits bass for the speakers it drives, so a *bed* speaker may not be set to `bass: sub` (the file is refused). Height speakers may still be `bass: sub`: on the author's rig they are on a separate amplifier. `false` means Roomtone is the only thing doing it, wherever you ask for it. |
| `room_defaults` | `author`: starting values are the author's measurements (only meaningful for the author's rig). `neutral`: zero delay, zero trim, no ceiling tone filters, no bass filters until autocal proposes some. |
| `output.port_names` | What PipeWire calls each output port, e.g. `playback_FL`. A 7.1 USB interface uses `FL FR RL RR FC LFE SL SR` (so `RL` is the rear-left port of the interface even when a ceiling speaker is wired to it). Interfaces with more outputs usually show `AUX8`, `AUX9`, and so on. |

### Presets

Shipped in the code and offered by the first-run helper and the Layout page. Choosing one writes a normal layout file you can then edit.

| Preset | Output ports | Speakers | Notes |
|---|---:|---|---|
| `2.0` | 2 | FL FR | No sub, no upmixers. |
| `2.1` | 6 | FL FR LFE | Sub on port 5 of a 6-output interface. |
| `5.1` | 6 | FL FR FC LFE SL SR | Surrounds on ports 2 and 3. |
| `5.1.2` | 8 | FL FR FC LFE SL SR RL RR | **The author's rig.** Reproduces the behaviour from before layouts, byte for byte (golden tests). The only preset with `room_defaults: author`. |
| `7.1` | 8 | FL FR FC LFE SL SR BL BR | |
| `7.1.2` | 10 | + TFL TFR | Needs a 10-output interface. |
| `7.1.4` | 12 | + TFL TFR TBL TBR | Needs a 12-output interface. |

Only 5.1.2 has numbers measured in a real room. Every other preset starts neutral and gets its delays and trims from autocal.

## What is configurable, and what is not

Configurable through the layout file: which speakers exist; which interface port each is on; the interface's sink name and port names; the microphone device; which speakers send their bass to the sub, and whether the amplifier already does that; the positions drawn in the plan view.

Followed from the layout by the code: the CamillaDSP channel count and mixers (`set-eq.py`), the router's port lists and sink name (`theater-route`), the channels autocal plays, measures, aligns and reports, the plan view and meters on the status page and the mpv overlay, and which upmixers are offered.

Not configurable (by design or for now):

- **One sub, at most.** No dual-sub or distributed-bass logic.
- **Bass EQ is one shared filter set** for every speaker with `bass: full` plus the sub, as before. It was built for a shared-sub system. For separate full-range speakers it is a compromise, not a per-speaker fit.
- **The upmixers target exactly 5.1.2.** They are offered only when the layout has FL FR FC LFE SL SR and exactly two height speakers (so 5.1.2 and 7.1.2; on 7.1.2 the rear pair stays silent). With any other layout they are not offered and the router and `set-eq.py` say why. There is no stereo-to-5.1, 5.1-to-7.1 or 4-height upmixer yet.
- **The player decides its own port names.** The router links `mpv:output_<port name>` to the matching CamillaDSP input, so the player has to expose ports named like `output.port_names` (the author's wrapper asks mpv for a 7.1 map). Other players and other channel counts are not handled.
- **The numbers in `room_defaults: author`** (delays, trims, ceiling tone filters, the `drone2` bass set) are the author's room. Nobody else should start from them.
- No per-speaker crossover frequencies, no per-speaker distance entry, no room correction beyond what autocal does today.

## Migration

1. No file: nothing changes. The built-in layout is the 5.1.2 preset.
2. `roomtone-layout --init 5.1.2` (or the Layout page, "5.1.2") writes the same layout to `~/.config/roomtone/layout.yml`. Behaviour stays identical; golden tests compare the generated CamillaDSP configs, the router's links, the autocal stimulus order and the proposals before and after.
3. Anything else is a deliberate change: pick another preset or edit channels, save, and restart what the page tells you to restart (CamillaDSP profile reload, the router, the control service; autocal reads the file at the start of each run).
4. `results.json` gains a `layout` field and its `version` goes from 1 to 2. Version-1 files (every run made before this) are still read and are treated as 5.1.2. `room.json` keeps version 1; its keys are the channel names of the active layout.
5. Rolling back: delete or rename `layout.yml`, or restore one of the ten automatic backups (`layout.yml.bak-<time>`).

## Safety

Unchanged: the -20 dBFS autocal output cap, the abort conditions, the sub-distortion stop, the 60-second trial with automatic rollback. New: the Layout page can play a short tone on one output port to identify a speaker. It is capped at -40 dBFS peak, 2 seconds, one channel only, and goes through the same safety limits as the rest. The page is served on 127.0.0.1 only, with the same Host and Origin checks, the same per-session token on every POST and the same fixed action table as the rest of the control service; there is no shell and no free-form option anywhere in it.

## Honest limits

Written with an AI assistant; checked against simulated rooms and REW exports; used and checked by the author in his own room (one room, one author). No external or second-person validation yet. Most of the checking so far was done by the author himself.

- **The 5.1.2 layout is the only one that has ever run on real hardware.** Every other preset and every edited layout has been exercised in unit tests and in the simulated room only: the generated CamillaDSP configs are checked for structure and, where `camilladsp -c` is available, syntax; the simulated autocal runs per preset check that the pipeline completes, orders channels correctly and produces proposals for every speaker. Whether the sound is right on a real 7.1.4 system is untested.
- The simulated room's geometry for non-author layouts comes from the azimuth and elevation in the file, with the author's room dimensions. It shows the code works for N speakers; it says nothing about your room.
- The identify tone, the interface detection (`pw-link`, `pactl`) and the player port names are tested against fake command output, not against live PipeWire on other hardware.
- Nobody but the author has repeated any of this.
