"""timeline.py - on-screen TIMELINE like Nitish Rajput's (cinekit, 25 Nov 2026).

His look (Flydubai 1:22, 2:15, 6:16, 13:21 in research/NITISH_TRANSITIONS_FRAME_ACCURATE.md section 4): a horizontal timeline along
the bottom over the presenter / B-roll; the line draws on, dated markers pop as the line reaches them, a marker slides along and the
year labels pop; the event being talked about is highlighted.

  python timeline.py spec.json --out out/name [--sizes 1920x1080,1080x1920] [--plate img|clip]
-> out/name_16x9.mp4 / _9x16.mp4 (preview over a plate) + out/name_<fmt>.mov (alpha, qtrle) + out/name.sfx.json

spec.json:
 {"events": [{"date": "24 DEC 1999", "label": "IC-814 hijacked"}, {"date": "25 DEC", "label": "Amritsar, 45 min"}, ...],
  "current": 2,               # index highlighted at the end (the beat the narration is on); null = no highlight
  "title": "IC-814 | 8 DAYS",  # optional small header above the line
  "dur": 6.0, "fps": 30, "accent": "#ffc928", "past": "#ffffff", "future": "#7c8694",
  "position": "bottom" | "center"}   # vertical (9:16) frames always run the timeline top->bottom on the left third
Animation: 0-35 % line draws on with a glowing head; each marker pops (scale 0 -> 1.18 -> 1, tick) when the head reaches it and
its date + label slide in; the "now" slider glides from the first event to `current`, the current marker grows with an expanding
ring pulse and its label card turns accent-coloured; later events stay dimmed/dashed (the future); a slow drift keeps it alive.
Text: Pillow with libraqm (complex shaping for Telugu/Hindi/Tamil/Bengali); refuses non-Latin text when raqm is missing.
"""
import argparse, json, math, os, re, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

FF = os.environ.get("FFMPEG", "ffmpeg")
NOTO = "/usr/share/fonts/truetype/noto"
FONTS = {"latin": "NotoSans-Black.ttf", "telugu": "NotoSansTelugu-Bold.ttf", "devanagari": "NotoSansDevanagari-Bold.ttf",
         "tamil": "NotoSansTamil-Bold.ttf", "bengali": "NotoSansBengali-Bold.ttf"}
RAQM = features.check("raqm")


def script_of(t):
    for name, lo, hi in (("devanagari", 0x900, 0x97F), ("bengali", 0x980, 0x9FF), ("tamil", 0xB80, 0xBFF), ("telugu", 0xC00, 0xC7F)):
        if any(lo <= ord(c) <= hi for c in t): return name
    return "latin"


_FC = {}


def font(text, size):
    sc = script_of(text)
    if sc != "latin" and not RAQM:
        sys.exit(f"text '{text}' needs complex shaping but Pillow has no libraqm here")
    key = (sc, size)
    if key not in _FC:
        cands = [os.path.join(NOTO, FONTS[sc]), os.path.join(NOTO, FONTS[sc].replace("Black", "Bold")),
                 os.environ.get("TL_FONT", ""), "C:/Windows/Fonts/arialbd.ttf"]
        p = next((c for c in cands if c and os.path.exists(c)), None)
        kw = {"layout_engine": ImageFont.Layout.RAQM} if RAQM else {}
        _FC[key] = ImageFont.truetype(p, size, **kw) if p else ImageFont.load_default()
    return _FC[key]


def hexc(h, a=255):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def ease_out(u, p=3): u = min(1, max(0, u)); return 1 - (1 - u) ** p
def ease_io(u): u = min(1, max(0, u)); return u * u * (3 - 2 * u)


def pop(u):
    """0 -> 1.18 -> 1 overshoot over u in [0,1]"""
    if u <= 0: return 0.0
    if u >= 1: return 1.0
    return 1.18 * ease_out(u / 0.6) if u < 0.6 else 1.18 - 0.18 * ease_io((u - 0.6) / 0.4)


