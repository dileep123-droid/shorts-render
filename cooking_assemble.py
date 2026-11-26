# Cooking Shorts assembler (chinnidiaries246), runs on GitHub Actions (cooking_assemble.yml).
# The job JSON is fetched from a one-time presigned link (env JOB_URL); every input/output is a presigned GET/PUT link inside it.
# No storage keys, no credentials, no links are printed. Pillow draws the text cards, ffmpeg does the edit, numpy makes the music bed.
# modes: "layout"  -> card PNGs over plain backgrounds -> sheet JPG (layout check, no clips needed)
#        "sheet"   -> per-clip segments with text -> one frame per text card + raw QA frames -> JPGs (no final video)
#        "full"    -> finished Short + silent copy + sheets + report
import os, sys, json, math, time, subprocess, urllib.request, shutil, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
XF = 0.30                       # cross-dissolve length
SAFE_L, SAFE_R = 56, 950        # right 12% kept clear (YouTube buttons)
SAFE_TOP, SAFE_BOT = 200, 1548  # top 10% / bottom 18% (1574) kept clear
ACCENT = (244, 180, 0); WHITE = (255, 255, 255); SOFT = (242, 234, 222); DARK = (30, 20, 8)
PANEL = (20, 14, 8, 165)
WD = "/tmp/ck"; os.makedirs(WD, exist_ok=True)

def sh(cmd, quiet=True):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if r.returncode != 0:
        print("CMD FAILED:", (cmd if isinstance(cmd, str) else " ".join(cmd))[:300].replace("http", "hxxp"))
        print(r.stderr[-3000:].replace("http", "hxxp")); raise SystemExit(1)
    return r.stdout + r.stderr

def fetch(url, path=None, tries=5):
    for t in range(tries):
        try:
            d = urllib.request.urlopen(url, timeout=300).read()
            if path: open(path, "wb").write(d); return path
            return d
        except Exception as e:
            err = e; time.sleep(4 * (t + 1))
    raise SystemExit(f"fetch failed: {type(err).__name__}")

def put(url, path, ctype):
    data = open(path, "rb").read()
    for t in range(5):
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": ctype}), timeout=600); return
        except Exception as e:
            err = e; time.sleep(5 * (t + 1))
    raise SystemExit(f"put failed: {type(err).__name__}")

# ---------------------------------------------------------------- fonts (Poppins, SIL Open Font Licence, Google Fonts repo)
FD = WD + "/fonts"; os.makedirs(FD, exist_ok=True)
FONTS = {}
def font(style, size):
    k = (style, size)
    if k not in FONTS:
        p = f"{FD}/Poppins-{style}.ttf"
        if not os.path.exists(p):
            fetch(f"https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-{style}.ttf", p)
        FONTS[k] = ImageFont.truetype(p, size)
    return FONTS[k]

def tw(f, s): return f.getlength(s)

def wrap_tokens(tokens, width):
    """tokens: list of (word, font, color). Greedy wrap -> list of lines (list of tokens)."""
    lines, cur, cw = [], [], 0
    for wd, f, c in tokens:
        w = tw(f, wd); sp = tw(f, " ") if cur else 0
        if cur and cw + sp + w > width:
            lines.append(cur); cur, cw = [], 0; sp = 0
        cur.append((wd, f, c)); cw += sp + w
    if cur: lines.append(cur)
    # no widows: a lone last word takes one word from the line above
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        lines[-1].insert(0, lines[-2].pop())
    return lines

def words(text, f, c): return [(w, f, c) for w in text.split()]

def draw_line(d, x, y, line, shadow=True, center_w=None):
    if center_w is not None:
        lw = sum(tw(f, w) for w, f, c in line) + sum(tw(f, " ") for w, f, c in line[1:])
        x = x + (center_w - lw) / 2
    masc = max(f.getmetrics()[0] for w, f, c in line)
    for i, (w, f, c) in enumerate(line):
        if i: x += tw(f, " ")
        yy = y + (masc - f.getmetrics()[0])
        if shadow: d.text((x + 2, yy + 3), w, font=f, fill=(0, 0, 0, 140))
        d.text((x, yy), w, font=f, fill=c + (255,) if len(c) == 3 else c)
        x += tw(f, w)

