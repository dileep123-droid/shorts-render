"""parallax.py - 2.5D photo parallax clip generator (standalone, CPU).

Still image -> 4-6 s MP4 with a gentle depth-aware camera move (dolly / pan / orbit / rise), and optionally the
subject cut out and floated over a blurred, inpainted background.

Depth:   Depth-Anything-V2-Small (Apache-2.0), ONNX build from onnx-community/depth-anything-v2-small, run on onnxruntime.
Cut-out: rembg (MIT) with BiRefNet-general-lite (MIT) by default.
Render:  numpy/OpenCV backward warp per frame, piped as raw frames into ffmpeg (no temp files).

Usage:
  python parallax.py in.jpg --out out.mp4 [--size 1080x1920|1920x1080|source] [--dur 5] [--move dolly_in]
                     [--strength 1.0] [--cutout] [--bg-blur 14] [--save-depth depth.png]
Moves: dolly_in, dolly_out, pan_left, pan_right, orbit, rise
"""
import argparse, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / "_models" / "hf"))
os.environ.setdefault("U2NET_HOME", str(ROOT / "_models" / "u2net"))
import numpy as np
import cv2

FFMPEG_DEFAULT = r"C:/ffmpeg-master-latest-win64-gpl-shared/bin/ffmpeg.exe"
DEPTH_REPO = "onnx-community/depth-anything-v2-small"   # Apache-2.0 (Small only; Base/Large are CC-BY-NC)


# ---------------------------------------------------------------- depth
def estimate_depth(rgb, model_path=None):
    """Returns relative inverse depth in [0,1] (1 = near), same HxW as rgb."""
    import onnxruntime as ort
    if model_path is None:
        from huggingface_hub import hf_hub_download
        model_path = hf_hub_download(DEPTH_REPO, "onnx/model.onnx")
    h, w = rgb.shape[:2]
    s = 518 / min(h, w)
    nh, nw = max(14, int(round(h * s / 14)) * 14), max(14, int(round(w * s / 14)) * 14)
    x = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None]
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    d = sess.run(None, {"pixel_values": x})[0][0].astype(np.float32)
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    return np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)


def subject_mask(rgb, model="birefnet-general-lite"):
    from rembg import new_session, remove
    from PIL import Image
    m = remove(Image.fromarray(rgb), session=new_session(model), only_mask=True, post_process_mask=False)
    m = np.asarray(m.convert("L"), np.float32) / 255.0
    if m.shape[:2] != rgb.shape[:2]:
        m = cv2.resize(m, (rgb.shape[1], rgb.shape[0]))
    return m


# ---------------------------------------------------------------- motion presets
def ease(u):
    return 0.5 - 0.5 * np.cos(np.pi * u)


def camera(move, u):
    """-> (tx, ty, zoom) with tx,ty in [-1,1] (lateral parallax) and zoom in [0,1] (dolly amount).
    Mostly-linear easing so the camera is visibly moving on every frame (no dead start/end)."""
    e = 0.4 * u + 0.6 * ease(u)
    if move == "dolly_in":  return (e - 0.5, -0.3 * (e - 0.5), e)
    if move == "dolly_out": return (0.5 - e, 0.3 * (e - 0.5), 1 - e)
    if move == "pan_right": return (2 * e - 1, 0.0, 0.35 + 0.25 * e)
    if move == "pan_left":  return (1 - 2 * e, 0.0, 0.35 + 0.25 * e)
    if move == "rise":      return (0.3 * (e - 0.5), 1 - 2 * e, 0.3 + 0.6 * e)
    if move == "orbit":
        a = 2 * np.pi * u
        return (np.sin(a), 0.5 * np.cos(a), 0.45 + 0.35 * e)
    raise ValueError(move)


