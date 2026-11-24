# Long-video EPISODE ASSEMBLER (24 Nov 2026). 16:9 1080p master from shots.json + voice track + paragraph timing.
# Every rule is copied from the creators' logs: research/TRANSITION_RULES.md (budget, hard-cut default, mood blocks), TEASER_BREAK_FRAMES.md
# (title drop), NITISH_FLYDUBAI_BREAKDOWN.md (mix, chapter stops), PACE_CURVES_ALL_LANGS.md (per-paragraph pace). How it works:
# research/EPISODE_ASSEMBLER.md.
#
#   python assemble_episode.py targets  shots.json                     -> pace_targets.json (per-paragraph syl/s + pause hint for the TTS)
#   python assemble_episode.py pace     shots.json paras_dir lang      -> voice.wav + timing.json (emotion_tts.pace_fix per paragraph)
#   python assemble_episode.py plan     shots.json timing.json [--size 1920x1080] [--meta-avail a,b|r2] -> plan.json (EDL, chunks, audio)
#   python assemble_episode.py chunk    plan.json K                    -> chunks/c<K>.mp4  (video only; GitHub matrix job)
#   python assemble_episode.py mix      plan.json                      -> mix.wav          (whole timeline, one job)
#   python assemble_episode.py concat   plan.json                      -> master.mp4 + master_720.mp4
#   python assemble_episode.py fixture  ic814_te.md                    -> shots_fixture.json (test input parsed from the script)
#   python assemble_episode.py notes    shots.json [timing.json]       -> required clip seconds per shot (markdown)
# Assets: every "src" is an R2 key (fetched by r2io_assemble.py on the runner) or a local path. Env ASM_ASSETS = local asset folder.
import json, math, os, re, shutil, subprocess, sys, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for p in (os.environ.get("CINE_KIT_DIR", ""), "C:/Users/goddu/repos/shorts-render"):
    if p and os.path.exists(os.path.join(p, "cine_kit.py")) and not os.path.exists(os.path.join(HERE, "cine_kit.py")): sys.path.insert(0, p)
import cine_kit as CK

FF = os.environ.get("CK_FFMPEG", "ffmpeg")
FP = os.path.join(os.path.dirname(FF), os.path.basename(FF).replace("ffmpeg", "ffprobe")) if os.path.dirname(FF) else "ffprobe"
FPS = CK.FPS; SR = 48000
ASSETS = os.environ.get("ASM_ASSETS", "assets")
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(2 * FPS),
       "-keyint_min", str(2 * FPS), "-sc_threshold", "0", "-bf", "2", "-profile:v", "high", "-level", "4.2", "-video_track_timescale", "15360"]

# ---------------------------------------------------------------- pace targets (PACE_CURVES_ALL_LANGS.md section 4) ------------------------------
PACE = {  # lang: teaser start, teaser end, body, reveal, action, ending (syllables/s of the final voice)
    "hi": (5.2, 7.2, 8.0, 5.9, 8.9, 7.4), "te": (7.0, 7.0, 7.3, 6.3, 8.1, 7.4), "ta": (6.9, 7.7, 8.2, 6.8, 9.1, 7.7),
    "bn": (5.9, 6.5, 6.6, 5.4, 7.9, 6.9), "en": (3.7, 4.4, 4.6, 3.4, 5.3, 4.4), "mr": (6.9, 7.2, 7.4, 6.0, 8.3, 7.2)}
BEATS = ("teaser", "body", "reveal", "action", "ending")

# ---------------------------------------------------------------- recipes (TEASER_BREAK_FRAMES.md, NITISH_FLYDUBAI_BREAKDOWN.md) -------------------
TITLE_HOLD, TITLE_FADE, TITLE_BLACK = 2.6, 0.4, 0.3   # boom + title card held ~2.6 s, 0.4 s fade, 0.2-0.4 s black + total silence
CHAPTER_BLACK = 1.0                                  # chapter stop: ~1 s black + silence, then the chapter card on a soft hit
BED_FADE_IN_DELAY = 0.3                              # the body bed fades up 0.3 s after the presenter starts
FACE_MAX = 12.0                                      # owner face circle: key moments only, at most 12 s per [FACE] section
# Meta overlay elements (R2 s2work/shorts1/clips/kit_meta/<name>.mp4, light-on-black unless noted) -> blend mode, default opacity
ELEMENTS = {"film_burn": ("screen", 0.95), "light_leak": ("screen", 0.85), "flash_white": ("screen", 1.0), "glitch_vhs": ("screen", 0.8),
            "dust_beam": ("screen", 0.30), "embers": ("screen", 0.6), "red_alert": ("screen", 0.55), "fog_through": ("screen", 0.9),
            "smoke_wipe": ("screen", 0.9), "paper_tear": ("screen", 0.9), "ink_bleed": ("multiply", 0.9), "grunge_title_bg": ("plate", 1.0)}
META_PREFIX = "s2work/shorts1/clips/kit_meta/"
# a surviving transition may ride on a real Meta element instead of the procedural one (same budget, no extra effects)
KIND_ELEMENT = {"leak": ("film_burn", 0.7, -0.35), "flash": ("flash_white", 0.35, -0.05), "glitch": ("glitch_vhs", 0.3, -0.1)}   # (element, dur, offset)
WORD_ELEMENT = {"SMOKE": "smoke_wipe", "PAPER": "paper_tear", "TEAR": "paper_tear", "INK": "ink_bleed", "FOG": "fog_through"}

