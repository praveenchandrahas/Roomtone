"""PipewireBackend: plays through PipeWire with pw-play and records the measurement mic with pw-record, with live metering and instant abort.

*** UNTESTED ON HARDWARE. *** It is unit-tested only against fake pw-play / pw-record / pw-link executables (tests/test_autocal_sim.py), which prove the
process handling, metering loop and abort timing, not that the real tools behave as assumed. Things the owner must verify at the rig:
  * `pw-record --target MIC --rate 48000 --channels 1 --format f32 -` really streams to stdout (a RIFF header, then float32 samples);
  * the PLAY path. Default ("link", AUTOCAL_PLAY_METHOD=link) is the method of the owner's own play-through-camilla.sh, which he has used for every
    measurement on this rig: `pw-play -P "{ node.name=autocal-play node.autoconnect=false }" --channel-map FL,FR,RL,RR,FC,LFE,SL,SR FILE.wav`, wait for
    its 8 output ports, then `pw-link` port i -> camilladsp-capture:input_{i+1}. The older "target" method (AUTOCAL_PLAY_METHOD=target:
    `pw-play --target camilladsp-capture --channels 8`) is kept as a fallback but is the less certain of the two;
  * the mic name (env MIC, from `./rec.sh list`) and that the mic's capture level stays fixed during the run (it acts after the clip point);
  * how quickly SIGTERM silences pw-play at your quantum (the budget is 250 ms from the abort event).
The measurement runs THROUGH CamillaDSP (the owner's decision), so the played file goes to CamillaDSP's capture node, not to the USB sink.
One continuous file per position is played (not one pw-play per sweep): the sync-chirp placement needs the chirps and the sweeps in one clock.
"""
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import queue

import numpy as np

from . import ir as IR, profile as PF
from .backend import Backend, BackendError, Capture, BLOCK_S, FS