class Card:
    """Builds a panel card: list of blocks, each = (kind, payload, gap_before). Returns RGBA image + its frame position."""
    def __init__(self, width, pad=42):
        self.width, self.pad, self.blocks = width, pad, []
    def text(self, tokens, lh, gap=0, center=False, indent=0, bullet=None):
        self.blocks.append(("text", (tokens, lh, center, indent, bullet), gap))
    def badge(self, label, gap=0):
        self.blocks.append(("badge", label, gap))
    def rule(self, gap=0):
        self.blocks.append(("rule", None, gap))
    def layout(self):
        inner = self.width - 2 * self.pad; out = []; y = self.pad; nlines = 0
        for kind, p, gap in self.blocks:
            y += gap
            if kind == "text":
                tokens, lh, center, indent, bullet = p
                ls = wrap_tokens(tokens, inner - indent)
                out.append((kind, (ls, lh, center, indent, bullet), y)); y += lh * len(ls); nlines += len(ls)
            elif kind == "badge":
                out.append((kind, p, y)); y += 64
            else:
                out.append((kind, p, y)); y += 4
        return out, y + self.pad - 8, nlines
    def render(self):
        items, hgt, nlines = self.layout()
        M = 40  # shadow margin
        img = Image.new("RGBA", (self.width + 2 * M, hgt + 2 * M), (0, 0, 0, 0))
        sh_ = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh_).rounded_rectangle((M, M + 8, M + self.width, M + hgt + 8), 30, fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img, sh_.filter(ImageFilter.GaussianBlur(16)))
        pn = Image.new("RGBA", img.size, (0, 0, 0, 0)); dp = ImageDraw.Draw(pn)
        dp.rounded_rectangle((M, M, M + self.width, M + hgt), 26, fill=PANEL, outline=(255, 255, 255, 34), width=2)
        img = Image.alpha_composite(img, pn); d = ImageDraw.Draw(img)
        inner = self.width - 2 * self.pad
        for kind, p, y in items:
            x0 = M + self.pad; y0 = M + y
            if kind == "text":
                ls, lh, center, indent, bullet = p
                for i, ln in enumerate(ls):
                    f = ln[0][1]; asc = f.getmetrics()[0]
                    ty = y0 + i * lh + (lh - f.size * 1.0) / 2 - (asc - f.size) * 0.55 - 4
                    if bullet and i == 0:
                        r = 7; cy = y0 + lh / 2
                        d.ellipse((x0 + 6, cy - r, x0 + 6 + 2 * r, cy + r), fill=ACCENT + (255,))
                    draw_line(d, x0 + indent, ty, ln, center_w=(inner if center else None))
            elif kind == "badge":
                f = font("SemiBold", 38); w = tw(f, p) + 2 * 26
                d.rounded_rectangle((x0, y0, x0 + w, y0 + 58), 29, fill=ACCENT + (255,))
                d.text((x0 + 26, y0 + 6), p, font=f, fill=DARK + (255,))
            else:
                d.rounded_rectangle((x0, y0, x0 + 120, y0 + 5), 2, fill=ACCENT + (255,))
        return img, M, nlines

def place_bottom(img, M, x=SAFE_L, bottom=SAFE_BOT - 10):
    return x - M, bottom - (img.size[1] - 2 * M) - M

def step_card(n, title, line):
    c = Card(SAFE_R - SAFE_L)
    c.badge(f"Step {n}")
    c.text(words(title, font("Bold", 60), WHITE), 74, gap=20)
    c.text(words(disp(line), font("Regular", 46), SOFT), 62, gap=8)
    img, M, _ = c.render(); return img, place_bottom(img, M)

def disp(t): return t.replace(" - ", " – ")

def title_card(title, sub):
    c = Card(SAFE_R - SAFE_L, pad=50)
    c.text(words(title, font("Bold", 84), WHITE), 100, center=True)
    c.text(words(disp(sub), font("Medium", 46), ACCENT), 62, gap=18, center=True)
    img, M, _ = c.render()
    return img, (SAFE_L - M, int(1010 - img.size[1] / 2))

def split_item(s):
    if " - " in s:
        a, b = s.rsplit(" - ", 1); return a, b
    return s, ""

