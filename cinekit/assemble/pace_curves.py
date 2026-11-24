# Measure narration pace (syllables per second per ~30 s subtitle window) for each creator folder in research/creator_subs.
# Syllable = akshara for Indic scripts (consonants + independent vowels - viramas), vowel groups for Latin words.
# Usage: python pace_curves.py [channel ...]   -> prints JSON per channel (and writes pace_curves.json next to this file)
import json, os, re, statistics as st, sys

SUBS = "E:/mb_builds/shorts_v1/research/creator_subs"
BASES = (0x0900, 0x0980, 0x0B80, 0x0C00)          # Devanagari, Bengali, Tamil, Telugu (same layout inside each block)
TS = re.compile(r"^\[(\d+):(\d\d)(?::(\d\d))?\]\s*(.*)")

def syl(text):
    text = re.sub(r"\[[^\]]*\]", " ", text)        # [Music], [Applause]
    n = 0
    for ch in text:
        c = ord(ch)
        for b in BASES:
            o = c - b
            if 0 <= o < 0x80:
                if 0x15 <= o <= 0x39 or 0x58 <= o <= 0x5F: n += 1          # consonant
                elif 0x04 <= o <= 0x14 or o in (0x60, 0x61): n += 1        # independent vowel
                elif o == 0x4D: n -= 1                                      # virama / pulli / hasant
                break
    for w in re.findall(r"[A-Za-z]+", text):
        n += max(1, len(re.findall(r"[aeiouy]+", w.lower())) - (1 if w.lower().endswith("e") and len(w) > 3 else 0))
    for num in re.findall(r"\d+", text):            # numbers are spoken: ~1.5 syllables per digit
        n += int(round(1.5 * len(num)))
    return max(0, n)

def windows(path):
    blocks, cur = [], None
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = TS.match(line.strip())
        if m:
            a, b, c = m.group(1), m.group(2), m.group(3)
            t = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
            cur = [t, m.group(4)]; blocks.append(cur)
        elif cur is not None and not line.startswith("#"):
            cur[1] += " " + line.strip()
    dur = None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.search(r"duration:\s*(\d+)s", line)
            if m: dur = int(m.group(1)); break
    out = []
    for i, (t, txt) in enumerate(blocks):
        t1 = blocks[i + 1][0] if i + 1 < len(blocks) else (dur or t + 30)
        if t1 - t < 8: continue
        out.append((t, t1, syl(txt) / (t1 - t)))
    return out, dur

def pct(x, p):
    x = sorted(x); return x[min(len(x) - 1, max(0, int(round(p * (len(x) - 1)))))] if x else None

def channel(ch):
    teaser, body, endw, first3 = [], [], [], []
    vids = 0; vmin = []; ramp = [[], [], []]
    for fn in sorted(os.listdir(f"{SUBS}/{ch}")):
        if not fn.endswith(".txt") or fn[:8] < os.environ.get("MINDATE", "0"): continue
        w, dur = windows(f"{SUBS}/{ch}/{fn}")
        if len(w) < 10: continue
        vids += 1
        D = w[-1][1]
        vb = [r for t0, t1, r in w if 60 <= t0 < D - 75 and r >= 1.0]
        if vb: vmin.append(min(vb))
        for q in range(3):
            if q < len(w) and w[q][2] >= 1.0: ramp[q].append(w[q][2])
        for t0, t1, r in w:
            if r < 1.0: continue                                     # pure music / silence window
            if t0 < 60: teaser.append(r)
            elif t0 >= D - 75: endw.append(r)
            else: body.append(r)
        first3.append([round(r, 2) for _, _, r in w[:3]])
    R = lambda v: round(v, 2) if v is not None else None
    return {"videos": vids, "teaser_median": R(st.median(teaser)) if teaser else None,
            "teaser_p25_p75": [R(pct(teaser, .25)), R(pct(teaser, .75))],
            "body_median": R(st.median(body)), "body_p25_p75": [R(pct(body, .25)), R(pct(body, .75))],
            "reveal_dip_p10_p20": [R(pct(body, .10)), R(pct(body, .20))],
            "reveal_dip_p5": R(pct(body, .05)), "reveal_dip_median_of_video_min": R(st.median(vmin)) if vmin else None,
            "teaser_ramp_w1_w2_w3": [R(st.median(x)) if x else None for x in ramp],
            "action_p90_p95": [R(pct(body, .90)), R(pct(body, .95))],
            "ending_median": R(st.median(endw)) if endw else None,
            "first3_windows_examples": first3[:4]}

if __name__ == "__main__":
    chs = sys.argv[1:] or ["nitish", "dhruv", "te_vrraja", "ta_madangowri", "bn_romanchopedia", "en_thinkschool"]
    res = {c: channel(c) for c in chs}
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pace_curves.json"), "w"), indent=1)
    print(json.dumps(res, indent=1, ensure_ascii=False))

