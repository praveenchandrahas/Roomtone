"""Speaker layout for Roomtone and linux-theater-dsp: one small file says which speakers you have and where they are wired.

CANONICAL COPY: linux-theater-dsp/scripts/layout.py. Roomtone carries an identical copy as autocal/layout.py (a test in each repository
checks the presets against the golden table below, and Roomtone's test compares the file with the sibling copy when it is present).
Standard library only. No code from the layout file is ever executed; it is parsed as data and validated.

The file (default ~/.config/roomtone/layout.yml, or $ROOMTONE_LAYOUT) is YAML or JSON:

    version: 1
    name: 5.1.2 (the author's rig)
    preset: 5.1.2                 # which preset it started from (informational)
    room_defaults: author         # author = the author's measured delays/trims/tone filters; neutral = all zero
    amp_bass_management: true     # true: the amplifier sends bass to the sub itself (Z906-like); false: Roomtone must
    crossover_hz: 140             # Roomtone's own crossover, used only for channels with bass: sub
    output:
      device: alsa_output.usb-...   # PipeWire sink node name of the interface
      channels: 8                   # number of output ports on the interface
      port_names: [FL, FR, RL, RR, FC, LFE, SL, SR]   # PipeWire port suffix of each output port (playback_<name>), in port order
    microphone: {device: null}      # PipeWire source node name; null = autocal picks it as before
    channels:
      - {name: FL, role: bed, port: 0, azimuth: -30, elevation: 0, bass: full}
      ...

Roles: bed (a speaker at ear height), height (a ceiling/top speaker), sub (the subwoofer/LFE output; at most one).
bass: full (leave its low end alone) | sub (Roomtone high-passes it at crossover_hz and sends the low end to the sub). Azimuth is degrees seen from
the listening position (0 = front centre, negative = left, positive = right), elevation degrees above ear height; both only draw the plan view.
"""
import copy
import datetime
import json
import os
import re
import subprocess

LAYOUT_VERSION = 1
DEFAULT_PATH = "~/.config/roomtone/layout.yml"
ENV_PATH = "ROOMTONE_LAYOUT"
MAX_BYTES = 1 << 16
MAX_CHANNELS = 32
ROLES = ("bed", "height", "sub")
BASS = ("full", "sub")
ROOM_DEFAULTS = ("author", "neutral")
BACKUPS_KEPT = 10
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,7}$")
PORT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
SINK_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,200}$")

AUTHOR_DEVICE = "alsa_output.usb-0d8c_USB_Sound_Device-00.analog-surround-71"
# PipeWire's port suffix for the first ports of a standard interface, in port order (the 7.1 map FL FR RL RR FC LFE SL SR); beyond that AUX<n>
STD_PORTS = ["FL", "FR", "RL", "RR", "FC", "LFE", "SL", "SR"]


class LayoutError(ValueError):
    """The layout file is not usable; the message says where and why."""


def std_port_names(n):
    """Port suffixes of an n-channel interface: 2 -> FL FR; 6 -> FL FR RL RR FC LFE; 8 -> + SL SR; more -> + AUX8, AUX9 ..."""
    return [STD_PORTS[i] if i < min(n, 8) else f"AUX{i}" for i in range(n)]


def _ch(name, role, port, az, el, bass="full"):
    return {"name": name, "role": role, "port": port, "azimuth": az, "elevation": el, "bass": bass}


def _preset(name, n, chans, amp=False, room="neutral", device="", note=""):
    return {"version": LAYOUT_VERSION, "name": name, "preset": name, "room_defaults": room, "amp_bass_management": amp, "crossover_hz": 140,
            "output": {"device": device, "channels": n, "port_names": std_port_names(n)}, "microphone": {"device": None}, "channels": chans, "note": note}


