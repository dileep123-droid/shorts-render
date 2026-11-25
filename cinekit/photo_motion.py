"""photo_motion.py - "moving photos": a still never looks frozen (cinekit, 25 Nov 2026; owner: "a sent photo looked like a still").

Modes (all CPU, numpy/OpenCV backward warps with float transforms, so there is no zoompan stair-stepping):
  kenburns  real Ken Burns: push 1.00 -> 1.20, lateral drift, a slow roll, near-constant velocity (ease only in the first 0.4 s),
            plus drifting dust/light so even a flat photo has depth cues
  cutout    Nitish's cut-out look (T9 family): the subject is cut out (rembg BiRefNet), the background is inpainted, blurred and
            dimmed, and the two layers move AGAINST each other (subject pushes in and drifts one way, background the other)
            with a soft drop shadow and a warm rim
  depth     2.5D depth parallax (parallax.py v2, Depth-Anything-V2-Small) at a stronger default
  push      photo -> next photo with the parallax cut-out push (nitish_tr.parallaxpush) between two cut-out clips

  python photo_motion.py in.jpg --out out.mp4 [--mode cutout] [--size 1080x1920] [--dur 5] [--next b.jpg]
Each run also writes <out>.motion.json: per-frame motion measured with optical flow (px/frame at the output width).
Rule used as the pass mark: the slowest frame still moves >= 0.6 px/frame and the clip moves >= 8 % of the width in total
(a still that "looks frozen" measured ~0.1-0.3 px/frame in the v1 parallax sample).
"""
import argparse, json, math, os, subprocess, sys
from pathlib import Path
import numpy as np
import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "transitions"))
FF = os.environ.get("FFMPEG", "ffmpeg")