class Dust:
    """Drifting dust motes / light specks with their own depth, so they parallax with the camera."""
    def __init__(self, W, H, n, seed=7):
        r = np.random.default_rng(seed)
        self.W, self.H = W, H
        self.x = r.uniform(0, W, n); self.y = r.uniform(0, H, n); self.z = r.uniform(0, 1, n) ** 1.6
        self.vx = r.normal(0.012, 0.01, n) * W; self.vy = r.normal(-0.02, 0.012, n) * H   # per second
        self.a = r.uniform(0.25, 0.7, n); self.ph = r.uniform(0, 2 * np.pi, n)

    def layer(self, t, tx, ty, s, A, focus):
        W, H = self.W, self.H; k = 2  # draw at half res
        img = np.zeros((H // k, W // k), np.float32)
        x = (self.x + self.vx * t + tx * A * 1.6 * (self.z - focus) - W / 2) * (1 + (s - 1) * (1 + self.z)) + W / 2
        y = (self.y + self.vy * t + ty * A * 1.0 * (self.z - focus) - H / 2) * (1 + (s - 1) * (1 + self.z)) + H / 2
        x %= W; y %= H
        tw = 0.65 + 0.35 * np.sin(self.ph + t * 2.2)
        for xi, yi, zi, ai, ti in zip(x, y, self.z, self.a, tw):
            rad = max(1, int((0.8 + 3.2 * zi) * W / 1920 * 2.2 / k))
            cv2.circle(img, (int(xi / k), int(yi / k)), rad, float(ai * ti * (0.5 + 0.5 * zi)), -1, cv2.LINE_AA)
        img = cv2.GaussianBlur(img, (0, 0), 1.2 + 0.0015 * W)
        return cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- main render
def cover_fit(img, W, H, overscan):
    h, w = img.shape[:2]
    s = max(W * overscan / w, H * overscan / h)
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4
    return cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=interp)


def render(a):
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        sys.exit(f"cannot read {a.image}")
    if a.size == "source":
        h, w = bgr.shape[:2]; k = min(1.0, 1920 / max(h, w))
        W, H = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    else:
        W, H = map(int, a.size.lower().split("x"))
    OV = 1.06 + 0.05 * a.strength  # overscan so edges never show
    can = cover_fit(bgr, W, H, OV)
    Hc, Wc = can.shape[:2]
    rgb = cv2.cvtColor(can, cv2.COLOR_BGR2RGB)
    t0 = time.time()
    depth = estimate_depth(rgb, a.depth_model)
    print(f"  depth {time.time()-t0:.1f}s  canvas {Wc}x{Hc} -> out {W}x{H}")
    # smooth depth, and grow near regions slightly so silhouettes carry their edge with them (less tearing)
    k = max(3, int(Wc * 0.004) | 1)
    depth = cv2.dilate(depth, np.ones((k, k), np.uint8))
    depth = cv2.GaussianBlur(depth, (0, 0), Wc * 0.006)
    if a.save_depth:
        cv2.imwrite(a.save_depth, (depth * 255).astype(np.uint8))

    mask = None
    if a.cutout:
        t0 = time.time()
        mask = subject_mask(rgb, a.mask_model)
        print(f"  mask {time.time()-t0:.1f}s  coverage {mask.mean()*100:.1f}%")
        if mask.mean() < 0.01:
            print("  WARNING: no subject found, falling back to plain depth parallax"); mask = None
    bg_img, fg_img, fg_alpha, d_fg = can.astype(np.float32), None, None, None
    if mask is not None:
        # background = subject removed (inpainted at half res) + blur + slight dim
        hm = (mask > 0.15).astype(np.uint8) * 255
        hm = cv2.dilate(hm, np.ones((int(Wc * 0.02) | 1,) * 2, np.uint8))
        small = cv2.resize(can, (Wc // 2, Hc // 2)); sm = cv2.resize(hm, (Wc // 2, Hc // 2))
        inp = cv2.resize(cv2.inpaint(small, sm, 6, cv2.INPAINT_TELEA), (Wc, Hc), interpolation=cv2.INTER_CUBIC)
        hmf = (cv2.GaussianBlur(hm, (0, 0), 3).astype(np.float32) / 255)[..., None]
        bg = can * (1 - hmf) + inp * hmf
        if a.bg_blur > 0:
            bg = cv2.GaussianBlur(bg, (0, 0), a.bg_blur * Wc / 1080)
        bg_img = bg.astype(np.float32) * a.bg_dim
        fg_img = can.astype(np.float32)
        fg_alpha = mask.astype(np.float32)
        d_fg = float(np.median(depth[mask > 0.5])) if (mask > 0.5).any() else 0.9
        # background depth: fill the subject area with surrounding depth
        d8 = (depth * 255).astype(np.uint8)
        depth_bg = cv2.inpaint(cv2.resize(d8, (Wc // 4, Hc // 4)), cv2.resize(hm, (Wc // 4, Hc // 4)), 5, cv2.INPAINT_TELEA)
        depth = cv2.resize(depth_bg, (Wc, Hc), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255
        depth = np.minimum(depth, d_fg)  # background never sits in front of the subject

    n = int(round(a.dur * a.fps))
    A = a.strength * 0.06 * W           # lateral parallax: near-vs-far layer offset ~6% of width over the move
    ZG = a.strength * 0.15              # global push: scale 1.00 -> 1.15 ...
    ZD = a.strength * 0.07              # ... plus up to +0.07 for the nearest depth (near ~1.22, far ~1.15)
    focus = a.focus
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = Wc / 2.0, Hc / 2.0
    px = xx - W / 2.0; py = yy - H / 2.0   # output pixel relative to center

    def scale(zm, d):
        return 1.0 + zm * (ZG + ZD * d)

    def src_coords(dmap, tx, ty, zm):
        s = scale(zm, dmap)
        sx = tx * A * (dmap - focus); sy = ty * A * 0.6 * (dmap - focus)
        return (cx + px / s - sx).astype(np.float32), (cy + py / s - sy).astype(np.float32)

    dust = Dust(W, H, a.dust) if a.dust > 0 else None
    dust_col = np.array([200, 228, 255], np.float32) / 255 * 255   # warm-white specks (BGR)
    if a.light:
        gy, gx = np.mgrid[0:H // 4, 0:W // 4].astype(np.float32)

    cmd = [a.ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(a.fps),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", a.out]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    oy, ox = int(round(cy - H / 2)), int(round(cx - W / 2))
    d0 = np.ascontiguousarray(depth[oy:oy + H, ox:ox + W])   # first-guess depth (constant)
    bg_u8 = np.clip(bg_img, 0, 255).astype(np.uint8)
    if fg_img is not None:
        fg_img = np.clip(fg_img, 0, 255).astype(np.uint8)
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for f in range(n):
            tx, ty, zm = camera(a.move, f / max(1, n - 1))
            # two fixed-point passes: sample depth where the pixel came from (sharper silhouettes)
            mx, my = src_coords(d0, tx, ty, zm)
            for _ in range(2):
                dd = cv2.remap(depth, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                mx, my = src_coords(dd, tx, ty, zm)
            out = cv2.remap(bg_u8, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            if fg_img is not None:
                # subject = rigid "card" at its own depth: crisp, moves more than the background
                s = 1.0 + zm * (ZG + ZD * d_fg * a.fg_boost)
                sx = tx * A * (d_fg - focus) * a.fg_boost; sy = ty * A * 0.6 * (d_fg - focus) * a.fg_boost
                M = np.float32([[s, 0, W / 2 - s * cx + s * sx], [0, s, H / 2 - s * cy + s * sy]])
                fg = cv2.warpAffine(fg_img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
                al = cv2.warpAffine(fg_alpha, M, (W, H), flags=cv2.INTER_LINEAR)[..., None]
                out = (fg.astype(np.float32) * al + out.astype(np.float32) * (1 - al) + 0.5).astype(np.uint8)
            if dust is not None or a.light:
                o = out.astype(np.float32); t = f / a.fps
                if a.light:   # slow drifting warm glow (light leak), screen-blended
                    u = f / max(1, n - 1)
                    lx, ly = (0.15 + 0.7 * u) * W / 4, (0.25 + 0.1 * np.sin(u * np.pi)) * H / 4
                    g = np.exp(-(((gx - lx) ** 2 + (gy - ly) ** 2) / (2 * (0.28 * W / 4) ** 2))) * a.light
                    g = cv2.resize(g, (W, H))[..., None] * np.array([120, 190, 255], np.float32)
                    o = 255 - (255 - o) * (255 - g) / 255
                if dust is not None:
                    lay = dust.layer(t, tx, ty, scale(zm, 0.5), A, focus)[..., None] * a.dust_alpha
                    o = o + lay * (dust_col - o * 0.6)
                out = np.clip(o + 0.5, 0, 255).astype(np.uint8)
            ff.stdin.write(out.tobytes())
    finally:
        ff.stdin.close(); ff.wait()
    if ff.returncode:
        sys.exit("ffmpeg failed")
    print(f"  render {time.time()-t0:.1f}s  -> {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1080x1920", help="WxH or 'source'")
    ap.add_argument("--dur", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--move", default="dolly_in", choices=["dolly_in", "dolly_out", "pan_left", "pan_right", "orbit", "rise"])
    ap.add_argument("--strength", type=float, default=1.0, help="0.5 subtle .. 1.5 strong")
    ap.add_argument("--focus", type=float, default=0.35, help="depth (0 far..1 near) that stays still laterally")
    ap.add_argument("--cutout", action="store_true", help="cut the subject out and float it over a blurred background")
    ap.add_argument("--bg-blur", type=float, default=10.0, help="background blur sigma at 1080 px (cut-out mode)")
    ap.add_argument("--bg-dim", type=float, default=0.82, help="background brightness multiplier (cut-out mode)")
    ap.add_argument("--fg-boost", type=float, default=1.3, help="extra subject motion (cut-out mode)")
    ap.add_argument("--mask-model", default="birefnet-general-lite", help="rembg model (birefnet-portrait for people)")
    ap.add_argument("--depth-model", default=None, help="path to a Depth-Anything-V2-Small ONNX (default: download)")
    ap.add_argument("--dust", type=int, default=70, help="number of drifting dust/light specks (0 = off)")
    ap.add_argument("--dust-alpha", type=float, default=0.55, dest="dust_alpha")
    ap.add_argument("--light", type=float, default=0.18, help="drifting warm light-leak strength (0 = off)")
    ap.add_argument("--save-depth", default=None)
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--ffmpeg", default=os.environ.get("CINEKIT_FFMPEG", FFMPEG_DEFAULT))
    a = ap.parse_args()
    if not Path(a.ffmpeg).exists():
        sys.exit(f"ffmpeg not found: {a.ffmpeg}")
    render(a)


if __name__ == "__main__":
    main()