def ingr_card(items, page, pages):
    best = None
    for fs in (48,):
        c = Card(SAFE_R - SAFE_L, pad=44)
        hdr = words("Ingredients", font("Bold", 58), WHITE) + ([(f"  {page} of {pages}", font("Medium", 36), (220, 205, 185))] if pages > 1 else [])
        c.text(hdr, 76)
        c.rule(gap=6)
        lh = int(fs * 1.32)
        for i, it in enumerate(items):
            a, b = split_item(it)
            tok = words(a, font("Regular", fs), WHITE) + ([("– " + b.replace(" ", " "), font("SemiBold", fs), ACCENT)] if b else [])
            c.text(tok, lh, gap=(20 if i == 0 else 8), indent=40, bullet=True)
        img, M, nl = c.render()
        best = (img, M, nl, fs)
        if nl - 1 <= 9: break
    img, M, nl, fs = best
    return img, place_bottom(img, M), nl - 1, fs

def item_lines(it, fs=48):
    a, b = split_item(it); inner = SAFE_R - SAFE_L - 2 * 44 - 40
    tok = words(a, font("Regular", fs), WHITE) + ([("– " + b.replace(" ", " "), font("SemiBold", fs), ACCENT)] if b else [])
    return len(wrap_tokens(tok, inner))

def balance_pages(pages, maxl=9):
    if all(sum(item_lines(i) for i in pg) <= maxl + 1 for pg in pages): return pages
    items = [i for pg in pages for i in pg]; ln = [item_lines(i) for i in items]
    for n in (len(pages), len(pages) + 1, len(pages) + 2):
        import itertools
        best = None
        for cuts in itertools.combinations(range(1, len(items)), n - 1):
            b = (0,) + cuts + (len(items),); m = max(sum(ln[b[k]:b[k + 1]]) for k in range(n))
            if best is None or m < best[0]: best = (m, b)
        if best[0] <= maxl:
            b = best[1]; return [items[b[k]:b[k + 1]] for k in range(n)]
    return pages

def hero_card(kicker, title, line):
    c = Card(SAFE_R - SAFE_L, pad=48)
    c.text(words(kicker, font("SemiBold", 44), ACCENT), 60, center=True)
    c.text(words(title, font("Bold", 76), WHITE), 92, gap=10, center=True)
    c.text(words(line, font("Regular", 46), SOFT), 62, gap=12, center=True)
    img, M, _ = c.render(); return img, place_bottom(img, M)

def brand_png():
    f = font("Medium", 34); t = "chinnidiaries246"; w = int(tw(f, t)) + 44
    img = Image.new("RGBA", (w + 20, 74), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 8, w, 64), 28, fill=(0, 0, 0, 70))
    d.text((22, 14), t, font=f, fill=(255, 255, 255, 205))
    p = WD + "/brand.png"; img.save(p); return p, (SAFE_L, SAFE_TOP + 6)

# ---------------------------------------------------------------- plan per dish
def plan(dish):
    """Returns list of segments: {id, L (time on screen), cards:[(png, x, y, a, b)], get}"""
    clips = dish["clips"]; segs = []
    dish["pages"] = balance_pages(dish["pages"])
    for c in clips:
        L = float(c["dur"])
        if c["role"] == "ingredients": L = max(L, 8.0, 1.95 + 2.45 * len(dish["pages"]) + 0.15)
        segs.append(dict(id=c["id"], role=c["role"], L=L, get=c.get("get"), step=c.get("step")))
    # missing clips: the shot before holds longer
    full = [s for s in segs if s["get"]]
    for i, s in enumerate(segs):
        if not s["get"]:
            j = i - 1
            while j >= 0 and not segs[j]["get"]: j -= 1
            if j < 0:
                j = i + 1
                while not segs[j]["get"]: j += 1
            segs[j]["L"] += s["L"]
    segs = full
    stepn = 0; cardinfo = []
    for k, s in enumerate(segs):
        s["cards"] = []; L = s["L"]
        if s["role"] == "ingredients":
            img, (x, y) = title_card(dish["title"], dish["subtitle"]); p = f"{WD}/{s['id']}_t.png"; img.save(p)
            s["cards"].append((p, x, y, 0.20, 1.85))
            pages = dish["pages"]; n = len(pages)
            t0 = 1.95; span = (L - 0.15 - t0) / n
            for pi, items in enumerate(pages):
                img, (x, y), nl, fs = ingr_card(items, pi + 1, n); p = f"{WD}/{s['id']}_p{pi+1}.png"; img.save(p)
                s["cards"].append((p, x, y, t0 + pi * span + 0.05, t0 + (pi + 1) * span - 0.05))
                cardinfo.append(dict(card=f"ingredients p{pi+1}", lines=nl, font=fs))
        elif s["role"] == "step":
            stepn += 1
            t, ln = s["step"]
            img, (x, y) = step_card(stepn, t, ln); p = f"{WD}/{s['id']}_s.png"; img.save(p)
            s["cards"].append((p, x, y, 0.35, L - 0.18))
        else:
            t, ln = s["step"] or ("", "")
            img, (x, y) = hero_card(t, dish["title"], ln); p = f"{WD}/{s['id']}_h.png"; img.save(p)
            s["cards"].append((p, x, y, 0.45, L + 5))
    return segs, cardinfo

