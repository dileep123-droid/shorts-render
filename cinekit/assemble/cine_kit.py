# Cinematic kit, phase 1 (24 Nov 2026): mood grades, transitions + real visual FLASH, texture layer (grain/dust/scanlines/shake),
# real SFX placement and the long-video audio mix (voice -14 LUFS, ducked music bed, silence gates) for Shorts.
# Used by shorts_render.py ONLY when the job has "kit": 1. Jobs without it never import this file and render exactly as before.
# Every look copies a technique logged in research/NITISH_FLYDUBAI_SHOT_LOG.md, NITISH_FLYDUBAI_BREAKDOWN.md and deep_logs/dhruv_*.md
# (row times are quoted next to each recipe). Pure ffmpeg + numpy (the runner installs only boto3, pillow, numpy).
import json, math, os, re, subprocess, wave
import numpy as np

FPS = 30

# ---------------------------------------------------------------- 1) MOOD GRADES ----------------------------------------------------------------
# warm  = the existing Shorts look, byte-identical filter (Nitish/Dhruv presenter + normal B-roll: "Warm studio tones").
# danger= red/crimson duotone (Nitish 00:04 "Desaturated B&W, heavy red tint", 00:10.8 "Monochromatic red tint", 00:15.2 "Red duotone",
#         00:48.8 "Heavy red monochrome grade", 00:54.6 "High-contrast crimson grade").
# sepia = amber archive (Nitish 01:23 "sepia-toned vintage grading on 1990s archival", 02:24 "High-contrast sepia"; Dhruv "B&W vintage newsreel").
# bw    = bleached B&W archival (Dhruv "High-contrast B&W archival", "High-grain monochrome", "Archival B&W").
# cold  = cold desaturated crisis with red accents (Nitish 15:13 "De-saturated cockpit interior interrupted by flashing red alarm",
#         01:00.6 "Muted B&W, red accents", 05:23 "Cold blue tone", 12:12 "Desaturated, high-contrast, red accent edges").
# flash = high-key overexposure (Nitish 00:14.0 "High key overexposure flash"); the shot also opens on a white flash.
GRADES = {
    "warm": "eq=contrast=1.07:saturation=1.10:gamma=0.98,vignette=angle=PI/5",
    "danger": "hue=s=0,eq=contrast=1.28:brightness=-0.03,"
              "curves=r='0/0.05 0.35/0.42 0.7/0.86 1/1':g='0/0 0.45/0.06 0.8/0.42 1/0.80':b='0/0.02 0.45/0.07 0.8/0.40 1/0.76',"
              "vignette=angle=PI/4",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,curves=preset=vintage,"
             "eq=contrast=1.10:brightness='0.02*sin(40*t)':eval=frame,vignette=angle=PI/4",
    "bw": "hue=s=0,eq=contrast=1.32:brightness='0.03*sin(40*t)':eval=frame,vignette=angle=PI/4",
    "cold": "colorhold=color=0xC0282D:similarity=0.30:blend=0.10,colortemperature=temperature=9800,"
            "eq=contrast=1.14:saturation=0.90:gamma=0.95,vignette=angle=PI/4",
    "flash": "eq=brightness=0.08:contrast=0.90:saturation=0.60:gamma=1.18,vignette=angle=PI/6",
    "none": "null",   # long-video assembler: maps / finished graphics / presenter plates keep their own colours (Shorts never use it)
}
MOOD_ALIAS = {"warm-normal": "warm", "normal": "warm", "warm_normal": "warm", "red": "danger", "red-danger": "danger", "danger_red": "danger",
              "history": "sepia", "sepia_archive": "sepia", "archive": "sepia", "b&w": "bw", "bw_archive": "bw", "black-white": "bw",
              "cold-desat-crisis": "cold", "cold_blue": "cold", "crisis": "cold", "flash-white": "flash", "white": "flash", "highkey": "flash"}
def mood_of(v):
    v = (v or "").strip().lower(); v = MOOD_ALIAS.get(v, v)
    return v if v in GRADES else None

