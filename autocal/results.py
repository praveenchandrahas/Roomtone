"""results.json / run.json assembly (schema section 3) and the JSON schemas used to validate every file autocal writes."""
import json
import os

import numpy as np

from .analysis import GRID
from .stimulus import RESULT_ORDER

POSITION_LABELS = [("centre", (0.0, 0.0), 2.0), ("left 20 cm", (-20.0, 0.0), 1.0), ("right 20 cm", (20.0, 0.0), 1.0),
                   ("fore 20 cm", (0.0, 20.0), 1.0), ("aft 20 cm", (0.0, -20.0), 1.0)]
PROMPT_TEXT = {1: "Move the mic 20 cm LEFT of the centre position, then continue", 2: "Move the mic 20 cm RIGHT of the centre position, then continue",
               3: "Move the mic 20 cm FORWARD of the centre position, then continue", 4: "Move the mic 20 cm BACK of the centre position, then continue"}


def _r(a, nd=2):
    return [round(float(v), nd) for v in np.asarray(a, float)]


def build_results(run_id, created, mode, simulated, target_name, hump_db, meas, prop, commands=None, verify=None):
    """The results.json dict. `prop` is analysis.propose()'s result; `meas` the Measurement."""
    positions = [{"index": p.index, "label": p.label, "offset_cm": [p.offset_cm[0], p.offset_cm[1]]} for p in meas.pos]
    resp = {n: {k: _r(v) for k, v in d.items()} for n, d in prop["response"].items()}
    b = prop["bass"]
    res = {"version": 1, "id": run_id, "created": created, "mode": mode, "simulated": simulated,
           "target": {"name": target_name, "hump_db": hump_db}, "channels": list(RESULT_ORDER), "freq": _r(GRID, 2), "positions": positions,
           "response_db": resp, "target_db": _r(prop["target"]), "predicted_after_db": {n: _r(v) for n, v in prop["predicted"].items()},
           "table": prop["table"],
           "bass": {"freq": _r(b["freq"]), "current_db": _r(b["current_db"]), "proposed_extra_db": _r(b["proposed_extra_db"]),
                    "filters_current": [_filt(f) for f in b["filters_current"]], "filters_extra_proposed": [_filt(f) for f in b["filters_extra_proposed"]],
                    "rms_to_target_before_db": round(b["rms_to_target_before_db"], 2), "rms_to_target_after_db": round(b["rms_to_target_after_db"], 2)},
           "heights": {**prop["heights"], "rms_to_bed_before_db": _opt(prop["heights"]["rms_to_bed_before_db"]),
                       "rms_to_bed_after_db": _opt(prop["heights"]["rms_to_bed_after_db"])},
           "ir": prop["ir"], "arrival_scatter": prop["arrival_scatter"], "warnings": prop["warnings"], "verdict": prop["verdict"],
           "summary": prop["summary"], "commands": commands or {"apply": f"autocal apply {run_id}", "keep": "autocal keep", "rollback": "autocal rollback"},
           "verify": verify}
    return res


def _opt(v):
    return None if v is None else round(float(v), 2)


def _filt(f):
    out = {"name": f["name"], "freq": float(f["freq"]), "gain": float(f["gain"]), "q": float(f["q"])}
    if "type" in f:
        out["type"] = f["type"]
    return out


# ---- JSON schemas (a small JSON-Schema subset, checked by validate()) -----------------------------------------------------------------

NUM = {"type": "number"}
NUMN = {"type": ["number", "null"]}
STR = {"type": "string"}
CH = {"type": "string", "enum": list(RESULT_ORDER)}
FILTER = {"type": "object", "required": ["name", "freq", "gain", "q"], "properties": {"name": STR, "freq": NUM, "gain": NUM, "q": NUM, "type": STR}}
CURVE = {"type": "array", "items": NUM}