# ---------------------------------------------------------------- media
def probe(p):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", p],
                                  capture_output=True, text=True).stdout)
    v = [s for s in j["streams"] if s["codec_type"] == "video"]; a = [s for s in j["streams"] if s["codec_type"] == "audio"]
    return dict(dur=float(j["format"]["duration"]), w=v[0]["width"] if v else 0, h=v[0]["height"] if v else 0,
                fps=v[0].get("r_frame_rate") if v else "", audio=bool(a))

def audio_level(p):
    o = subprocess.run(["ffmpeg", "-hide_banner", "-i", p, "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mx = [l for l in o.splitlines() if "max_volume" in l]; mn = [l for l in o.splitlines() if "mean_volume" in l]
    f = lambda ls: float(ls[0].split(":")[1].split()[0]) if ls else -99.0
    return f(mn), f(mx)

def build_segment(s, last, src, out_v, out_a, info):
    need = s["L"] + (0 if last else XF)
    pr = probe(src); info.update(src_dur=round(pr["dur"], 2), src_res=f"{pr['w']}x{pr['h']}", src_fps=pr["fps"])
    ss = 0.5 if pr["dur"] - 0.5 >= need * 0.75 else max(0.15, min(0.5, pr["dur"] - need))
    avail = max(0.5, pr["dur"] - ss - 0.05)
    slow = 1.0 if avail >= need else min(need / avail, 1.30)
    info.update(trim_start=round(ss, 2), slow=round(slow, 3), hold=round(max(0, need - avail * slow), 2))
    ins = ["-ss", f"{ss:.3f}", "-i", src]
    fc = [f"[0:v]setpts=(PTS-STARTPTS)*{slow:.4f},fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={W}:{H},setsar=1,tpad=stop_mode=clone:stop_duration={need:.3f},trim=duration={need:.3f},setpts=PTS-STARTPTS,format=yuva420p[v0]"]
    last_lbl = "v0"
    for k, (png, x, y, a, b) in enumerate(s["cards"]):
        b = min(b, need - 0.02)
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{need:.3f}", "-i", png]
        fo = f",fade=t=out:st={b - 0.25:.3f}:d=0.25:alpha=1" if b < need - 0.1 else ""
        fc.append(f"[{k+1}:v]format=rgba,fade=t=in:st={a:.3f}:d=0.28:alpha=1{fo}[c{k}]")
        fc.append(f"[{last_lbl}][c{k}]overlay=x={x}:y='{y}+44*pow(max(0\\,1-(t-{a:.3f})/0.32)\\,2)':eval=frame:"
                  f"enable='between(t\\,{a - 0.02:.3f}\\,{b + 0.02:.3f})'[o{k}]")
        last_lbl = f"o{k}"
    fc.append(f"[{last_lbl}]format=yuv420p[vout]")
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + ins + ["-filter_complex", ";".join(fc), "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-r", str(FPS), "-an", out_v])
    # audio
    has = False
    if pr["audio"]:
        mean, mx = audio_level(src); info.update(src_mean_db=mean, src_max_db=mx)
        has = mean > -50 and mx > -30
    info["has_sound"] = has
    if has:
        tempo = 1 / slow
        af = (f"atrim=start={ss:.3f},asetpts=PTS-STARTPTS,aresample=48000,atempo={tempo:.4f},"
              f"highpass=f=60,loudnorm=I={-18 if info.get('src_mean_db', -99) > -40 else -23}:TP=-3:LRA=11,aresample=48000,apad,atrim=duration={need:.3f}")
        sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-af", af, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out_a])
    else:
        sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{need:.3f}",
            "-c:a", "pcm_s16le", out_a])

