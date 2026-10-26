-- theater_status.lua: status panel and control menu for the home theatre rig, as an overlay on mpv.
--
--   C    open or close the theatre panel: the status first (source, route, profile, engine, CamillaDSP load/clipping/underruns),
--        then one row per setting showing its current value (EQ, profile mode, stereo flavour, 5.1 flavour, adaptive engine).
--        Up/Down select a row, Enter (or Right, or a click) steps it to the next value, Left to the previous; Esc or C closes.
--        The panel stays open and re-reads the status, so a row shows what the router reports after the change, not what was asked.
--
-- It runs the `eq` command (the router stays the only thing that changes audio) and, for live DSP numbers, asks theater-ctld on
-- 127.0.0.1 if that is running. If theater-ctld is not running the panel still works from `eq status --json`.
-- Changes that cause about a second of silence (profile mode, flavour, engine) or make the sound louder (EQ off) ask for a second
-- confirmation, because mpv is playing whenever this panel is open. The panel is drawn by this script (it does not use uosc).
--
-- Install: scripts/install-theater-status.sh (copies this file into the mpv config folders; --bindings adds the `c` line to input.conf).
-- Options (script-opts/theater_status.conf):  eq=/path/to/eq   ctl_url=http://127.0.0.1:8765   refresh=1.5

local mp = require "mp"
local utils = require "mp.utils"
local msg = require "mp.msg"
local options = require "mp.options"

local opts = { eq = "", ctl_url = "http://127.0.0.1:8765", refresh = 1.5, font_size = 26 }
options.read_options(opts, "theater_status")

local state = { status = nil, menu = false, sel = 1, items = {}, pending = nil, busy = false }
local ov = mp.create_osd_overlay("ass-events")
local timer

-- ---------------------------------------------------------------- helpers
local function eq_path()
    if opts.eq ~= "" then return opts.eq end
    local home = os.getenv("HOME") or ""
    local p = home .. "/.local/bin/eq"
    if utils.file_info(p) then return p end
    return "eq"
end

local function esc(s)                       -- ASS-safe text (same idiom as uosc)
    s = tostring(s == nil and "" or s)
    return (s:gsub("\\", "\\\239\187\191"):gsub("{", "\\{"):gsub("\n", "\\N"))
end

local function run(args, cb)
    mp.command_native_async({ name = "subprocess", args = args, capture_stdout = true, capture_stderr = true, playback_only = false },
        function(ok, res, err)
            cb(ok and res and res.status == 0, res, err)
        end)
end

local function have(t, ...)                 -- safe nested lookup
    for _, k in ipairs({ ... }) do
        if type(t) ~= "table" then return nil end
        t = t[k]
    end
    return t
end

-- ---------------------------------------------------------------- status
local function set_status(js)
    local d = js and utils.parse_json(js)
    if type(d) == "table" and (d.eq or d.error) then state.status = d else state.status = nil end
end

local function fetch(cb)
    run({ "curl", "-s", "--max-time", "1", opts.ctl_url .. "/v1/status" }, function(ok, res)
        if ok and res.stdout and res.stdout ~= "" then
            set_status(res.stdout)
            if state.status and state.status.eq then return cb() end
        end
        run({ eq_path(), "status", "--json" }, function(ok2, res2)
            if ok2 then set_status(res2.stdout) else state.status = nil end
            cb()
        end)
    end)
end

