# Adapter: the IC-814 planner's shots.json (E:/mb_builds/shorts_v1/longform/ic814/build_shots.py) -> the assembler's input schema.
# Read-only on the planner's files. Paragraph text / beat / [PAUSE] / [FACE] come from the script named in shots.json ("script").
#   planner shot: id, para (int|null), section, start_est, dur, type (meta_clip|face_circle|text_card|doc_card|map|chapter_stop|title_drop),
#                 mood, transition, sfx (free text), text, meta_id, reuse_of, map_id, card, plate, overlays [card ids], ai_tag, face, note
import json, os, re

CLIPS = "s2work/shorts1/clips/ic814/"           # Meta clips land here as <meta_id>_1.mp4 (checked in R2, 24 Nov)
MAPS = "s2work/cinekit/maps/"                   # <map_id>_16x9.mp4 (cinekit_map.yml)
CARDS = "s2work/cinekit/cards/"                 # convention for rendered cards: <card>.mov (kinetic_text, alpha) / <card>_16x9.mp4 (doc_highlight)
# planner sound words -> our 39 SFX (kit/sfx/manifest.json). Whooshes are left to the transition rules (else they double up).
SFX_MAP = [(r"glitch", "glitch"), (r"braam|low hit|logo hit", "braam"), (r"stamp|thud|dull", "impact_drum"), (r"heartbeat", "heartbeat"),
           (r"paper slide|paper", "paper_slide"), (r"marker", "marker"), (r"radio", "radio_squelch"), (r"alarm|chime", "alarm"),
           (r"shutter|camera", "shutter"), (r"riser", "riser_short"), (r"tick|clock|counter|stopwatch", "tick"), (r"click|pop|latch", "click"),
           (r"rewind", "rewind"), (r"typewriter", "tick")]
AMBIENCE = r"drone|wind|engine|footsteps|ring|cello|strings|chatter|murmur|hum|crowd|rain|ambience"   # not in our SFX set (music bed / to add)

def sfx_of(txt):
    t = (txt or "").lower(); out, sil = [], 0.0
    for pat, nm in SFX_MAP:
        if re.search(pat, t) and nm not in out: out.append(nm)
    m = re.search(r"silence\s*([\d.]+)?\s*s?", t)
    if "silence" in t: sil = float(m.group(1)) if m and m.group(1) else 1.2
    amb = re.findall(AMBIENCE, t)
    return out[:2], sil, amb

def parse_script(md):
    txt = open(md, encoding="utf-8").read().split("# PART 2")[0]; secs, paras = [], []
    for sm in re.finditer(r"^## (S\d+)\. (.+?)$(.*?)(?=^## S\d+\.|\Z)", txt, re.M | re.S):
        sid, title, body = sm.group(1), sm.group(2).strip(), sm.group(3)
        kind = "teaser" if sid == "S0" else ("intro" if sid in ("S1", "S2") else "chapter")
        secs.append({"id": sid, "kind": kind, "title": re.sub(r"\[.*?\]", "", title).strip(), "face": "[FACE]" in title})
        for pm in re.finditer(r"^\*\*(P\d+)\*\*(.*?)(?=^\*\*P\d+\*\*|\Z)", body, re.M | re.S):
            pid, pb = pm.group(1), pm.group(2)
            spoken = " ".join(l[1:].strip() for l in pb.splitlines() if l.startswith(">"))
            snd = " ".join(l[6:].strip() for l in pb.splitlines() if l.startswith("SOUND:")).lower()
            pause = 2.0 if "[PAUSE" in spoken else 0.0
            beat = ("teaser" if kind == "teaser" else "ending" if sid == secs[-1]["id"] and sid == "S12" else
                    "reveal" if (pause or "silence" in snd or "braam" in snd) else
                    "action" if re.search(r"commando|gunfire|stab|grenade|attack|storm|scuffle|struggle", snd + " " + pb.lower()) else "body")
            paras.append({"id": pid, "section": sid, "beat": beat, "text": re.sub(r"\[PAUSE[^\]]*\]", "", spoken).strip(), "pause_before": pause})
    return secs, paras

def card_text(card):
    s = (card or {}).get("spec", {})
    for k in ("title", "text", "label", "name", "headline"):
        if s.get(k): return str(s[k])
    if s.get("words"): return " ".join(w.get("text", "") for w in s["words"])
    if "to" in s: return f"{s.get('prefix', '')}{s['to']}{s.get('suffix', '')}"
    return ""

