"""nitish_tr.py - Nitish Rajput's transitions rebuilt frame by frame (cinekit, 25 Nov 2026).

Recipes come from research/NITISH_TRANSITIONS_FRAME_ACCURATE.md (60-fps contact sheets). His frame counts are at 60 fps;
everything here runs at the clip fps (30 by default) and converts with f60(): 6 f at 60 = 3 f at 30.

API (numpy + OpenCV only):
    frames = render(name, A, B, fps=30, **params)
        A : outgoing frames (N,H,W,3) uint8, the LAST frames of shot A (len >= length(name))
        B : incoming frames (N,H,W,3) uint8, the FIRST frames of shot B (len >= length(name))
        returns length(name) frames that REPLACE A[-L:] + ... + B[:L] in this sense:
            output = A_full[:-L] + frames + B_full[L:]      (the cut sits inside 'frames', at cut_index(name))
        A[i] and B[i] are taken at the same output position i, so overlap transitions mix A[i] with B[i]
        and cut transitions use A[:c] then B[c:].
    names()        -> list of transition names
    info(name)     -> dict(type code, frames at 60, frames at fps, cut index, use, avoid)

CLI (join two clips with one transition, for tests / the long-video assembler):
    python nitish_tr.py whipsmear a.mp4 b.mp4 out.mp4 [--size 1920x1080] [--fps 30] [--hold 1.5]
"""
import math, os, subprocess, sys
import numpy as np
import cv2

FPS_REF = 60


def f60(n, fps):
    """frames at 60 fps -> frames at fps (at least 1)"""
    return max(1, int(round(n * fps / FPS_REF)))


def _ease_out(u, p=3):
    return 1 - (1 - np.clip(u, 0, 1)) ** p


def _ease_in(u, p=2):
    return np.clip(u, 0, 1) ** p


def _ease_io(u):
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


def _px(img, v):
    """v is given for a 1280-px-wide frame (the 720p contact sheets) -> pixels for this frame"""
    return v * img.shape[1] / 1280.0


def _hblur(img, sigma):
    if sigma < 0.5:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigmaX=float(sigma), sigmaY=0.01)


def _blur(img, sigma):
    if sigma < 0.5:
        return img
    return cv2.GaussianBlur(img, (0, 0), float(sigma))


def _affine(img, scale=1.0, dx=0.0, dy=0.0, rot=0.0, border=cv2.BORDER_REFLECT):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, scale)
    M[0, 2] += dx; M[1, 2] += dy
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=border)


def _zoom_blur(img, amount, n=6):
    """radial (zoom) blur: average of progressively scaled copies"""
    if amount < 0.005:
        return img
    acc = np.zeros(img.shape, np.float32)
    for k in range(n):
        acc += _affine(img, 1 + amount * k / (n - 1)).astype(np.float32)
    return (acc / n).astype(np.uint8)


def _mix(a, b, m):
    m = m[..., None] if m.ndim == 2 else m
    return (a.astype(np.float32) * (1 - m) + b.astype(np.float32) * m).clip(0, 255).astype(np.uint8)


def _exposure(img, gain, warm=0.0):
    x = img.astype(np.float32) * gain
    if warm:
        x[..., 0] *= 1 + warm; x[..., 2] *= 1 - warm * 0.6
    return x.clip(0, 255).astype(np.uint8)


def _vignette(h, w, strength=0.6, power=2.0):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    return 1 - strength * r ** power


def _rgb_split(img, off):
    o = int(round(off))
    out = img.copy()
    out[..., 0] = np.roll(img[..., 0], -o, axis=1)
    out[..., 2] = np.roll(img[..., 2], o, axis=1)
    return out


def _scanlines(img, period=4, depth=0.25):
    out = img.astype(np.float32)
    out[::period] *= 1 - depth
    return out.clip(0, 255).astype(np.uint8)


