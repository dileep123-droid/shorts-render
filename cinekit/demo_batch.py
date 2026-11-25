"""cinekit demo batch (GitHub runner, 25 Nov): run several timeline / photo-motion renders in one job, upload the results and
(optional, when VERTEX_TOKEN is set) ask Gemini whether each moving photo still reads as a frozen still.

  python demo_batch.py batch.json        batch.json = {"out": "s2work/cinekit/demo25/", "jobs": [
        {"tool": "timeline", "spec": "timeline/examples/ic814_en.json", "name": "tl_ic814_en", "sizes": "1920x1080,1080x1920",
         "plate": "s2work/cinekit/stills/v2_coast.jpg"},
        {"tool": "photo", "image": "s2work/cinekit/stills/v2_meta_person.jpg", "name": "pm_person_cutout",
         "args": "--mode cutout --size 1080x1920 --dur 5", "next": null}]}
"""
import base64, json, os, shlex, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

BUCKET = "eduorbex-media"
HERE = Path(__file__).resolve().parent
FF = os.environ.get("FFMPEG", "ffmpeg")


def s3():
    import boto3
    return boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")


S3 = s3()


def get(key, path):
    assert key.startswith("s2work/cinekit/") and ".." not in key, key
    S3.download_file(BUCKET, key, path); return path


def put(path, key, out):
    assert key.startswith(out) and out.startswith("s2work/cinekit/") and ".." not in key, key
    ct = "video/mp4" if path.endswith(".mp4") else ("video/quicktime" if path.endswith(".mov") else "application/json")
    S3.upload_file(path, BUCKET, key, ExtraArgs={"ContentType": ct})


def still_check(mp4, kind):
    """Gemini watches a 540-px copy at 10 fps: does it read as a moving camera shot or as a frozen still?"""
    if not os.environ.get("VERTEX_TOKEN"): return None
    small = mp4[:-4] + "_chk.mp4"
    subprocess.run([FF, "-v", "error", "-y", "-i", mp4, "-vf", "scale=-2:540", "-c:v", "libx264", "-crf", "26", "-an", small], check=True)
    prompt = ("This short clip was made from a single still photo (or is an animated timeline graphic). As a strict video editor, judge "
              "whether it reads as LIVE moving footage or as a frozen still / slideshow. Answer ONLY JSON: {\"motion_visible\": 0-10, "
              "\"looks_like_a_still\": true/false, \"depth_feel\": 0-10, \"artifacts\": \"...\", \"pro_quality\": 0-10, \"why\": \"one sentence\"}")
    if kind == "timeline":
        prompt = ("This is an animated on-screen TIMELINE graphic for a documentary (like Nitish Rajput's). Judge it as a motion designer. "
                  "Answer ONLY JSON: {\"readability\": 0-10, \"animation_quality\": 0-10, \"pro_quality\": 0-10, \"matches_documentary_style\": 0-10, "
                  "\"problems\": \"...\", \"why\": \"one sentence\"}")
    body = {"contents": [{"role": "user", "parts": [
        {"inlineData": {"mimeType": "video/mp4", "data": base64.b64encode(open(small, "rb").read()).decode()}, "videoMetadata": {"fps": 10}},
        {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500, "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    P = os.environ["VERTEX_PROJECT"]
    url = f"https://aiplatform.googleapis.com/v1/projects/{P}/locations/global/publishers/google/models/gemini-3.8-flash:generateContent"
    for t in range(3):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={
                "Authorization": "Bearer " + os.environ["VERTEX_TOKEN"], "x-goog-user-project": P, "Content-Type": "application/json"}), timeout=300))
            u = r.get("usageMetadata", {}); pin = u.get("promptTokenCount", 0); po = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
            txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
            j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1]); j["_inr"] = round((pin * 0.5 + po * 3.0) / 1e6 * 88, 3); j["_in"] = pin
            return j
        except urllib.error.HTTPError as e:
            print("  gemini HTTP", e.code)
        except Exception as e:
            print("  gemini", type(e).__name__)
        time.sleep(8)
    return None


def main():
    B = json.load(open(sys.argv[1], encoding="utf-8")); out = B["out"]; work = Path("work"); work.mkdir(exist_ok=True)
    report = []
    for jb in B["jobs"]:
        t0 = time.time(); name = jb["name"]; rec = {"name": name, "tool": jb["tool"]}
        try:
            if jb["tool"] == "strip":   # contact sheet of the frames around given times (check transitions without touching the laptop)
                v = get(jb["video"], str(work / Path(jb["video"]).name)); rows = []
                for t in jb["times"]:
                    o = work / f"{name}_{t}.png"
                    subprocess.run([FF, "-v", "error", "-y", "-ss", f"{max(0, t - 0.1):.3f}", "-i", v, "-frames:v", "1", "-vf",
                                    "fps=30,scale=160:-2,tile=12x1:padding=2",
                                    str(o)], check=True); rows.append(o)
                subprocess.run([FF, "-v", "error", "-y"] + sum([["-i", str(r)] for r in rows], []) + ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(rows))) + f"vstack=inputs={len(rows)}", str(work / f"{name}.png")], check=True)
                put(str(work / f"{name}.png"), out + f"{name}.png", out); rec["status"] = "ok"; report.append(rec); print("DEMO", json.dumps(rec), flush=True); continue
            if jb["tool"] == "timeline":
                plate = get(jb["plate"], str(work / ("plate_" + Path(jb["plate"]).name))) if jb.get("plate") else None
                cmd = [sys.executable, str(HERE / "timeline" / "timeline.py"), str(HERE / jb["spec"]), "--out", str(work / name),
                       "--sizes", jb.get("sizes", "1920x1080,1080x1920")] + (["--plate", plate] if plate else [])
                subprocess.run(cmd, check=True)
                files = sorted(work.glob(name + "_*.mp4")) + sorted(work.glob(name + "_*.mov")) + [work / f"{name}.sfx.json"]
            else:
                img = get(jb["image"], str(work / Path(jb["image"]).name))
                cmd = [sys.executable, str(HERE / "photo_motion.py"), img, "--out", str(work / f"{name}.mp4")] + shlex.split(jb.get("args", ""))
                if jb.get("next"): cmd += ["--next", get(jb["next"], str(work / Path(jb["next"]).name))]
                subprocess.run(cmd, check=True)
                files = [work / f"{name}.mp4", work / f"{name}.mp4.motion.json"]
                rec["motion"] = json.loads((work / f"{name}.mp4.motion.json").read_text())
            for f in files:
                if f.exists(): put(str(f), out + f.name, out)
            chk = [f for f in files if f.suffix == ".mp4"]
            if chk: rec["gemini"] = still_check(str(chk[-1] if jb["tool"] == "timeline" else chk[0]), jb["tool"])
            rec["status"] = "ok"
        except Exception as e:
            rec["status"] = "failed"; rec["error"] = str(e)[:300]
        rec["seconds"] = round(time.time() - t0, 1)
        print("DEMO", json.dumps(rec)[:600], flush=True); report.append(rec)
    Path("report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    put("report.json", out + "report.json", out)


if __name__ == "__main__":
    main()