RESULTS_SCHEMA = {
    "type": "object",
    "required": ["version", "id", "created", "mode", "simulated", "target", "channels", "freq", "positions", "response_db", "target_db",
                 "predicted_after_db", "table", "bass", "heights", "ir", "arrival_scatter", "warnings", "verdict", "summary", "commands", "verify"],
    "properties": {
        "version": {"enum": [1]}, "id": {"type": "string", "pattern": r"^[0-9]{8}-[0-9]{6}$"}, "created": STR, "mode": {"enum": ["single", "five"]},
        "simulated": {"type": "boolean"},
        "target": {"type": "object", "required": ["name", "hump_db"], "properties": {"name": STR, "hump_db": NUM}},
        "channels": {"type": "array", "items": CH, "minItems": 8, "maxItems": 8},
        "freq": CURVE,
        "positions": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["index", "label", "offset_cm"],
                      "properties": {"index": {"type": "integer"}, "label": STR, "offset_cm": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2}}}},
        "response_db": {"type": "object", "additionalProperties": {"type": "object", "required": ["avg"], "additionalProperties": CURVE}},
        "target_db": CURVE,
        "predicted_after_db": {"type": "object", "additionalProperties": CURVE},
        "table": {"type": "array", "minItems": 8, "maxItems": 8, "items": {"type": "object", "required": [
            "channel", "arrival_ms", "delay_now_ms", "delay_new_ms", "level_db", "trim_now_db", "trim_new_db", "polarity", "snr_db", "notes"],
            "properties": {"channel": CH, "arrival_ms": NUMN, "delay_now_ms": NUM, "delay_new_ms": NUM, "level_db": NUMN, "trim_now_db": NUM,
                           "trim_new_db": NUM, "snr_db": NUMN, "notes": {"type": "array", "items": STR},
                           "polarity": {"type": "object", "required": ["ok", "flip_proposed", "margin_db"],
                                        "properties": {"ok": {"type": "boolean"}, "flip_proposed": {"type": "boolean"}, "margin_db": NUMN}}}}},
        "bass": {"type": "object", "required": ["freq", "current_db", "proposed_extra_db", "filters_current", "filters_extra_proposed",
                                                "rms_to_target_before_db", "rms_to_target_after_db"],
                 "properties": {"freq": CURVE, "current_db": CURVE, "proposed_extra_db": CURVE, "filters_current": {"type": "array", "items": FILTER},
                                "filters_extra_proposed": {"type": "array", "items": FILTER}, "rms_to_target_before_db": NUM, "rms_to_target_after_db": NUM}},
        "heights": {"type": "object", "required": ["tone_filters_proposed", "trim_new_db", "rms_to_bed_before_db", "rms_to_bed_after_db", "above_5k_matched"],
                    "properties": {"tone_filters_proposed": {"type": "array", "items": {"type": "object", "required": ["freq", "gain", "q"]}},
                                   "trim_new_db": NUM, "rms_to_bed_before_db": NUMN, "rms_to_bed_after_db": NUMN, "above_5k_matched": {"type": "boolean"}}},
        "ir": {"type": "object", "additionalProperties": {"type": "object", "required": ["t_ms", "h"], "properties": {"t_ms": CURVE, "h": CURVE}}},
        "arrival_scatter": {"type": "object", "additionalProperties": CURVE},
        "warnings": {"type": "array", "items": STR}, "verdict": {"enum": ["PASS", "WARN", "FAIL"]}, "summary": STR,
        "commands": {"type": "object", "required": ["apply", "keep", "rollback"], "properties": {"apply": STR, "keep": STR, "rollback": STR}},
        "verify": {"type": ["object", "null"]},
    }}