# automatic rule (owner 24 Nov): danger words -> danger; a year before 1950 or history words -> sepia; crisis words -> cold; else warm.
# Run on the shot sheet's English beat/label (make_short.py) and, as a fallback, on the voiced line (any of our 5 languages).
DANGER = (r"\b(war|wars|warfare|battle|blast|blasts|explosion|explode[sd]?|bomb(s|ing|ed)?|missile|attack(s|ed)?|terror\w*|"
          r"kill(s|ed|ing)?|death|deaths|dead|die[sd]?|murder\w*|massacre|shot dead|hijack\w*|crash(ed)?|invasion|airstrike)\b"
          r"|युद्ध|हमला|हमले|धमाका|विस्फोट|मौत|मारे|बम|మరణ|చనిపో|యుద్ధ|దాడి|పేలుడు|బాంబు|போர்|தாக்குதல்|மரண|வெடி|குண்டு|যুদ্ধ|হামলা|মৃত্যু|বিস্ফোরণ|বোমা")
# a year = 1000-1949 NOT followed by a unit ("1500 km", "1200 feet", "1000 times" are not years)
HISTORY = (r"\b(1[0-8]\d\d|19[0-4]\d)\b(?![,.]?\d)(?!\s*(km|kms|m|mm|cm|ft|feet|kg|g|kmph|mph|people|crore|lakh|rs|rupees|%|years?|"
           r"metres|meters|tonnes|tons|litres|liters|degrees|times|x)\b)(?!\s*[%°])|\b(ancient|century|centuries|empire|british raj|colonial|medieval|dynasty|freedom struggle|"
           r"independence movement|in history|historic(al)?)\b|इतिहास|प्राचीन|సంవత్సరాల క్రితం|చరిత్ర|வரலாறு|ইতিহাস")
CRISIS = (r"\b(crisis|collapse[sd]?|shortage|scam|fraud|recession|default(ed)?|bankrupt\w*|surveillance|leak(ed)?|outbreak|pandemic|"
          r"flood(s|ed)?|drought|famine|emergency|warning)\b|संकट|घोटाला|బాధ|సంక్షోభ|కుంభకోణం|நெருக்கடி|மோசடி|সংকট|কেলেঙ্কারি")
def auto_mood(text):
    t = (text or "").lower()
    if re.search(DANGER, t): return "danger"
    if re.search(HISTORY, t): return "sepia"
    if re.search(CRISIS, t): return "cold"
    return "warm"

# ---------------------------------------------------------------- 4) TEXTURE LAYER --------------------------------------------------------------
def _dust(seed):
    # (drawbox has t but no n -> frame = floor(t*30)) flickering white + dark specks (Nitish 00:33 "Film dirt", Dhruv "High-grain B&W"): 4 small boxes, new place every frame, on 2 of 5 frames
    out = []
    for q, (pa, pb, sz, col) in enumerate([(7919, 104729, 4, "white@0.55"), (6151, 92821, 3, "white@0.45"), (3571, 49157, 5, "black@0.45"), (2939, 72931, 3, "black@0.5")]):
        o = seed * 131 + q * 977
        out.append(f"drawbox=x='mod(floor(t*30)*{pa}+{o},iw)':y='mod(floor(t*30)*{pb}+{o * 3},ih)':w={sz}:h={sz}:color={col}:t=fill:enable='lt(mod(n+{q * 2 + seed},5),2)'")
    out.append(f"drawbox=x='mod(floor(t*30)*397+{211 + seed * 53},iw)':y=0:w=2:h=ih:color=white@0.16:t=fill:enable='lt(mod(n+{seed},11),3)'")   # scratch
    return ",".join(out)
