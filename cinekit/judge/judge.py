"""cinekit JUDGE (25 Nov 2026, GitHub runner): Gemini compares each of our transitions with Nitish Rajput's real frames.

  python judge.py plan.json --out out            (env VERTEX_TOKEN, VERTEX_PROJECT; CF_R2_* for R2)

plan.json (made on the laptop by judge_plan.py, staged to R2 s2work/cinekit/judge_tmp/plan.json):
  {"model": "...", "refs": {"T1": {"sheet": "do_whip", "recipe": "..."}, ...},
   "cands": [{"id": 101, "file": "101_NK_whipsmear.mp4", "frames": 3, "ref": "T1", "rep": 0}, ...]}
Inputs (R2, read only): s2work/cinekit/judge_tmp/gt/<sheet>_sheet.png (60-fps contact sheets, 8 cols x 240x135, pad 2)
                        s2work/cinekit/transitions_book/previews/<file> (3 s 640x360 30 fps, cut window centred)
                        s2work/shorts1/clips/ic814/* (the A/B source clips, for the mechanical effect-length check)
Output (R2): s2work/cinekit/judge_tmp/results/<tag>.json + usage.jsonl.  Nothing private in the log (scores only).
Both strips go to Gemini at the SAME timebase (30 fps): the 60-fps reference keeps every other frame.
"""
import argparse, base64, io, json, os, subprocess, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BUCKET = "eduorbex-media"; TMP = "s2work/cinekit/judge_tmp/"
FF = os.environ.get("FFMPEG", "ffmpeg")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PIN, POUT, INR = 0.50, 3.00, 88.0      # same assumed prices as research/transitions_hifps/vx.py
_S3 = None; LOCK = threading.Lock()


def s3():
    global _S3
    if _S3 is None:
        import boto3
        _S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                           aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")
    return _S3


def get(key, path):
    assert key.startswith(("s2work/cinekit/", "s2work/shorts1/clips/")) and ".." not in key, key
    if not Path(path).exists():
        s3().download_file(BUCKET, key, path)
    return path


def put(path, key):
    assert key.startswith(TMP) and ".." not in key, key
    s3().upload_file(path, BUCKET, key)


# ---------------------------------------------------------------- strips
def sheet_frames(png, nframes=None, step=2):
    """60-fps contact sheet (8 cols of 240x135 tiles, 2 px padding) -> every other frame (30 fps)"""
    im = np.asarray(Image.open(png).convert("RGB"))
    tw, th, pad, cols = 240, 135, 2, 8
    rows = (im.shape[0] + pad) // (th + pad)
    n = nframes or rows * cols
    fr = []
    for k in range(n):
        r, c = divmod(k, cols); y, x = r * (th + pad), c * (tw + pad)
        t = im[y:y + th, x:x + tw]
        if t.shape[:2] == (th, tw): fr.append(t)
    return fr[::step]


