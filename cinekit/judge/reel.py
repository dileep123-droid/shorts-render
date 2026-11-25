"""Comparison reel (GitHub runner, 25 Nov): our picked transitions next to Nitish Rajput's real frames, side by side.
  python reel.py plan.json --out out --tag reel1        plan["reel"] = {"key": "<R2 out key>", "sections": [{"title": ..., "pairs": [
        {"code": "T1", "sheet": "do_whip", "frames": 48, "file": "101_NK_whipsmear.mp4", "D": 3, "name": "Whip smear-in",
         "score": "Gemini 7/10", "use": "place change, presenter -> B-roll"}]}]}
Each pair: real speed once, then 4x slow motion (every frame held 4x) so 2-6-frame transitions can be seen. Left = Nitish (his
60-fps contact-sheet frames, every other frame = 30 fps; small 240-px source, so it is softer), right = ours (Book preview)."""
import argparse, json, os, subprocess
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import judge as JD

W, H = 1920, 1080; FPS = 30
F_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"; F_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def fnt(p, s): return ImageFont.truetype(p, s) if os.path.exists(p) else ImageFont.load_default()


def card(title, sub, n):
    im = Image.new("RGB", (W, H), (12, 12, 16)); d = ImageDraw.Draw(im)
    d.text((W // 2, H // 2 - 40), title, font=fnt(F_B, 64), fill=(255, 210, 60), anchor="mm")
    d.text((W // 2, H // 2 + 40), sub, font=fnt(F_R, 34), fill=(220, 220, 220), anchor="mm")
    return [np.asarray(im)] * n


def compose(ref, ours, p, slow, k, n):
    im = Image.new("RGB", (W, H), (12, 12, 16)); d = ImageDraw.Draw(im)
    pw, ph = 940, 529; y = 190
    im.paste(Image.fromarray(ref).resize((pw, ph), Image.BICUBIC), (13, y))
    im.paste(Image.fromarray(ours).resize((pw, ph), Image.BICUBIC), (W - pw - 13, y))
    d.text((13 + pw // 2, y - 30), "NITISH RAJPUT (real frames)", font=fnt(F_B, 30), fill=(255, 255, 255), anchor="mm")
    d.text((W - 13 - pw // 2, y - 30), "OURS (cinekit)", font=fnt(F_B, 30), fill=(255, 210, 60), anchor="mm")
    d.text((W // 2, 60), f"{p['code']}  {p['name']}", font=fnt(F_B, 46), fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, 112), p.get("score", ""), font=fnt(F_R, 30), fill=(255, 210, 60), anchor="mm")
    d.text((W // 2, y + ph + 50), "use: " + p.get("use", ""), font=fnt(F_R, 30), fill=(210, 210, 210), anchor="mm")
    if p.get("avoid"): d.text((W // 2, y + ph + 95), "avoid: " + p["avoid"], font=fnt(F_R, 26), fill=(160, 160, 170), anchor="mm")
    d.text((W // 2, H - 60), ("SLOW MOTION 4x" if slow else "REAL SPEED") + f"   frame {k + 1}/{n}", font=fnt(F_B, 28),
           fill=(255, 120, 90) if slow else (140, 220, 140), anchor="mm")
    return np.asarray(im)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--out", default="out"); ap.add_argument("--tag", default="reel")
    a = ap.parse_args()
    P = json.load(open(a.plan, encoding="utf-8")); R = P["reel"]; out = Path(a.out); (out / "dl").mkdir(parents=True, exist_ok=True)
    N = P["spec"]["fps"] * P["spec"]["seconds"]
    enc = subprocess.Popen([JD.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            str(out / f"{a.tag}.mp4")], stdin=subprocess.PIPE)
    wr = lambda frs: [enc.stdin.write(np.ascontiguousarray(f).tobytes()) for f in frs]
    wr(card("Our transitions vs Nitish Rajput", "left: his real frames  |  right: ours  |  each one at real speed, then 4x slow motion", 75))
    for sec in R["sections"]:
        wr(card(sec["title"], sec.get("sub", ""), 50))
        for p in sec["pairs"]:
            png = JD.get(f"{JD.TMP}gt/{p['sheet']}_sheet.png", str(out / "dl" / f"{p['sheet']}.png"))
            ref = JD.sheet_frames(png, p.get("frames"))
            prev = JD.decode(JD.get("s2work/cinekit/transitions_book/previews/" + p["file"], str(out / "dl" / p["file"])))
            D = p["D"]; pre = (N - D) // 2; n = len(ref)
            s0 = max(0, min(N - n, pre - max(4, (n - D) // 2)))
            ours = [f[:312] for f in prev[s0:s0 + n]]
            n = min(n, len(ours))
            wr([compose(ref[k], ours[k], p, False, k, n) for k in range(n)])
            wr([compose(ref[k], ours[k], p, True, k, n) for k in range(n) for _ in range(4)])
            print("REEL", p["code"], p["file"], n, "frames", flush=True)
    enc.stdin.close(); enc.wait()
    JD.put(str(out / f"{a.tag}.mp4"), R["key"])
    print("REEL DONE", R["key"], flush=True)


if __name__ == "__main__":
    main()