TEX = {   # name -> filter (procedural, no downloads)
    "grain": "noise=c0s=12:c0f=t+u",
    "grain_heavy": "noise=c0s=20:c0f=t+u",
    "grain_light": "noise=c0s=7:c0f=t+u",
    "scanlines": "drawgrid=w=iw:h=6:t=2:c=black@0.26",                                         # CRT scan lines (Nitish 00:15.2, 00:25.8)
    "vhs": "drawgrid=w=iw:h=4:t=1:c=black@0.30,rgbashift=rh=3:bh=-3,drawbox=x=0:y='mod(t*420,ih+120)-120':w=iw:h=120:color=white@0.06:t=fill",
    "rgb": "rgbashift=rh=5:bh=-5",
}
MOOD_TEX = {"warm": [], "danger": ["grain", "scanlines"], "sepia": ["grain_heavy", "dust"], "bw": ["grain_heavy", "dust"],
            "cold": ["grain_light", "scanlines"], "flash": ["grain_light"], "none": []}
def texture(mood, fx, seed):
    names = [x for x in (fx or []) if x in TEX or x == "dust"]
    if not any(x in ("notex", "clean") for x in (fx or [])): names = MOOD_TEX.get(mood, []) + names
    seen, out = set(), []
    for x in names:
        if x in seen: continue
        seen.add(x); out.append(_dust(seed) if x == "dust" else TEX[x])
    return ",".join(out)

# ---------------------------------------------------------------- 2) TRANSITIONS ----------------------------------------------------------------
# A shot's "transition" is the way INTO that shot (the old renderer put the FLASH hit at the shot's start too).
# kind: cut | flash (2 white frames + 2 fading, hard cut under it) | leak (warm light-leak flash, Dhruv) | glitch (hard cut + 0.2 s RGB split,
# noise, torn bands, scan lines on both sides) | whip (xfade smoothleft 0.2 s + directional blur both sides) | zoom (xfade zoomin 0.25 s)
# | dissolve (xfade fade 0.4 s, as the long video) | dip (xfade fadeblack 0.4 s).
# Shot-sheet words that describe the CLIP (MATCH, THROUGH, LOOP, MORPH, XRAY, RISE, RAMP, FLY-THROUGH, END ...) stay hard cuts.
def transition_of(v):
    v = (v or "").upper()
    if "FLASH" in v: return "leak" if ("LEAK" in v or "YELLOW" in v or "WARM" in v) else "flash"
    if "LEAK" in v or "BURN" in v: return "leak"
    if "GLITCH" in v or "VHS" in v: return "glitch"
    if "WHIP" in v: return "whip"
    if "ZOOM" in v or "PUNCH" in v: return "zoom"
    if "DISSOLVE" in v or v.strip() in ("FADE", "CROSSFADE", "XFADE"): return "dissolve"
    if "DIP" in v or "BLACK" in v: return "dip"
    return "cut"
XF = {"whip": ("smoothleft", 0.20), "zoom": ("zoomin", 0.25), "dissolve": ("fade", 0.40), "dip": ("fadeblack", 0.40)}
def ext_frames(kind): return int(round(XF[kind][1] * FPS)) if kind in XF else 0

GLITCH_FX = ("rgbashift=rh=18:bh=-18:gv=6:enable='{e}',noise=alls=45:allf=t:enable='{e}',"
             "swaprect=w=1020:h=90:x1=0:y1='mod(n*577,h-90)':x2=60:y2='mod(n*331+400,h-90)':enable='{e}',"
             "drawgrid=w=iw:h=4:t=2:c=black@0.45:enable='{e}'")
def head_tail(kin, kout, Lp):
    """effects on the first frames (incoming transition kin) and last frames (outgoing kout) of one rendered shot of length Lp s"""
    f = []
    if kin == "glitch": f.append(GLITCH_FX.format(e="lt(t,0.2)"))
    if kout == "glitch": f.append(GLITCH_FX.format(e=f"gte(t,{Lp - 0.1:.3f})"))
    if kin == "whip": f.append("dblur=angle=0:radius=45:enable='lt(t,0.12)'")
    if kout == "whip": f.append(f"dblur=angle=0:radius=45:enable='gte(t,{Lp - 0.25:.3f})'")
    return ",".join(f)

