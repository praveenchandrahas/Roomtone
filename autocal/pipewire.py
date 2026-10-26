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
import struct
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
        self.rec_format = None

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
        muted = level = None
        if mic:
            rc, out = self._run(self.pactl, "get-source-mute", mic)
            muted = True if (rc == 0 and "yes" in out.lower()) else (False if (rc == 0 and "no" in out.lower()) else None)
            rc, out = self._run(self.pactl, "get-source-volume", mic)
            pct = re.findall(r"(\d+)%", out) if rc == 0 else []
            level = float(pct[0]) if pct else None
        return {"mic": mic, "other_streams": streams, "profile": "live CamillaDSP profile (room.json)", "notes": notes,
                "mic_muted": muted, "mic_level_pct": level}

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
            engine = next((n for n in names if n.startswith("theater-upmix")), None)      # the adaptive engine's own pw-cat streams
            streams.append(f"{idx}:{engine or next((n for n in names if n), '?')}")
        return streams

    def current_room(self):
        return PF.current_room()

    # -- audio -----------------------------------------------------------------------------------------------------------------------
    def record_noise(self, seconds, monitor=None):
        return self.play_record(np.zeros((int(seconds * FS), 8)), monitor, stage="noise", pre_s=0.0, post_s=0.0, silent=True)

    REC_CH = 2          # record both channels like rec.sh does (a TRRS mic adapter may carry the mic on one channel only)

    def _start_recorder(self):
        if not self.mic:
            raise BackendError("no microphone selected: export MIC=<name> (see ./rec.sh list)")
        return subprocess.Popen([self.pw_record, "--target", self.mic, "--rate", str(FS), "--channels", str(self.REC_CH), "--format", "f32", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    @staticmethod
    def _parse_fmt(hdr):
        """(format code, channels, rate, bits) from the 'fmt ' chunk in a WAV header, or None."""
        j = hdr.find(b"fmt ")
        if j < 0 or len(hdr) < j + 24:
            return None
        tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", hdr, j + 8)
        if tag == 0xFFFE and len(hdr) >= j + 8 + 26:                       # WAVE_FORMAT_EXTENSIBLE: the real code is the first 2 bytes of the GUID
            tag = struct.unpack_from("<H", hdr, j + 8 + 24)[0]
        return tag, ch, rate, bits

    @staticmethod
    def _decode(raw, tag, bits, ch, big=False):
        """Interleaved samples (little-endian, or big-endian for AU) -> float array (frames, ch). Raises BackendError for a format we do not know."""
        e = ">" if big else "<"
        if tag == 3 and bits == 32:
            a = np.frombuffer(raw, dtype=e + "f4").astype(np.float64)
        elif tag == 1 and bits == 16:
            a = np.frombuffer(raw, dtype=e + "i2") / 32768.0
        elif tag == 1 and bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            v = ((b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]) if big else (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
            a = np.where(v >= 1 << 23, v - (1 << 24), v) / 2.0 ** 23
        elif tag == 1 and bits == 32:
            a = np.frombuffer(raw, dtype=e + "i4") / 2.0 ** 31
        else:
            raise BackendError(f"pw-record wrote an unexpected sample format (format code {tag}, {bits} bit)")
        return a.reshape(-1, ch)

    AU_ENC = {2: (1, 8), 3: (1, 16), 4: (1, 24), 5: (1, 32), 6: (3, 32)}      # AU encoding code -> (WAV-style format code, bits)

    def _reader(self, proc, q):
        """Reads the recorder's stdout: parses a RIFF/WAV header if there is one (otherwise assumes float32 x REC_CH), then puts blocks of
        float samples, shape (frames, channels), on q. At the end q gets None; a problem is put on q as an exception."""
        buf = b""; fmt = None; header_done = False; big = False
        try:
            while True:
                chunk = proc.stdout.read(4800 * 8)
                if not chunk:
                    q.put(None); return
                buf += chunk
                if not header_done:
                    if len(buf) < 12:
                        continue
                    container = "raw (assumed float32)"
                    if buf[:4] == b"RIFF":
                        i = buf.find(b"data")
                        if i < 0:
                            continue
                        fmt = self._parse_fmt(buf[:i]); buf = buf[i + 8:]; container = "WAV"
                    elif buf[:4] == b".snd":                                     # AU: what pw-cat writes to stdout when no container is given; big-endian
                        if len(buf) < 24:
                            continue
                        off, _, enc, rate_, ch_ = struct.unpack(">IIIII", buf[4:24])
                        if enc not in self.AU_ENC or len(buf) < off:
                            if enc not in self.AU_ENC:
                                raise BackendError(f"pw-record wrote an AU stream with an unsupported encoding ({enc})")
                            continue
                        fmt = (self.AU_ENC[enc][0], ch_, rate_, self.AU_ENC[enc][1]); big = True; buf = buf[off:]; container = "AU (big-endian)"
                    header_done = True
                    tag, ch, rate, bits = fmt or (3, self.REC_CH, FS, 32)
                    self.rec_format = {"tag": tag, "channels": ch, "rate": rate, "bits": bits, "header": fmt is not None, "container": container}
                    if rate != FS:
                        raise BackendError(f"pw-record is delivering {rate} Hz, not {FS}")
                tag, ch, rate, bits = self.rec_format["tag"], self.rec_format["channels"], self.rec_format["rate"], self.rec_format["bits"]
                frame = bits // 8 * ch; n = len(buf) // frame * frame
                if n:
                    q.put(self._decode(buf[:n], tag, bits, ch, big)); buf = buf[n:]
        except BackendError as e:
            q.put(e)

    def _mic_channel(self, blk):
        """The analysed channel of a recorded block: channel 0 (what rec.sh recordings have always been analysed with), or AUTOCAL_MIC_CHANNEL."""
        k = int(os.environ.get("AUTOCAL_MIC_CHANNEL", "0"))
        return blk[:, k if k < blk.shape[1] else 0]

    def meter(self, seconds=20.0, emit=print):
        """Live mic levels, no sound played: per 100 ms the rms of each channel and of their sum, so a dead, muted or cancelling input is visible."""
        rec = self._start_recorder(); q = queue.Queue()
        th = threading.Thread(target=self._reader, args=(rec, q), daemon=True); th.start()
        t0 = time.monotonic(); pend = np.zeros((0, self.REC_CH))
        def db(v):
            return 20 * np.log10(max(v, 1e-9))
        try:
            while time.monotonic() - t0 < seconds:
                try:
                    blk = q.get(timeout=2.0)
                except queue.Empty:
                    raise BackendError("pw-record delivered no audio for 2 s (wrong --target, or the recorder died)")
                if blk is None:
                    raise BackendError("pw-record ended unexpectedly")
                if isinstance(blk, Exception):
                    raise blk
                pend = np.concatenate([pend, blk]) if pend.shape[1] == blk.shape[1] else blk
                while len(pend) >= 4800:
                    b, pend = pend[:4800], pend[4800:]
                    rms = np.sqrt(np.mean(b ** 2, 0)); mix = np.sqrt(np.mean(np.mean(b, 1) ** 2))
                    emit("  ".join(f"ch{i} {db(r):6.1f}" for i, r in enumerate(rms)) + f"   sum {db(mix):6.1f}   peak {db(np.max(np.abs(b))):6.1f} dBFS")
        finally:
            self._stop(rec, term_only=True)
        return self.rec_format

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
                if isinstance(blk, Exception):
                    raise blk
                pend = np.concatenate([pend, self._mic_channel(blk)])
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
