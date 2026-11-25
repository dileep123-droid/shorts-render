"""Shorts renderer test on a GitHub runner (25 Nov): renders one finished Short's inputs through shorts_render.py in SR_LOCAL mode.
  python srtest.py <R2 prefix s2work/cinekit/srtest/>
R2 prefix holds: job_old.json (the Short's own job, no "tr"), job_tr.json (same + "tr": "shorts_v1" + sheet words "trw"),
shorts_render_before.py / shorts_render_after.py (the renderer before / after the change; shorts_tr.py from the repo root). Inputs are read (read-only) from the keys in the job's "files".
Runs: A) BEFORE renderer + job_old, B) AFTER renderer + job_old  -> proves the old path is untouched (framemd5 of bg.mp4 + final2.txt)
      C) AFTER renderer + job_tr   -> the new Shorts transitions (sh_tr_test.mp4 + 720p preview uploaded to the prefix).
Word timing = voice pauses + character share (no face circles: assemble mode with FW = []); fine for a visual test."""
import hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path
import boto3

PFX = sys.argv[1]; assert PFX.startswith("s2work/cinekit/srtest/")
B = "eduorbex-media"
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")
REPO = Path(__file__).resolve().parents[2]
IN = Path("in"); IN.mkdir(exist_ok=True)
for f in ("job_old.json", "job_tr.json", "shorts_render_before.py", "shorts_render_after.py"): S3.download_file(B, PFX + f, str(IN / f))
J = json.load(open(IN / "job_old.json", encoding="utf-8"))
for n, k in J["files"].items():
    if n == "face.mp4": continue
    assert k.startswith("s2work/shorts1/") and ".." not in k
    S3.download_file(B, k, str(IN / n))
# ---- word timing from pauses
o = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(IN / "voice.flac"), "-af", "silencedetect=n=-32dB:d=0.12", "-f", "null", "-"], capture_output=True, text=True).stderr
st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", o)]; en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", o)]
D = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(IN / "voice.flac")], capture_output=True, text=True).stdout)
s0 = en[0] if st and st[0] < 0.05 else 0.0
pz = sorted([(a, b) for a, b in zip(st, en) if a > 0.3 and b < D - 0.3], key=lambda p: -(p[1] - p[0]))
L = J["lines"]; need = len(L) - 1
ch = [len(x) for x in L]; tot = sum(ch); exp = [s0 + sum(ch[:i + 1]) / tot * (D - s0) for i in range(need)]
cands = sorted(pz[:max(need * 2, need)], key=lambda p: p[0]); sel = []
for e in exp:
    left = [p for p in cands if p not in sel and (not sel or p[0] > sel[-1][0])]
    if left: sel.append(min(left, key=lambda p: abs((p[0] + p[1]) / 2 - e)))
while len(sel) < need: sel.append((exp[len(sel)], exp[len(sel)] + 0.05))
lines_t = []; a = s0
for k in range(len(L)):
    b = sel[k][0] if k < need else D - 0.1; lines_t.append([a, max(b, a + 0.5)]); a = sel[k][1] if k < need else b
words = []
for k, ln in enumerate(L):
    ws = ln.split(); a, b = lines_t[k]; tl = sum(len(w) + 1 for w in ws); t = a
    for w in ws: d = (b - a) * (len(w) + 1) / tl; words.append([round(t, 3), round(t + d, 3)]); t += d
json.dump({"words": words, "lines_t": lines_t, "FW": []}, open(IN / "align.json", "w"))
for jf in ("job_old.json", "job_tr.json"):
    j = json.load(open(IN / jf, encoding="utf-8")); j["face"] = []; j["files"] = {k: k for k in j["files"] if k != "face.mp4"}
    j["out"] = "s2work/shorts1/gh_local/tr_test/"; j.pop("final", None); j.pop("ref", None)
    json.dump(j, open(IN / jf, "w", encoding="utf-8"), ensure_ascii=False)


def run(renderer, job, root):
    shutil.rmtree(root, ignore_errors=True)
    env = dict(os.environ, SR_LOCAL=str(IN.resolve()), SR_JOB=job, SR_ROOT=str(Path(root).resolve()))
    r = subprocess.run([sys.executable, str(renderer), "tr_test", "assemble"], env=env, capture_output=True, text=True)
    log = r.stdout[-4000:] + r.stderr[-2000:]
    print(f"=== {renderer.name} {job} rc {r.returncode}\n" + "\n".join(l for l in log.splitlines() if re.search(r"shots|shorts_tr|background|final|rc [1-9]|Error|error", l)), flush=True)
    return r.returncode


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


def fmd5(p):
    return hashlib.md5(subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-map", "0:v", "-f", "framemd5", "-"], capture_output=True).stdout).hexdigest()


shutil.copy(IN / "shorts_render_before.py", REPO / "shorts_render_before.py"); shutil.copy(IN / "shorts_render_after.py", REPO / "shorts_render_after.py")
ra = run(REPO / "shorts_render_before.py", "job_old.json", "A"); rb = run(REPO / "shorts_render_after.py", "job_old.json", "B")
same = {"bg_framemd5": [fmd5("A/w/bg.mp4"), fmd5("B/w/bg.mp4")], "final2_md5": [md5("A/w/final2.txt"), md5("B/w/final2.txt")],
        "final_video_framemd5": [fmd5("A/out/sh_supermoon_tonight_te.mp4") if os.path.exists("A/out/sh_supermoon_tonight_te.mp4") else None,
                                 fmd5("B/out/sh_supermoon_tonight_te.mp4") if os.path.exists("B/out/sh_supermoon_tonight_te.mp4") else None]}
same["identical"] = all(v[0] == v[1] and v[0] for v in same.values())
print("OLD PATH CHECK", json.dumps(same), flush=True)
rc = run(REPO / "shorts_render_after.py", "job_tr.json", "C")
outs = list(Path("C/out").glob("*.mp4"))
res = {"before_rc": ra, "after_old_rc": rb, "tr_rc": rc, "old_path": same, "tr_outputs": [p.name for p in outs]}
for p in outs:
    S3.upload_file(str(p), B, PFX + "out/" + p.name.replace("sh_supermoon_tonight_te", "sh_tr_test"), ExtraArgs={"ContentType": "video/mp4"})
S3.put_object(Bucket=B, Key=PFX + "out/result.json", Body=json.dumps(res, indent=1).encode())
print("RESULT", json.dumps(res), flush=True)