def shake_xy(fx, hit):
    """camera shake added to the crop x/y (Nitish 00:04.2 "Handheld shake", 00:33 "Jittery shake", 00:54.6 "Violent camera shake")"""
    fx = fx or []; sx = sy = ""
    if "shake" in fx or "handheld" in fx:   # handheld: slow wander, 6-9 px
        sx += "+9*sin(2*PI*1.3*t)+5*sin(2*PI*3.1*t+1)"; sy += "+7*sin(2*PI*1.7*t+2)+4*sin(2*PI*2.9*t)"
    if "shake_hit" in fx or "impact" in fx or hit:   # impact: 40 px, decays in ~0.5 s
        sx += "+40*exp(-6*t)*sin(2*PI*9*t)"; sy += "+28*exp(-6*t)*sin(2*PI*7*t+1)"
    return sx, sy

def shot_vf(fill, zexp, xexp, mood, fx, kin, kout, nf, seed, hit_shake, W=1080, H=1920):
    """the filter chain of one shot in kit mode (same scale/crop/zoom as the old one + grade + texture + shake + head/tail effects).
    W/H default to the Shorts frame (byte-identical filter); the long-video assembler passes 1920x1080."""
    sx, sy = shake_xy(fx, hit_shake)
    if sx: zexp = f"({zexp})*1.06"   # 3 % margin on each side for the shake crop
    x = f"max(0,min(iw-ow,({xexp}){sx}))" if sx else xexp
    y = f"max(0,min(ih-oh,(ih-oh)/2{sy}))" if sy else "(ih-oh)/2"
    Lp = nf / FPS
    parts = [f"{fill}fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,tpad=stop_mode=clone:stop_duration=0.6",
             f"scale=w='trunc({W}*({zexp})/2)*2':h='trunc({H}*({zexp})/2)*2':eval=frame,crop={W}:{H}:x='{x}':y='{y}'",
             GRADES[mood]]
    tx = texture(mood, fx, seed)
    if tx: parts.append(tx)
    parts.append(f"trim=end_frame={nf},setpts=PTS-STARTPTS")
    ht = head_tail(kin, kout, Lp)
    if ht: parts.append(ht)
    return ",".join(parts)

def join_graph(nparts, kinds, starts, flashes, D, W=1080, H=1920):
    """filter graph: parts -> one timeline. kinds[i] = transition INTO part i; starts[i] = its start frame. xfade parts were rendered
    longer by ext_frames(kinds[i+1]) so every shot keeps its slot (captions, labels, face, SFX do not move)."""
    g = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,setsar=1[p{i}]" for i in range(nparts)]   # zoom scale leaves SAR 9153:9152
    cur = "[p0]"
    for i in range(1, nparts):
        k = kinds[i]
        if k in XF:
            g.append(f"{cur}[p{i}]xfade=transition={XF[k][0]}:duration={ext_frames(k) / FPS:.4f}:offset={starts[i] / FPS:.4f},settb=AVTB[j{i}]")
        else:
            g.append(f"{cur}[p{i}]concat=n=2:v=1:a=0,settb=AVTB[j{i}]")
        cur = f"[j{i}]"
    for q, (tc, kind) in enumerate(flashes):   # real visual flash: 2 full frames + 2 fading (white) / 1 + 5 (warm light leak)
        if kind == "leak":
            src = f"color=c=0xFFB24A:s={W}x{H}:r={FPS}:d={6 / FPS:.4f},format=yuva420p,colorchannelmixer=aa=0.85,fade=t=out:st={1 / FPS:.4f}:d={5 / FPS:.4f}:alpha=1"
            st = tc - 2 / FPS
        else:
            src = f"color=c=white:s={W}x{H}:r={FPS}:d={4 / FPS:.4f},format=yuva420p,fade=t=out:st={2 / FPS:.4f}:d={2 / FPS:.4f}:alpha=1"
            st = tc - 1 / FPS
        g.append(f"{src},setpts=PTS-STARTPTS+{max(0.0, st):.4f}/TB[fl{q}]")
        g.append(f"{cur}[fl{q}]overlay=eof_action=pass:repeatlast=0[fo{q}]"); cur = f"[fo{q}]"
    g.append(f"{cur}trim=duration={D:.4f},format=yuv420p[bg]")
    return ";\n".join(g)