def _presets():
    FL, FR = _ch("FL", "bed", 0, -30, 0), _ch("FR", "bed", 1, 30, 0)
    FC, LFE = _ch("FC", "bed", 4, 0, 0), _ch("LFE", "sub", 5, 0, 0)
    P = {}
    P["2.0"] = _preset("2.0", 2, [FL, FR], note="Two speakers. No sub, so no bass management; the upmixers are not offered.")
    P["2.1"] = _preset("2.1", 6, [FL, FR, LFE], note="Two speakers and a sub on the LFE output (port 5 of a 6-channel interface).")
    P["5.1"] = _preset("5.1", 6, [FL, FR, FC, LFE, _ch("SL", "bed", 2, -110, 0), _ch("SR", "bed", 3, 110, 0)], note="Surrounds on the interface's rear pair (ports 2, 3).")
    P["5.1.2"] = _preset("5.1.2", 8, [FL, FR, FC, LFE, _ch("SL", "bed", 6, -100, 0), _ch("SR", "bed", 7, 100, 0),
                                       _ch("RL", "height", 2, -135, 50, "sub"), _ch("RR", "height", 3, 135, 50, "sub")],
                         amp=True, room="author", device=AUTHOR_DEVICE,
                         note="The author's rig: Z906 for the five satellites and the sub (it does its own bass management), a USB 7.1 interface whose "
                              "RL/RR outputs feed the two ceiling speakers. Reproduces the behaviour from before layouts existed.")
    P["7.1"] = _preset("7.1", 8, [FL, FR, FC, LFE, _ch("SL", "bed", 6, -90, 0), _ch("SR", "bed", 7, 90, 0), _ch("BL", "bed", 2, -150, 0), _ch("BR", "bed", 3, 150, 0)],
                       note="Sides on ports 6, 7 and rears on ports 2, 3 of a standard 7.1 interface.")
    p712 = [FL, FR, FC, LFE, _ch("SL", "bed", 6, -90, 0), _ch("SR", "bed", 7, 90, 0), _ch("BL", "bed", 2, -150, 0), _ch("BR", "bed", 3, 150, 0),
            _ch("TFL", "height", 8, -45, 45, "sub"), _ch("TFR", "height", 9, 45, 45, "sub")]
    P["7.1.2"] = _preset("7.1.2", 10, p712, note="Needs a 10-output interface; the top speakers are on ports 8 and 9 (PipeWire names AUX8, AUX9).")
    p714 = p712 + [_ch("TBL", "height", 10, -135, 45, "sub"), _ch("TBR", "height", 11, 135, 45, "sub")]
    P["7.1.4"] = _preset("7.1.4", 12, p714, note="Needs a 12-output interface; tops on ports 8 to 11.")
    return P


PRESETS = _presets()
PRESET_ORDER = ["2.0", "2.1", "5.1", "5.1.2", "7.1", "7.1.2", "7.1.4"]
AUTHOR_PRESET = "5.1.2"