def _bands(img, rng, n_bands=(8, 20), shift=(0.02, 0.15), fringe=10):
    h, w = img.shape[:2]
    out = img.copy()
    n = rng.integers(*n_bands)
    cuts = np.sort(rng.choice(np.arange(4, h - 4), size=n - 1, replace=False))
    edges = [0] + list(cuts) + [h]
    for a, b in zip(edges[:-1], edges[1:]):
        if rng.random() < 0.35:
            continue
        s = int(rng.uniform(*shift) * w * rng.choice([-1, 1]))
        band = np.roll(img[a:b], s, axis=1)
        if fringe:
            band = _rgb_split(band, _px(img, fringe) * rng.uniform(0.5, 1.2))
        out[a:b] = band
    return out


def _fbm(h, w, seed, octaves=5):
    """fractal value noise in [0,1] (procedural ink / burn edge)"""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32); amp = 1.0; tot = 0.0
    for o in range(octaves):
        gh, gw = 3 * 2 ** o + 1, int(3 * 2 ** o * w / h) + 2
        g = rng.random((gh, gw)).astype(np.float32)
        acc += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC); tot += amp; amp *= 0.5
    acc /= tot
    return (acc - acc.min()) / max(1e-6, acc.max() - acc.min())


# ================================================================= transitions
# each: fn(A, B, fps, **kw) -> frames ; LEN[name](fps) -> length ; CUT[name](fps) -> index of B's first frame (or None for overlap)

def whipsmear(A, B, fps, direction=1, **kw):
    """T1 whip smear-in [GT do_whip]: B enters already moving, offset +25% -> +6% -> 0 of width with heavy horizontal blur
    collapsing in 2-3 f @60; A gets 1 f of smear. Alternate direction between consecutive whips. Whoosh peaks ~0.05-0.1 s after the cut."""
    w = A.shape[2]
    out = [_hblur(_affine(A[0], dx=-direction * 0.06 * w, border=cv2.BORDER_REPLICATE), _px(A[0], 30))]
    steps = [(0.25, 40), (0.06, 16), (0.0, 4)] if fps >= 50 else [(0.22, 40), (0.05, 12)]
    for k, (off, sg) in enumerate(steps):
        b = B[1 + k]
        out.append(_hblur(_affine(b, dx=direction * off * w, border=cv2.BORDER_REPLICATE), _px(b, sg)))
    return np.stack(out)
def whipsmear_len(fps): return 4 if fps >= 50 else 3


def crashout(A, B, fps, roll=1.5, start_scale=2.7, blur=18, **kw):
    """T2 presenter blur crash-out [GT eli_punch]: hard cut to B at ~200% + Gaussian blur ~20 px, ease-out (cubic) to 100% / 0 px
    in 16-20 f @60, with a +-1-2 deg roll that settles. His signature return to the presenter."""
    L = crashout_len(fps); out = []
    for i in range(L):
        u = i / (L - 1); e = 1 - _ease_out(u, 2.2)                         # extreme close-up -> medium in ~6 f @30
        img = _affine(B[i], scale=1 + (start_scale - 1) * e, rot=roll * e)
        out.append(_zoom_blur(_blur(img, _px(img, blur) * e), 0.10 * e))
    return np.stack(out)
def crashout_len(fps): return f60(12, fps)


def zoomout(A, B, fps, start_scale=2.2, **kw):
    """T13 zoom-out reveal [GT do_3425]: B starts tight on a detail, eases out to context in 0.3-0.4 s (no blur)"""
    L = f60(14, fps); out = []
    for i in range(L):
        e = 1 - _ease_out(i / (L - 1), 2.5)
        out.append(_zoom_blur(_affine(B[i], scale=1 + (start_scale - 1) * e), 0.08 * e))
    return np.stack(out)