def decode(path, n=None, start=0.0, size=(640, 360)):
    W, H = size
    vf = f"fps=30,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgb24"
    cmd = [FF, "-v", "error", "-ss", str(start), "-i", path, "-vf", vf] + (["-frames:v", str(n)] if n else []) + ["-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def tile(frames, cols=6, tw=240, label0=0, title=None):
    font = ImageFont.truetype(FONT, 14) if os.path.exists(FONT) else ImageFont.load_default()
    n = len(frames); rows = (n + cols - 1) // cols
    th = int(round(frames[0].shape[0] * tw / frames[0].shape[1]))
    top = 26 if title else 0
    sheet = Image.new("RGB", (cols * (tw + 2), top + rows * (th + 2)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    if title: d.text((6, 5), title, fill=(255, 220, 80), font=font)
    for i, f in enumerate(frames):
        im = Image.fromarray(f).resize((tw, th), Image.LANCZOS)
        x, y = (i % cols) * (tw + 2), top + (i // cols) * (th + 2)
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 34, y + 17], fill=(0, 0, 0)); d.text((x + 3, y + 1), str(label0 + i), fill=(255, 255, 255), font=font)
    return sheet


def png_b64(im):
    b = io.BytesIO(); im.save(b, "PNG", optimize=True); return base64.b64encode(b.getvalue()).decode()


# ---------------------------------------------------------------- mechanical check: how many frames are neither A nor B
def effect_frames(prev, A, B, pre, D, crop=312):
    """frames (in the transition window +-3) whose picture is far from BOTH source frames at that position -> effect length"""
    n = 0; per = []
    for i in range(max(0, pre - 3), min(len(prev), pre + D + 3)):
        p = prev[i, :crop].astype(np.float32)
        ai = A[min(len(A) - 1, i)][:crop].astype(np.float32)
        # B frame at output position i: book assembles A[:pre] + mid + B[D:], mid[j] uses B[j] -> B index = i - pre
        bidx = i - pre
        bi = B[min(len(B) - 1, max(0, bidx))][:crop].astype(np.float32)
        da, db = np.abs(p - ai).mean(), np.abs(p - bi).mean()
        per.append(round(float(min(da, db)), 1))
        if min(da, db) > 10: n += 1
    return n, per


# ---------------------------------------------------------------- Gemini
PROMPT = """You are a senior film editor auditing transitions for a YouTube documentary channel. The target look is the Hindi
documentary creator Nitish Rajput, whose transitions were measured frame by frame.

IMAGE 1 = REFERENCE: consecutive frames of a REAL transition from a Nitish Rajput video ({sheet}), shown at 30 fps
(every frame of a 60-fps contact sheet was kept every other frame), numbered left to right, top to bottom.
The measured recipe of this transition type ({code}): {recipe}

IMAGE 2 = CANDIDATE: consecutive frames (30 fps, numbered) of OUR transition between two different shots
(shot A = a valley/town, shot B = an aerial airport/plane shot). The cut / transition happens around frame {mid}.

Step 1: describe both strips factually: on which frame numbers the transition effect is visible, how many frames it lasts,
motion direction, blur type (directional / radial / gaussian / none), any flash, colour or exposure events, masks or shapes.
Step 2: score the CANDIDATE from 0 to 10 on each:
- type_match: is it the same kind of transition as the reference (same mechanism, not just "some transition")?
- timing_match: is its length and speed close to the reference (Nitish's effects are very short: 2-6 frames at 60 fps for
  whips/flashes/glitches, 15-30 frames at 60 fps for masks and pushes)? Too long or too slow must score low.
- realism: would a viewer believe a professional editor made it? Deduct for visible artifacts: tearing, edge stretching,
  black corners, hard aliasing, cheap template look, wrong colours.
- overall: how well it would pass as a Nitish Rajput edit in a real video.
Be strict: a plain hard cut or an unrelated wipe must get type_match <= 2. Most generic template transitions are 2-5.
Also say where an editor would use it (place change, time jump, reveal, action, chapter, teaser montage, presenter return)
and where to avoid it.
Answer ONLY JSON:
{{"ref": {{"effect_frames": [first, last], "length_frames": n, "events": "..."}},
 "cand": {{"effect_frames": [first, last], "length_frames": n, "events": "...", "artifacts": "..."}},
 "type_match": n, "timing_match": n, "realism": n, "overall": n, "verdict": "use|maybe|reject",
 "use_for": ["..."], "avoid": ["..."], "why": "one sentence"}}"""


def vertex(parts, model, project, tag, out_dir):
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 3000, "responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingLevel": "low"}}}
    url = f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/{model}:generateContent"
    last = None
    for t in range(4):
        try:
            rq = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
                "Authorization": "Bearer " + os.environ["VERTEX_TOKEN"], "x-goog-user-project": project, "Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(rq, timeout=300))
            u = r.get("usageMetadata", {})
            pin = u.get("promptTokenCount", 0); po = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
            rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "in": pin, "out": u.get("candidatesTokenCount", 0),
                   "think": u.get("thoughtsTokenCount", 0), "inr": round((pin * PIN + po * POUT) / 1e6 * INR, 3), "where": "github judge"}
            with LOCK:
                open(f"{out_dir}/usage.jsonl", "a", encoding="utf-8").write(json.dumps(rec) + "\n")
            txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
            return json.loads(txt[txt.find("{"):txt.rfind("}") + 1]), rec
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403): break
        except Exception as e:
            last = type(e).__name__ + " " + str(e)[:120]
        time.sleep(6 * (t + 1))
    raise RuntimeError(f"{tag}: {last}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--out", default="out"); ap.add_argument("--tag", default="j1")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    P = json.load(open(a.plan, encoding="utf-8")); out = Path(a.out); (out / "dl").mkdir(parents=True, exist_ok=True)
    project = os.environ["VERTEX_PROJECT"]; model = P["model"]
    spec = P["spec"]; N = spec["fps"] * spec["seconds"]
    ca = get(spec["clip_a"], str(out / "dl" / "clip_a.mp4")); cb = get(spec["clip_b"], str(out / "dl" / "clip_b.mp4"))
    A = decode(ca, N, 0.5); B = decode(cb, N, 0.3)
    refimg = {}
    for code, r in P["refs"].items():
        png = get(f"{TMP}gt/{r['sheet']}_sheet.png", str(out / "dl" / f"{r['sheet']}.png"))
        fr = sheet_frames(png, r.get("frames"))
        refimg[code] = tile(fr, cols=6, title=f"REFERENCE {code} (Nitish Rajput, 30 fps)")
    results = []; spent = 0.0

    def one(c):
        prev = decode(get("s2work/cinekit/transitions_book/previews/" + c["file"], str(out / "dl" / c["file"])))
        D = c["frames"]; pre = (N - D) // 2
        nfx, per = effect_frames(prev, A, B, pre, D)
        s0 = max(0, pre - 5); n = min(30, D + 11)
        if D + 11 > 30: s0 = max(0, pre - 3)
        fr = [f[:312] for f in prev[s0:s0 + n]]                       # crop the burned-in name label (no hint for the judge)
        cimg = tile(fr, cols=6, title="CANDIDATE (30 fps)")
        r = P["refs"][c["ref"]]
        prompt = PROMPT.format(sheet=r["sheet"], code=c["ref"], recipe=r["recipe"], mid=pre - s0)
        parts = [{"inlineData": {"mimeType": "image/png", "data": png_b64(refimg[c["ref"]])}},
                 {"inlineData": {"mimeType": "image/png", "data": png_b64(cimg)}}, {"text": prompt}]
        tag = f"{c['id']:03d}_{c['ref']}_r{c.get('rep', 0)}"
        try:
            j, rec = vertex(parts, model, project, tag, out)
        except Exception as e:
            print("WARNING", tag, str(e)[:160], flush=True); return dict(c, error=str(e)[:200], mech_effect_frames=nfx)
        res = dict(c, judge=j, mech_effect_frames=nfx, mech_curve=per, inr=rec["inr"])
        print(f"JUDGED {tag} overall={j.get('overall')} type={j.get('type_match')} timing={j.get('timing_match')} "
              f"real={j.get('realism')} mech={nfx}f Rs{rec['inr']}", flush=True)
        return res

    t0 = time.time()
    with ThreadPoolExecutor(a.threads) as ex:
        for r in ex.map(one, P["cands"]):
            results.append(r); spent += r.get("inr", 0)
    print(f"DONE {len(results)} judged in {(time.time() - t0) / 60:.1f} min, Rs {spent:.1f}", flush=True)
    rp = out / f"{a.tag}.json"; rp.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    put(str(rp), f"{TMP}results/{a.tag}.json")
    if (out / "usage.jsonl").exists(): put(str(out / "usage.jsonl"), f"{TMP}results/{a.tag}_usage.jsonl")


if __name__ == "__main__":
    main()