# ---------------------------------------------------------------------------------------------------------------------
class Layout:
    """A validated layout. Use load(), from_dict() or preset(); never build one by hand."""

    def __init__(self, d, source="built-in"):
        self.d = d
        self.source = source
        self.name = d["name"]
        self.preset = d.get("preset")
        self.room_defaults = d["room_defaults"]
        self.amp_bass_management = d["amp_bass_management"]
        self.crossover_hz = d["crossover_hz"]
        self.device = d["output"]["device"]
        self.n = d["output"]["channels"]
        self.port_names = list(d["output"]["port_names"])
        self.mic = d["microphone"].get("device")
        self.channels = [dict(c) for c in d["channels"]]             # file order = the order results are listed in
        self.by_name = {c["name"]: c for c in self.channels}

    # -- order and lookup ---------------------------------------------------------------------------------------------
    @property
    def result_order(self):
        return [c["name"] for c in self.channels]

    @property
    def hw_order(self):
        """Channel names in output-port order (only ports that have a speaker)."""
        return [c["name"] for c in sorted(self.channels, key=lambda c: c["port"])]

    def port(self, name):
        return self.by_name[name]["port"]

    def port_name(self, name):
        return self.port_names[self.by_name[name]["port"]]

    def names_by_role(self, role):
        return [c["name"] for c in self.channels if c["role"] == role]

    @property
    def beds(self):
        return self.names_by_role("bed")

    @property
    def heights(self):
        return self.names_by_role("height")

    @property
    def sub(self):
        s = self.names_by_role("sub")
        return s[0] if s else None

    @property
    def bass_to_sub(self):
        """Channels Roomtone high-passes, sending their low end to the sub (empty without a sub)."""
        return [c["name"] for c in self.channels if c["bass"] == "sub" and c["role"] != "sub"] if self.sub else []

    @property
    def bass_eq_channels(self):
        """Channels that reproduce bass and get the shared bass EQ: bass: full speakers plus the sub (not the ones sent to the sub)."""
        return [c["name"] for c in self.channels if c["bass"] == "full" or c["role"] == "sub"]

    @property
    def reference(self):
        """The channel the others are timed against and the one that carries the sync chirps: FL if there is one, else the first bed speaker."""
        if "FL" in self.by_name and self.by_name["FL"]["role"] == "bed":
            return "FL"
        b = self.beds
        return b[0] if b else self.channels[0]["name"]

    def is_author_512(self):
        a = PRESETS[AUTHOR_PRESET]
        return (self.n == a["output"]["channels"] and self.port_names == a["output"]["port_names"]
                and [(c["name"], c["role"], c["port"], c["bass"]) for c in self.channels] == [(c["name"], c["role"], c["port"], c["bass"]) for c in a["channels"]]
                and self.amp_bass_management == a["amp_bass_management"])

    def position(self, name):
        c = self.by_name[name]
        return (c["azimuth"], c["elevation"])

    # -- which upmixes fit --------------------------------------------------------------------------------------------
    def upmix_support(self):
        """({kind: True/False}, [reasons]). The upmixers write 5.1.2: FL FR FC LFE SL SR plus exactly two height speakers (and nothing else is fed).
        stereo (2.0 -> 5.1.2) and stereo-ad (adaptive) need that target; bed (5.1 -> 5.1.2) too."""
        why = []
        need = {"FL", "FR", "FC", "LFE", "SL", "SR"}
        missing = sorted(need - set(self.by_name))
        if missing:
            why.append("the upmixers write 5.1.2 and this layout has no " + ", ".join(missing))
        if len(self.heights) != 2:
            why.append(f"the upmixers need exactly two height speakers; this layout has {len(self.heights)}")
        ok = not why
        if ok:
            tops = self.heights
            if not (self.by_name[tops[0]]["azimuth"] < 0 < self.by_name[tops[1]]["azimuth"]):
                ok = False
                why.append("the two height speakers must be listed left first, then right (negative then positive azimuth)")
        return ({"stereo": ok, "stereo-ad": ok, "bed": ok}, why)

    def upmix_slots(self):
        """Logical upmix slot -> output port index: FL FR TL TR FC LFE SL SR. Only valid when upmix_support says yes."""
        h = self.heights
        return {"FL": self.port("FL"), "FR": self.port("FR"), "TL": self.port(h[0]), "TR": self.port(h[1]), "FC": self.port("FC"),
                "LFE": self.port("LFE"), "SL": self.port("SL"), "SR": self.port("SR")}

    # -- output ---------------------------------------------------------------------------------------------------------
    def to_dict(self):
        return copy.deepcopy(self.d)

    def to_yaml(self):
        return dump_yaml(self.d)

    def summary(self):
        kinds = ", ".join(self.result_order)
        return f"{self.name}: {len(self.channels)} speakers ({kinds}) on {self.n} output ports; " + \
               ("amplifier does bass management" if self.amp_bass_management else "Roomtone does bass management") + f"; source: {self.source}"