def mirrorzoom(A, B, fps, **kw):
    """T3 mirror-edge zoom-through [GT eli_whip]: A scales 100->130% over its last 3-4 f @60 with mirrored edges + radial blur,
    B starts zoomed + radially blurred and settles over 8-12 f @60."""
    na, nb = f60(4, fps), f60(10, fps); out = []
    for i in range(na):
        u = (i + 1) / na
        out.append(_zoom_blur(_affine(A[i], scale=1 + 0.3 * u, border=cv2.BORDER_REFLECT), 0.12 * u))
    for j in range(nb):
        e = 1 - _ease_out((j + 1) / nb, 2)
        out.append(_zoom_blur(_affine(B[na + j], scale=1 + 0.25 * e, border=cv2.BORDER_REFLECT), 0.12 * e))
    return np.stack(out)


def white(A, B, fps, vignette=False, **kw):
    """T4 white frame insert [GT sumitra_250, mohit_1529]: 6 f @60 of solid white, hard on / hard off, then a hard cut to B.
    vignette=True: the Mohit variant (soft dark oval at the edges). Boom + whoosh start on the white frame."""
    n = f60(6, fps); out = []
    h, w = A.shape[1:3]
    wf = np.full((h, w, 3), 255, np.uint8)
    if vignette:
        wf = (wf.astype(np.float32) * _vignette(h, w, 0.45, 2.5)[..., None]).astype(np.uint8)
    for i in range(n):
        out.append(wf)
    return np.stack(out)


def flashbang(A, B, fps, **kw):
    """T5 flashbang decay [GT fd_0013]: A jumps near-white in 1 f (darker corners), holds 4 f, decays exponentially over ~30 f @60
    to a desaturated, low-contrast, slightly blurred A with a 2 f glow flicker; then a hard cut with a short RGB glitch into B."""
    hold, dec, gl = f60(4, fps), f60(30, fps), f60(4, fps)
    h, w = A.shape[1:3]; vg = _vignette(h, w, 0.5, 2.0)[..., None]
    out = []
    for i in range(hold + dec):
        a = A[i].astype(np.float32)
        k = 1.0 if i < hold else math.exp(-(i - hold) / (dec * 0.3))
        if i == hold + dec // 3: k = min(1, k + 0.3)                     # glow flicker
        gray = a.mean(2, keepdims=True)
        lowc = (gray + (a - gray) * 0.35) * 0.85 + 20                   # desaturated, low contrast
        x = lowc * (1 - k) + (255 * vg + a * 0.15) * k
        out.append(_blur(x.clip(0, 255).astype(np.uint8), _px(A[0], 3) * (1 - k)))
    base = hold + dec
    for j in range(gl):
        b = B[base + j]
        out.append(_scanlines(_rgb_split(_exposure(b, 1.25), _px(b, 14) * (1 - j / gl)), 3, 0.3))
    return np.stack(out)


def rgbimpact(A, B, fps, echo=True, **kw):
    """T6 exposure-blip + RGB impact cut [GT fd_glitch]: on A ~0.2 s before the cut 2 f @60 blown out (warm), then 2-4 f normal;
    hard cut; 2 f darker; 2-4 f BLOWN-OUT RGB split ~10-15 px + scanlines + magenta/cyan fringe (near white), decaying over ~6 f
    into a desaturated, glowing B that carries echo trails (frame blending). Impact + glitch crackle."""
    na = f60(12, fps); blip = f60(2, fps); dark = f60(2, fps); split = f60(4, fps); decay = f60(8, fps); tail = f60(12, fps)
    out = []
    for i in range(na):
        out.append(_exposure(A[i], 1.9, warm=0.12) if i < blip else A[i])
    c = na; acc = None; n = dark + split + decay + tail
    for j in range(n):
        b = B[c + j].astype(np.float32)
        if echo:   # echo trails: running blend of past frames
            acc = b if acc is None else acc * 0.7 + b * 0.3
            b = b * 0.45 + acc * 0.55
        g = b.mean(2, keepdims=True); b = g + (b - g) * 0.45            # desaturated impact look
        glow = _blur(b.clip(0, 255).astype(np.uint8), _px(A[0], 18)).astype(np.float32)
        if j < dark:
            k = 0.0; b = b * 0.55
        elif j < dark + split:
            k = 1.0
        else:
            k = max(0.0, 1 - (j - dark - split + 1) / (decay + 1)) ** 1.5
        lift = 0.32 * k
        x = 255 - (255 - b) * (255 - glow * (0.6 + 1.4 * k)) / 255     # screen with its own glow (bloom)
        x = x * (1 - lift) + 255 * lift
        x[..., 0] += 25 * k; x[..., 2] += 30 * k; x[..., 1] -= 10 * k  # magenta tint while blown
        xi = x.clip(0, 255).astype(np.uint8)
        if k > 0.05:
            xi = _scanlines(_rgb_split(xi, _px(xi, 20) * k + 3), 2, 0.45 * k)
        out.append(xi)
    return np.stack(out)


