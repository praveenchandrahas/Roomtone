#!/usr/bin/env python3
"""Writes bedtest-5.1.flac: a listening test for the 5.1 bed (FL FR FC LFE SL SR, 5.1(side) layout).

Seven tests, each introduced by beeps (1 beep = T1, 2 beeps = T2, ...):
  T1 channel order   T2 phantom vs real centre   T3 front pan   T4 around you
  T5 click alignment T6 polarity pairs           T7 dialogue-like
The printed list of timecodes tells you where each test starts. Play it with mpv-atmos so it goes through
the same chain as films.

Usage:  python3 make-bedtest.py [out.flac]     (needs numpy, scipy and ffmpeg; deterministic, seed 7)
"""
import numpy as np, subprocess, sys, json
from scipy.signal import butter, sosfilt
FS=48000; FL,FR,FC,LFE,SL,SR=range(6)
NAMES=["FL","FR","FC","LFE","SL","SR"]
rng=np.random.default_rng(7)
def dbf(x): return 10**(x/20)
def pink(n,lo,hi):
    w=rng.standard_normal(n+FS); F=np.fft.rfft(w); f=np.fft.rfftfreq(len(w),1/FS); f[0]=1
    F=F/np.sqrt(f); y=np.fft.irfft(F,len(w))[FS//2:FS//2+n]
    y=sosfilt(butter(4,[lo,hi],btype="band",fs=FS,output="sos"),y); return y/np.sqrt(np.mean(y**2))   # unit rms
def env(n,att=0.01,rel=0.02):
    e=np.ones(n); a=int(att*FS); r=int(rel*FS); e[:a]=np.linspace(0,1,a); e[-r:]=np.linspace(1,0,r); return e
class Song:
    def __init__(s): s.tracks=[]; s.t=0.0; s.marks=[]
    def put(s,ch,sig,t=None):
        t=s.t if t is None else t; a=int(round(t*FS)); s.tracks.append((ch,a,sig))
    def adv(s,d): s.t+=d
    def mark(s,label): s.marks.append((round(s.t,2),label))
    def render(s):
        n=max(a+len(sg) for _,a,sg in s.tracks)+FS; out=np.zeros((n,6))
        for ch,a,sg in s.tracks: out[a:a+len(sg),ch]+=sg
        return out
S=Song()
def beeps(n):
    for i in range(n):
        b=np.sin(2*np.pi*1000*np.arange(int(.12*FS))/FS)*env(int(.12*FS),.01,.01)*dbf(-26); S.put(FC,b); S.adv(.3)
    S.adv(1.2)
def header(n,label):
    S.mark(label); beeps(n)
S.adv(1.0)
# ---- T1 channel order
header(1,"T1 channel order: noise burst FL, FR, FC, SL, SR (about 0.6 s each), then a low sweep on the sub")
for ch in (FL,FR,FC,SL,SR):
    S.put(ch,pink(int(.6*FS),200,8000)*env(int(.6*FS))*dbf(-26)); S.adv(1.2)
sw=np.sin(2*np.pi*np.cumsum(np.geomspace(30,100,int(1.2*FS)))/FS)*env(int(1.2*FS),.05,.05)*dbf(-22); S.put(LFE,sw); S.adv(2.5)
# ---- T2 phantom centre vs real centre
header(2,"T2 phantom centre: real centre (FC) then FL+FR together, alternating x4; same spot, same loudness, same tone?")
for i in range(4):
    x=pink(int(.9*FS),400,5000)*env(int(.9*FS))
    S.put(FC,x*dbf(-28)); S.adv(1.5)
    S.put(FL,x*dbf(-34)); S.put(FR,x*dbf(-34)); S.adv(1.5)
S.adv(1.0)
# ---- T3 front pan
header(3,"T3 front pan: continuous noise glides FL -> FC -> FR (6 s) and back (6 s); smooth, no jump or lump at the centre?")
def pan(path,dur,lo=400,hi=4000,level=-30):
    n=int(dur*FS); x=pink(n,lo,hi)*dbf(level)*env(n,.1,.1); pos=np.linspace(0,len(path)-1,n)
    seg=np.minimum(pos.astype(int),len(path)-2); fr=pos-seg
    for k,ch in enumerate(path):
        g=np.zeros(n)
        for j in range(len(path)-1):
            m=seg==j
            if k==j: g[m]=np.cos(fr[m]*np.pi/2)
            if k==j+1: g[m]=np.sin(fr[m]*np.pi/2)
        S.put(ch,x*g)
    S.adv(dur+1.0)
pan([FL,FC,FR],6); pan([FR,FC,FL],6)
S.adv(1.0)
# ---- T4 around
header(4,"T4 around you: front-centre -> right -> right surround -> back to front -> left -> left surround (about 5 s per leg); no gaps or jumps where the pair changes?")
pan([FC,FR,SR],7); pan([SR,FR,FC,FL,SL],12)
S.adv(1.0)
# ---- T5 click alignment
header(5,"T5 click alignment: 6 clicks each on ALL five speakers, then FL+FR, FC, SL+SR; the all-five click should be one tight point, not smeared or doubled")
def click(chs,n=6,lvl=-28):
    for i in range(n):
        k=int(.004*FS); c=rng.standard_normal(k); c=sosfilt(butter(4,[1500,8000],btype="band",fs=FS,output="sos"),c); c=c/np.sqrt(np.mean(c**2))*np.hanning(k)*dbf(lvl)
        for ch in chs: S.put(ch,c,S.t) if False else S.put(ch,c)
        S.adv(.8)
    S.adv(1.0)
# identical click waveform on all channels in a burst
def click_same(chs,n=6,lvl=-28):
    c0=sosfilt(butter(4,[1500,8000],btype="band",fs=FS,output="sos"),rng.standard_normal(int(.004*FS)))
    c0=c0/np.sqrt(np.mean(c0**2))*np.hanning(len(c0))*dbf(lvl)
    for i in range(n):
        for ch in chs: S.put(ch,c0)
        S.adv(.8)
    S.adv(1.0)
click_same([FL,FR,FC,SL,SR]); click_same([FL,FR]); click_same([FC]); click_same([SL,SR])
# ---- T6 polarity pairs
header(6,"T6 polarity pairs (A = in phase, B = second speaker inverted; repeated A,B,A,B): in phase should be fuller, louder and solid; inverted thin and hard to place")
for a,b,lab in ((FL,FR,"FL+FR"),(FL,FC,"FL+FC"),(FR,FC,"FR+FC"),(SL,SR,"SL+SR"),(FL,SL,"FL+SL"),(FR,SR,"FR+SR")):
    S.mark("   pair "+lab)
    for rep in range(2):
        x=pink(int(1.1*FS),150,700)*env(int(1.1*FS))*dbf(-28)
        S.put(a,x); S.put(b,x); S.adv(1.7)         # A
        S.put(a,x); S.put(b,-x); S.adv(1.7)        # B
    S.adv(.6)
S.adv(1.0)
# ---- T7 dialogue-like
header(7,"T7 dialogue-like (speech-band noise with syllable rhythm): FC only, then FC + FL/FR at low level, then FL+FR only; does it sit on the screen?")
def speechy(dur):
    n=int(dur*FS); x=pink(n,300,3400)
    e=np.abs(np.convolve(rng.standard_normal(n),np.hanning(int(.08*FS)),"same")); e=e/e.max(); e=e**1.5
    return x*e/np.sqrt(np.mean((x*e)**2))
x=speechy(5)*env(int(5*FS))
S.put(FC,x*dbf(-26)); S.adv(6.0)
S.put(FC,x*dbf(-26)); S.put(FL,x*dbf(-35)); S.put(FR,x*dbf(-35)); S.adv(6.0)
S.put(FL,x*dbf(-32)); S.put(FR,x*dbf(-32)); S.adv(6.0)
S.mark("end"); S.adv(1.0)
out=S.render(); print("duration",round(len(out)/FS,1),"s  peak",round(20*np.log10(np.abs(out).max()),1),"dBFS")
out_path = sys.argv[1] if len(sys.argv) > 1 else "bedtest-5.1.flac"
pcm = out.astype(np.float32)
proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(FS), "-ac", "6", "-i", "-",
                       "-af", "channelmap=channel_layout=5.1(side)", "-c:a", "flac", "-sample_fmt", "s16", out_path],
                      input=pcm.tobytes(), capture_output=True)
if proc.returncode != 0:
    sys.exit("ffmpeg failed: " + proc.stderr.decode())
print("wrote", out_path)
for t, l in S.marks:
    print(f"{int(t//60)}:{t%60:04.1f}  {l}")
