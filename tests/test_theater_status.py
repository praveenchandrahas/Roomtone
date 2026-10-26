"""theater_status.lua under a mocked mpv API (lupa).   Run: python3 tests/test_theater_status.py"""
import json, os, sys
from lupa import LuaRuntime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "overlay", "theater_status.lua")).read()
ok = True


def check(msg, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + msg, extra)
    ok = ok and cond


STATUS = {"schema": 1, "eq": {"switch": "on", "router": "active", "camilla": "active", "camilla_summary": "x"},
          "dsp": {"profile": "film", "pmode": "auto", "flavours": {"stereo": "balanced", "bed": "balanced"},
                  "engine": {"state": "on", "service": "inactive", "ports_up": False, "linked": False}},
          "apps": [], "mpv": {"playing": True, "channels": ["FL"], "verdict": "cam", "route": {"via": 8, "direct": 0, "to_usb": 8, "channels": 8},
                              "source": {"codec": "truehd", "profile": "Atmos", "channels": 8, "kind": "film", "why": "Atmos: object rendering, no upmix"}},
          "live": {"ok": True, "state": "Running", "load_percent": 7.5, "clipped_samples": 0}, "underruns_60s": 0}


def world(uosc=False, ctl=True, menu="auto"):
    lua = LuaRuntime(unpack_returned_tuples=True)
    calls, msgs, bindings, osd = [], [], {}, []
    state = {"status": STATUS, "ctl": ctl}

    def subprocess_(cmd, cb):
        args = [cmd["args"][i] for i in sorted(cmd["args"].keys())]
        calls.append(args)
        if args[0] == "curl":
            res = {"status": 0 if state["ctl"] else 7, "stdout": json.dumps(state["status"]) if state["ctl"] else ""}
        elif len(args) > 2 and args[1:3] == ["status", "--json"]:
            res = {"status": 0, "stdout": json.dumps(state["status"])}
        else:
            res = {"status": 0, "stdout": "ok"}
        cb(True, lua.table_from(res), None)

    class Overlay:
        def __init__(self): self.data = ""; self.res_x = self.res_y = 0; self.removed = False
        def update(self, *a): self.removed = False
        def remove(self, *a): self.removed = True; self.data = ""
    overlay = Overlay()
    lua.execute("""
        function mkenv(py)
          package.preload["mp"] = function() return py.mp end
          package.preload["mp.utils"] = function() return py.utils end
          package.preload["mp.msg"] = function() return { info = function() end, warn = function() end } end
          package.preload["mp.options"] = function() return { read_options = function(o) for k, v in pairs(py.opts) do o[k] = v end end } end
        end
    """)
    mp = lua.table(
        create_osd_overlay=lambda kind: _OverlayProxy(overlay),
        command_native_async=lambda cmd, cb: subprocess_(cmd, cb),
        osd_message=lambda text, secs=None: osd.append(text),
        get_osd_size=lambda: (1280, 720), get_mouse_pos=lambda: lua.table(x=100, y=60),
        add_periodic_timer=lambda s, f: None, add_key_binding=lambda *a: None,
        add_forced_key_binding=lambda k, name, fn, opts=None: bindings.__setitem__(name, fn),
        remove_key_binding=lambda name: bindings.pop(name, None),
        register_script_message=lambda name, fn: bindings.__setitem__("msg:" + name, fn),
        register_event=lambda *a: None, get_script_name=lambda: "theater_status",
        find_config_file=lambda n: "/cfg/scripts" if uosc else None,
        commandv=lambda *a: msgs.append(list(a)))
    utils = lua.table(parse_json=lambda s: lua.table_from(json.loads(s), recursive=True) if s else None,
                      format_json=lambda t: json.dumps(_py(t)), file_info=lambda p: True if (uosc and "uosc" in p) else (True if p.endswith("/eq") else None))
    lua.globals().mkenv(lua.table(mp=mp, utils=utils, opts=lua.table(menu=menu, eq="/bin/eq")))
    api = lua.execute(SRC.replace("local mp = require", "local mp = require", 1))
    return api, calls, msgs, bindings, osd, overlay, state, lua


class _OverlayProxy:
    def __init__(self, o): object.__setattr__(self, "o", o)
    def __getattr__(self, k): return getattr(self.o, k)
    def __setattr__(self, k, v): setattr(self.o, k, v)