# ---------------------------------------------------------------- 2b) TRANSITION RULES (24 Nov, research/TRANSITION_RULES.md) ------------------
# Counted over 3,734 shots / 255 min of Nitish + Dhruv: hard cut = 60 % of teaser cuts and 67 % of body cuts; effects (flash, leak, glitch, whip,
# zoom, dissolve, dip) = 7.0 / min in the teaser and 3.1 / min in the body (median gap 10.5 s, p25 5.5 s); 72 % of cuts INTO the presenter are
# hard cuts; a colour mood holds ~13 s (stable blocks change 2.2 / min); high-key flash = 1 % of shots; a whoosh sits on only 8 % of hard cuts.
# So a sheet transition word is a REQUEST: it survives only on a beat that earns it and inside the budget; everything else is a hard cut.
# Job "tr_rules": 0 switches this off (old kit behaviour, byte-identical). Beat "tr_lock": true keeps its transition; "mood_lock": true its mood.
EFFECT_KINDS = ("flash", "leak", "glitch", "whip", "zoom", "dissolve", "dip")
BUDGET = {"teaser": (7.0, 2.0), "body": (3.0, 5.5), "reveal": (4.0, 5.5), "action": (4.0, 3.0), "ending": (4.0, 5.0)}   # (max per 60 s, min gap s)
MOOD_MIN_BLOCK = 8.0

def smooth_moods(shots, min_block=MOOD_MIN_BLOCK):
    """a colour mood holds >= min_block s; a new mood inside a younger block is absorbed into it (kept in s['mood_req']).
    A SILENCE reveal, a section start or mood_lock may start a new block at once."""
    if not shots: return 0
    cur, start, changes = shots[0]["mood"], shots[0]["t0"], 0
    for s in shots[1:]:
        m = s["mood"]
        if m == cur or m == "none": continue            # "none" = maps/cards keep their own colours and do not break a mood block
        if cur == "none": cur, start = m, s["t0"]; continue
        if s.get("mood_lock") or s.get("silence") or s.get("section_start") or s.get("tr_lock") or s["t0"] - start >= min_block - 1e-6:
            cur, start = m, s["t0"]; changes += 1
        else:
            s["mood_req"] = m; s["mood"] = cur
    return changes

def _score(s, prev):
    if s.get("silence"): return 5                                   # reveal after a silence (dip / flash + braam)
    if s.get("section_start"): return 4                             # first shot of a chapter / section
    if prev is not None and s["mood"] != prev["mood"] and "none" not in (s["mood"], prev["mood"]): return 3   # a real (smoothed) mood-block change
    if s.get("section") == "teaser": return 2                       # teaser montage cut
    return 1