def sliceglitch(A, B, fps, seed=3, **kw):
    """T7 horizontal slice / datamosh glitch [GT do_hglitch, eli_62475]: 8-20 horizontal bands shifted 2-15% of width with RGB
    fringe, in bursts of 8-11 f @60; heavy blocky datamosh bridging the cut (12-14 f @60) that resolves into B. Glitch/static SFX."""
    rng = np.random.default_rng(seed)
    burst, gap, bridge = f60(8, fps), f60(2, fps), f60(6, fps)
    out = []
    for i in range(burst):
        out.append(_bands(A[i], rng))
    for i in range(gap):
        out.append(A[burst + i])
    c = burst + gap
    h, w = A.shape[1:3]
    for j in range(bridge):
        u = j / max(1, bridge - 1)
        src = A[c + j] if u < 0.4 else B[c + j]
        blk = max(4, int(_px(src, 24) * (1 - abs(u - 0.4))))
        small = cv2.resize(src, (max(1, w // blk), max(1, h // blk)), interpolation=cv2.INTER_AREA)
        mosh = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        mosh = (mosh // 32) * 32 + 16                                  # posterize
        k = 1 - abs(u - 0.4) / 0.6
        x = _mix(src, mosh, np.full((h, w), np.clip(k, 0, 1), np.float32))
        out.append(_bands(x, rng, shift=(0.02, 0.1)))
    return np.stack(out)


def cutblack(A, B, fps, hold=0.8, **kw):
    """T8a cut-to-black pause [GT eli_45113]: hard cut to pure black held 0.5-1.8 s before a reveal line (narration pauses / sub drop)"""
    n = max(1, int(round(hold * fps)))
    return np.zeros((n,) + A.shape[1:], np.uint8)


def teaserstop(A, B, fps, hold=1.8, **kw):
    """T8b teaser stop [GT fd_black]: vignette closes in over ~6 f @60, 2 f red tint pulse, fade to black over ~6 f, black held ~1.8 s"""
    nv, nr, nf = f60(6, fps), f60(2, fps), f60(6, fps); nh = int(round(hold * fps))
    h, w = A.shape[1:3]; out = []
    for i in range(nv):
        u = (i + 1) / nv
        out.append((A[i].astype(np.float32) * _vignette(h, w, 0.4 + 0.5 * u, 1.6)[..., None]).astype(np.uint8))
    vg = _vignette(h, w, 0.9, 1.6)[..., None]
    for i in range(nr):
        a = A[nv + i].astype(np.float32) * vg; a[..., 0] = a[..., 0] * 1.3 + 30; a[..., 1:] *= 0.6
        out.append(a.clip(0, 255).astype(np.uint8))
    for i in range(nf):
        k = 1 - (i + 1) / nf
        out.append((A[nv + nr + i].astype(np.float32) * vg * k).astype(np.uint8))
    out += [np.zeros_like(A[0])] * nh
    return np.stack(out)


_DEPTH_CACHE = {}


def _layers(img, n=3):
    """depth layers (far->near) for the parallax push; Depth-Anything if available, else a luminance/position guess"""
    key = (img.shape, int(img[::17, ::17].sum()))
    if key in _DEPTH_CACHE:
        d = _DEPTH_CACHE[key]
    else:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
            from parallax import estimate_depth
            d = estimate_depth(img)
        except Exception as e:   # no model on this machine: lower in frame = nearer
            print("  depth fallback:", str(e)[:80])
            h, w = img.shape[:2]
            d = np.repeat(np.linspace(0, 1, h, dtype=np.float32)[:, None], w, 1)
        _DEPTH_CACHE[key] = d
    qs = np.quantile(d, np.linspace(0, 1, n + 1)[1:-1])
    masks = []
    for k in range(n):
        lo = -1 if k == 0 else qs[k - 1]
        m = (d > lo).astype(np.float32)
        masks.append(cv2.GaussianBlur(m, (0, 0), 2))
    return masks   # masks[k] = everything at depth >= layer k (layer 0 = whole frame)


def _shift(img, dx, border=cv2.BORDER_CONSTANT):
    return _affine(img, dx=dx, border=border)


def parallaxpush(A, B, fps, direction=-1, **kw):
    """T9 parallax cut-out push [GT fd_104718]: A's layers slide out right->left together at different speeds (foreground cut-out
    fastest), B's background is revealed behind with a slight drift and B's subject (near layer) enters from the right edge;
    ~30 f @60, ease-out, with motion blur on the moving layers. Whoosh."""
    L = f60(30, fps); h, w = A.shape[1:3]
    la = _layers(A[0]); lb = _layers(B[min(len(B) - 1, L)])
    ones = np.ones((h, w), np.float32)
    out = []; prev = 0.0
    for i in range(L):
        e = _ease_out((i + 1) / L, 3); v = (e - prev) * w; prev = e
        mb = min(_px(A[0], 40), v * 0.35)                               # motion blur ~ per-frame speed
        def lay(img, m, dx):
            ii = _hblur(_shift(img, dx), mb * abs(dx) / max(1, abs(dx) + 1)).astype(np.float32)
            mm = _hblur((_shift((m * 255).astype(np.uint8), dx)), mb).astype(np.float32)[..., None] / 255
            return ii, mm
        frame = _shift(B[i], direction * -0.18 * w * (1 - e), cv2.BORDER_REFLECT).astype(np.float32)   # B background drifts in slowly
        for img, m, dx in ((A[i], ones, direction * w * 1.0 * e), (A[i], la[1], direction * w * 1.15 * e),
                           (B[i], lb[2], direction * -w * 0.85 * (1 - e)), (A[i], la[2], direction * w * 1.35 * e)):
            ii, mm = lay(img, m, dx)
            frame = frame * (1 - mm) + ii * mm
        out.append(frame.clip(0, 255).astype(np.uint8))
    return np.stack(out)


def defocuspush(A, B, fps, **kw):
    """T10 defocus-out + vertical push [GT do_117797]: A ramps Gaussian blur 0->12 px over 8 f @60, B pushes up from below covering
    in 3-6 f @60 (2 f half/half split)."""
    nb, npsh = f60(8, fps), f60(6, fps); h = A.shape[1]; out = []
    for i in range(nb):
        out.append(_blur(A[i], _px(A[i], 12) * (i + 1) / nb))
    for j in range(npsh):
        e = _ease_out((j + 1) / npsh, 2)
        y = int(round(h * (1 - e)))
        fr = _blur(A[nb + j], _px(A[0], 12)).copy()
        bb = B[nb + j]
        if y < h: fr[y:] = bb[:h - y]
        out.append(fr)
    return np.stack(out)


def dissolvemotion(A, B, fps, **kw):
    """T14 cross-dissolve with continuing motion [GT do_7820]: linear 20-24 f @60 while both layers keep moving (place/time shift)"""
    L = f60(22, fps); h, w = A.shape[1:3]
    return np.stack([_mix(A[i], B[i], np.full((h, w), (i + 1) / (L + 1), np.float32)) for i in range(L)])


def bloom(A, B, fps, **kw):
    """T15 light bloom bridge [GT mohit_554]: A ramps a warm bloom (screen-blended blurred copy) over 12 f @60, hard cut to B which
    starts overexposed and warm and decays over ~20 f @60"""
    na, nb = f60(12, fps), f60(20, fps); out = []
    for i in range(na):
        u = (i + 1) / na; a = A[i].astype(np.float32) / 255
        g = _blur(A[i], _px(A[i], 30)).astype(np.float32) / 255 * np.array([1.0, 0.85, 0.6], np.float32)
        x = 1 - (1 - a) * (1 - g * u * 1.2)
        out.append((x * 255 * (1 + 0.3 * u)).clip(0, 255).astype(np.uint8))
    for j in range(nb):
        k = 1 - _ease_out((j + 1) / nb, 2)
        out.append(_exposure(B[na + j], 1 + 0.9 * k, warm=0.15 * k))
    return np.stack(out)


def gridsnake(A, B, fps, cols=4, rows=3, **kw):
    """T16 grid tile snake [GT fd_grid, mohit_254]: 4x3 tiles switch A->B one at a time in serpentine order, 2 f @60 per tile,
    the leading tile flashes white for 1-2 f @60 first. A tick per tile."""
    per = max(1, 2 * fps / FPS_REF)                                     # tiles per output frame inverse
    n = cols * rows; L = int(math.ceil(n * per)) + 1
    h, w = A.shape[1:3]
    order = []
    for r in range(rows):
        cs = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
        order += [(r, c) for c in cs]
    out = []
    for i in range(L):
        done = int(math.floor(i / per)); fr = A[i].copy()
        for t, (r, c) in enumerate(order):
            y0, y1 = r * h // rows, (r + 1) * h // rows; x0, x1 = c * w // cols, (c + 1) * w // cols
            if t < done: fr[y0:y1, x0:x1] = B[i][y0:y1, x0:x1]
            elif t == done: fr[y0:y1, x0:x1] = _exposure(B[i][y0:y1, x0:x1], 2.4)   # leading tile: overexposed flash
        out.append(fr)
    return np.stack(out)


def inkburn(A, B, fps, origin=(0.62, 0.45), edge="paper", seed=11, **kw):
    """T18 ink-bleed / burn-hole matte from the subject [GT do_98463, eli_95630]: an organic mask grows from the point that matters
    (map pin, envelope), slow then accelerating, filling in ~24-30 f @60; irregular edge with a charred dark rim (edge='char')
    or a thin light paper fringe (edge='paper'). Paper/burn whoosh."""
    L = f60(28, fps); h, w = A.shape[1:3]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - origin[0] * w) / w) ** 2 + ((yy - origin[1] * h) / w) ** 2)
    d = d / d.max()
    field = d + 0.22 * (_fbm(h, w, seed) - 0.5)
    out = []
    for i in range(L):
        r = -0.15 + 1.4 * _ease_in((i + 1) / L, 2.2)
        m = np.clip((r - field) / 0.015, 0, 1)
        rim = np.clip(1 - np.abs(r - field) / 0.035, 0, 1) * (1 - m * 0.3)
        fr = _mix(A[i], B[i], m).astype(np.float32)
        if edge == "char":
            fr = fr * (1 - 0.85 * rim[..., None]); fr += rim[..., None] * np.array([60, 25, 0], np.float32) * 0.5
        else:
            fr = fr * (1 - 0.6 * rim[..., None]) + 245 * 0.6 * rim[..., None]
        out.append(fr.clip(0, 255).astype(np.uint8))
    return np.stack(out)


def xreveal(A, B, fps, border=True, **kw):
    """T17 panel-border shape reveal, X version [GT do_18275]: the X-shaped gap widens over ~30 f @60, ease-in-out, showing B inside"""
    L = f60(30, fps); h, w = A.shape[1:3]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    dist = np.minimum(np.abs(u - v), np.abs(u + v)) / math.sqrt(2)     # distance to the two diagonals
    out = []
    for i in range(L):
        e = _ease_io((i + 1) / L); half = 0.004 + 0.75 * e
        m = np.clip((half - dist) / 0.004, 0, 1)
        fr = _mix(A[i], B[i], m)
        if border and e < 0.97:
            bd = np.clip(1 - np.abs(dist - half) / 0.006, 0, 1)
            fr = _mix(fr, np.full_like(fr, 255), bd * 0.95)
        out.append(fr)
    return np.stack(out)


def spire(A, B, fps, **kw):
    """T17 spire version [GT fd_triangle]: a thin triangle grows up from the bottom centre, then widens to fill (~24 f @60)"""
    L = f60(24, fps); h, w = A.shape[1:3]; out = []
    for i in range(L):
        e = _ease_in((i + 1) / L, 1.6)
        top = h * (1 - min(1, e * 1.8)); half = w * (0.01 + 1.4 * max(0, e - 0.25) ** 1.3)
        m = np.zeros((h, w), np.uint8)
        pts = np.array([[w / 2 - half, h], [w / 2 + half, h], [w / 2 + half * 0.15, top], [w / 2 - half * 0.15, top]], np.int32)
        cv2.fillPoly(m, [pts], 255)
        m = cv2.GaussianBlur(m, (0, 0), 1.2).astype(np.float32) / 255
        fr = _mix(A[i], B[i], m)
        edge = cv2.Canny((m * 255).astype(np.uint8), 50, 150)
        edge = cv2.GaussianBlur(cv2.dilate(edge, None), (0, 0), 1.5).astype(np.float32) / 255
        fr = (fr.astype(np.float32) + edge[..., None] * np.array([255, 140, 30], np.float32)).clip(0, 255).astype(np.uint8)
        out.append(fr)
    return np.stack(out)


def cut(A, B, fps, **kw):
    """control: plain hard cut (1 frame of B)"""
    return B[:1].copy()


T = {   # name: (fn, type code, frames @60, use, avoid, cut-based?)
    "whipsmear": (whipsmear, "T1", 4, "presenter->B-roll, place to place, action, teaser montage (alternate direction)", "documents, emotional beats", True),
    "crashout": (crashout, "T2", 18, "return to the presenter / face in the teaser; punch into a key face or object", "back-to-back; calm exposition", True),
    "zoomout": (zoomout, "T13", 22, "reveal context after a detail (document word, face, pin)", "action montage", True),
    "mirrorzoom": (mirrorzoom, "T3", 14, "push THROUGH into the next scene (place change, 'go inside')", "documents; with another zoom next to it", True),
    "white": (white, "T4", 6, "shock line, action hit, punchline (teaser mostly)", "calm body, more than once in 30 s in body", True),
    "flashbang": (flashbang, "T5", 38, "single teaser shock beat (crash, blast, 'struggle')", "anything calm; more than once a video section", True),
    "rgbimpact": (rgbimpact, "T6", 48, "impact into an action/danger shot, surveillance/tech beat", "history, family/emotional beats", True),
    "sliceglitch": (sliceglitch, "T7", 24, "tech/system/surveillance, glitch reframe of the same subject, danger entry", "history, emotion (rarest effect in body)", True),
    "cutblack": (cutblack, "T8a", 48, "long-form only: pause before a reveal line, death/loss reveal, chapter end", "Shorts (no 1 s black+silence stops)", True),
    "teaserstop": (teaserstop, "T8b", 122, "long-form only: end of the teaser before the presenter/title", "Shorts", True),
    "parallaxpush": (parallaxpush, "T9", 30, "between two photos / AI stills of the same story (place or person change)", "fast montage", False),
    "defocuspush": (defocuspush, "T10", 14, "time jump or 'meanwhile' scene change", "action hits", True),
    "dissolvemotion": (dissolvemotion, "T14", 22, "time passing, place shift, emotional/family beats, ending", "action, teaser, Shorts body", False),
    "bloom": (bloom, "T15", 32, "into the past / memory, title drop, chapter stop (warm)", "danger scenes, documents", True),
    "gridsnake": (gridsnake, "T16", 25, "teaser / title card exit, list of names, 'case file' reveal", "body exposition (2 per video)", False),
    "inkburn": (inkburn, "T18", 28, "reveal starting from the subject (map pin, letter, photo) with a position match", "action; more than ~8 per video", False),
    "xreveal": (xreveal, "T17", 30, "from a 4-panel / split-screen collage to the next scene", "single full-frame shots without panels", False),
    "spire": (spire, "T17", 24, "dramatic reveal of a person or monument (B&W cut-out)", "fast montage", False),
    "cut": (cut, "-", 1, "default (2/3 of all cuts)", "-", True),
}


def names():
    return list(T)


def length(name, fps=30):
    if name == "whipsmear": return whipsmear_len(fps)
    if name == "crashout": return crashout_len(fps)
    if name == "gridsnake": return int(math.ceil(12 * max(1, 2 * fps / FPS_REF))) + 1
    if name == "cutblack": return int(round(0.8 * fps))
    if name == "teaserstop": return f60(6, fps) * 2 + f60(2, fps) + int(round(1.8 * fps))
    if name == "cut": return 1
    return {"zoomout": f60(14, fps), "mirrorzoom": f60(4, fps) + f60(10, fps), "white": f60(6, fps),
            "flashbang": f60(4, fps) + f60(30, fps) + f60(4, fps),
            "rgbimpact": f60(12, fps) + f60(2, fps) + f60(4, fps) + f60(8, fps) + f60(12, fps),
            "sliceglitch": f60(8, fps) + f60(2, fps) + f60(6, fps), "parallaxpush": f60(30, fps),
            "defocuspush": f60(8, fps) + f60(6, fps), "dissolvemotion": f60(22, fps), "bloom": f60(12, fps) + f60(20, fps),
            "inkburn": f60(28, fps), "xreveal": f60(30, fps), "spire": f60(24, fps)}[name]


def info(name, fps=30):
    fn, code, n60, use, avoid, cutb = T[name]
    return {"name": name, "type": code, "frames_60": n60, "frames": length(name, fps), "cut_based": cutb, "use": use, "avoid": avoid,
            "recipe": (fn.__doc__ or "").strip()}


def render(name, A, B, fps=30, **kw):
    L = length(name, fps)
    if len(A) < L or len(B) < L:
        raise ValueError(f"{name}: needs {L} frames of A and B")
    fr = T[name][0](A[:L], B[:L], fps, **kw)
    assert len(fr) == L, (name, len(fr), L)
    return fr


# ---------------------------------------------------------------- CLI: join two clips
def _decode(ff, path, size, fps, n, start=0.0):
    W, H = size
    vf = f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgb24"
    raw = subprocess.run([ff, "-v", "error", "-ss", str(start), "-i", path, "-vf", vf, "-frames:v", str(n), "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if len(fr) < n: fr = np.concatenate([fr, np.repeat(fr[-1:], n - len(fr), 0)])
    return fr


def main():
    import argparse
    ap = argparse.ArgumentParser(description="join two clips with one Nitish transition")
    ap.add_argument("name", choices=names()); ap.add_argument("a"); ap.add_argument("b"); ap.add_argument("out")
    ap.add_argument("--size", default="1920x1080"); ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hold", type=float, default=1.5, help="seconds of A before and of B after the transition")
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    a = ap.parse_args()
    W, H = map(int, a.size.split("x")); L = length(a.name, a.fps); h = int(a.hold * a.fps)
    A = _decode(a.ffmpeg, a.a, (W, H), a.fps, h + L); B = _decode(a.ffmpeg, a.b, (W, H), a.fps, h + L)
    fr = np.concatenate([A[:h], render(a.name, A[h:h + L], B[:L], a.fps), B[L:L + h]])
    p = subprocess.run([a.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(a.fps), "-i", "-",
                        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", a.out], input=fr.tobytes())
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