def _py(x):
    if hasattr(x, "items") and not isinstance(x, dict):
        keys = list(x.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [_py(x[k]) for k in sorted(keys)]
        return {k: _py(v) for k, v in x.items()}
    return x


# ---- status panel
api, calls, msgs, bindings, osd, overlay, st, lua = world()
api.state.status = lua.table_from(STATUS, recursive=True)
text = "\n".join(api.lines_for_panel().values())
check("status lines show source, path, profile, engine and DSP numbers", all(s in text for s in ("truehd 8ch -> film", "through CamillaDSP", "film  (mode auto)", "Engine: on", "load 8%", "underruns/min 0")), text)
check("ASS escaping neutralises braces and backslashes", "{" not in api.esc("a{b}\\c").replace("\\{", "") and "\\{" in api.esc("{"))
api.state.status = lua.table_from({"eq": {"switch": "off"}, "dsp": {}, "mpv": {}}, recursive=True)
check("a sparse status does not crash the panel", "OFF" in "\n".join(api.lines_for_panel().values()))

# ---- rows: current value, next value, guards
api, calls, msgs, bindings, osd, overlay, st, lua = world()
api.state.status = lua.table_from(STATUS, recursive=True)
items = list(api.build_items(1).values())
check("one row each for EQ, mode, both flavours and the engine, showing current values",
      [(i["label"], i["value"]) for i in items] == [("EQ", "on"), ("Profile mode", "auto"), ("Stereo flavour", "balanced"), ("5.1 flavour", "balanced"), ("Adaptive engine", "on")], str([(i["label"], i["value"]) for i in items]))
check("next values step through the lists", [i["next"] for i in items] == ["off", "film", "wide", "wide", "off"], str([i["next"] for i in items]))
back = list(api.build_items(-1).values())
check("previous values wrap round", [i["next"] for i in back] == ["off", "bed", "voice", "subtle", "off"], str([i["next"] for i in back]))
check("only EQ going on is unrisky; EQ off, mode, flavours and engine ask first", [i["risky"] for i in items] == [True, True, True, True, True])
api.state.status = lua.table_from({"eq": {"switch": "off"}, "dsp": {}, "mpv": {}}, recursive=True)
eqrow = list(api.build_items(1).values())[0]
check("with EQ off, the EQ row steps to on and does not ask", eqrow["next"] == "on" and not eqrow["risky"])
check("argv are fixed lists of enum words only", all(all(a.isalnum() for a in list(i["args"].values())) for i in list(api.build_items(1).values())))
api.state.status = lua.table_from(STATUS, recursive=True)
items = list(api.build_items(1).values())
api.perform(items[1])
check("perform runs `eq mode film` and nothing else", [c for c in calls if c[0] == "/bin/eq" and c[1] != "status"] == [["/bin/eq", "mode", "film"]], str(calls))

# ---- the panel: status first, rows below, stays open and reflects the router
api, calls, msgs, bindings, osd, overlay, st, lua = world()
api.open_menu()
check("C opens one panel: status lines, then CONTROLS rows with their values; the keys are bound",
      api.state.menu and "theater-menu-ENTER" in bindings and overlay.data.index("THEATRE") < overlay.data.index("CONTROLS") < overlay.data.index("Profile mode") and "balanced" in overlay.data and not msgs)
bindings["theater-menu-DOWN"]()
check("Down moves the selection to the second row", api.state.sel == 2)
bindings["theater-menu-ENTER"]()
check("a risky step asks first and shows from -> to (nothing run yet)", api.state.pending is not None and "auto -> film" in overlay.data and not [c for c in calls if c[1:2] == ["mode"]])
bindings["theater-menu-ESC"]()
check("Esc cancels the question and keeps the panel open", api.state.pending is None and api.state.menu)
st["status"] = json.loads(json.dumps(STATUS))
bindings["theater-menu-ENTER"](); st["status"]["dsp"]["pmode"] = "film"; bindings["theater-menu-ENTER"]()
check("Enter twice runs `eq mode film`, leaves the panel open and re-reads the status",
      [c for c in calls if c[1:2] == ["mode"]] == [["/bin/eq", "mode", "film"]] and api.state.menu and "film" in overlay.data.split("Profile mode")[1].split("\n")[0], overlay.data)
bindings["theater-menu-LEFT"]()
check("Left asks for the previous value (film -> auto)", api.state.pending is not None and "film -> auto" in overlay.data)
bindings["theater-menu-ESC"](); bindings["theater-menu-ESC"]()
check("a second Esc closes the panel and frees the keys", not api.state.menu and "theater-menu-ENTER" not in bindings and overlay.removed)
api, calls, msgs, bindings, osd, overlay, st, lua = world()
api.open_menu(); bindings["theater-menu-MBTN_LEFT"]()
check("a click on the title area runs nothing", not [c for c in calls if c[0] == "/bin/eq" and c[1] != "status"])

# ---- no ctld: falls back to eq status --json
api, calls, msgs, bindings, osd, overlay, st, lua = world(ctl=False)
api.open_menu()
check("without theater-ctld the status comes from `eq status --json`", any(c[0] == "curl" for c in calls) and any(c[1:3] == ["status", "--json"] for c in calls) and api.state.status is not None)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