def apply_rules(shots, D, section="body", log=print):
    """in place: smooth moods, then keep only the transitions that earn a slot in the per-section budget; the rest become hard cuts.
    shots: dicts with t0, kind, mood (+ optional section, section_start, silence, presenter, tr_lock, mood_lock, whoosh)."""
    n_req = sum(1 for s in shots[1:] if s["kind"] in EFFECT_KINDS)
    moods_req = sum(1 for a, b in zip(shots, shots[1:]) if a["mood"] != b["mood"])
    mood_ch = smooth_moods(shots)
    for s in shots:
        s.setdefault("section", section)
        if s.get("tr_lock"): continue
        # kind must fit the beat (TRANSITION_RULES.md rule 5): no warm leak into danger (-> flash), no dissolve in action/teaser (-> cut)
        if s["kind"] == "leak" and s["mood"] == "danger": s["kind_req"] = "leak"; s["kind"] = "flash"
        if s["kind"] == "dissolve" and s["section"] in ("action", "teaser"): s["kind_req"] = "dissolve"; s["kind"] = "cut"
    # per-section totals for pieces shorter than a minute: round(rate * seconds / 60), at least 1
    span = {}
    for i, s in enumerate(shots):
        t1 = shots[i + 1]["t0"] if i + 1 < len(shots) else D
        span[s["section"]] = span.get(s["section"], 0.0) + max(0.0, t1 - s["t0"])
    total = {k: max(1, int(round(BUDGET.get(k, BUDGET["body"])[0] * v / 60))) for k, v in span.items()}
    cand = []
    for i in range(1, len(shots)):
        s = shots[i]
        if s["kind"] not in EFFECT_KINDS: continue
        if s.get("tr_lock"): cand.append((-99, s["t0"], i)); continue
        if s.get("presenter") and not (s.get("silence") or s.get("section_start")): continue   # hard cut into the presenter
        cand.append((-_score(s, shots[i - 1]), s["t0"], i))
    kept, used = [], {}
    for sc, t, i in sorted(cand):
        s = shots[i]; sec = s["section"]; rate, gap = BUDGET.get(sec, BUDGET["body"])
        if sc != -99:
            if used.get(sec, 0) >= total[sec]: continue
            if any(abs(t - shots[j]["t0"]) < gap for j in kept): continue
            if sum(1 for j in kept if abs(t - shots[j]["t0"]) < 30) >= rate: continue
        kept.append(i); used[sec] = used.get(sec, 0) + 1
    keep = set(kept)
    for i, s in enumerate(shots):
        if i == 0 or (s["kind"] in EFFECT_KINDS and i not in keep):
            if s["kind"] != "cut": s["kind_req"] = s["kind"]
            s["kind"] = "cut"
            if s["mood"] == "flash" and not s.get("mood_lock"):       # high-key is a hit, not a look: without its flash it keeps the block's mood
                s["mood_req"] = "flash"; s["mood"] = shots[i - 1]["mood"] if i else "warm"
        if s["kind"] == "cut" and not s.get("section_start"): s["whoosh"] = False   # whoosh only with an effect (8 % of hard cuts)
    log("kit rules: effects requested", n_req, "-> kept", len(keep), "| mood changes requested", moods_req, "-> kept", mood_ch,
        "| per section", {k: f"{used.get(k, 0)}/{total[k]}" for k in total})
    return shots

# ---------------------------------------------------------------- 3) SFX PLACEMENT -------------------------------------------------------------
# name -> default gain (files are peak-normalised to -1 dBFS); "anchor": the SFX's loudest moment lands on the target time,
# risers END on it (manifest "peak" = loudest point of the file).
GAIN = {"whoosh": 0.30, "impact": 0.42, "braam": 0.45, "sub": 0.55, "riser": 0.30, "tick": 0.30, "heartbeat": 0.40, "paper": 0.40,
        "page": 0.40, "marker": 0.35, "scribble": 0.35, "glitch": 0.32, "radio": 0.30, "alarm": 0.25, "shutter": 0.35, "click": 0.22, "rewind": 0.35}
def gain_of(nm): return next((v for k, v in GAIN.items() if nm.startswith(k)), 0.3)
JLEAD = 0.10   # SFX hit lands 3 frames BEFORE the picture cut (Nitish: "audio hits precede visual cuts by 2-4 frames")

def place(man, nm, target, gain=None, anchor="peak"):
    f = man["files"].get(nm)
    if not f: return None
    if nm.startswith("riser") or anchor == "end": st = target - f["dur"]
    elif anchor == "start": st = target
    else: st = target - f.get("peak", 0.0)
    return (round(st, 3), nm, gain if gain is not None else gain_of(nm))