# ---------------------------------------------------------------- music bed (procedural, original, seeded)
def music_bed(seconds, path, seed=7):
    sr = 48000; n = int(seconds * sr) + sr; t = np.arange(n) / sr
    rng = random.Random(seed)
    def note(f): return 440.0 * 2 ** ((f - 69) / 12)
    out = np.zeros((n, 2))
    # tanpura-like drone on D (Sa) and A (Pa), slow shimmer
    for m, g in ((50, 0.16), (57, 0.10), (62, 0.07), (38, 0.10)):
        f = note(m); env = 0.75 + 0.25 * np.sin(2 * np.pi * (0.07 + 0.01 * rng.random()) * t + rng.random() * 6)
        sig = sum((0.6 ** h) * np.sin(2 * np.pi * f * (h + 1) * t + rng.random() * 6) for h in range(5))
        out[:, 0] += g * env * sig; out[:, 1] += g * env * np.roll(sig, 240)
    # soft pad chords (D - Bm - G - A), 4 bars of 2.86 s (84 bpm, 4/4)
    beat = 60 / 84; bar = 4 * beat
    chords = [(62, 66, 69), (59, 62, 66), (55, 59, 62), (57, 61, 64)]
    i = 0; st = 0.0
    while st < seconds + 1:
        ch = chords[i % 4]; a = int(st * sr); b = min(n, int((st + bar + 0.6) * sr)); tt = t[a:b] - st
        env = np.minimum(1, tt / 0.9) * np.minimum(1, np.maximum(0, (bar + 0.6 - tt)) / 0.9)
        for m in ch:
            f = note(m)
            s_ = np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * 2 * f * tt) + 0.08 * np.sin(2 * np.pi * 3 * f * tt)
            out[a:b, 0] += 0.045 * env * s_; out[a:b, 1] += 0.045 * env * np.sin(2 * np.pi * f * 1.003 * tt)
        st += bar; i += 1
    # gentle plucked melody (Karplus-Strong) on D major pentatonic, every half beat with rests
    scale = [62, 64, 66, 69, 71, 74, 76, 78]
    def pluck(f, dur):
        N = int(sr / f); buf = np.array([rng.uniform(-1, 1) for _ in range(N)]); m = int(dur * sr); y = np.zeros(m)
        for k in range(m):
            y[k] = buf[k % N]; buf[k % N] = 0.497 * (buf[k % N] + buf[(k + 1) % N])
        return y * np.exp(-np.arange(m) / (sr * 0.9))
    cache = {}; st = bar * 1.0; idx = 3
    while st < seconds - 1:
        if rng.random() < 0.62:
            idx = max(0, min(len(scale) - 1, idx + rng.choice([-2, -1, -1, 1, 1, 2, 0])))
            m = scale[idx]
            if m not in cache: cache[m] = pluck(note(m), 1.6)
            y = cache[m]; a = int(st * sr); b = min(n, a + len(y)); pan = 0.35 + 0.3 * rng.random()
            out[a:b, 0] += 0.20 * (1 - pan) * y[:b - a]; out[a:b, 1] += 0.20 * pan * y[:b - a]
        st += beat / 2
    # light shaker on the off-beats
    st = bar * 2
    while st < seconds - 2:
        a = int((st + beat / 2) * sr); m = int(0.06 * sr)
        if a + m < n:
            nz = np.array([rng.uniform(-1, 1) for _ in range(m)]) * np.exp(-np.arange(m) / (sr * 0.012))
            nz = np.diff(np.concatenate([[0], nz]))
            out[a:a + m, 0] += 0.035 * nz; out[a:a + m, 1] += 0.03 * nz
        st += beat
    out = out[: int(seconds * sr)]
    out /= np.max(np.abs(out)) + 1e-9; out *= 0.7
    raw = WD + "/music_raw.wav"
    import wave
    with wave.open(raw, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes((out * 32767).astype("<i2").tobytes())
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw, "-af",
        f"lowpass=f=7000,loudnorm=I=-22:TP=-4:LRA=7,aresample=48000,afade=t=in:d=1.5,afade=t=out:st={seconds - 2.5:.2f}:d=2.5",
        "-c:a", "pcm_s16le", path])