def text_img(t, size, fill, stroke=0, stroke_fill=(0, 0, 0, 255)):
    f = font(t, size); bb = f.getbbox(t, stroke_width=stroke)
    im = Image.new("RGBA", (bb[2] - bb[0] + 4, bb[3] - bb[1] + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2 - bb[0], 2 - bb[1]), t, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def frame(S, W, H, t, vertical):
    """one RGBA frame of the timeline overlay at time t"""
    sc = W / 1920 if not vertical else H / 1920 * 1.55   # phone: bigger type on 9:16
    ev = S["events"]; n = len(ev); cur = S.get("current"); dur = S["dur"]
    acc, past, fut = hexc(S.get("accent", "#ffc928")), hexc(S.get("past", "#ffffff")), hexc(S.get("future", "#7c8694"), 200)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    # geometry: line from P0 to P1, event k at fraction fk
    if vertical:
        x = int(W * 0.18); y0, y1 = int(H * 0.16), int(H * 0.86); P = lambda f: (x, y0 + (y1 - y0) * f)
    else:
        y = int(H * (0.83 if S.get("position", "bottom") == "bottom" else 0.55)); x0, x1 = int(W * 0.08), int(W * 0.92)
        P = lambda f: (x0 + (x1 - x0) * f, y)
    fk = [(k + 0.5) / n for k in range(n)]
    drift = 0.004 * math.sin(t * 0.9)
    T_DRAW = 0.35 * dur; head = ease_io(t / T_DRAW) * 1.0                 # line draw-on
    cur_f = fk[cur] if cur is not None else None
    lw = max(3, int(6 * sc))
    # base track (future, dim) then drawn part
    a, b = P(0), P(head)
    d.line([P(0), P(1)], fill=fut[:3] + (60,), width=lw)
    split = min(head, cur_f + 0.5 / n) if cur_f is not None else head    # beyond the current event = the future: dashed, dim
    d.line([a, P(split)], fill=past, width=lw)
    if head > split:
        k = 0; seg = 0.012
        while split + k * seg * 2 < head:
            d.line([P(split + k * seg * 2), P(min(head, split + k * seg * 2 + seg))], fill=fut, width=lw); k += 1
    # glow on the drawn part + head
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gd = ImageDraw.Draw(glow)
    gd.line([a, P(split)], fill=acc[:3] + (150,), width=lw * 4)
    if head < 1: gd.ellipse([b[0] - 18 * sc, b[1] - 18 * sc, b[0] + 18 * sc, b[1] + 18 * sc], fill=acc[:3] + (230,))
    im = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10 * sc)), im); d = ImageDraw.Draw(im)
    # "now" slider: after the draw-on, glides from the first event to the current one
    if cur is not None:
        t_s0, t_s1 = T_DRAW, T_DRAW + 0.25 * dur
        sf = fk[0] + (cur_f - fk[0]) * ease_io((t - t_s0) / (t_s1 - t_s0)) if t > t_s0 else fk[0]
        d.line([P(0), P(min(sf, head))], fill=acc, width=lw + 2)
    t_hl = T_DRAW + 0.25 * dur                                            # moment the current event is highlighted
    for k, e in enumerate(ev):
        tk = fk[k] * T_DRAW                                               # head reaches it
        u = (t - tk) / 0.35
        if u <= 0: continue
        s = pop(u); is_cur = cur is not None and k == cur and t >= t_hl
        is_future = cur is not None and k > cur
        cx, cy = P(fk[k]); r = (13 if not is_cur else 20) * sc * s
        if is_cur:   # ring pulse every 1.2 s after the highlight
            for q in range(2):
                ph = ((t - t_hl) / 1.2 + q * 0.5) % 1.0
                rr = r + 70 * sc * ph; al = int(200 * (1 - ph))
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=acc[:3] + (al,), width=max(2, int(4 * sc)))
        col = acc if is_cur else (fut if is_future else past)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(15, 15, 18, 255), outline=col, width=max(3, int(5 * sc)))
        if is_cur: d.ellipse([cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5], fill=acc)
        # labels slide in (date above, text below; vertical: to the right)
        v = ease_out((t - tk - 0.08) / 0.4)
        if v <= 0: continue
        dsz = int((40 if is_cur else 32) * sc); lsz = int((34 if is_cur else 28) * sc)
        dt = text_img(e["date"], dsz, acc if (is_cur or not is_future) else fut, stroke=max(1, int(3 * sc)))
        lt = text_img(e["label"], lsz, (20, 20, 20, 255) if is_cur else (col[:3] + (255,)), stroke=0 if is_cur else max(1, int(3 * sc)))
        al = v
        if vertical:
            ox = int(cx + 40 * sc + (1 - v) * 40 * sc)
            im.alpha_composite(fade(dt, al), (ox, int(cy - dt.height - 4 * sc)))
            lx, ly = ox, int(cy + 6 * sc)
        else:
            im.alpha_composite(fade(dt, al), (int(cx - dt.width / 2), int(cy - 34 * sc - dt.height + (1 - v) * 20 * sc)))
            lx, ly = int(cx - lt.width / 2), int(cy + 30 * sc - (1 - v) * 20 * sc)
        if is_cur:   # accent card behind the current label
            pad = int(10 * sc); card = Image.new("RGBA", (lt.width + 2 * pad, lt.height + 2 * pad), acc[:3] + (int(240 * al),))
            im.alpha_composite(card, (lx - pad, ly - pad))
        im.alpha_composite(fade(lt, al), (lx, ly))
    if S.get("title"):
        tt = text_img(S["title"], int(30 * sc), (255, 255, 255, 230), stroke=max(1, int(3 * sc)))
        v = ease_out(t / 0.5); tx, ty = (P(0)[0], P(0)[1] - 120 * sc) if not vertical else (P(0)[0] - 10 * sc, P(0)[1] - 90 * sc)
        im.alpha_composite(fade(tt, v), (int(tx), int(ty)))
    if drift and not vertical:   # tiny horizontal drift: never frozen
        im = im.transform(im.size, Image.AFFINE, (1, 0, -drift * W, 0, 1, 0), resample=Image.BILINEAR)
    return im