class PipewireBackend(Backend):
    simulated = False
    name = "PipeWire (UNTESTED ON HARDWARE)"
    NODE = "autocal-play"                                   # node name of the test stream (link method)
    CH_MAP = "FL,FR,RL,RR,FC,LFE,SL,SR"                     # hardware order; file channel i -> CamillaDSP capture input i+1

    def __init__(self, mic=None, target=None, pw_play="pw-play", pw_record="pw-record", pactl="pactl", workdir=None, kill_grace_s=0.2,
                 pw_link="pw-link", method=None, link_timeout_s=3.0):
        self.mic = mic or os.environ.get("MIC")
        self.target = target or os.environ.get("AUTOCAL_PLAY_TARGET", "camilladsp-capture")
        self.method = method or os.environ.get("AUTOCAL_PLAY_METHOD", "link")
        if self.method not in ("link", "target"):
            raise BackendError(f"AUTOCAL_PLAY_METHOD must be link or target, not {self.method!r}")
        self.pw_link, self.link_timeout_s = pw_link, link_timeout_s
        self.pw_play, self.pw_record, self.pactl = pw_play, pw_record, pactl
        self.workdir = workdir or tempfile.mkdtemp(prefix="autocal-")
        self.kill_grace_s = kill_grace_s

    # -- preflight -------------------------------------------------------------------------------------------------------------------
    def _run(self, *argv):
        try:
            r = subprocess.run(list(argv), capture_output=True, text=True, timeout=10)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError):
            return 127, ""

    def preflight(self):
        notes = []
        if not shutil.which(self.pw_play) and not os.path.isfile(self.pw_play):
            raise BackendError(f"{self.pw_play} not found (pipewire-bin)")
        if self.method == "link":
            if not shutil.which(self.pw_link) and not os.path.isfile(self.pw_link):
                raise BackendError(f"{self.pw_link} not found (pipewire-bin)")
            rc, out = self._run(self.pw_link, "-i")
            n_in = sum(1 for l in out.splitlines() if l.strip().startswith(self.target + ":input_"))
            if n_in != 8:
                raise BackendError(f"{self.target} has {n_in} input ports (need 8). Is CamillaDSP running? Try: eq status")
        rc, out = self._run(self.pactl, "list", "short", "sources")
        sources = [l.split("\t")[1] for l in out.splitlines() if "\t" in l and ".monitor" not in l]
        mic = self.mic if self.mic in sources else None
        if self.mic and not mic:
            notes.append(f"MIC={self.mic} is not among the capture devices: {', '.join(sources) or 'none'}")
        streams = self._other_streams()
        return {"mic": mic, "other_streams": streams, "profile": "live CamillaDSP profile (room.json)", "notes": notes}

    def _other_streams(self):
        """Playback streams other than CamillaDSP's own (its playback node is always a sink-input of the pulse view: 8ch float32).
        Parses `pactl list sink-inputs`; each stream is named from application.name, node.name or media.name."""
        rc, out = self._run(self.pactl, "list", "sink-inputs")
        streams = []
        for blk in re.split(r"(?m)^Sink Input #", out)[1:]:
            idx = blk.split("\n", 1)[0].strip()
            props = dict(re.findall(r'(?m)^\s*(application\.name|node\.name|media\.name) = "([^"]*)"', blk))
            names = [props.get(k, "") for k in ("application.name", "node.name", "media.name")]
            if any("camilladsp" in n.lower() for n in names):
                continue
            streams.append(f"{idx}:{next((n for n in names if n), '?')}")
        return streams

    def current_room(self):
        return PF.current_room()

    # -- audio -----------------------------------------------------------------------------------------------------------------------
    def record_noise(self, seconds, monitor=None):
        return self.play_record(np.zeros((int(seconds * FS), 8)), monitor, stage="noise", pre_s=0.0, post_s=0.0, silent=True)

    def _start_recorder(self):
        if not self.mic:
            raise BackendError("no microphone selected: export MIC=<name> (see ./rec.sh list)")
        return subprocess.Popen([self.pw_record, "--target", self.mic, "--rate", str(FS), "--channels", "1", "--format", "f32", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    @staticmethod
    def _reader(proc, q):
        """Reads the recorder's stdout: skips a RIFF/WAV header if present, then puts float32 sample blocks on q (None at EOF)."""
        buf = b""; header_done = False
        while True:
            chunk = proc.stdout.read(4800 * 4)
            if not chunk:
                q.put(None); return
            buf += chunk
            if not header_done:
                if len(buf) < 12:
                    continue
                if buf[:4] == b"RIFF":
                    i = buf.find(b"data")
                    if i < 0:
                        continue
                    buf = buf[i + 8:]
                header_done = True
            n = len(buf) // 4 * 4
            if n:
                q.put(np.frombuffer(buf[:n], dtype="<f4").astype(np.float64)); buf = buf[n:]

    def play_record(self, x8, monitor=None, stage="play", pre_s=1.0, post_s=1.0, silent=False):
        self.check_cap(x8)
        wav = os.path.join(self.workdir, f"{stage}.wav")
        if not silent:
            IR.write_wav24(wav, x8)
        rec = self._start_recorder(); q = queue.Queue()
        th = threading.Thread(target=self._reader, args=(rec, q), daemon=True); th.start()
        cap = Capture(x=np.zeros(0), fs=FS, play_start_t=pre_s, info={"stage": stage})
        got = []; n_got = 0; play = None; play_started = False; t_end = None
        nb = int(BLOCK_S * FS); pend = np.zeros(0)
        dur = len(x8) / FS
        try:
            while True:
                try:
                    blk = q.get(timeout=1.0)
                except queue.Empty:
                    raise BackendError("pw-record delivered no audio for 1 s (wrong --target, or the recorder died)")
                if blk is None:
                    raise BackendError("pw-record ended unexpectedly")
                pend = np.concatenate([pend, blk])
                while len(pend) >= nb:
                    b, pend = pend[:nb], pend[nb:]
                    got.append(b); n_got += nb; t = n_got / FS
                    if not silent and not play_started and t >= pre_s:
                        play = self._start_player(wav)
                        play_started = True; t_end = t + dur + post_s
                    if monitor is not None:
                        why = monitor(t, b)
                        if why:
                            cap.aborted, cap.trigger_t = why, t
                            tm = time.monotonic()
                            self._stop(play)
                            cap.stop_t = t + (time.monotonic() - tm)       # recording time at which pw-play was gone
                            raise _Done()
                    if silent and t >= dur:
                        raise _Done()
                    if t_end is not None and t >= t_end:
                        raise _Done()
        except _Done:
            pass
        finally:
            self._stop(play)
            self._stop(rec, term_only=True)
        cap.x = np.concatenate(got) if got else np.zeros(0)
        cap.stop_t = cap.stop_t or len(cap.x) / FS
        return cap

    def _start_player(self, wav):
        """Starts pw-play and makes sure its 8 channels reach CamillaDSP's 8 capture inputs. Nothing is audible until the links exist
        (autoconnect is off), and the stimulus starts with seconds of silence, so the short wait here costs nothing."""
        if self.method == "target":
            return subprocess.Popen([self.pw_play, "--target", self.target, "--channels", "8", wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc = subprocess.Popen([self.pw_play, "-P", "{ node.name=%s node.autoconnect=false }" % self.NODE, "--channel-map", self.CH_MAP, wav],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + self.link_timeout_s
        outs = []
        while True:
            rc, out = self._run(self.pw_link, "-o")
            outs = [l.strip() for l in out.splitlines() if l.strip().startswith(self.NODE + ":output_")]
            if len(outs) == 8:
                break
            if proc.poll() is not None:
                raise BackendError("pw-play exited before its ports appeared")
            if time.monotonic() > deadline:
                self._stop(proc)
                raise BackendError(f"expected 8 output ports from the test stream, found {len(outs)}")
            time.sleep(0.1)
        for i, o in enumerate(outs):                      # listing order = channel order, as in play-through-camilla.sh
            rc, _ = self._run(self.pw_link, o, f"{self.target}:input_{i + 1}")
            if rc != 0:
                self._stop(proc)
                raise BackendError(f"could not link {o} -> {self.target}:input_{i + 1}")
        return proc

    def _stop(self, proc, term_only=False):
        """Stops a child process: SIGTERM, then SIGKILL after kill_grace_s. Returns when the process is gone."""
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=self.kill_grace_s)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        except OSError:
            pass


class _Done(Exception):
    pass