def sh(cmd, quiet=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAILED:", " ".join(cmd)[:600], "\n", r.stderr[-2500:], flush=True); raise SystemExit(2)
    return r

def probe_len(p):
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def asset(src):
    """local path of an asset: a local file as is, an R2 key under ASSETS/ (fetched beforehand by r2io_assemble.py)"""
    if not src: return None
    if os.path.exists(src): return src
    return os.path.join(ASSETS, src.replace("/", "__"))

def syl(text, lang=None):
    try:
        sys.path.insert(0, "E:/mb_builds/tools/cinekit/voice"); import emotion_tts as ET   # same akshara rule as the TTS check
        return ET.syllables(text, lang)
    except Exception:
        import pace_curves as PC; return PC.syl(text)

# ---------------------------------------------------------------- input normalisation (shots.json from the planning agent) -----------------------
def load_shots(path):
    """tolerant reader: accepts our schema (EPISODE_ASSEMBLER.md section 2) and common aliases (para/paragraph/p, src/clip/r2/key, tr/transition)"""
    J = json.load(open(path, encoding="utf-8"))
    if J.get("script") and any(s.get("type") in ("meta_clip", "text_card", "doc_card", "chapter_stop") for s in J.get("shots", [])):
        import planner_adapter; J = planner_adapter.convert(path)          # the IC-814 planner's format (build_shots.py)
    paras = J.get("paragraphs") or J.get("paras") or []
    shots = J.get("shots") or []
    for s in shots:
        s["para"] = s.get("para") or s.get("paragraph") or s.get("p") or s.get("pid")
        s["src"] = s.get("src") or s.get("clip") or s.get("r2") or s.get("key") or s.get("file")
        s["transition"] = s.get("transition") or s.get("tr") or "CUT"
        s.setdefault("type", "presenter" if s.get("presenter") else "meta")
    if not paras:   # derive paragraphs from the shots
        seen = []
        for s in shots:
            if s["para"] not in [p["id"] for p in seen]: seen.append({"id": s["para"], "section": s.get("section", "S1"), "beat": s.get("beat", "body")})
        paras = seen
    for p in paras: p.setdefault("beat", "body")
    J["paragraphs"], J["shots"] = paras, shots
    J.setdefault("sections", [{"id": sid, "kind": "chapter"} for sid in dict.fromkeys(p["section"] for p in paras)])
    return J

# ---------------------------------------------------------------- targets / pace (TTS side) ---------------------------------------------------
def para_targets(J):
    lang = J.get("lang", "te"); ts, te_, body, rev, act, end = PACE.get(lang, PACE["te"])
    teas = [p for p in J["paragraphs"] if p["beat"] == "teaser"]
    out = {}
    for p in J["paragraphs"]:
        b = p["beat"]
        if b == "teaser":
            k = teas.index(p); tgt = ts + (te_ - ts) * (k / max(1, len(teas) - 1))
        else: tgt = {"body": body, "reveal": rev, "action": act, "ending": end}.get(b, body)
        n = syl(p.get("text", ""), lang); speech = n / body if n else 0      # seconds at body speed
        # slower beats come from PAUSES (atempo only speeds up, emotion_tts.MAX_TEMPO 1.25): pause = speech*(body/target - 1)
        pause = round(max(0.0, speech * (body / tgt - 1)), 2) if tgt < body else 0.0
        out[p["id"]] = {"lang": lang, "beat": b, "target_syl_s": round(tgt, 2), "syllables": n, "est_seconds": round(n / tgt, 2) if n else None,
                        "pause_total_s": pause,
                        # ONLY pause_tag itself may go into the TTS text; placement is a note for the writer (instruction text gets read aloud)
                        "pause_tag": ("<long pause>" if pause >= 1.5 else "<short pause>" if pause > 0.3 else ""),
                        "placement": ("before the key line" if pause >= 1.5 else "between sentences" if pause > 0.3 else "")}
    return out

def cmd_targets(shots_json):
    J = load_shots(shots_json); T = para_targets(J)
    out = os.path.join(os.path.dirname(os.path.abspath(shots_json)), "pace_targets.json")
    json.dump(T, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1); print("wrote", out, len(T), "paragraphs")

def cmd_pace(shots_json, paras_dir, lang, gap=0.35):
    """voice each paragraph separately (P<id>.wav in paras_dir, pause tags only), then pace_fix each to its own target and join with a
    0.35 s breath -> voice.wav + timing.json. No style/emotion words are ever sent to the TTS (AB_RESULTS.md)."""
    sys.path.insert(0, "E:/mb_builds/tools/cinekit/voice"); import emotion_tts as ET
    J = load_shots(shots_json); T = para_targets(J); od = os.path.dirname(os.path.abspath(shots_json))
    os.makedirs(f"{od}/paced", exist_ok=True); timing, files, t = [], [], 0.0
    for p in J["paragraphs"]:
        src = os.path.join(paras_dir, f"{p['id']}.wav")
        if not os.path.exists(src): print("missing", src); continue
        dst = f"{od}/paced/{p['id']}.wav"
        r = ET.pace_fix(src, dst, lang, p.get("text"), target=T[p["id"]]["target_syl_s"])
        d = probe_len(dst); timing.append({"id": p["id"], "start": round(t, 3), "end": round(t + d, 3), "pace": r}); files.append(dst); t += d + gap
    lst = f"{od}/paced/list.txt"
    with open(lst, "w") as f:
        for q, fn in enumerate(files):
            f.write(f"file '{os.path.abspath(fn)}'\n")
            if q < len(files) - 1: f.write(f"file '{os.path.abspath(od)}/paced/gap.wav'\n")
    sh([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", str(gap), f"{od}/paced/gap.wav"])
    sh([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-ar", "48000", "-ac", "1", f"{od}/voice.wav"])
    json.dump({"voice": f"{od}/voice.wav", "paras": timing}, open(f"{od}/timing.json", "w"), indent=1); print("voice.wav + timing.json", round(t, 1), "s")

# ---------------------------------------------------------------- PLAN: the whole timeline in one place ------------------------------------------
def tr_of(word):
    w = (word or "").upper(); el = next((e for k, e in WORD_ELEMENT.items() if k in w), None)
    k = CK.transition_of(word)
    if el and k == "cut": k = "dissolve"     # an element wipe sits on a 0.4 s crossfade
    return k, el

def meta_available(arg):
    """-> {element name: R2 key}. 'r2' lists s2work/shorts1/clips/kit_meta/ (files are kit_<name>_<n>.mp4); 'a,b' = test names."""
    if not arg: return {}
    if arg != "r2": return {x.strip(): f"{META_PREFIX}kit_{x.strip()}.mp4" for x in arg.split(",") if x.strip()}
    try:
        import r2io_assemble as R; out = {}
        for k in sorted(R.ls(META_PREFIX)):
            nm = re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(k))[0])
            nm = nm[4:] if nm.startswith("kit_") else nm
            if nm in ELEMENTS: out.setdefault(nm, k)
        return out
    except Exception as ex:
        print("meta elements: cannot list R2 (", type(ex).__name__, ") -> procedural effects only"); return {}

def master_to_voice(t, vmap):
    """master-timeline second -> voice-track second (vmap rows: src0, src1|None, dst0; inserted gaps have no voice time)"""
    best = None
    for src0, src1, dst0 in vmap:
        if dst0 <= t + 1e-6: best = (src0, src1, dst0)
    if best is None: return t
    v = best[0] + (t - best[2])
    return min(v, best[1]) if best[1] is not None else v

def cmd_plan(shots_json, timing_json, size="1920x1080", meta="", out=None):
    J = load_shots(shots_json); TM = json.load(open(timing_json, encoding="utf-8"))
    W, H = (int(x) for x in size.lower().split("x"))
    P = {p["id"]: p for p in J["paragraphs"]}; tim = {x["id"]: x for x in TM["paras"]}
    secs = {s["id"]: s for s in J["sections"]}; order = [p["id"] for p in J["paragraphs"] if p["id"] in tim]
    avail = meta_available(meta)
    teaser_last = max((i for i, pid in enumerate(order) if P[pid]["beat"] == "teaser"), default=-1)
    # 1) master time of every paragraph = voice time + inserted gaps (title drop after the teaser, ~1 s chapter stops). This is the ONLY
    #    place where time is shifted; shots, overlays, SFX, beds and the voice map all derive from pm / gaps.
    off, vmap, segs_gap, pm, gap_before = 0.0, [], [], {}, {}
    first_after_title = order[teaser_last + 1] if 0 <= teaser_last < len(order) - 1 else None
    run_src0, run_dst0 = 0.0, 0.0
    for i, pid in enumerate(order):
        p = P[pid]; a, b = tim[pid]["start"], tim[pid]["end"]
        gap = None
        if pid == first_after_title:
            gap = ("title", TITLE_HOLD + TITLE_FADE + TITLE_BLACK)
        elif i and p["section"] != P[order[i - 1]]["section"] and secs.get(p["section"], {}).get("kind", "chapter") == "chapter":
            gap = ("chapter", CHAPTER_BLACK)
        if gap:
            cut_src = tim[order[i - 1]]["end"] + 0.15          # the last line ends cleanly, no reverb tail (Khamenei 00:51.4)
            vmap.append((run_src0, cut_src, run_dst0))
            g = {"type": gap[0], "t0": round(cut_src + off, 3), "dur": gap[1], "section": p["section"]}
            segs_gap.append(g); gap_before[pid] = g
            off = g["t0"] + g["dur"] - a
            run_src0, run_dst0 = a, a + off
        pm[pid] = (a + off, b + off)
    vmap.append((run_src0, None, run_dst0))
    D = round(pm[order[-1]][1] + 1.2, 3)
    # 2) shot slots: a paragraph's shots fill [previous slot end (or its gap end), next paragraph start (or next gap start)) by weight
    bounds, s0 = {}, 0.0
    for i, pid in enumerate(order):
        if pid in gap_before: s0 = gap_before[pid]["t0"] + gap_before[pid]["dur"]
        nxt = order[i + 1] if i + 1 < len(order) else None
        e = D if nxt is None else (gap_before[nxt]["t0"] if nxt in gap_before else pm[nxt][0])
        bounds[pid] = (round(s0, 4), round(e, 4)); s0 = e
    segs = []
    for pid in order:
        sh_ = [s for s in J["shots"] if s["para"] == pid] or [{"id": f"{pid}_black", "para": pid, "type": "black", "transition": "CUT"}]
        a, b = bounds[pid]; wsum = sum(float(s.get("weight") or s.get("dur") or 1) for s in sh_); t = a
        for s in sh_:
            L = (b - a) * float(s.get("weight") or s.get("dur") or 1) / wsum
            k, el = tr_of(s.get("transition"))
            sec = P[pid]["beat"]; text = " ".join(str(s.get(f) or "") for f in ("beat_en", "label", "detail", "visual"))
            mood = ("none" if s.get("type") in ("map", "card") else CK.mood_of(s.get("mood"))) or (
                "none" if s.get("type") == "presenter" else CK.auto_mood(text + " " + P[pid].get("gloss", "")))
            segs.append({"type": "shot", "id": s.get("id"), "para": pid, "stype": s.get("type", "meta"), "src": s.get("src"), "at": s.get("at"),
                         "plate": s.get("plate"), "face": s.get("face"), "fallback_text": s.get("fallback_text"), "planned_dur": s.get("planned_dur"),
                         "t0": round(t, 4), "L": round(L, 4), "kind": k, "element": el, "mood": mood, "fx": list(s.get("fx") or []),
                         "sfx": s.get("sfx") or [], "silence": (1.2 if s.get("silence") is True else float(s.get("silence") or 0)),
                         "label": s.get("label"), "tag": s.get("tag"), "presenter": s.get("type") == "presenter" or bool(s.get("presenter")),
                         "section": sec, "section_id": P[pid]["section"], "overlays": s.get("overlays") or [], "tr_lock": s.get("tr_lock"),
                         "mood_lock": s.get("mood_lock"), "move": s.get("move", "in" if len(segs) % 2 == 0 else "out"), "whoosh": False})
            t += L
    for g in segs_gap: segs.append(dict(g, type=g["type"], kind="cut", t0=g["t0"], L=g["dur"]))
    segs.sort(key=lambda s: s["t0"])
    # section starts: the first shot after a gap or after a change of section id
    for i, s in enumerate(segs):
        if s["type"] != "shot": continue
        prev = segs[i - 1] if i else None
        if prev is None or prev["type"] != "shot" or prev.get("section_id") != s["section_id"]:
            s["section_start"] = True
            sc = secs.get(s["section_id"], {})
            if sc.get("card_id"):   # rendered chapter card (kinetic_text "chapter", alpha) on the chapter's first shot; text fallback if not rendered
                s["overlays"] = [{"src": f"s2work/cinekit/cards/{sc['card_id']}.mov", "at": 0, "dur": min(2.6, s["L"]), "fallback_text": sc.get("card")}] + s["overlays"]
                sc["card_mov"] = True
        # PAUSE before a reveal paragraph (script "[PAUSE 2s]") -> music+SFX silence before its first shot, braam on the cut
        p = P[s["para"]]
        if s.get("section_start") is None and p.get("pause_before") and (i == 0 or segs[i - 1].get("para") != s["para"]):
            s["silence"] = max(s["silence"], min(1.5, float(p["pause_before"])))
    # 3) rules: one budget over the shot list (title/black are fixed recipes outside it)
    shots = [s for s in segs if s["type"] == "shot"]
    shots[0]["kind"] = "cut"
    CK.apply_rules(shots, D, "body")
    for i, s in enumerate(segs):   # a gap segment is entered and left with hard cuts (the recipes have their own fades)
        if s["type"] != "shot": continue
        if i and segs[i - 1]["type"] != "shot": s["kind"] = "cut"
    for s in shots:
        if s["kind"] in ("cut",) and s.get("element") and not s.get("tr_lock"): s["element"] = None
    # 4) frames: segment i owns frames [F_i, F_i+1); the outgoing side of an xfade is rendered longer by the overlap
    F = [int(round(s["t0"] * FPS)) for s in segs] + [int(round(D * FPS))]
    for i, s in enumerate(segs):
        nk = segs[i + 1]["kind"] if i + 1 < len(segs) else "cut"
        s["f0"], s["nf_slot"] = F[i], F[i + 1] - F[i]
        s["nf"] = s["nf_slot"] + (CK.ext_frames(nk) if i + 1 < len(segs) else 0)
        s["kout"] = nk
    # 5) overlays (absolute times): Meta elements on surviving transitions, ambient dust on archive blocks, cards, face circle
    ov = []
    for i, s in enumerate(segs):
        if s["type"] != "shot": continue
        t0 = s["t0"]
        el = s.get("element")
        if not el and s["kind"] in KIND_ELEMENT and KIND_ELEMENT[s["kind"]][0] in avail: el = KIND_ELEMENT[s["kind"]][0]
        if el and el in avail:
            dur, off_ = KIND_ELEMENT.get(s["kind"], (el, 0.8, -0.4))[1:] if s["kind"] in KIND_ELEMENT else (0.8, -0.4)
            ov.append({"kind": "element", "name": el, "key": avail[el], "t0": round(max(0, t0 + off_), 3), "dur": dur, "blend": ELEMENTS[el][0], "op": ELEMENTS[el][1], "speed": "fit"})
            s["element_used"] = el
        if s["mood"] == "sepia" and s.get("section_start") is None and (i == 0 or segs[i - 1].get("mood") != "sepia") and "dust_beam" in avail:
            blk = [x for x in segs[i:] if x["type"] == "shot"]; e = t0
            for x in blk:
                if x["mood"] != "sepia": break
                e = x["t0"] + x["L"]
            ov.append({"kind": "element", "name": "dust_beam", "key": avail["dust_beam"], "t0": t0, "dur": round(min(e - t0, 20), 3), "blend": "screen", "op": 0.30, "speed": 1.0})
        if s["mood"] == "danger" and s["section"] == "action" and s["kind"] in ("flash", "glitch") and "red_alert" in avail:
            ov.append({"kind": "element", "name": "red_alert", "key": avail["red_alert"], "t0": t0, "dur": 1.5, "blend": "screen", "op": 0.5, "speed": 1.0})
        for e in ("embers", "fog_through", "smoke_wipe", "paper_tear", "ink_bleed", "dust_beam", "red_alert"):
            if e in s["fx"] and e in avail:
                ov.append({"kind": "element", "name": e, "key": avail[e], "t0": t0, "dur": round(s["L"], 3), "blend": ELEMENTS[e][0], "op": ELEMENTS[e][1], "speed": 1.0})
        for o in s["overlays"]:   # alpha cards (kinetic_text .mov, doc_highlight --alpha) or named elements, times relative to the shot
            isel = o.get("name") in ELEMENTS
            if isel and o["name"] not in avail: continue
            ov.append({"kind": "element" if isel else "alpha", "name": o.get("name"), "src": o.get("src"), "key": avail.get(o.get("name")) if isel else None,
                       "t0": round(t0 + float(o.get("at", 0)), 3), "dur": min(float(o.get("dur", s["L"])), s["L"] - float(o.get("at", 0))),
                       "blend": o.get("blend", ELEMENTS.get(o.get("name"), ("alpha",))[0]), "fallback_text": o.get("fallback_text"),
                       "op": float(o.get("op", ELEMENTS.get(o.get("name"), (None, 1.0))[1])), "speed": o.get("speed", 1.0), "sfx": o.get("sfx")})
    face = []
    face_clip = J.get("face_clip")
    if face_clip and J.get("face_from_shots"):   # the planner marks face_circle shots (key moments): one window per run of them
        run = None
        for s in shots:
            if s.get("face"):
                if run and abs(run["t1"] - s["t0"]) < 0.05: run["t1"] = round(s["t0"] + s["L"], 3)
                else:
                    run = {"t0": round(s["t0"], 3), "t1": round(s["t0"] + s["L"], 3), "src": face_clip, "pos": s["face"] if isinstance(s["face"], str) else "bottom-right"}
                    # the face clip is lip-synced on the VOICE track: master time -> voice time through the voice map (gaps removed)
                    run["at"] = run["t0"] if J.get("face_timebase") == "master" else round(master_to_voice(run["t0"], vmap), 3); face.append(run)
            else: run = None
    elif face_clip:
        done = set()
        for pid in order:
            p = P[pid]; sid = p["section"]
            if (p.get("face") or secs.get(sid, {}).get("face")) and sid not in done:
                a, b = pm[pid]   # face clip is lip-synced on the voice track: its time = the paragraph's VOICE time (J["face_timebase"]="master" otherwise)
                at = a if J.get("face_timebase") == "master" else tim[pid]["start"]
                face.append({"t0": round(a, 3), "t1": round(min(b, a + FACE_MAX), 3), "src": face_clip, "at": round(at, 3)}); done.add(sid)
    # 6) chunks: cut only at a hard cut or a gap (nothing crosses a seam), ~ one section per chunk, <= 45 shots
    chunks, c0, n = [], 0, 0
    for i in range(1, len(segs)):
        n += 1
        seam_ok = segs[i]["kind"] == "cut" and segs[i - 1]["kind"] not in CK.XF and segs[i]["type"] == "shot" and not any(
            o["t0"] <= segs[i]["t0"] < o["t0"] + o["dur"] for o in ov if o["kind"] == "element" and o["dur"] < 2)
        new_sec = segs[i]["type"] != "shot" or segs[i - 1]["type"] != "shot"
        if (new_sec and n >= 6) or (seam_ok and n >= 45):
            if segs[i]["kind"] == "cut": chunks.append((c0, i)); c0, n = i, 0
    chunks.append((c0, len(segs)))
    # 7) audio plan: voice map, music beds per section, SFX, silences
    music = []
    for sid in dict.fromkeys(P[pid]["section"] for pid in order):
        ps = [pid for pid in order if P[pid]["section"] == sid]; kind = secs.get(sid, {}).get("kind", "chapter")
        bed = secs.get(sid, {}).get("music") or (J.get("music") or {}).get(P[ps[0]]["beat"]) or (J.get("music") or {}).get(kind) or (J.get("music") or {}).get("body")
        a = bounds[ps[0]][0]; b = bounds[ps[-1]][1]
        if bed: music.append({"t0": round(a + (BED_FADE_IN_DELAY if kind == "chapter" else 0), 3), "t1": round(b, 3), "src": bed, "section": sid,
                              "lufs": float(secs.get(sid, {}).get("music_lufs", J.get("music_lufs", -24.0)))})
    sfx_shots = [dict(s, t0=s["t0"]) for s in shots]
    for s in sfx_shots:
        if s.get("element_used") == "film_burn": s["kind"] = "leak"
    man = {"files": {k: v for k, v in sfx_manifest().get("files", {}).items()}}
    ev = CK.sfx_events(sfx_shots, man, D) if man["files"] else []
    silences = [(round(max(0.0, s["t0"] - s["silence"]), 3), s["t0"]) for s in shots if s.get("silence")]
    for g in segs_gap:
        if g["type"] == "title":
            T0 = g["t0"]
            ev += [x for x in (CK.place(man, "riser_drama" if "riser_drama" in man["files"] else "riser_short", T0 - CK.JLEAD),
                               CK.place(man, "impact_deep", T0 - CK.JLEAD, 1.0), CK.place(man, "sub_drop", T0 - 0.02, 0.9, anchor="start"),
                               CK.place(man, "braam_long", T0, 0.45, anchor="start")) if x]   # the loudest moment of the episode: sub-boom on frame 1 of the title
            silences.append((round(T0 + TITLE_HOLD + TITLE_FADE, 3), round(T0 + g["dur"], 3)))      # black + total silence
            silences.append((round(T0 - 0.05, 3), round(T0 + TITLE_HOLD + TITLE_FADE, 3), "bed"))   # teaser bed cuts at the boom
        else:
            silences.append((g["t0"], round(g["t0"] + g["dur"], 3)))
            x = CK.place(man, "sub_knock", g["t0"] + g["dur"], 0.45, anchor="start");  ev += [x] if x else []
    for o in ov:
        for e in (o.get("sfx") or []):
            x = CK.place(man, e.get("name", e) if isinstance(e, dict) else e, o["t0"] + (e.get("at", 0) if isinstance(e, dict) else 0)); ev += [x] if x else []
    plan = {"episode": J.get("episode", "ep"), "lang": J.get("lang"), "W": W, "H": H, "fps": FPS, "D": D, "segments": segs, "overlays": ov,
            "face": face, "chunks": [{"k": k, "i0": a, "i1": b, "t0": segs[a]["f0"] / FPS, "t1": (segs[b]["f0"] if b < len(segs) else F[-1]) / FPS}
                                     for k, (a, b) in enumerate(chunks)],
            "title": J.get("title") or {}, "sections": secs, "elements": avail,
            "audio": {"voice": TM.get("voice", "voice.wav"), "vmap": [list(v) for v in vmap], "music": music, "sfx": sorted(e for e in ev if e),
                      "silences": [list(s) for s in silences]},
            "rules": {"effects": sum(1 for s in shots[1:] if s["kind"] in CK.EFFECT_KINDS), "shots": len(shots),
                      "hard_cut_share": round(sum(1 for s in shots[1:] if s["kind"] == "cut") / max(1, len(shots) - 1), 3)}}
    plan["assets"] = sorted(set([s["src"] for s in segs if s.get("src")] + [o["src"] for o in ov if o.get("src")] + [o["key"] for o in ov if o.get("key")] +
                                ([face_clip] if face_clip else []) + [m["src"] for m in music] + [plan["audio"]["voice"]] +
                                ([avail["grunge_title_bg"]] if "grunge_title_bg" in avail else [])))
    out = out or os.path.join(os.path.dirname(os.path.abspath(shots_json)), "plan.json")
    json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("plan:", out, "| D", round(D, 1), "s | shots", len(shots), "| effects", plan["rules"]["effects"], "| hard cuts", plan["rules"]["hard_cut_share"],
          "| chunks", len(chunks), "| overlays", len(ov), "| sfx", len(plan["audio"]["sfx"]), "| beds", len(music))
    return plan

def sfx_manifest():
    for p in (os.path.join(ASSETS, "sfx", "manifest.json"), "E:/mb_builds/shorts_v1/kit/sfx/manifest.json"):
        if os.path.exists(p): return json.load(open(p, encoding="utf-8"))
    return {"files": {}}

# ---------------------------------------------------------------- CHUNK render (video only) -----------------------------------------------------
def ass_time(t): t = max(0, t); return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
def ass_esc(s): return (s or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")

def font_for(text):
    for rng, nm in (((0x0C00, 0x0C7F), "Noto Sans Telugu"), ((0x0B80, 0x0BFF), "Noto Sans Tamil"), ((0x0980, 0x09FF), "Noto Sans Bengali"),
                    ((0x0900, 0x097F), "Noto Sans Devanagari")):
        if any(rng[0] <= ord(c) <= rng[1] for c in text or ""): return nm
    return "Noto Sans"

def write_ass(path, W, H, events):
    s = H / 1080
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Tag,Noto Sans,%d,&H40FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,9,%d,%d,%d,1\n"
            "Style: Label,Noto Sans,%d,&H00000000,&H00000000,&H0028C9FF,&H00000000,1,0,0,0,100,100,1,0,3,10,0,1,%d,%d,%d,1\n"
            "Style: Title,Noto Sans,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,6,0,1,0,6,5,0,0,0,1\n"
            "Style: Sub,Noto Sans,%d,&H0028C9FF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,10,0,1,0,3,5,0,0,0,1\n"
            "Style: Chapter,Noto Sans,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,4,0,1,0,4,5,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") % (
        W, H, int(24 * s), int(28 * s), int(28 * s), int(22 * s), int(40 * s), int(60 * s), int(60 * s), int(60 * s), int(120 * s), int(40 * s), int(84 * s))
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for a, b, style, txt, extra in events:
            f.write(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{{\\fn{font_for(txt)}}}{extra}{ass_esc(txt)}\n")

def render_segment(s, plan, T, i):
    W, H = plan["W"], plan["H"]; out = f"{T}/s{i:04d}.mp4"; nf = s["nf"]; L = nf / FPS
    p = asset(s.get("src")) if s.get("src") else None
    missing = s["type"] == "shot" and s.get("stype") not in ("black", "plate") and not (p and os.path.exists(p))
    if missing: print(f"  MISSING asset for {s.get('id')}: {s.get('src')} -> dark plate fallback", flush=True)
    if s["type"] in ("chapter",) or s.get("stype") == "black" or (s.get("stype") == "plate" and s.get("plate") == "black"):
        sh([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={L + 0.1:.3f}", "-frames:v", str(nf),
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", out]); return out
    if s.get("stype") == "plate" or missing:
        # card plate (planner "dark textured plate"): the Meta grunge texture if we have it, else a dark slate with grain; graded by the mood
        g = asset((plan.get("elements") or {}).get("grunge_title_bg"))
        src = ["-stream_loop", "-1", "-ss", f"{(i * 1.7) % 4:.2f}", "-i", g] if g and os.path.exists(g) else ["-f", "lavfi", "-i", f"color=c=0x16181c:s={W}x{H}:r={FPS}:d={L + 1:.2f}"]
        md = "danger" if s.get("plate") == "red" else (s["mood"] if s["mood"] in CK.GRADES else "cold")
        vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,eq=brightness=-0.04:saturation=0.6,"
              f"{CK.GRADES[md]},noise=c0s=7:c0f=t+u,trim=end_frame={nf},setpts=PTS-STARTPTS")
        sh([FF, "-y", "-loglevel", "error"] + src + ["-vf", vf, "-an", "-frames:v", str(nf), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", out])
        return out
    if s["type"] == "title":
        # grunge plate (Meta) or dark slate, 105 % slow push, fade to black 0.4 s, then 0.3 s black (TEASER_BREAK Khamenei recipe)
        bg = asset((plan.get("elements") or {}).get("grunge_title_bg")); hold = TITLE_HOLD + TITLE_FADE
        src = ["-stream_loop", "-1", "-i", bg] if bg and os.path.exists(bg) else ["-f", "lavfi", "-i", f"color=c=0x101215:s={W}x{H}:r={FPS}:d={L + 1:.2f}"]
        vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
              f"scale=w='trunc({W}*(1+0.05*t/{hold:.2f})/2)*2':h='trunc({H}*(1+0.05*t/{hold:.2f})/2)*2':eval=frame,crop={W}:{H},"
              f"{CK.GRADES['cold']},noise=c0s=8:c0f=t+u,fade=t=out:st={TITLE_HOLD:.2f}:d={TITLE_FADE:.2f},trim=end_frame={nf},setpts=PTS-STARTPTS")
        sh([FF, "-y", "-loglevel", "error"] + src + ["-vf", vf, "-an", "-frames:v", str(nf), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", out]); return out
    still = p.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    st_ = s.get("stype"); z = {"map": 0.0, "presenter": 0.03, "parallax": 0.0, "card": 0.0}.get(st_, 0.09)
    Ls = s["L"]
    zexp = {"in": f"1+{z}*t/{Ls:.3f}", "out": f"1+{z}-{z}*t/{Ls:.3f}"}.get(s.get("move"), f"1+{z}") if z else "1"
    xexp = "(iw-ow)/2"
    if still:
        src = ["-loop", "1", "-t", f"{L + 1:.3f}", "-i", p]; fill = ""
    else:
        cl = probe_len(p); at = float(s.get("at") or 0.0); at = min(at, max(0.0, cl - 0.5)); avail = max(0.3, cl - at)
        fill = ""
        if avail < L:   # short clip: slow down up to 1.5x, then boomerang (as shorts_render), then hold the last frame
            f = min(1.5, L / avail); fill = f"setpts={f:.4f}*(PTS-STARTPTS),"
            if avail * f < L - 0.05: fill += "split[fw][bw];[bw]reverse[rv];[fw][rv]concat=n=2:v=1:a=0,"
            print(f"  short clip {s.get('id')}: needs {L:.2f}s from {at:.2f}, has {avail:.2f}s (x{f:.2f}{' + boomerang' if 'reverse' in fill else ''})", flush=True)
        fill += f"tpad=stop_mode=clone:stop_duration={L + 0.5:.2f},"
        src = ["-ss", f"{at:.3f}", "-t", f"{avail:.3f}", "-i", p]
    hit = s["mood"] == "danger" and s["kind"] in ("flash", "glitch")
    vf = CK.shot_vf(fill, zexp, xexp, s["mood"], s["fx"], s["kind"] if i else "cut", s["kout"], nf, i, hit, W, H)
    sh([FF, "-y", "-loglevel", "error"] + src + ["-vf", vf, "-an", "-frames:v", str(nf), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", out])
    return out

def cmd_chunk(plan_json, k, T=None):
    plan = json.load(open(plan_json, encoding="utf-8")); c = plan["chunks"][int(k)]; W, H = plan["W"], plan["H"]
    T = T or f"work_c{int(k):03d}"; os.makedirs(T, exist_ok=True); os.makedirs("chunks", exist_ok=True)
    segs = plan["segments"][c["i0"]:c["i1"]]; F0 = segs[0]["f0"]; Fend = (plan["segments"][c["i1"]]["f0"] if c["i1"] < len(plan["segments"]) else int(round(plan["D"] * FPS)))
    Dc = (Fend - F0) / FPS
    parts = []
    for j, s in enumerate(segs):
        s = dict(s); s["kind"] = s["kind"] if j else "cut"
        if j == len(segs) - 1: s["nf"] = s["nf_slot"]           # nothing after the chunk's last segment
        parts.append(render_segment(s, plan, T, j))
    kinds = [s["kind"] if j else "cut" for j, s in enumerate(segs)]; starts = [s["f0"] - F0 for s in segs]
    used_el = {round(s["t0"], 3) for s in segs if s.get("element_used")}
    fl = [(starts[j] / FPS, kinds[j]) for j, s in enumerate(segs) if j and kinds[j] in ("flash", "leak") and round(s["t0"], 3) not in used_el]
    open(f"{T}/join.txt", "w").write(CK.join_graph(len(parts), kinds, starts, fl, Dc, W, H))
    sh([FF, "-y", "-loglevel", "error"] + sum((["-i", p] for p in parts), []) + ["-filter_complex_script", f"{T}/join.txt", "-map", "[bg]",
       "-r", str(FPS), "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p", f"{T}/bg.mp4"])
    # overlay pass: Meta elements (blend), alpha cards, face circle, ASS text (AI tag, labels, chapter card, title)
    t0c, t1c = F0 / FPS, Fend / FPS
    ins, g, cur, q = ["-i", f"{T}/bg.mp4"], [], "[0:v]", 1; fallback_ev = []
    evs = [o for o in plan["overlays"] if o["t0"] < t1c and o["t0"] + o["dur"] > t0c]
    for e, o in enumerate(evs):
        a = max(0.0, o["t0"] - t0c); b = min(Dc, o["t0"] + o["dur"] - t0c)
        if b - a < 1 / FPS: continue
        if o["kind"] == "element":
            p = asset(o.get("key"))
            if not (p and os.path.exists(p)): continue
            el_len = probe_len(p) or 5.0
            spd = (el_len / max(0.1, o["dur"])) if o.get("speed") == "fit" else 1.0 / float(o.get("speed") or 1.0)
            ins += ["-stream_loop", "-1", "-i", p]
            start_in = max(0.0, (t0c - o["t0"])) * spd   # element already running when the chunk starts
            g.append(f"[{q}:v]setpts=(PTS-STARTPTS)/{spd:.4f},trim=start={start_in / spd:.4f}:duration={b - a:.4f},setpts=PTS-STARTPTS,"
                     f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=gbrp[el{e}]")
            g.append(f"{cur}split[base{e}][cp{e}]")
            g.append(f"[cp{e}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS,format=gbrp[seg{e}]")
            mode = o.get("blend", "screen"); mode = "screen" if mode == "plate" else mode
            g.append(f"[seg{e}][el{e}]blend=all_mode={mode}:all_opacity={float(o.get('op', 1.0)):.2f}:shortest=1,format=yuv420p,setpts=PTS+{a:.4f}/TB[bl{e}]")
            g.append(f"[base{e}][bl{e}]overlay=eof_action=pass:repeatlast=0[o{e}]"); cur = f"[o{e}]"; q += 1
        elif o.get("src"):
            p = asset(o["src"])
            if not (p and os.path.exists(p)):
                print("  missing overlay", o["src"], "-> text fallback" if o.get("fallback_text") else "")
                if o.get("fallback_text"): fallback_ev.append((a, b, "Chapter", o["fallback_text"], r"{\fad(150,200)}"))
                continue
            ins += ["-i", p]; skip = max(0.0, t0c - o["t0"])
            g.append(f"[{q}:v]trim=start={skip:.4f}:duration={b - a:.4f},setpts=PTS-STARTPTS+{a:.4f}/TB,scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0,format=yuva420p,colorchannelmixer=aa={float(o.get('op', 1.0)):.2f}[al{e}]")
            g.append(f"{cur}[al{e}]overlay=eof_action=pass:repeatlast=0[o{e}]"); cur = f"[o{e}]"; q += 1
    for e, fc in enumerate(x for x in plan["face"] if x["t0"] < t1c and x["t1"] > t0c):
        p = asset(fc["src"])
        if not (p and os.path.exists(p)): continue
        a = max(0.0, fc["t0"] - t0c); b = min(Dc, fc["t1"] - t0c); d = int(round(H * 0.30)); r = d // 2
        ins += ["-ss", f"{fc['at'] + max(0.0, t0c - fc['t0']):.3f}", "-i", p]
        g.append(f"[{q}:v]trim=duration={b - a:.4f},setpts=PTS-STARTPTS+{a:.4f}/TB,fps={FPS},scale={d}:{d}:force_original_aspect_ratio=increase,crop={d}:{d},"
                 f"format=yuva420p,geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='if(lte(hypot(X-{r},Y-{r}),{r - 6}),255,if(lte(hypot(X-{r},Y-{r}),{r - 1}),0,0))',"
                 f"fade=t=in:st={a:.3f}:d=0.25:alpha=1,fade=t=out:st={max(a, b - 0.25):.3f}:d=0.25:alpha=1[fc{e}]")
        g.append(f"color=c=white:s={d}x{d}:r={FPS}:d={b - a:.3f},format=yuva420p,geq=lum=255:cb=128:cr=128:a='if(between(hypot(X-{r},Y-{r}),{r - 7},{r - 1}),255,0)',"
                 f"setpts=PTS-STARTPTS+{a:.4f}/TB[rg{e}]")
        mx, my = int(W * 0.035), int(H * 0.06)
        ox = f"{mx}" if "left" in fc.get("pos", "bottom-right") else f"W-w-{mx}"; oy = f"{my}" if "top" in fc.get("pos", "") else f"H-h-{my}"
        g.append(f"{cur}[fc{e}]overlay=x={ox}:y={oy}:eof_action=pass:repeatlast=0[fa{e}];[fa{e}][rg{e}]overlay=x={ox}:y={oy}:eof_action=pass:repeatlast=0[fo{e}]")
        cur = f"[fo{e}]"; q += 1
    ev = list(fallback_ev)
    for s in segs:
        a = s["t0"] - t0c; b = a + s["L"]
        if s["type"] == "shot" and s.get("stype") == "card" and s.get("fallback_text") and not (s.get("src") and os.path.exists(asset(s["src"]) or "")):
            ev.append((a, b, "Chapter", s["fallback_text"], r"{\fad(150,200)}"))   # doc card not rendered yet: its headline on the plate
        if s["type"] == "title":
            ti = plan.get("title") or {}
            ev.append((a + 0.05, a + TITLE_HOLD + TITLE_FADE * 0.5, "Title", ti.get("text", plan["episode"].upper()), r"{\fad(120,380)\blur3\t(0,250,\blur0)}"))
            if ti.get("sub"): ev.append((a + 0.5, a + TITLE_HOLD + TITLE_FADE * 0.5, "Sub", ti["sub"], r"{\fad(250,380)\pos(%d,%d)}" % (W // 2, int(H * 0.64))))
            continue
        if s["type"] != "shot": continue
        if s.get("tag"): ev.append((a, b, "Tag", s["tag"], ""))
        if s.get("label"): ev.append((a + 0.2, b - 0.05, "Label", s["label"], r"{\fad(150,150)}"))
        sec = plan["sections"].get(s.get("section_id"), {})
        if s.get("section_start") and sec.get("card") and not sec.get("card_mov"):
            ev.append((a, a + 2.6, "Chapter", sec["card"], r"{\fad(300,400)\blur4\t(0,300,\blur0)}"))
    vf_tail = ""
    if ev:
        write_ass(f"{T}/c.ass", W, H, ev)
        fd = os.environ.get("ASM_FONTS"); vf_tail = f"ass={T}/c.ass:shaping=complex" + (f":fontsdir={fd}" if fd else "")
    g.append(f"{cur}{(vf_tail + ',') if vf_tail else ''}trim=end_frame={Fend - F0},format=yuv420p[v]")
    open(f"{T}/ov.txt", "w", encoding="utf-8").write(";\n".join(g))
    out = f"chunks/c{int(k):03d}.mp4"
    sh([FF, "-y", "-loglevel", "error"] + ins + ["-filter_complex_script", f"{T}/ov.txt", "-map", "[v]", "-frames:v", str(Fend - F0)] + ENC + [out])
    print("chunk", k, "frames", Fend - F0, "->", out, round(probe_len(out), 3), "s", flush=True)

# ---------------------------------------------------------------- MIX (whole timeline, memory-mapped blocks) --------------------------------------
def _decode(path, raw, ch, extra=None):
    sh([FF, "-y", "-loglevel", "error"] + (extra or []) + ["-i", path, "-f", "f32le", "-ac", str(ch), "-ar", str(SR), raw])
    return np.memmap(raw, dtype=np.float32, mode="r").reshape(-1, ch) if ch > 1 else np.memmap(raw, dtype=np.float32, mode="r")

def _lufs(path):
    o = subprocess.run([FF, "-hide_banner", "-i", path, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(re.findall(r"\{[^{}]*\}", o)[-1]); return m

def cmd_mix(plan_json, T="work_mix", duck_db=-5.0):
    """voice two-pass loudnorm -14 LUFS placed on the master timeline (gaps inserted); one music bed per section at -24 LUFS (bed alone = voice
    -10 dB), 1.5 s crossfades, voice-activity ducking duck_db (attack 0.6 / release 0.1 per 50 ms, -42 dB gate, as episode_assemble.py);
    SFX from the plan; silence windows mute music+SFX (+ voice in the title/chapter black); look-ahead limiter 0.89. Block-wise, ~100 MB RAM."""
    plan = json.load(open(plan_json, encoding="utf-8")); A = plan["audio"]; D = plan["D"]; n = int(round(D * SR)); os.makedirs(T, exist_ok=True)
    vsrc = asset(A["voice"]); m = _lufs(vsrc)
    sh([FF, "-y", "-loglevel", "error", "-i", vsrc, "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}", "-ac", "1", f"{T}/voice_n.wav"])
    vraw = _decode(f"{T}/voice_n.wav", f"{T}/voice.f32", 1)
    V = np.memmap(f"{T}/vm.f32", dtype=np.float32, mode="w+", shape=(n,))
    for src0, src1, dst0 in A["vmap"]:
        i0 = int(round(src0 * SR)); i1 = len(vraw) if src1 is None else min(len(vraw), int(round(src1 * SR))); d0 = int(round(dst0 * SR))
        L = max(0, min(i1 - i0, n - d0))
        if L: seg = np.array(vraw[i0:i0 + L]); r = min(L, 240); seg[:r] *= np.linspace(0, 1, r); seg[-r:] *= np.linspace(1, 0, r); V[d0:d0 + L] = seg
    V.flush()
    hop = int(0.05 * SR); nh = n // hop + 1; act = np.zeros(nh, np.float32)
    for h in range(nh):
        x = V[h * hop:(h + 1) * hop]
        if len(x): act[h] = 1.0 if 20 * np.log10(np.sqrt(np.mean(np.square(x)) + 1e-12)) > -42 else 0.0
    sm = np.zeros_like(act); s_ = 0.0
    for q, a_ in enumerate(act): s_ = s_ + (a_ - s_) * (0.6 if a_ > s_ else 0.1); sm[q] = s_
    duck_h = (10 ** (duck_db * sm / 20)).astype(np.float32)
    # gates: (s0, s1) mutes music + SFX + voice; (s0, s1, "bed") mutes the bed only (title: the boom tail rings, the teaser bed stops)
    gate_all = np.ones(nh, np.float32); gate_bed = np.ones(nh, np.float32)
    for w in A["silences"]:
        h0, h1 = int(w[0] * SR / hop), int(math.ceil(w[1] * SR / hop))
        if len(w) > 2 and w[2] == "bed": gate_bed[max(0, h0):h1] = 0
        else: gate_all[max(0, h0):h1] = 0
    beds = []
    for q, b in enumerate(A["music"]):
        p = asset(b["src"]); dur = b["t1"] - b["t0"] + 1.5
        g = b["lufs"] - float(_lufs(p)["input_i"])
        raw = _decode(p, f"{T}/bed{q}.f32", 2, ["-stream_loop", "-1", "-t", f"{dur:.3f}"])
        beds.append((int(round(b["t0"] * SR)), raw, np.float32(10 ** (g / 20))))
    man = sfx_manifest(); cache = {}
    def sfx(nm):
        if nm not in cache:
            p = os.path.join(ASSETS, "sfx", nm + ".wav")
            if not os.path.exists(p): p = f"E:/mb_builds/shorts_v1/kit/sfx/{nm}.wav"
            cache[nm] = np.array(_decode(p, f"{T}/sfx_{nm}.f32", 2))
        return cache[nm]
    OUT = np.memmap(f"{T}/mix.f32", dtype=np.float32, mode="w+", shape=(n, 2)); B = SR * 30; xf = int(1.5 * SR)
    for b0 in range(0, n, B):
        b1 = min(n, b0 + B); L = b1 - b0; hh = (np.arange(b0, b1) // hop).clip(0, nh - 1)
        bus = np.zeros((L, 2), np.float32)
        for st, raw, g in beds:
            a = max(b0, st); e = min(b1, st + len(raw))
            if e <= a: continue
            seg = np.array(raw[a - st:e - st]) * g; idx = np.arange(a, e) - st
            env = np.minimum(1.0, np.minimum(idx / xf, (len(raw) - idx) / xf)).astype(np.float32)   # 1.5 s crossfades between section beds
            bus[a - b0:e - b0] += seg * env[:, None]
        bus *= (duck_h[hh] * gate_bed[hh] * gate_all[hh])[:, None]
        fx = np.zeros((L, 2), np.float32)
        for st, nm, gn in A["sfx"]:
            i0 = int(round(st * SR))
            if i0 >= b1: continue
            y = sfx(nm)
            if i0 + len(y) <= b0: continue
            a = max(b0, i0); e = min(b1, i0 + len(y)); fx[a - b0:e - b0] += y[a - i0:e - i0] * np.float32(gn)
        fx *= gate_all[hh][:, None]
        v = np.array(V[b0:b1]) * gate_all[hh] * np.float32(0.70711)   # mono -14 LUFS on both channels = -14 LUFS stereo (not -11)
        OUT[b0:b1] = v[:, None] + bus + fx
    tail = int(0.35 * SR); OUT[-tail:] *= np.linspace(1, 0, tail, dtype=np.float32)[:, None]; OUT.flush(); del OUT
    o = subprocess.run([FF, "-hide_banner", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", f"{T}/mix.f32", "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    li0 = float((re.findall(r"I:\s+(-?[\d.]+) LUFS", o) or ["-14"])[-1]); gain = max(-3.0, min(3.0, -14.0 - li0))   # whole mix to -14 LUFS (small trim)
    sh([FF, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", f"{T}/mix.f32", "-af",
        f"volume={gain:.2f}dB,alimiter=limit=0.89:attack=5:release=60:level=disabled:latency=1", "-c:a", "pcm_s16le", "mix.wav"])
    li = _lufs("mix.wav"); print("mix.wav", round(D, 1), "s | integrated", li["input_i"], "LUFS | true peak", li["input_tp"], "| beds", len(beds), "| sfx", len(A["sfx"]))

# ---------------------------------------------------------------- CONCAT ------------------------------------------------------------------
def cmd_concat(plan_json):
    plan = json.load(open(plan_json, encoding="utf-8")); ep = plan["episode"]
    with open("chunks/list.txt", "w") as f:
        for c in plan["chunks"]: f.write(f"file 'c{c['k']:03d}.mp4'\n")
    sh([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "chunks/list.txt", "-i", "mix.wav", "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-t", f"{plan['D']:.3f}", "-movflags", "+faststart", f"{ep}_master.mp4"])
    sh([FF, "-y", "-loglevel", "error", "-i", f"{ep}_master.mp4", "-vf", "scale=-2:720", "-c:v", "libx264", "-crf", "24", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", f"{ep}_master_720.mp4"])
    d = probe_len(f"{ep}_master.mp4"); print("master", f"{ep}_master.mp4", round(d, 2), "s (plan", plan["D"], ")")
    if abs(d - plan["D"]) > 0.2: print("WARNING: master length differs from plan by", round(d - plan["D"], 2), "s")

# ---------------------------------------------------------------- FIXTURE from the script (test input; the real shots.json comes from the planner) --
def cmd_fixture(md, out=None, clip_prefix="s2work/shorts1/clips/ic814/"):
    txt = open(md, encoding="utf-8").read().split("# PART 2")[0]
    sections, paras, shots = [], [], []
    for sm in re.finditer(r"^## (S\d+)\. (.+?)$(.*?)(?=^## S\d+\.|\Z)", txt, re.M | re.S):
        sid, title, body = sm.group(1), sm.group(2).strip(), sm.group(3)
        face = "[FACE]" in title; title_c = re.sub(r"\[.*?\]", "", title).strip()
        kind = "teaser" if sid == "S0" else ("intro" if sid in ("S1", "S2") else "chapter")   # Nitish: title -> presenter + question stack -> dated restart
        sections.append({"id": sid, "kind": kind, "title": title_c, "face": face, "card": title_c if re.match(r"DAY|DAYS|AFTERMATH|CLOSING", title_c) else None})
        for pm_ in re.finditer(r"^\*\*(P\d+)\*\*(.*?)(?=^\*\*P\d+\*\*|\Z)", body, re.M | re.S):
            pid, pb = pm_.group(1), pm_.group(2)
            spoken = " ".join(l[1:].strip() for l in pb.splitlines() if l.startswith(">"))
            pause = 2.0 if "[PAUSE" in spoken else 0.0
            spoken_c = re.sub(r"\[PAUSE[^\]]*\]", "", spoken).strip()
            vis = " ".join(l[7:].strip() for l in pb.splitlines() if l.startswith("VISUAL:"))
            snd = " ".join(l[6:].strip() for l in pb.splitlines() if l.startswith("SOUND:"))
            onscr = " ".join(l[10:].strip() for l in pb.splitlines() if l.startswith("ON-SCREEN:"))
            low = (vis + " " + snd).lower()
            beat = "teaser" if kind == "teaser" else ("ending" if sid in ("S12",) else "reveal" if (pause or "braam" in low or "silence" in low) else
                                                     "action" if re.search(r"commando|gunfire|stab|grenade|attack|take-off roar|storm", low) else "body")
            paras.append({"id": pid, "section": sid, "beat": beat, "text": spoken_c, "pause_before": pause, "face": face and sid != "S12" or None})
            n = max(1, round(syl(spoken_c, "te") / PACE["te"][2] / (2.8 if kind == "teaser" else 4.3)))   # creators: 2.8 s teaser / 4.3 s body shots
            tagm = re.search(r"`([^`]+)`", onscr)
            for q in range(n):
                tr = "GLITCH" if kind == "teaser" and q == 0 else ("LEAK" if "1999" in onscr and q == 0 and beat == "body" else "CUT")
                shots.append({"id": f"ic814_{pid.lower()}_{q + 1}", "para": pid, "type": "meta", "src": f"{clip_prefix}ic814_{pid.lower()}_{q + 1}.mp4",
                              "transition": tr, "detail": vis[:220], "label": (tagm.group(1) if tagm and q == 0 and tagm.group(1) != "COMING UP" else None),
                              "tag": "AI చిత్రణ" if "AI" in vis else None, "silence": 1.2 if (pause and q == 0) else 0})
    J = {"episode": "ic814", "lang": "te", "title": {"text": "IC-814", "sub": "KANDAHAR · 7 DAYS"}, "sections": sections, "paragraphs": paras, "shots": shots,
         "music": {}, "notes": "TEST FIXTURE parsed from ic814_te.md by assemble_episode.py fixture; NOT the planner's shots.json"}
    out = out or os.path.join(HERE, "test", "ic814_fixture_shots.json"); os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(J, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("fixture", out, "| sections", len(sections), "| paragraphs", len(paras), "| shots", len(shots))

def cmd_notes(shots_json, timing_json=None, margin=0.6):
    """required clip seconds per shot = its slot (from TTS timing, or est. syllables / target pace) + 0.6 s handle (xfade overlap + push)."""
    J = load_shots(shots_json); T = para_targets(J); tim = {}
    if timing_json and os.path.exists(timing_json): tim = {x["id"]: x["end"] - x["start"] for x in json.load(open(timing_json))["paras"]}
    rows = []
    for p in J["paragraphs"]:
        sh_ = [s for s in J["shots"] if s["para"] == p["id"]]
        if not sh_: continue
        sec = tim.get(p["id"]) or T[p["id"]]["est_seconds"] or 4.0
        sec += T[p["id"]]["pause_total_s"] if p["id"] not in tim else 0
        wsum = sum(float(s.get("weight") or 1) for s in sh_)
        for s in sh_:
            need = sec * float(s.get("weight") or 1) / wsum + margin; have = s.get("dur")
            rows.append((s.get("id"), p["id"], p["beat"], round(need, 1), have, "ASK META: " + str(math.ceil(need)) + " s" if (have is None or float(have) < need) else "ok"))
    print("| shot | para | beat | needs s | planned dur | action |\n|---|---|---|---|---|---|")
    for r in rows: print("| " + " | ".join(str(x) for x in r) + " |")
    return rows

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__ or open(__file__, encoding="utf-8").read().split("import json")[0]); sys.exit(1)
    c = a[0]
    opt = {x.split("=")[0].lstrip("-"): x.split("=", 1)[1] for x in a if x.startswith("--") and "=" in x}; pos = [x for x in a[1:] if not x.startswith("--")]
    if c == "targets": cmd_targets(pos[0])
    elif c == "pace": cmd_pace(pos[0], pos[1], pos[2])
    elif c == "plan": cmd_plan(pos[0], pos[1], opt.get("size", "1920x1080"), opt.get("meta-avail", ""), opt.get("out"))
    elif c == "chunk": cmd_chunk(pos[0], pos[1])
    elif c == "mix": cmd_mix(pos[0])
    elif c == "concat": cmd_concat(pos[0])
    elif c == "fixture": cmd_fixture(pos[0], opt.get("out"))
    elif c == "notes": cmd_notes(pos[0], pos[1] if len(pos) > 1 else None)
    else: sys.exit("unknown command " + c)
