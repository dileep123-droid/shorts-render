"""make_map.py - cinematic MAP FLY-TO clip generator (standalone).

MapLibre GL JS (BSD-3, local npm copy) + OpenFreeMap vector tiles (OSM, ODbL) + Natural Earth shaded relief
(public domain, served by OpenFreeMap), rendered frame-by-frame in a fresh headless Playwright Chromium and
piped straight into ffmpeg (no temp frames on disk).

Usage:
  python make_map.py route.json --out E:/path/map_x --format both        # writes map_x_9x16.mp4 + map_x_16x9.mp4
  python make_map.py route.json --out E:/path/map_x.mp4 --format vertical

route.json is either a list of points or {"points":[...], "title": "...", <any option below>}
  point: {"name": "Kathmandu", "lat": 27.70, "lon": 85.36, "label": "24 Dec 1999", "km": 1100 | "auto" | null,
          "side": "left|right|top|bottom" (optional label side)}
  "km" on a point = the distance tag drawn on the leg ARRIVING at that point ("auto" = great-circle distance).
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
import shutil
FFMPEG_DEFAULT = r"C:/ffmpeg-master-latest-win64-gpl-shared/bin/ffmpeg.exe"
if not Path(FFMPEG_DEFAULT).exists():
    FFMPEG_DEFAULT = shutil.which("ffmpeg") or "ffmpeg"   # Linux / GitHub runner

DEFAULTS = dict(
    fps=30, intro_sec=0.7, leg_sec=1.9, hold_sec=0.6, outro_sec=1.3, km_linger=0.5,
    stop_zoom=5.3, pitch=32, bearing_drift=10, fit_padding=90, close_km=250,
    accent="#ffc928", route_color="#ffffff", land="#16191e", water="#0a1520", border="#d9b26f",
    relief=True, relief_opacity=0.55, city_names=False, plane=True,
    title=None, title_top=0.085, scale=1.0, frame_timeout_ms=20000,
    style_url="https://tiles.openfreemap.org/styles/dark",
    credit="© OpenStreetMap contributors · OpenFreeMap · Natural Earth",
)
FORMATS = {"vertical": (540, 960, "9x16"), "horizontal": (960, 540, "16x9")}  # CSS px, rendered at DPR 2


def load_cfg(path, overrides):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg = dict(DEFAULTS)
    if isinstance(data, list):
        cfg["points"] = data
    else:
        cfg.update({k: v for k, v in data.items()})
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if len(cfg.get("points", [])) < 2:
        sys.exit("need at least 2 points")
    return cfg


def render(cfg, fmt, out_mp4, ffmpeg, crf, headful=False):
    from playwright.sync_api import sync_playwright
    w, h, _ = FORMATS[fmt]
    if fmt == "horizontal" and cfg.get("title_top") == DEFAULTS["title_top"]:
        cfg = dict(cfg, title_top=0.06)
    vendor = HERE / "node_modules" / "maplibre-gl" / "dist"
    files = {"/index.html": (HERE / "map.html", "text/html"),
             "/vendor/maplibre-gl.js": (vendor / "maplibre-gl.js", "application/javascript"),
             "/vendor/maplibre-gl.css": (vendor / "maplibre-gl.css", "text/css")}

    def handle(route):
        p = "/" + route.request.url.split("://cinekit.local/", 1)[1].split("?")[0]
        if p in files:
            f, ct = files[p]
            route.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ct})
        else:
            route.fulfill(status=404, body=b"")

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(cfg["fps"]), "-c:v", "png", "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           str(out_mp4)]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful, args=[
            "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--use-angle=swiftshader", "--enable-webgl",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding"])
        try:
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2)
            page = ctx.new_page()
            page.on("console", lambda m: m.type in ("error",) and print("  [page]", m.text[:200]))
            page.route("http://cinekit.local/**", handle)
            page.goto("http://cinekit.local/index.html")
            if not page.evaluate("!!document.createElement('canvas').getContext('webgl2')"):
                raise RuntimeError("WebGL2 not available in headless Chromium (swiftshader flags failed)")
            info = page.evaluate("cfg => window.setupMap(cfg)", cfg)
            n = info["nFrames"]
            print(f"  {fmt}: {w*2}x{h*2}, {info['total']:.2f}s, {n} frames")
            ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            t0 = time.time(); slow = 0
            try:
                for f in range(n):
                    waited = page.evaluate("f => window.renderFrame(f)", f)
                    if waited >= cfg["frame_timeout_ms"]:
                        slow += 1
                    ff.stdin.write(page.screenshot(type="png"))
                    if f % 60 == 0:
                        print(f"    frame {f}/{n}  ({time.time()-t0:.0f}s)", flush=True)
            finally:
                ff.stdin.close(); ff.wait()
            print(f"  RENDER_TIME {fmt} {time.time()-t0:.1f}s for {n} frames", flush=True)
            if slow:
                print(f"  WARNING: {slow} frames hit the tile-load timeout (may show missing tiles)")
            if ff.returncode != 0:
                raise RuntimeError("ffmpeg failed")
        finally:
            browser.close()
    print("  wrote", out_mp4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("route_json")
    ap.add_argument("--out", required=True, help="output .mp4, or a stem when --format both")
    ap.add_argument("--format", choices=["vertical", "horizontal", "both"], default="both")
    ap.add_argument("--ffmpeg", default=os.environ.get("CINEKIT_FFMPEG", FFMPEG_DEFAULT))
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--fps", type=int); ap.add_argument("--leg-sec", type=float, dest="leg_sec")
    ap.add_argument("--hold-sec", type=float, dest="hold_sec"); ap.add_argument("--stop-zoom", type=float, dest="stop_zoom")
    ap.add_argument("--pitch", type=float); ap.add_argument("--title")
    ap.add_argument("--accent"); ap.add_argument("--no-relief", action="store_true")
    ap.add_argument("--headful", action="store_true", help="debug: show the browser")
    a = ap.parse_args()
    if not Path(a.ffmpeg).exists():
        sys.exit(f"ffmpeg not found: {a.ffmpeg} (use --ffmpeg or CINEKIT_FFMPEG)")
    ov = dict(fps=a.fps, leg_sec=a.leg_sec, hold_sec=a.hold_sec, stop_zoom=a.stop_zoom, pitch=a.pitch,
              title=a.title, accent=a.accent, relief=(False if a.no_relief else None))
    cfg = load_cfg(a.route_json, ov)
    fmts = ["vertical", "horizontal"] if a.format == "both" else [a.format]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for fmt in fmts:
        if a.format == "both" or out.suffix.lower() != ".mp4":
            stem = out.with_suffix("") if out.suffix.lower() == ".mp4" else out
            target = stem.parent / f"{stem.name}_{FORMATS[fmt][2]}.mp4"
        else:
            target = out
        render(dict(cfg), fmt, target, a.ffmpeg, a.crf, a.headful)


if __name__ == "__main__":
    main()