def fade(img, a):
    if a >= 1: return img
    x = np.asarray(img).copy(); x[..., 3] = (x[..., 3] * max(0, a)).astype(np.uint8); return Image.fromarray(x)


def plate(path, W, H, n, fps):
    if path and os.path.exists(path):
        vf = f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgb24"
        args = ["-loop", "1"] if path.lower().endswith((".jpg", ".png", ".jpeg")) else ["-stream_loop", "-1"]
        raw = subprocess.run([FF, "-v", "error"] + args + ["-i", path, "-vf", vf, "-frames:v", str(n), "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    g = (np.array([18, 22, 30], np.float32) * (1 - yy) + np.array([6, 6, 10], np.float32) * yy)
    return np.repeat(np.broadcast_to(g, (H, W, 3))[None].astype(np.uint8), 1, 0)


def render(S, out, W, H, plate_path=None):
    fps = S.get("fps", 30); n = int(round(S["dur"] * fps)); vertical = H > W
    tag = "9x16" if vertical else "16x9"
    P = plate(plate_path, W, H, n, fps)
    mov = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                            "-c:v", "qtrle", f"{out}_{tag}.mov"], stdin=subprocess.PIPE)
    mp4 = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            f"{out}_{tag}.mp4"], stdin=subprocess.PIPE)
    for i in range(n):
        ov = np.asarray(frame(S, W, H, i / fps, vertical)); mov.stdin.write(ov.tobytes())
        bg = P[min(i, len(P) - 1)].astype(np.float32)
        if plate_path: bg *= 0.72                                         # dim the plate a little under the graphic
        a = ov[..., 3:4].astype(np.float32) / 255
        mp4.stdin.write((bg * (1 - a) + ov[..., :3] * a).clip(0, 255).astype(np.uint8).tobytes())
    for p in (mov, mp4): p.stdin.close(); p.wait()
    # SFX hook: whoosh with the draw-on, a tick per marker, a pop + soft hit on the highlight
    dur = S["dur"]; n_ev = len(S["events"]); T_DRAW = 0.35 * dur
    sfx = [{"t": 0.0, "type": "whoosh"}] + [{"t": round((k + 0.5) / n_ev * T_DRAW, 3), "type": "tick"} for k in range(n_ev)]
    if S.get("current") is not None: sfx.append({"t": round(T_DRAW + 0.25 * dur, 3), "type": "pop"})
    Path(f"{out}.sfx.json").write_text(json.dumps(sfx, indent=1))
    print("TIMELINE", f"{out}_{tag}.mp4", n, "frames", "raqm" if RAQM else "no-raqm", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec"); ap.add_argument("--out", required=True); ap.add_argument("--sizes", default="1920x1080,1080x1920")
    ap.add_argument("--plate", default=None)
    a = ap.parse_args()
    S = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    for sz in a.sizes.split(","):
        W, H = map(int, sz.split("x")); render(S, a.out, W, H, a.plate)


if __name__ == "__main__":
    main()
