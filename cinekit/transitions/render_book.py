"""TRANSITIONS BOOK renderer (runs on a GitHub ubuntu-24.04 runner, CPU / Mesa llvmpipe).

  python render_book.py --ids 1-20 --tag b1a --gl <dir of the gl-transitions npm package> [--local DIR]

Each item of book_spec.json -> a 3 s 640x360 30 fps H.264 preview between two of our Meta clips, number + name burned
in at the bottom. Kinds: gl (gl-transitions GLSL via moderngl), kit (our Meta overlays: screen blend / luma matte / dip),
xfade (ffmpeg), lottie (LottieFiles alpha used as a matte, fetched at render time, never stored),
nk (25 Nov: Nitish's transitions rebuilt frame by frame in nitish_tr.py; ids 101+; retimed Book variants 140+).
R2: reads only s2work/shorts1/clips/ ; writes only s2work/cinekit/transitions_book/ (previews/ + results/).
--local DIR: read clips from DIR/<basename> and skip R2 (for a laptop smoke test).
"""
import argparse, io, json, math, os, re, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "book_spec.json").read_text(encoding="utf-8"))
W, H = SPEC["size"]; FPS = SPEC["fps"]; N = FPS * SPEC["seconds"]
FONT = os.environ.get("BOOK_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
BUCKET = "eduorbex-media"
READ_OK = ("s2work/shorts1/clips/",)
WRITE_OK = SPEC["out_prefix"]
FF = os.environ.get("FFMPEG", "ffmpeg")
_S3 = None


def s3():
    global _S3
    if _S3 is None:
        import boto3
        _S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                           aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")
    return _S3


def r2_get(key, path, local=None):
    if not key.startswith(READ_OK) or ".." in key:
        sys.exit(f"refused read outside {READ_OK}: {key}")
    if local:
        return str(Path(local) / Path(key).name)
    if not Path(path).exists():
        s3().download_file(BUCKET, key, path)
    return path


def r2_put(path, key):
    if not key.startswith(WRITE_OK) or ".." in key:
        sys.exit(f"refused write outside {WRITE_OK}: {key}")
    ct = "video/mp4" if path.endswith(".mp4") else "application/json"
    s3().upload_file(path, BUCKET, key, ExtraArgs={"ContentType": ct})


# ---------------------------------------------------------------- frames
VF = f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgb24"


def decode(path, start, n):
    cmd = [FF, "-v", "error", "-ss", str(start), "-i", path, "-vf", VF, "-frames:v", str(n), "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if len(fr) == 0:
        raise RuntimeError(f"no frames from {path}")
    if len(fr) < n:
        fr = np.concatenate([fr, np.repeat(fr[-1:], n - len(fr), 0)])
    return fr


def encode(frames, out, label):
    lbl = out + ".txt"
    Path(lbl).write_text(label, encoding="utf-8")
    vf = (f"drawtext=fontfile={FONT}:textfile={Path(lbl).name}:fontsize=15:fontcolor=white:box=1:boxcolor=black@0.55:"
          f"boxborderw=6:x=(w-text_w)/2:y=h-text_h-12")
    cmd = [FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", Path(out).name]
    p = subprocess.run(cmd, input=np.ascontiguousarray(frames, np.uint8).tobytes(), capture_output=True, cwd=str(Path(out).parent))
    os.remove(lbl)
    if p.returncode:
        raise RuntimeError("encode: " + p.stderr.decode()[-300:])


def assemble(A, B, mid, D):
    """A[:pre] + mid (D frames) + B[D:...] = N frames; mid built from A[pre:pre+D] and B[:D]."""
    pre = (N - D) // 2
    return np.concatenate([A[:pre], mid, B[D:D + (N - pre - D)]])


def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- gl-transitions
class GL:
    def __init__(self, gldir):
        import moderngl
        self.mgl = moderngl
        self.meta = {t["name"]: t for t in json.loads((Path(gldir) / "gl-transitions.json").read_text(encoding="utf-8"))}
        self.gldir = Path(gldir)
        self.ctx = None
        for kw in ({"backend": "egl"}, {}):
            try:
                self.ctx = moderngl.create_standalone_context(require=330, **kw); break
            except Exception as e:
                print("  gl context", kw or "default", "failed:", str(e)[:120])
        if self.ctx is None:
            raise RuntimeError("no OpenGL context")
        print("  GL_RENDERER", self.ctx.info.get("GL_RENDERER"), self.ctx.info.get("GL_VERSION"))
        self.vbo = self.ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4").tobytes())
        self.fbo = self.ctx.simple_framebuffer((W, H), components=4)
        self.ta = self.ctx.texture((W, H), 3); self.tb = self.ctx.texture((W, H), 3)
        for t in (self.ta, self.tb):
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)

    def program(self, name):
        body = (self.gldir / "glsl3" / f"{name}.glsl").read_text(encoding="utf-8")
        frag_tail = ("uniform sampler2D u_from;\nuniform sampler2D u_to;\nuniform float progress;\nuniform float ratio;\n"
                     "in vec2 v_uv;\nout vec4 fragColor;\n"
                     "vec4 getFromColor(vec2 uv){ return texture(u_from, uv); }\n"
                     "vec4 getToColor(vec2 uv){ return texture(u_to, uv); }\n"
                     f"#line 1\n{body}\nvoid main(){{ fragColor = transition(v_uv); }}\n")
        vert_tail = "in vec2 pos;\nout vec2 v_uv;\nvoid main(){ v_uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }\n"
        err = None
        for head in ("#version 300 es\nprecision highp float;\nprecision highp int;\n", "#version 330\n"):
            try:
                return self.ctx.program(vertex_shader=head + vert_tail, fragment_shader=head + frag_tail)
            except Exception as e:
                err = e
        raise RuntimeError(f"compile {name}: {str(err)[:400]}")

    def render(self, name, A, B, params=None):
        prog = self.program(name)
        m = self.meta.get(name, {})
        types = m.get("paramsTypes", {})
        vals = dict(m.get("defaultParams", {})); vals.update(params or {})
        for k, v in vals.items():
            if k not in prog:
                continue
            t = types.get(k, "float")
            if t.startswith("ivec"):
                v = tuple(int(x) for x in v)
            elif t.startswith(("vec", "bvec")):
                v = tuple(v)
            elif t == "int":
                v = int(v)
            elif t == "bool":
                v = bool(v)
            prog[k].value = v
        if "ratio" in prog: prog["ratio"].value = W / H
        if "u_from" in prog: prog["u_from"].value = 0
        if "u_to" in prog: prog["u_to"].value = 1
        vao = self.ctx.vertex_array(prog, [(self.vbo, "2f", "pos")])
        D = len(A); out = np.empty((D, H, W, 3), np.uint8)
        self.fbo.use()
        for i in range(D):
            p = i / (D - 1) if D > 1 else 1.0
            self.ta.write(np.ascontiguousarray(A[i][::-1]).tobytes()); self.tb.write(np.ascontiguousarray(B[i][::-1]).tobytes())
            self.ta.use(0); self.tb.use(1)
            prog["progress"].value = p
            self.fbo.clear(0, 0, 0, 0)
            vao.render(self.mgl.TRIANGLE_STRIP)
            px = np.frombuffer(self.fbo.read(components=4), np.uint8).reshape(H, W, 4)[::-1].astype(np.float32)
            out[i] = np.clip(px[..., :3] * (px[..., 3:] / 255.0), 0, 255).astype(np.uint8)   # straight alpha over black
        vao.release(); prog.release()
        return out


# ---------------------------------------------------------------- mattes (kit luma / lottie alpha)
def matte_mix(A, B, M):
    m = M[..., None]
    return (A * (1 - m) + B * m).astype(np.uint8)


def window_from_coverage(M, D, name):
    """M: (F,H,W) 0..1 matte. Takes frames from first visible coverage to peak, resamples to D, forces a clean end."""
    c = M.reshape(len(M), -1).mean(1)
    peak = float(c.max())
    if peak < 0.75:
        raise RuntimeError(f"{name}: matte never covers the frame (max coverage {peak:.2f})")
    if c[0] > 0.9:
        raise RuntimeError(f"{name}: matte is full-frame from the first frame (background layer)")
    s = int(np.argmax(c > 0.02)); e = int(np.argmax(c >= 0.97 * peak))
    if e <= s:
        e = min(len(M) - 1, s + 1)
    idx = np.linspace(s, e, D).round().astype(int)
    Mw = M[idx]
    ramp = smooth(np.arange(D) / max(D - 1, 1), 0.6, 1.0)[:, None, None]
    print(f"  coverage window {s}->{e} of {len(M)}, peak {peak:.2f}")
    return Mw + (1 - Mw) * ramp


def kit(item, A, B, tmp, local):
    D = item["frames"]; pre = (N - D) // 2
    path = r2_get(SPEC["kit_prefix"] + item["name"] + "_1.mp4", f"{tmp}/kit_{item['name']}.mp4", local)
    O = decode(path, 0, 6 * FPS)
    L = O.astype(np.float32).mean(3) / 255.0
    mode = item.get("mode", "screen")
    if mode == "screen":
        lum = L.reshape(len(L), -1).mean(1)
        peak = int(lum.argmax()); s = int(np.clip(peak - D // 2, 0, len(O) - D)); cut = peak - s
        base = np.concatenate([A[pre:pre + cut], B[cut:D]]).astype(np.float32) / 255
        ov = O[s:s + D].astype(np.float32) / 255
        w = np.minimum(1, np.minimum(np.arange(D) + 1, D - np.arange(D)) / 5.0)[:, None, None, None]
        mid = (1 - (1 - base) * (1 - ov * w)) * 255
        print(f"  overlay peak frame {peak}, cut at {cut}/{D}")
        return assemble(A, B, mid.astype(np.uint8), D)
    if mode == "dip":
        s = max(0, len(O) // 2 - D // 2); F = O[s:s + D].astype(np.float32)
        base = np.concatenate([A[pre:pre + D // 2], B[D // 2:D]]).astype(np.float32)
        w = (np.sin(np.pi * np.arange(D) / (D - 1)) * 0.95)[:, None, None, None]
        return assemble(A, B, (base * (1 - w) + F * w).astype(np.uint8), D)
    if mode == "matte_inv":
        lo, hi = item.get("thr", [0.25, 0.75]); M = smooth(1 - L, lo, hi)
    else:  # matte: bright smoke reveals B
        hi = float(np.percentile(L, 98)) or 1.0
        M = smooth(L / hi, 0.06, 0.35)
    Mw = window_from_coverage(M, D, item["name"])
    return assemble(A, B, matte_mix(A[pre:pre + D].astype(np.float32), B[:D].astype(np.float32), Mw), D)


def lottie(item, A, B, tmp):
    from rlottie_python import LottieAnimation
    D = item["frames"]; pre = (N - D) // 2
    req = urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=60).read()
    if data[:2] == b"PK":
        zf = zipfile.ZipFile(io.BytesIO(data))
        js = [n for n in zf.namelist() if n.endswith(".json") and not n.endswith("manifest.json")]
        js.sort(key=lambda n: -zf.getinfo(n).file_size)
        data = zf.read(js[0])
    jp = f"{tmp}/lottie_{item['id']}.json"
    Path(jp).write_bytes(data)
    anim = LottieAnimation.from_file(jp)
    total = anim.lottie_animation_get_totalframe(); w, h = anim.lottie_animation_get_size()
    sc = max(W / w, H / h); rw, rh = math.ceil(w * sc), math.ceil(h * sc)
    x0, y0 = (rw - W) // 2, (rh - H) // 2
    M = np.empty((total, H, W), np.float32)
    for f in range(total):
        im = np.asarray(anim.render_pillow_frame(frame_num=f, width=rw, height=rh).convert("RGBA"))
        M[f] = im[y0:y0 + H, x0:x0 + W, 3] / 255.0
    os.remove(jp)
    print(f"  lottie {w}x{h} {total} frames -> cover {rw}x{rh}")
    Mw = window_from_coverage(M, D, item["name"])
    return assemble(A, B, matte_mix(A[pre:pre + D].astype(np.float32), B[:D].astype(np.float32), Mw), D)


def xfade(item, pa, pb):
    D = item["frames"]; pre = (N - D) // 2
    fc = (f"[0:v]{VF},trim=end_frame={N},setpts=PTS-STARTPTS[a];[1:v]{VF},trim=end_frame={N},setpts=PTS-STARTPTS[b];"
          f"[a][b]xfade=transition={item['name']}:duration={D / FPS:.4f}:offset={pre / FPS:.4f},trim=end_frame={N}[v]")
    cmd = [FF, "-v", "error", "-ss", "0.5", "-i", pa, "-ss", "0.3", "-i", pb, "-filter_complex", fc, "-map", "[v]",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if len(fr) < N:
        fr = np.concatenate([fr, np.repeat(fr[-1:], N - len(fr), 0)])
    return fr[:N]


# ---------------------------------------------------------------- main
def parse_ids(s):
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-"); ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return ids


def safe(n):
    return re.sub(r"[^A-Za-z0-9_]+", "_", n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True); ap.add_argument("--tag", required=True)
    ap.add_argument("--gl", default="gltr/package"); ap.add_argument("--local", default=None)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", a.tag):
        sys.exit("bad tag")
    ids = parse_ids(a.ids)
    items = [it for it in SPEC["items"] if it["id"] in ids]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); tmp = str(out / "_tmp"); Path(tmp).mkdir(exist_ok=True)
    pa = r2_get(SPEC["clip_a"], f"{tmp}/clip_a.mp4", a.local); pb = r2_get(SPEC["clip_b"], f"{tmp}/clip_b.mp4", a.local)
    A = decode(pa, 0.5, N); B = decode(pb, 0.3, N)
    gl = None
    results = []
    T0 = time.time()
    for it in items:
        t0 = time.time(); fn = f"{it['id']:03d}_{safe(it['name'])}.mp4"
        res = {"id": it["id"], "name": it["name"], "kind": it["kind"], "file": fn}
        try:
            D = it["frames"]; pre = (N - D) // 2
            if it["kind"] == "gl":
                if gl is None:
                    gl = GL(a.gl)
                if it["name"] not in gl.meta:
                    raise RuntimeError("not in gl-transitions " + SPEC["gl_version"])
                if gl.meta[it["name"]].get("textures"):
                    raise RuntimeError("needs an extra texture (skipped)")
                frames = assemble(A, B, gl.render(it["name"], A[pre:pre + D], B[:D], it.get("params")), D)
            elif it["kind"] == "kit":
                frames = kit(it, A, B, tmp, a.local)
            elif it["kind"] == "xfade":
                frames = xfade(it, pa, pb)
            elif it["kind"] == "lottie":
                frames = lottie(it, A, B, tmp)
            elif it["kind"] == "nk":   # 25 Nov: Nitish transitions rebuilt frame by frame (nitish_tr.py)
                import nitish_tr as NT
                if NT.length(it["nk"], FPS) != D:
                    raise RuntimeError(f"spec frames {D} != nitish_tr length {NT.length(it['nk'], FPS)}")
                frames = assemble(A, B, NT.render(it["nk"], A[pre:pre + D], B[:D], FPS, **(it.get("params") or {})), D)
            else:
                raise RuntimeError("unknown kind")
            label = f"{it['id']:03d}  {it['name']}  |  {it['category']}  |  {it['kind']}"
            encode(frames, str(out / fn), label)
            if not a.local:
                r2_put(str(out / fn), WRITE_OK + "previews/" + fn)
            res["status"] = "ok"
        except Exception as e:
            res["status"] = "failed"; res["error"] = str(e)[:500]
            print(f"WARNING {it['id']} {it['name']}: {res['error']}")
        res["seconds"] = round(time.time() - t0, 1)
        print(f"RENDER_TIME {it['id']:03d} {it['name']} {res['status']} {res['seconds']} s")
        results.append(res)
    print(f"TOTAL_RENDER_SECONDS {time.time() - T0:.0f}")
    rp = out / f"results_{a.tag}.json"
    rp.write_text(json.dumps(results, indent=1), encoding="utf-8")
    if not a.local:
        r2_put(str(rp), WRITE_OK + f"results/{a.tag}.json")
    ok = sum(r["status"] == "ok" for r in results)
    print(f"uploaded {ok}/{len(results)} previews")


if __name__ == "__main__":
    main()