def sfx_events(shots, man, D):
    """-> list of (start s, sfx name, gain). One rule per transition kind, copied from the shot logs:
    flash: riser into a cinematic impact (Nitish 00:14 "Flash to white / Intense riser"); glitch: glitch hit (00:04 "Glitch cut / Heavy impact
    hits", 08:32 "glitch distortion"); whip/zoom: fast whoosh (00:10.8, Dhruv whip cuts); dip: sub drop; dissolve and the first shot of each new
    voiced line: soft whoosh (the old synthetic whoosh rule, now a real file); label card: soft click (Nitish "clean mechanical mouse clicks for
    timeline markers"); SILENCE beat: music + SFX gated to zero, then a braam on the reveal (Nitish 15:39 "Full audio cut-out ... dull impact").
    A beat's own "sfx" list adds named sounds at its start (e.g. ["heartbeat"], ["paper_slide"], [{"name": "tick_timer", "at": 0.2, "gain": 0.25}])."""
    ev = []; rot = {}
    def pick(base, alts):   # rotate through variants so the same file is not heard twice in a row
        n = rot.get(base, 0); rot[base] = n + 1; c = [a for a in alts if a in man["files"]]
        return c[n % len(c)] if c else base
    for i, s in enumerate(shots):
        t0 = s["t0"]; k = s["kind"]
        if i > 0:
            if k == "flash":
                ev.append(place(man, "riser_short", t0 - JLEAD)); ev.append(place(man, pick("impact", ["impact_big", "impact_trailer", "impact_deep"]), t0 - JLEAD))
            elif k == "leak": ev.append(place(man, pick("whoosh_soft", ["whoosh_soft2", "whoosh_soft"]), t0 - JLEAD))
            elif k == "glitch": ev.append(place(man, pick("glitch", ["glitch", "glitch_scifi", "glitch_static", "glitch_trailer"]), t0 - JLEAD))
            elif k == "whip": ev.append(place(man, pick("whoosh_fast", ["whoosh_fast", "whoosh_fast2"]), t0 - JLEAD))
            elif k == "zoom": ev.append(place(man, "whoosh_air", t0 - JLEAD))
            elif k == "dip": ev.append(place(man, pick("sub", ["sub_drop", "sub_knock"]), t0, anchor="start"))
            elif k == "dissolve" or s.get("whoosh"): ev.append(place(man, pick("whoosh_soft", ["whoosh_soft", "whoosh_soft2"]), t0 - JLEAD))
        if s.get("silence"): ev.append(place(man, pick("braam", ["braam", "braam_long"]), t0 - 0.05))
        if s.get("label") and s.get("label_sfx", "click"): ev.append(place(man, s.get("label_sfx", "click"), t0 + 0.2, anchor="start"))
        for x in s.get("sfx") or []:
            if isinstance(x, str): x = {"name": x}
            ev.append(place(man, x["name"], t0 + x.get("at", -JLEAD), x.get("gain"), x.get("anchor", "peak")))
    return sorted(e for e in ev if e and e[0] < D - 0.05)

# ---------------------------------------------------------------- 3b) AUDIO MIX (port of episode_assemble.py mux) --------------------------------
SR = 48000
def _read(ff, path, n=None):
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).reshape(-1, 2).copy()
    if n is not None: x = np.pad(x, ((0, max(0, n - len(x))), (0, 0)))[:n]
    return x