def convert(planner_json, cards_json=None):
    J = json.load(open(planner_json, encoding="utf-8")); base = os.path.dirname(os.path.abspath(planner_json))
    secs, paras = parse_script(os.path.join(base, J.get("script", "")))
    cards = {}
    cj = cards_json or os.path.join(base, "cards.json")
    if os.path.exists(cj): cards = {c["id"]: c for c in json.load(open(cj, encoding="utf-8"))["cards"]}
    S = J["shots"]; meta_of = {s["id"]: s.get("meta_id") for s in S}
    out, title, ambience, face_paras = [], {}, set(), set()
    sec_card = {}
    for i, s in enumerate(S):
        t = s["type"]; pid = f"P{s['para']}" if s.get("para") is not None else None
        if t == "title_drop":
            parts = [x.strip() for x in (s.get("text") or "").replace("TITLE DROP:", "").split("|")]
            title = {"text": parts[0] if parts else "", "sub": " · ".join(parts[1:])}; continue
        if t == "chapter_stop":
            nxt = next((x for x in S[i + 1:] if x.get("section")), None)
            if nxt: sec_card[nxt["section"]] = {"card": (s.get("text") or "").replace("CHAPTER:", "").strip(), "card_id": s.get("card")}
            continue
        if pid is None: continue                                   # the title's black gap: the assembler makes its own
        sfx, sil, amb = sfx_of(s.get("sfx")); ambience.update(amb)
        d = {"id": s["id"], "para": pid, "weight": float(s.get("dur") or 1), "mood": s.get("mood"), "transition": s.get("transition") or "CUT",
             "tag": (s.get("ai_tag") or "").split(" / ")[0] or None, "sfx": sfx, "planner_type": t, "planned_dur": s.get("dur"),
             "detail": s.get("note") or s.get("text") or ""}
        mid = s.get("meta_id") or meta_of.get(s.get("reuse_of"))
        if (s.get("text") or "").upper().startswith("BLACK"):
            d.update(type="black"); d["tag"] = None
            if i + 1 < len(S): S[i + 1]["_silence"] = float(re.findall(r"[\d.]+", s["text"])[0]) if re.findall(r"[\d.]+", s["text"]) else 1.2
        elif t in ("meta_clip", "face_circle"):
            d.update(type="meta", src=f"{CLIPS}{mid}_1.mp4", meta_id=mid)
            if t == "face_circle": d["face"] = s.get("face") or "bottom-left"; face_paras.add(pid)
        elif t == "map":
            d.update(type="map", src=f"{MAPS}{s['map_id']}_16x9.mp4", mood="none"); d["tag"] = None
        elif t == "doc_card":
            d.update(type="card", src=f"{CARDS}{s['card']}_16x9.mp4", mood="none", fallback_text=card_text(cards.get(s.get("card")))); d["tag"] = None
        elif t == "text_card":
            plate = (s.get("plate") or "").lower()
            d.update(type="plate", plate=("black" if "black" in plate else "red" if "red" in plate else "dark"), tag=None,
                     overlays=[{"src": f"{CARDS}{s['card']}.mov", "at": 0, "dur": float(s.get("dur") or 3), "fallback_text": card_text(cards.get(s.get("card")))}] if s.get("card") else [])
        d["overlays"] = d.get("overlays", []) + [{"src": f"{CARDS}{c}.mov", "at": 0, "dur": float(s.get("dur") or 3), "fallback_text": card_text(cards.get(c))}
                                                 for c in (s.get("overlays") or [])]
        if s.get("_silence") or sil: d["silence"] = s.get("_silence") or sil
        out.append(d)
    for x in secs:
        if x["id"] in sec_card: x.update(card=sec_card[x["id"]]["card"], card_mov=None, card_id=sec_card[x["id"]]["card_id"])
    for p in paras:
        if p["id"] in face_paras: p["face"] = True
    return {"episode": "ic814_te", "lang": "te", "title": title, "sections": secs, "paragraphs": paras, "shots": out, "music": {},
            "face_from_shots": True, "ambience_wanted": sorted(ambience), "source": planner_json}

if __name__ == "__main__":
    import sys
    R = convert(sys.argv[1]); o = sys.argv[2] if len(sys.argv) > 2 else "shots_asm.json"
    json.dump(R, open(o, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("converted", len(R["shots"]), "shots,", len(R["paragraphs"]), "paragraphs ->", o, "| ambience words not in SFX set:", R["ambience_wanted"])