# ---------------------------------------------------------------- sheets
def sheet(frames, cols, path, cell_w=360, label=True):
    ims = [(Image.open(p).convert("RGB"), lb) for p, lb in frames]
    ch = int(cell_w * H / W); rows = math.ceil(len(ims) / cols); pad = 8; lab = 40 if label else 0
    S = Image.new("RGB", (cols * (cell_w + pad) + pad, rows * (ch + lab + pad) + pad), (24, 24, 24)); d = ImageDraw.Draw(S)
    for i, (im, lb) in enumerate(ims):
        r, c = divmod(i, cols); x = pad + c * (cell_w + pad); y = pad + r * (ch + lab + pad)
        S.paste(im.resize((cell_w, ch), Image.LANCZOS), (x, y + lab))
        if label: d.text((x + 4, y + 6), lb, font=font("Medium", 24), fill=(255, 255, 255))
    S.save(path, quality=88)

def grab(src, t, out):
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-q:v", "2", out])
    return out

# ---------------------------------------------------------------- main
def do_dish(dish, mode, put_urls, report):
    name = dish["name"]; d = f"{WD}/{name}"; os.makedirs(d, exist_ok=True)
    segs, cardinfo = plan(dish)
    R = report.setdefault(name, {}); R["cards"] = cardinfo; R["missing"] = [c["id"] for c in dish["clips"] if not c.get("get")]
    R["segments"] = []
    if mode == "layout":
        frames = []
        for s in segs:
            bg = Image.new("RGB", (W, H)); arr = np.zeros((H, W, 3), np.uint8)
            yy = np.linspace(0, 1, H)[:, None]; arr[..., 0] = (150 + 70 * yy).astype(np.uint8); arr[..., 1] = (100 + 40 * yy).astype(np.uint8); arr[..., 2] = 60
            bg = Image.fromarray(arr).convert("RGBA")
            ui = ImageDraw.Draw(bg)
            ui.rectangle((0, int(H * 0.82), W, H), outline=(255, 0, 0), width=4); ui.rectangle((int(W * 0.88), 0, W, H), outline=(255, 0, 0), width=4)
            ui.rectangle((0, 0, W, int(H * 0.10)), outline=(255, 0, 0), width=4)
            bp, (bx, by) = brand_png(); bg.alpha_composite(Image.open(bp), (bx, by))
            for i, (png, x, y, a, b) in enumerate(s["cards"]):
                im = bg.copy(); im.alpha_composite(Image.open(png), (x, y)); p = f"{d}/{s['id']}_{i}.png"; im.convert("RGB").save(p)
                frames.append((p, f"{s['id']} c{i} {a:.1f}-{b:.1f}"))
        sheet(frames, 5, f"{d}/layout.jpg"); put(put_urls[f"{name}_layout.jpg"], f"{d}/layout.jpg", "image/jpeg"); return
    # download clips
    for s in segs:
        s["src"] = fetch(s["get"], f"{d}/{s['id']}.mp4")
    T = sum(s["L"] for s in segs); R["total"] = round(T, 2)
    qa, txt = [], []
    for i, s in enumerate(segs):
        info = {"id": s["id"], "L": s["L"]}
        build_segment(s, i == len(segs) - 1, s["src"], f"{d}/seg{i}.mp4", f"{d}/seg{i}.wav", info)
        R["segments"].append(info); print("seg", s["id"], json.dumps({k: v for k, v in info.items() if k != "id"}), flush=True)
        for k, (png, x, y, a, b) in enumerate(s["cards"]):
            tt = min(a + 0.9, (a + b) / 2, s["L"] - 0.2)
            txt.append((grab(f"{d}/seg{i}.mp4", tt, f"{d}/txt{i}_{k}.jpg"), f"{s['id']} @{tt:.1f}s"))
        pr = probe(s["src"])
        for q, fr in enumerate((0.55, 0.5 + (pr["dur"] - 0.5) * 0.5, pr["dur"] - 0.35)):
            qa.append((grab(s["src"], fr, f"{d}/qa{i}_{q}.jpg"), f"{s['id']} raw {fr:.1f}s"))
    sheet(txt, 4 if len(txt) > 6 else len(txt), f"{d}/sheet.jpg"); put(put_urls[f"{name}_sheet.jpg"], f"{d}/sheet.jpg", "image/jpeg")
    for q in range(0, len(qa), 6):
        qp = f"{d}/qa{q//6+1}.jpg"; sheet(qa[q:q+6], 3, qp, cell_w=480)
        if f"{name}_qa{q//6+1}.jpg" in put_urls: put(put_urls[f"{name}_qa{q//6+1}.jpg"], qp, "image/jpeg")
    if mode == "sheet": return
    # join: xfade chain + acrossfade chain + brand tag + music + end fade
    music_bed(T, f"{d}/music.wav", seed=dish.get("seed", 7))
    bp, (bx, by) = brand_png()
    ins = []
    for i in range(len(segs)): ins += ["-i", f"{d}/seg{i}.mp4"]
    for i in range(len(segs)): ins += ["-i", f"{d}/seg{i}.wav"]
    n = len(segs); ins += ["-i", f"{d}/music.wav", "-loop", "1", "-framerate", str(FPS), "-t", f"{T:.3f}", "-i", bp]
    fc = []; cur = "0:v"; off = 0.0
    for i in range(1, n):
        off += segs[i - 1]["L"]
        fc.append(f"[{cur}][{i}:v]xfade=transition=fade:duration={XF}:offset={off:.3f}[x{i}]"); cur = f"x{i}"
    fc.append(f"[{2*n+1}:v]format=rgba,fade=t=in:st=0.3:d=0.6:alpha=1[bt]")
    fc.append(f"[{cur}][bt]overlay=x={bx}:y={by}:shortest=1,fade=t=out:st={T - 1.2:.3f}:d=1.2,trim=duration={T:.3f},format=yuv420p[vout]")
    acur = f"{n}:a"
    for i in range(1, n):
        fc.append(f"[{acur}][{n+i}:a]acrossfade=d={XF}:c1=tri:c2=tri[a{i}]"); acur = f"a{i}"
    fc.append(f"[{acur}][{2*n}:a]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.89:level=disabled,"
              f"afade=t=out:st={T - 1.4:.3f}:d=1.4,atrim=duration={T:.3f}[aout]")
    final = f"{d}/{name}_v1.mp4"; silent = f"{d}/{name}_v1_silent.mp4"
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + ins + ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-maxrate", "7M", "-bufsize", "14M", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-g", "60", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final])
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", final, "-map", "0:v", "-c:v", "copy", "-an", "-movflags", "+faststart", silent])
    lo = subprocess.run(["ffmpeg", "-hide_banner", "-i", final, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    il = [l for l in lo.splitlines() if l.strip().startswith("I:")]
    R["final_lufs"] = il[-1].strip() if il else "?"
    R["final"] = probe(final); R["silent"] = probe(silent)
    R["final_mb"] = round(os.path.getsize(final) / 1e6, 1); R["silent_mb"] = round(os.path.getsize(silent) / 1e6, 1)
    # frames of the FINAL file for the check
    fin = []
    for t_ in np.linspace(0.8, T - 0.6, 12):
        fin.append((grab(final, float(t_), f"{d}/fin{int(t_*10)}.jpg"), f"final {t_:.1f}s"))
    sheet(fin, 6, f"{d}/final_sheet.jpg", cell_w=300); put(put_urls[f"{name}_final_sheet.jpg"], f"{d}/final_sheet.jpg", "image/jpeg")
    put(put_urls[f"{name}_v1.mp4"], final, "video/mp4"); put(put_urls[f"{name}_v1_silent.mp4"], silent, "video/mp4")
    print("done", name, R["final"], R["final_lufs"], flush=True)

def main():
    job = json.loads(fetch(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["inputs"]["job"]))
    report = {"mode": job["mode"], "music": "procedurally generated in this runner by cooking_assemble.py music_bed() (numpy synthesis: drone, pad chords, "
              "Karplus-Strong plucks, noise shaker). Original audio, no samples or third-party recordings; free to use commercially, no attribution."}
    try:
        for dish in job["dishes"]:
            do_dish(dish, job["mode"], job["put"], report)
    finally:
        p = WD + "/report.json"; json.dump(report, open(p, "w"), indent=1, default=str)
        if "report.json" in job["put"]: put(job["put"]["report.json"], p, "application/json")
    print("ALL DONE", flush=True)

if __name__ == "__main__":
    main()