def _lufs(ff, x, tmp):
    _write(tmp, x)
    o = subprocess.run([ff, "-hide_banner", "-i", tmp, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(json.loads(re.findall(r"\{[^{}]*\}", o)[-1])["input_i"])
def _write(path, x):
    w = wave.open(path, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()); w.close()

def mix(ff, T, voice_wav, music, sfx_dir, events, silences, D, music_lufs=-24.0, duck_db=-5.0, log=print):
    """voice two-pass loudnorm -14 LUFS; music bed looped (3 s crossfades) at music_lufs (bed alone = voice - 10 dB, owner 16 Nov);
    voice-activity ducking duck_db (attack 0.6 / release 0.1 per 50 ms hop, -42 dB gate) and silence gates exactly as episode_assemble.py;
    SFX added at their places (also gated, so a SILENCE beat is real silence apart from the voice); 0.89 peak ceiling.
    silences: list of (s0, s1) windows where music + SFX go to zero (ramps 0.08 s down / 0.05 s up: Nitish's cut is abrupt)."""
    n = int(round(D * SR))
    o = subprocess.run([ff, "-y", "-hide_banner", "-i", voice_wav, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(re.findall(r"\{[^{}]*\}", o)[-1])
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", voice_wav, "-af",
                    f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
                    f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}", "-ac", "2", "-c:a", "pcm_s16le", f"{T}/voice_n.wav"], check=True)
    v = _read(ff, f"{T}/voice_n.wav", n)
    x = _read(ff, music); need = n; k = int(3.0 * SR); bed = x.copy()
    while len(bed) < need and len(x) > k:
        r_ = np.linspace(0, 1, k, dtype=np.float32)[:, None]; bed = np.concatenate([bed[:-k], bed[-k:] * (1 - r_) + x[:k] * r_, x[k:]])
    bed = np.pad(bed, ((0, max(0, need - len(bed))), (0, 0)))[:need]
    env = np.ones(n, np.float32); fi = int(0.6 * SR); fo = int(2.0 * SR)
    env[:fi] = np.linspace(0, 1, fi); env[-fo:] = np.minimum(env[-fo:], np.linspace(1, 0, fo))
    bed *= env[:, None]
    g_ = music_lufs - _lufs(ff, bed, f"{T}/bed_m.wav"); bed *= np.float32(10 ** (g_ / 20))
    gate = np.ones(n, np.float32); rd, ru = int(0.08 * SR), int(0.05 * SR)
    for s0, s1 in silences:
        i0, i1 = max(0, int(s0 * SR)), min(n, int(s1 * SR))
        a = max(0, i0 - rd); gate[a:i0] = np.minimum(gate[a:i0], np.linspace(1, 0, i0 - a)); gate[i0:i1] = 0
        gate[i1:i1 + ru] = np.minimum(gate[i1:i1 + ru], np.linspace(0, 1, len(gate[i1:i1 + ru])))
    hop = int(0.05 * SR); rms = np.sqrt(np.mean(v[:len(v) // hop * hop, 0].reshape(-1, hop) ** 2, axis=1) + 1e-12)
    act = (20 * np.log10(rms) > -42).astype(np.float32); sm = np.zeros_like(act); s = 0.0
    for q, a_ in enumerate(act): s = s + (a_ - s) * (0.6 if a_ > s else 0.1); sm[q] = s
    duck = 10 ** (duck_db * np.repeat(sm, hop) / 20); duck = np.pad(duck, (0, n - len(duck)), constant_values=1.0)[:n].astype(np.float32)
    fx = np.zeros((n, 2), np.float32); cache = {}
    for st, nm, gn in events:
        if nm not in cache: cache[nm] = _read(ff, os.path.join(sfx_dir, nm + ".wav"))
        y = cache[nm]; i0 = int(round(st * SR)); a = max(0, -i0); i0 = max(0, i0); L = min(len(y) - a, n - i0)
        if L > 0: fx[i0:i0 + L] += y[a:a + L] * np.float32(gn)
    # a SILENCE window mutes everything but the voice; the reveal braam starts at its end, so it is not muted
    out = v + bed * (gate * duck)[:, None] + fx * gate[:, None]
    pk = float(np.abs(out).max())
    tail = int(0.35 * SR); out[-tail:] *= np.linspace(1, 0, tail, dtype=np.float32)[:, None]
    # ceiling 0.89 with a look-ahead limiter (episode_assemble scaled the whole mix down instead, which would also lower the voice
    # whenever one SFX transient peaks; the limiter only touches those transients)
    out.astype("<f4").tofile(f"{T}/mix.f32")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", f"{T}/mix.f32", "-af",
                    "alimiter=limit=0.89:attack=5:release=60:level=disabled:latency=1", "-c:a", "pcm_s16le", f"{T}/mix.wav"], check=True)
    os.remove(f"{T}/mix.f32")
    log("kit mix: voice -14 LUFS, bed", music_lufs, "LUFS, duck", duck_db, "dB, sfx", len(events), "silences", silences, "peak", round(pk, 3))
    return f"{T}/mix.wav"