# ---------------------------------------------------------------------------------------------------------------------
def _num(v, where, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise LayoutError(f"{where}: expected a number, got {v!r}")
    if not lo <= v <= hi:
        raise LayoutError(f"{where}: {v} is outside {lo:g}..{hi:g}")
    return v


def _int(v, where, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, float) and v == int(v):
            v = int(v)
        else:
            raise LayoutError(f"{where}: expected a whole number, got {v!r}")
    if not lo <= v <= hi:
        raise LayoutError(f"{where}: {v} is outside {lo}..{hi}")
    return v


def _obj(d, allowed, where, required=()):
    if not isinstance(d, dict):
        raise LayoutError(f"{where}: expected a mapping, got {type(d).__name__}")
    extra = sorted(set(d) - set(allowed))
    if extra:
        raise LayoutError(f"{where}: unknown key(s) {', '.join(map(repr, extra))} (allowed: {', '.join(allowed)})")
    miss = [k for k in required if k not in d]
    if miss:
        raise LayoutError(f"{where}: missing key(s) {', '.join(miss)}")


def validate(doc):
    """Validate a parsed layout and return the normalised dict (all keys present). Raises LayoutError with a message that says where."""
    _obj(doc, ("version", "name", "preset", "room_defaults", "amp_bass_management", "crossover_hz", "output", "microphone", "channels", "note"),
         "layout", required=("version", "output", "channels"))
    if isinstance(doc["version"], bool) or doc["version"] != LAYOUT_VERSION:
        raise LayoutError(f"layout.version: only version {LAYOUT_VERSION} is supported, got {doc['version']!r}")
    name = doc.get("name", "my layout")
    if not isinstance(name, str) or not 1 <= len(name) <= 80 or not name.isprintable():
        raise LayoutError("layout.name: expected 1 to 80 printable characters")
    preset = doc.get("preset")
    if preset is not None and (not isinstance(preset, str) or len(preset) > 40):
        raise LayoutError("layout.preset: expected text of up to 40 characters or nothing")
    rd = doc.get("room_defaults", "neutral")
    if rd not in ROOM_DEFAULTS:
        raise LayoutError(f"layout.room_defaults: must be one of {', '.join(ROOM_DEFAULTS)}, got {rd!r}")
    amp = doc.get("amp_bass_management", False)
    if not isinstance(amp, bool):
        raise LayoutError(f"layout.amp_bass_management: must be true or false, got {amp!r}")
    xo = _num(doc.get("crossover_hz", 140), "layout.crossover_hz", 40, 300)
    note = doc.get("note", "")
    if not isinstance(note, str) or len(note) > 400:
        raise LayoutError("layout.note: expected text of up to 400 characters")

    out = doc["output"]
    _obj(out, ("device", "channels", "port_names"), "output", required=("channels",))
    dev = out.get("device", "")
    if dev is None:
        dev = ""
    if not isinstance(dev, str) or (dev and not SINK_RE.match(dev)):
        raise LayoutError("output.device: expected a PipeWire sink node name (letters, digits, . _ - :), as shown by `pw-link -i`, or empty")
    n = _int(out["channels"], "output.channels", 2, MAX_CHANNELS)
    pn = out.get("port_names")
    if pn is None:
        pn = std_port_names(n)
    if not isinstance(pn, list) or len(pn) != n:
        raise LayoutError(f"output.port_names: expected a list of {n} port names (one per output port), got {len(pn) if isinstance(pn, list) else pn!r}")
    for i, p in enumerate(pn):
        if not isinstance(p, str) or not PORT_RE.match(p):
            raise LayoutError(f"output.port_names[{i}]: {p!r} is not a port suffix such as FL or AUX8")
    if len(set(pn)) != n:
        raise LayoutError("output.port_names: every port name must be different")

    mic = doc.get("microphone", {"device": None})
    _obj(mic, ("device",), "microphone")
    md = mic.get("device")
    if md is not None and (not isinstance(md, str) or not SINK_RE.match(md)):
        raise LayoutError("microphone.device: expected a PipeWire source node name or null")

    chans = doc["channels"]
    if not isinstance(chans, list) or not 1 <= len(chans) <= MAX_CHANNELS:
        raise LayoutError(f"channels: expected a list of 1 to {MAX_CHANNELS} speakers")
    outc, names, ports = [], set(), {}
    for i, c in enumerate(chans):
        w = f"channels[{i}]"
        _obj(c, ("name", "role", "port", "azimuth", "elevation", "bass"), w, required=("name", "role", "port"))
        nm = c["name"]
        if not isinstance(nm, str) or not NAME_RE.match(nm):
            raise LayoutError(f"{w}.name: {nm!r} must be 1 to 8 letters or digits, starting with a letter")
        if nm in names:
            raise LayoutError(f"{w}.name: {nm!r} is used twice")
        names.add(nm)
        role = c["role"]
        if role not in ROLES:
            raise LayoutError(f"{w}.role: must be one of {', '.join(ROLES)}, got {role!r}")
        port = _int(c["port"], f"{w}.port", 0, n - 1)
        if port in ports:
            raise LayoutError(f"{w}.port: output port {port} is already used by {ports[port]!r}; each speaker needs its own port")
        ports[port] = nm
        az = _num(c.get("azimuth", 0), f"{w}.azimuth", -180, 180)
        el = _num(c.get("elevation", 0), f"{w}.elevation", -10, 90)
        bass = c.get("bass", "full")
        if bass not in BASS:
            raise LayoutError(f"{w}.bass: must be one of {', '.join(BASS)}, got {bass!r}")
        if role == "height" and el < 10:
            raise LayoutError(f"{w}.elevation: a height speaker needs an elevation of at least 10 degrees (got {el:g})")
        outc.append({"name": nm, "role": role, "port": port, "azimuth": az, "elevation": el, "bass": bass})
    subs = [c for c in outc if c["role"] == "sub"]
    if len(subs) > 1:
        raise LayoutError("channels: at most one speaker may have role sub (found " + ", ".join(c["name"] for c in subs) + ")")
    if not any(c["role"] == "bed" for c in outc):
        raise LayoutError("channels: at least one speaker must have role bed")
    if not subs:
        for i, c in enumerate(outc):
            if c["bass"] == "sub":
                raise LayoutError(f"channels[{i}].bass: {c['name']} is set to send its bass to the sub, but the layout has no speaker with role sub")
    if amp:
        for i, c in enumerate(outc):
            if c["bass"] == "sub" and c["role"] == "bed":
                raise LayoutError(f"channels[{i}].bass: {c['name']} is a bed speaker driven by an amplifier that already manages bass "
                                  "(amp_bass_management: true). Set bass: full, or set amp_bass_management: false if Roomtone should do it")
    return {"version": LAYOUT_VERSION, "name": name, "preset": preset, "room_defaults": rd, "amp_bass_management": amp, "crossover_hz": xo,
            "output": {"device": dev, "channels": n, "port_names": list(pn)}, "microphone": {"device": md}, "channels": outc, "note": note}


def from_dict(doc, source="built-in"):
    return Layout(validate(doc), source)


def preset(name):
    if name not in PRESETS:
        raise LayoutError(f"unknown preset {name!r} (choose from: {', '.join(PRESET_ORDER)})")
    return Layout(validate(copy.deepcopy(PRESETS[name])), f"preset {name}")


def default_layout():
    return preset(AUTHOR_PRESET)


# ---------------------------------------------------------------------------------------------------------------------
# reading and writing. YAML is read with PyYAML when it is installed; otherwise with the small parser below, which reads exactly what dump_yaml
# writes (plus JSON). Nothing in the file is ever executed (yaml.safe_load only).
def _scalar(s):
    s = s.strip()
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return json.loads(s) if s[0] == '"' else s[1:-1].replace("''", "'")
    if s[0] == "[" and s[-1] == "]":
        return [_scalar(x) for x in _split_top(s[1:-1])] if s[1:-1].strip() else []
    if s[0] == "{" and s[-1] == "}":
        return _flow_map(s)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _split_top(s):
    parts, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch in "[{":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _flow_map(s):
    out = {}
    for part in _split_top(s[1:-1]):
        k, sep, v = part.partition(":")
        if not sep:
            raise LayoutError(f"cannot read {part.strip()!r} (expected key: value)")
        out[k.strip()] = _scalar(v)
    return out


def parse_text(text):
    """Parse YAML/JSON text to Python data. Raises LayoutError."""
    t = text.lstrip()
    if t.startswith("{"):
        try:
            return json.loads(text, object_pairs_hook=_no_dupes)
        except ValueError as e:
            raise LayoutError(f"not valid JSON: {e}")
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise LayoutError(f"not valid YAML: {e}")
    return _simple_yaml(text)


def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise LayoutError(f"duplicate key {k!r}")
        d[k] = v
    return d


def _simple_yaml(text):
    """Reads the subset dump_yaml writes: 'key: scalar', nested mappings by two-space indent, lists of one-line {flow: maps} or [flow, lists]."""
    root = {}
    stack = [(-1, root)]           # (indent, container)
    last_key = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw.rstrip()
        ind = len(line) - len(line.lstrip())
        body = line.strip()
        if " #" in body and not any(q in body for q in "'\""):
            body = body.split(" #", 1)[0].rstrip()
        while len(stack) > 1 and stack[-1][0] >= ind:
            stack.pop()
        cont = stack[-1][1]
        if body.startswith("- "):
            if not isinstance(cont, list):
                raise LayoutError(f"unexpected list item {body!r}")
            item = body[2:].strip()
            cont.append(_scalar(item) if item[:1] in "{[" else _scalar(item))
            continue
        k, sep, v = body.partition(":")
        if not sep:
            raise LayoutError(f"cannot read the line {body!r} (expected key: value; install python3-yaml for full YAML)")
        k = k.strip()
        if v.strip() == "":
            nxt = _peek_is_list(text, raw)
            cont[k] = [] if nxt else {}
            stack.append((ind, cont[k]))
        else:
            cont[k] = _scalar(v)
    return root


def _peek_is_list(text, raw):
    lines = text.splitlines()
    i = lines.index(raw)
    for nxt in lines[i + 1:]:
        if nxt.strip() and not nxt.lstrip().startswith("#"):
            return nxt.lstrip().startswith("- ")
    return False


def _q(s):
    if s is None:
        return "null"
    if isinstance(s, bool):
        return "true" if s else "false"
    if isinstance(s, (int, float)):
        return repr(s) if isinstance(s, float) else str(s)
    if re.match(r"^[A-Za-z0-9_.:/\-]+$", s) and s not in ("true", "false", "null", "yes", "no", "on", "off") and not re.match(r"^[-+]?[0-9.]+(e[-+]?[0-9]+)?$", s, re.I):
        return s
    return json.dumps(s)


def dump_yaml(d):
    """The file as text, in the fixed shape the docs show (one speaker per line)."""
    o = d["output"]
    t = ["# Roomtone speaker layout. Edit with `roomtone-layout` / the Layout page, or by hand; see docs/layouts.md.", f"version: {d['version']}",
         f"name: {_q(d['name'])}", f"preset: {_q(d.get('preset'))}", f"room_defaults: {d['room_defaults']}",
         f"amp_bass_management: {_q(d['amp_bass_management'])}", f"crossover_hz: {_q(d['crossover_hz'])}"]
    if d.get("note"):
        t.append(f"note: {_q(d['note'])}")
    t += ["output:", f"  device: {_q(o['device'])}", f"  channels: {o['channels']}", "  port_names: [" + ", ".join(_q(p) for p in o["port_names"]) + "]",
          "microphone:", f"  device: {_q(d['microphone'].get('device'))}", "channels:"]
    for c in d["channels"]:
        t.append(f"  - {{name: {c['name']}, role: {c['role']}, port: {c['port']}, azimuth: {_q(c['azimuth'])}, elevation: {_q(c['elevation'])}, bass: {c['bass']}}}")
    return "\n".join(t) + "\n"


def parse_file_text(text):
    doc = parse_text(text)
    if not isinstance(doc, dict):
        raise LayoutError("the file is empty or not a mapping")
    return validate(doc)


def path_for(path=None):
    return os.path.expanduser(path or os.environ.get(ENV_PATH) or DEFAULT_PATH)


def load(path=None, strict=False):
    """The active layout. No file -> the built-in 5.1.2 layout (the author's rig, behaviour as before layouts existed).
    A file that exists but is unusable raises LayoutError (callers print it and stop; they never guess)."""
    p = path_for(path)
    if not os.path.isfile(p):
        if strict:
            raise LayoutError(f"{p}: no such file")
        L = default_layout()
        L.source = "built-in default (no layout file)"
        return L
    try:
        with open(p, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise LayoutError(f"cannot read {p}: {e.strerror or e}")
    if len(raw) > MAX_BYTES:
        raise LayoutError(f"{p}: file is larger than {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise LayoutError(f"{p}: not UTF-8 text")
    try:
        return Layout(parse_file_text(text), p)
    except LayoutError as e:
        raise LayoutError(f"{p}: {e}")


def save(layout_or_dict, path=None, now=None):
    """Validate, back up the existing file (layout.yml.bak-YYYYmmdd-HHMMSS, newest 10 kept), then write atomically. Returns (path, backup path or None)."""
    d = validate(layout_or_dict.to_dict() if isinstance(layout_or_dict, Layout) else layout_or_dict)
    p = path_for(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    backup = None
    if os.path.isfile(p):
        stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = f"{p}.bak-{stamp}"
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = f"{p}.bak-{stamp}-{n}"
        with open(p, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
        old = sorted(f for f in os.listdir(os.path.dirname(p)) if f.startswith(os.path.basename(p) + ".bak-"))
        for f in old[:-BACKUPS_KEPT]:
            try:
                os.unlink(os.path.join(os.path.dirname(p), f))
            except OSError:
                pass
    tmp = f"{p}.tmp{os.getpid()}"
    try:
        with open(tmp, "w") as fh:
            fh.write(dump_yaml(d))
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p, backup


# ---------------------------------------------------------------------------------------------------------------------
# first-run helper: what does PipeWire say about the interface?
def parse_sink_ports(text):
    """From `pw-link -i` output: {node name: [port suffix, ...]} for playback-style input ports (name:playback_XX), in the order listed."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(.+):playback_([A-Za-z0-9_]+)$", line)
        if m and not m.group(1).startswith(("camilladsp", "mpv", "theater-")):
            out.setdefault(m.group(1), []).append(m.group(2))
    return out


def detect_interfaces(pw_link="pw-link"):
    """[(device, [port suffixes])] of the sinks PipeWire currently shows, or [] when pw-link is unavailable. Read-only."""
    try:
        r = subprocess.run([pw_link, "-i"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(parse_sink_ports(r.stdout).items(), key=lambda kv: -len(kv[1]))


def suggest_preset(n_ports):
    """The largest preset the interface can drive (by output count): 2 -> 2.0, 6 -> 5.1, 8 -> 7.1, 10 -> 7.1.2, 12+ -> 7.1.4. The author's 5.1.2 is
    never suggested for anyone else: it carries his measured numbers."""
    for name, need in (("7.1.4", 12), ("7.1.2", 10), ("7.1", 8), ("5.1", 6), ("2.0", 2)):
        if n_ports >= need:
            return name
    return "2.0"


def propose(device, ports, preset_name=None):
    """A layout dict for a detected interface: the suggested (or given) preset, with the device and its real port names filled in. Does not save."""
    n = len(ports)
    name = preset_name or suggest_preset(n)
    d = copy.deepcopy(PRESETS[name])
    need = d["output"]["channels"]
    if n < need:
        raise LayoutError(f"preset {name} needs {need} output ports and {device} has {n}")
    d["output"] = {"device": device, "channels": n, "port_names": list(ports)}
    d["name"] = f"{name} on {device.split('.')[-1] if '.' in device else device}"[:80]
    d["room_defaults"] = "neutral"
    d["note"] = "Proposed from the ports PipeWire lists. Check that each speaker is on the port you think (use the identify buttons), then save."
    return d


# ---------------------------------------------------------------------------------------------------------------------
def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="roomtone-layout", description="Show, check, create or detect the speaker layout (docs/layouts.md). Nothing here plays sound.")
    ap.add_argument("--file", help=f"layout file (default {DEFAULT_PATH}, or ${ENV_PATH})")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--show", action="store_true", help="print the active layout (default)")
    g.add_argument("--validate", action="store_true", help="check the file and say what is wrong, if anything")
    g.add_argument("--presets", action="store_true", help="list the presets")
    g.add_argument("--init", metavar="PRESET", help="write a preset to the layout file (an existing file is backed up first)")
    g.add_argument("--detect", action="store_true", help="read the interface's output ports from PipeWire (pw-link -i) and propose a preset; writes nothing unless --write")
    ap.add_argument("--device", help="with --detect: which sink to use when several are found")
    ap.add_argument("--write", action="store_true", help="with --detect: write the proposal to the layout file")
    a = ap.parse_args(argv)
    try:
        if a.presets:
            for n in PRESET_ORDER:
                p = PRESETS[n]
                print(f"{n:<6} {p['output']['channels']:>2} ports  {' '.join(c['name'] for c in p['channels'])}\n         {p['note']}")
            return 0
        if a.init:
            p, bak = save(preset(a.init).to_dict(), a.file)
            print(f"wrote {p}" + (f" (previous file saved as {bak})" if bak else ""))
            return 0
        if a.detect:
            found = detect_interfaces()
            if not found:
                print("no output ports found (is PipeWire running? `pw-link -i` shows them). Nothing written.")
                return 1
            for dev, ports in found:
                print(f"{dev}: {len(ports)} ports ({', '.join(ports)}) -> suggested preset {suggest_preset(len(ports))}")
            dev, ports = next(((d, p) for d, p in found if d == a.device), found[0]) if a.device else found[0]
            d = propose(dev, ports)
            print("\nProposal for", dev + ":\n")
            print(dump_yaml(validate(d)))
            if a.write:
                p, bak = save(d, a.file)
                print(f"wrote {p}" + (f" (previous file saved as {bak})" if bak else ""))
            else:
                print("(not written; add --write to save it, then check each speaker with the Layout page's identify buttons)")
            return 0
        L = load(a.file, strict=a.validate)
        if a.validate:
            print(f"OK: {L.summary()}")
            for kind, why in (("upmixers", L.upmix_support()),):
                ok, reasons = why
                print("upmixers: " + ("stereo, stereo-ad and 5.1 -> 5.1.2 fit this layout" if ok["stereo"] else "none fit: " + "; ".join(reasons)))
            return 0
        print(L.summary())
        print(L.to_yaml())
        return 0
    except LayoutError as e:
        print(f"layout error: {e}")
        return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