STATUS_SCHEMA = {
    "type": "object",
    "required": ["id", "state", "mode", "position", "positions_total", "step", "progress", "message", "mic", "level_dbfs", "prompt", "simulated", "error", "updated"],
    "properties": {
        "id": STR, "state": {"enum": ["idle", "preflight", "ramp", "channel_id", "sweeping", "waiting_user", "analysing", "done", "aborted", "error"]},
        "mode": {"enum": ["single", "five"]}, "position": {"type": "integer"}, "positions_total": {"type": "integer"}, "step": STR,
        "progress": {"type": "number", "minimum": 0, "maximum": 1}, "message": STR,
        "mic": {"type": "object", "required": ["peak_dbfs", "rms_dbfs", "noise_floor_dbfs"],
                "properties": {"peak_dbfs": NUMN, "rms_dbfs": NUMN, "noise_floor_dbfs": NUMN}},
        "level_dbfs": NUMN,
        "prompt": {"type": ["object", "null"], "required": ["id", "text", "action"], "properties": {"id": STR, "text": STR, "action": {"enum": ["continue"]}}},
        "simulated": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "updated": STR}}

RUN_SCHEMA = {
    "type": "object", "required": ["id", "created", "mode", "simulated", "target", "room_before", "version"],
    "properties": {"id": {"type": "string", "pattern": r"^[0-9]{8}-[0-9]{6}$"}, "created": STR, "mode": {"enum": ["single", "five"]},
                   "simulated": {"type": "boolean"}, "target": {"type": "object", "required": ["name", "hump_db"], "properties": {"name": STR, "hump_db": NUM}},
                   "room_before": {"type": "object", "required": ["version", "align", "height_tone", "bass"]}, "version": {"enum": [1]}}}


def validate(obj, schema, path="$"):
    """Minimal JSON-Schema validator (type incl. unions, enum, required, properties, additionalProperties-as-schema, items, minItems/maxItems,
    pattern, minimum/maximum). Returns a list of error strings (empty = valid)."""
    import re
    errs = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]

        def ok(x, k):
            return {"object": isinstance(x, dict), "array": isinstance(x, list), "string": isinstance(x, str), "boolean": isinstance(x, bool),
                    "integer": isinstance(x, int) and not isinstance(x, bool), "null": x is None,
                    "number": isinstance(x, (int, float)) and not isinstance(x, bool) and not (isinstance(x, float) and (x != x or abs(x) == float("inf")))}[k]
        if not any(ok(obj, k) for k in ts):
            return [f"{path}: expected {t}, got {type(obj).__name__} ({str(obj)[:40]})"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if isinstance(obj, str) and "pattern" in schema and not re.search(schema["pattern"], obj):
        errs.append(f"{path}: {obj!r} does not match {schema['pattern']}")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: {obj} < {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: {obj} > {schema['maximum']}")
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing '{k}'")
        props = schema.get("properties", {}); add = schema.get("additionalProperties")
        for k, v in obj.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif isinstance(add, dict):
                errs += validate(v, add, f"{path}.{k}")
    if isinstance(obj, list):
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(obj[:5000]):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def check_results(res):
    """Contract checks beyond the schema: equal lengths, 8 distinct channels, ir <= 2000 points."""
    errs = validate(res, RESULTS_SCHEMA)
    n = len(res.get("freq", []))
    for k, d in res.get("response_db", {}).items():
        for kk, v in d.items():
            if len(v) != n:
                errs.append(f"response_db.{k}.{kk}: length {len(v)} != freq {n}")
    for k, v in res.get("predicted_after_db", {}).items():
        if len(v) != n:
            errs.append(f"predicted_after_db.{k}: length {len(v)} != freq {n}")
    if len(res.get("target_db", [])) != n:
        errs.append("target_db length != freq")
    if sorted(r.get("channel") for r in res.get("table", [])) != sorted(RESULT_ORDER):
        errs.append("table must hold the 8 channels once each")
    for k, v in res.get("ir", {}).items():
        if len(v["t_ms"]) > 2000 or len(v["t_ms"]) != len(v["h"]):
            errs.append(f"ir.{k}: more than 2000 points or mismatched lengths")
    b = res.get("bass", {})
    if not (len(b.get("freq", [])) == len(b.get("current_db", [])) == len(b.get("proposed_extra_db", []))):
        errs.append("bass: freq/current_db/proposed_extra_db lengths differ")
    return errs


def write_json(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