local function lines_for_panel()
    local s, L = state.status, {}
    if not s or not s.eq then return { "Theatre: status not available (is `eq` installed?)" } end
    local dsp, m = s.dsp or {}, s.mpv or {}
    local en = dsp.engine or {}
    L[#L + 1] = "THEATRE"
    local src = m.source
    L[#L + 1] = "Source:  " .. (src and (src.codec .. " " .. tostring(src.channels) .. "ch -> " .. src.kind .. "  (" .. src.why .. ")") or (m.playing and "not identified" or "-"))
    local path = ({ cam = "through CamillaDSP (EQ active)", direct = "direct, no EQ", mixed = "routing unfinished",
                    ["usb-missing"] = "USB interface not connected" })[m.verdict or ""]
    if path then L[#L + 1] = "Path:    " .. path end
    L[#L + 1] = "Profile: " .. tostring(dsp.profile) .. "  (mode " .. tostring(dsp.pmode) .. ((dsp.pmode and dsp.pmode ~= "auto") and ", pinned" or "") .. ")"
    L[#L + 1] = "EQ:      " .. string.upper(tostring(s.eq.switch)) .. "     Engine: " .. tostring(en.state) ..
        (en.linked and " (linked)" or (en.ports_up and " (ports up)" or ""))
    local live = s.live
    if live and live.ok then
        L[#L + 1] = string.format("DSP:     %s  load %s%%  clipped %s  underruns/min %s", tostring(live.state),
            live.load_percent and string.format("%.0f", live.load_percent) or "?", tostring(live.clipped_samples), tostring(s.underruns_60s))
    elseif s.underruns_60s ~= nil then
        L[#L + 1] = "Underruns (last minute): " .. tostring(s.underruns_60s)
    end
    return L
end

-- ---------------------------------------------------------------- the rows (each is a setting with its current value)
local ROWS = {
    { key = "eq",     label = "EQ",             vals = { "on", "off" },                          cmd = { "%s" } },
    { key = "mode",   label = "Profile mode",   vals = { "auto", "film", "stereo", "bed" },      cmd = { "mode", "%s" } },
    { key = "stereo", label = "Stereo flavour", vals = { "voice", "balanced", "wide" },          cmd = { "flavour", "stereo", "%s" } },
    { key = "bed",    label = "5.1 flavour",    vals = { "subtle", "balanced", "wide" },         cmd = { "flavour", "bed", "%s" } },
    { key = "engine", label = "Adaptive engine", vals = { "on", "off" },                         cmd = { "engine", "%s" } },
}

local function current(key)
    local s = state.status or {}
    local dsp = s.dsp or {}
    if key == "eq" then return have(s, "eq", "switch")
    elseif key == "mode" then return dsp.pmode
    elseif key == "stereo" then return have(dsp, "flavours", "stereo")
    elseif key == "bed" then return have(dsp, "flavours", "bed")
    elseif key == "engine" then return have(dsp, "engine", "state") end
end

local function index_of(vals, v)
    for i, x in ipairs(vals) do if x == v then return i end end
end

-- Row list for the current status: value now, the value one step on (dir = 1) or back (dir = -1), the argv to get there, and whether to ask first.
local function build_items(dir)
    dir = dir or 1
    local items = {}
    for _, r in ipairs(ROWS) do
        local cur = current(r.key)
        local i = index_of(r.vals, cur)
        local nxt = r.vals[i and ((i - 1 + dir) % #r.vals + 1) or 1]
        local args = {}
        for _, a in ipairs(r.cmd) do args[#args + 1] = (a:gsub("%%s", nxt)) end
        local risky, note = true, "about 1 second of silence"
        if r.key == "eq" then
            risky = (nxt == "off")
            note = "EQ off is louder than EQ on"
        end
        items[#items + 1] = { key = r.key, label = r.label, value = cur, next = nxt, args = args, risky = risky, note = note,
                              title = r.label .. ": " .. tostring(cur) .. " -> " .. tostring(nxt) }
    end
    return items
end

local render, close_menu

local function perform(item)
    if state.busy then return end
    if item.value == item.next then mp.osd_message("Theatre: already set", 1.5); return end
    state.busy = true
    local args = { eq_path() }
    for _, a in ipairs(item.args) do args[#args + 1] = a end
    run(args, function(ok, res)
        state.busy = false
        mp.osd_message(ok and ("Theatre: " .. table.concat(item.args, " ") .. " done") or ("Theatre: failed (" .. table.concat(item.args, " ") .. ")"), 2.0)
        fetch(function() state.items = build_items(1); render() end)
    end)
end

-- ---------------------------------------------------------------- rendering
local function draw(lines, sel_line)
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    ov.res_x, ov.res_y = w, h
    local fs = opts.font_size
    local out = {}
    for i, l in ipairs(lines) do
        local style = "{\\an7\\pos(" .. 40 .. "," .. (40 + (i - 1) * math.floor(fs * 1.35)) .. ")\\fs" .. fs .. "\\bord2\\shad0\\1c&HFFFFFF&\\3c&H202020&"
        if l == "THEATRE" or l:sub(1, 8) == "CONTROLS" then style = style .. "\\b1\\1c&HF0C040&" end
        if sel_line and i == sel_line then style = style .. "\\1c&H40E0FF&\\b1" end
        out[#out + 1] = style .. "}" .. esc(l)
    end
    ov.data = table.concat(out, "\n")
    ov:update()
end

local first_row_line = 0

function render()
    if not state.menu then ov:remove(); return end
    local lines = lines_for_panel()
    lines[#lines + 1] = ""
    if state.pending then
        local it = state.pending.item
        lines[#lines + 1] = "CONTROLS"
        lines[#lines + 1] = it.label .. ": " .. tostring(it.value) .. " -> " .. tostring(it.next) .. ".  " .. it.note .. "."
        lines[#lines + 1] = "Enter = continue     Esc = cancel"
        draw(lines, nil)
        return
    end
    lines[#lines + 1] = "CONTROLS   (Up/Down select, Enter or Right = next value, Left = previous, Esc or C closes)"
    first_row_line = #lines + 1
    for i, it in ipairs(state.items) do
        lines[#lines + 1] = string.format("%s %-16s %s", i == state.sel and ">" or " ", it.label, tostring(it.value == nil and "?" or it.value))
    end
    draw(lines, first_row_line + state.sel - 1)
end

-- ---------------------------------------------------------------- keys and mouse (bound only while the panel is open)
local KEYS = { "UP", "DOWN", "LEFT", "RIGHT", "ENTER", "KP_ENTER", "ESC", "WHEEL_UP", "WHEEL_DOWN", "MBTN_LEFT" }

local function step(dir)
    if state.pending then return end
    local it = build_items(dir)[state.sel]
    if not it then return end
    if it.risky and it.value ~= it.next then
        state.pending = { item = it }
    else
        perform(it)
    end
end

local function confirm()
    local it = state.pending and state.pending.item
    state.pending = nil
    if it then perform(it) end
end

local function key_handler(name)
    return function()
        if name == "UP" or name == "WHEEL_UP" then
            if not state.pending then state.sel = (state.sel - 2) % #state.items + 1 end
        elseif name == "DOWN" or name == "WHEEL_DOWN" then
            if not state.pending then state.sel = state.sel % #state.items + 1 end
        elseif name == "ENTER" or name == "KP_ENTER" or name == "RIGHT" then
            if state.pending then confirm() else step(1) end
        elseif name == "LEFT" then step(-1)
        elseif name == "ESC" then
            if state.pending then state.pending = nil else close_menu(); return end
        elseif name == "MBTN_LEFT" then
            local pos = mp.get_mouse_pos()
            local _, h = mp.get_osd_size()
            local fs = opts.font_size
            local line = math.floor((pos.y * ((ov.res_y or h) / math.max(h or 1, 1)) - 40) / math.floor(fs * 1.35)) + 1
            local row = line - first_row_line + 1
            if not state.pending and row >= 1 and row <= #state.items then state.sel = row; step(1) end
        end
        render()
    end
end

function close_menu()
    state.menu, state.pending = false, nil
    for _, k in ipairs(KEYS) do mp.remove_key_binding("theater-menu-" .. k) end
    render()
end

local function open_menu()
    fetch(function()
        state.items, state.pending = build_items(1), nil
        state.menu = true
        for _, k in ipairs(KEYS) do
            mp.add_forced_key_binding(k, "theater-menu-" .. k, key_handler(k), { repeatable = (k == "UP" or k == "DOWN") })
        end
        render()
    end)
end

-- ---------------------------------------------------------------- refresh while open
local function tick()
    if state.menu and not state.pending then fetch(function() state.items = build_items(1); render() end) end
end

timer = mp.add_periodic_timer(opts.refresh, tick)
mp.add_key_binding(nil, "control", function() if state.menu then close_menu() else open_menu() end end)
mp.register_event("shutdown", function() ov:remove() end)
msg.info("theater_status loaded: bind `c script-binding " .. mp.get_script_name() .. "/control`")

-- for tests
return { state = state, build_items = build_items, lines_for_panel = lines_for_panel, perform = perform, open_menu = open_menu,
         key_handler = key_handler, set_status = set_status, esc = esc }