def cover(img, W, H, over=1.0):
    h, w = img.shape[:2]; s = max(W / w, H / h) * over
    r = cv2.resize(img, (int(math.ceil(w * s)), int(math.ceil(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    return r


def warp(img, W, H, scale, dx, dy, rot):
    """sample a WxH view centred on img with the given scale (1 = cover), offset in output px, roll in degrees"""
    h, w = img.shape[:2]; s = scale * max(W / w, H / h)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, s)
    M[0, 2] += W / 2 - w / 2 + dx; M[1, 2] += H / 2 - h / 2 + dy
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def lin(u, a=0.12):
    """position 0..1 with near-constant velocity: a short ease-in over the first 12 %, then linear (camera clearly moving every frame)"""
    norm = 1 - a / 2
    return (u * u / (2 * a) if u < a else u - a / 2) / norm


class Dust:
    def __init__(self, W, H, n=60, seed=5):
        r = np.random.default_rng(seed)
        self.p = r.random((n, 2)) * [W, H]; self.z = r.uniform(0.3, 1.0, n); self.v = r.normal(0, 1, (n, 2)) * [6, 4]
        self.a = r.uniform(0.15, 0.55, n); self.W, self.H = W, H

    def draw(self, fr, t, cam_dx, cam_scale):
        ov = np.zeros(fr.shape[:2], np.float32)
        for (x, y), z, v, a in zip(self.p, self.z, self.v, self.a):
            px = (x + v[0] * t + cam_dx * z * 1.6 - self.W / 2) * (1 + (cam_scale - 1) * z * 1.5) + self.W / 2
            py = (y + v[1] * t - self.H / 2) * (1 + (cam_scale - 1) * z * 1.5) + self.H / 2
            cv2.circle(ov, (int(px) % self.W, int(py) % self.H), max(1, int(3 * z)), float(a * z), -1, cv2.LINE_AA)
        ov = cv2.GaussianBlur(ov, (0, 0), 1.6)[..., None]
        return (fr.astype(np.float32) + ov * np.array([255, 236, 200], np.float32)).clip(0, 255).astype(np.uint8)


def kenburns(img, W, H, n, fps, a):
    d = Dust(W, H, a.dust); out = []
    s0, s1 = 1.0, 1.0 + 0.20 * a.strength; dx1 = 0.045 * W * a.strength * a.dir; dy1 = -0.02 * H * a.strength; r1 = 1.2 * a.strength * a.dir
    base = cover(img, W, H, 1.3)
    for i in range(n):
        u = lin(i / (n - 1)); sc = (s0 + (s1 - s0) * u) * 1.03   # 3 % margin for the roll
        fr = warp(base, W, H, sc, dx1 * u, dy1 * u, r1 * u)
        out.append(d.draw(fr, i / fps, dx1 * u, sc) if a.dust else fr)
    return out


def cutout(img, W, H, n, fps, a):
    from parallax import subject_mask
    big = cover(img, W, H, 1.30)
    m = subject_mask(big, a.mask_model)
    m = cv2.GaussianBlur((m > 0.5).astype(np.float32), (0, 0), 1.5)
    if m.mean() < 0.02: print("  cut-out: no subject found -> kenburns"); return kenburns(img, W, H, n, fps, a)
    hole = cv2.dilate((m > 0.05).astype(np.uint8) * 255, np.ones((25, 25), np.uint8))
    sm = 0.35; small = cv2.resize(big, None, fx=sm, fy=sm); hs = cv2.resize(hole, (small.shape[1], small.shape[0]))
    bg = cv2.resize(cv2.inpaint(small, hs, 9, cv2.INPAINT_TELEA), (big.shape[1], big.shape[0]), interpolation=cv2.INTER_CUBIC)
    bg = (cv2.GaussianBlur(bg, (0, 0), a.bg_blur * W / 1080).astype(np.float32) * a.bg_dim).clip(0, 255).astype(np.uint8)
    sh = cv2.GaussianBlur(m, (0, 0), 18 * W / 1080)
    rim = np.clip(m - cv2.GaussianBlur(cv2.erode(m, np.ones((7, 7), np.uint8)), (0, 0), 3), 0, 1)
    d = Dust(W, H, a.dust); out = []; S = a.strength
    for i in range(n):
        u = lin(i / (n - 1))
        # background: slow pull the other way; subject: pushes in and drifts (layer separation ~10 % of width over the clip)
        b = warp(bg, W, H, 1.06 + 0.05 * S * u, -0.04 * W * S * u * a.dir, 0, -0.4 * S * u * a.dir)
        fsc = (1.0 + 0.16 * S * u); fdx = 0.06 * W * S * u * a.dir; fdy = -0.015 * H * S * u
        f = warp(big, W, H, fsc, fdx, fdy, 0.6 * S * u * a.dir)
        fm = warp((m * 255).astype(np.uint8), W, H, fsc, fdx, fdy, 0.6 * S * u * a.dir).astype(np.float32)[..., None] / 255
        sd = warp((sh * 255).astype(np.uint8), W, H, fsc, fdx + 18 * W / 1080, fdy + 26 * W / 1080, 0.6 * S * u * a.dir).astype(np.float32)[..., None] / 255
        rm = warp((rim * 255).astype(np.uint8), W, H, fsc, fdx, fdy, 0.6 * S * u * a.dir).astype(np.float32)[..., None] / 255
        fr = b.astype(np.float32) * (1 - 0.55 * sd)
        fr = fr * (1 - fm) + f.astype(np.float32) * fm
        fr += rm * np.array([255, 170, 70], np.float32) * 0.55
        fr = fr.clip(0, 255).astype(np.uint8)
        out.append(d.draw(fr, i / fps, fdx, fsc) if a.dust else fr)
    return out


def depth(img, W, H, n, fps, a, src):
    tmp = str(Path(a.out).with_suffix(".depth.mp4"))
    subprocess.run([sys.executable, str(HERE / "parallax.py"), src, "--out", tmp, "--size", f"{W}x{H}", "--dur", str(a.dur), "--fps", str(fps),
                    "--move", a.move, "--strength", str(1.35 * a.strength), "--ffmpeg", FF], check=True)
    vf = f"fps={fps},format=rgb24"
    raw = subprocess.run([FF, "-v", "error", "-i", tmp, "-vf", vf, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    os.remove(tmp)
    return list(np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3))


def measure(frames, W):
    """optical-flow motion per frame (px/frame at output width), on a 360-px-wide copy"""
    sc = 360 / frames[0].shape[1]; prev = None; mags = []
    for f in frames:
        g = cv2.cvtColor(cv2.resize(f, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        if prev is not None:
            fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            mags.append(float(np.median(np.linalg.norm(fl, axis=2))) / sc)
        prev = g
    tot = sum(mags)
    return {"px_per_frame_min": round(min(mags), 2), "px_per_frame_median": round(float(np.median(mags)), 2),
            "total_px": round(tot, 1), "total_pct_width": round(100 * tot / W, 1),
            "frozen_frames_pct": round(100 * sum(m < 0.3 for m in mags) / len(mags), 1),
            "pass": bool(min(mags) >= 0.6 and tot >= 0.08 * W)}


def encode(frames, out, fps, W, H):
    p = subprocess.run([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                       input=np.ascontiguousarray(np.stack(frames)).tobytes())
    if p.returncode: sys.exit("encode failed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image"); ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="cutout", choices=["kenburns", "cutout", "depth", "push"])
    ap.add_argument("--next", default=None, help="second image (mode push)")
    ap.add_argument("--size", default="1080x1920"); ap.add_argument("--dur", type=float, default=5.0); ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--strength", type=float, default=1.0); ap.add_argument("--dir", type=int, default=1, choices=[-1, 1])
    ap.add_argument("--move", default="dolly_in"); ap.add_argument("--dust", type=int, default=50)
    ap.add_argument("--bg-blur", type=float, default=7.0, dest="bg_blur"); ap.add_argument("--bg-dim", type=float, default=0.8, dest="bg_dim")
    ap.add_argument("--mask-model", default="birefnet-general-lite", dest="mask_model")
    a = ap.parse_args()
    W, H = map(int, a.size.split("x")); n = int(round(a.dur * a.fps))
    img = cv2.cvtColor(cv2.imread(a.image), cv2.COLOR_BGR2RGB)
    if a.mode == "kenburns": fr = kenburns(img, W, H, n, a.fps, a)
    elif a.mode == "cutout": fr = cutout(img, W, H, n, a.fps, a)
    elif a.mode == "depth": fr = depth(img, W, H, n, a.fps, a, a.image)
    else:
        import nitish_tr as NT
        img2 = cv2.cvtColor(cv2.imread(a.next), cv2.COLOR_BGR2RGB)
        A = cutout(img, W, H, n, a.fps, a); a.dir = -a.dir; B = cutout(img2, W, H, n, a.fps, a)
        L = NT.length("parallaxpush", a.fps); h = n - L
        fr = list(A[:h]) + list(NT.render("parallaxpush", np.stack(A[h:h + L]), np.stack(B[:L]), a.fps)) + list(B[L:n])
    m = measure(fr, W); m.update(mode=a.mode, size=a.size, dur=a.dur)
    print("MOTION", json.dumps(m), flush=True)
    encode(fr, a.out, a.fps, W, H)
    Path(a.out + ".motion.json").write_text(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
