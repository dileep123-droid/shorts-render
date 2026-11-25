# Owner 25 Nov 2026: pilot check of the cloud-built voice-fix master against the laptop-built one (on GitHub: the laptop stays light).
# Laptop version from Drive, cloud version + original master from R2. Per-frame md5 of all three: frames not found in the master = the
# re-encoded bridges; laptop vs cloud: frames, duration, PSNR in the bridges, audio difference, decode check, sample descriptions (avcC)
# and in-band SPS/PPS. JPG grabs (laptop | cloud) at every bridge edge. Everything goes to R2 (small files); the log prints counts only.
#   python vfix_compare.py <R2 key of the job JSON: {"drive_id", "cloud", "master", "out"}>
import os, sys, json, subprocess, hashlib, urllib.request, boto3
from botocore.config import Config
import drive_io as DI
sys.excepthook = lambda t, v, tb: print("FAILED", t.__name__)
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 8}))
B = "eduorbex-media"; key = sys.argv[1]; assert key.startswith("s2work/voicefix_pilot/") and key.endswith("/compare_job.json")
J = json.loads(S3.get_object(Bucket=B, Key=key)["Body"].read()); OUT = J["out"]; FF = "./ff/ffmpeg"; FP = "./ff/ffprobe"
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True)
r = DI._api(f"https://www.googleapis.com/drive/v3/files/{J['drive_id']}?alt=media")
with open("/tmp/a.mp4", "wb") as f:
    while True:
        b = r.read(16 << 20)
        if not b: break
        f.write(b)
S3.download_file(B, J["cloud"], "/tmp/b.mp4"); S3.download_file(B, J["master"], "/tmp/m.mp4")
F = {"laptop": "/tmp/a.mp4", "cloud": "/tmp/b.mp4", "master": "/tmp/m.mp4"}; RES = {"ffmpeg": sh(f"{FF} -version").stdout.splitlines()[0]}
def fmd5(p):
    o = sh(f"{FF} -v error -i {p} -map 0:v -f framemd5 -").stdout
    return [l.split(",")[-1].strip() for l in o.splitlines() if l and not l.startswith("#")]
H = {k: fmd5(p) for k, p in F.items()}
for k, p in F.items():
    RES[k] = {"frames": len(H[k]), "duration": float(sh(f"{FP} -v error -show_entries format=duration -of csv=p=0 {p}").stdout.strip() or 0),
              "size": os.path.getsize(p), "decode": (sh(f"{FF} -v error -i {p} -f null -").stderr.strip()[:300] or "clean") if k != "master" else "-"}
def runs(flags):
    out = []; s = None
    for i, f in enumerate(flags + [False]):
        if f and s is None: s = i
        if not f and s is not None: out.append([s, i]); s = None
    return out
ms = set(H["master"])
for k in ("laptop", "cloud"): RES[k]["not_in_master"] = runs([h not in ms for h in H[k]])
n = min(len(H["laptop"]), len(H["cloud"])); same = [H["laptop"][i] == H["cloud"][i] for i in range(n)]
RES["laptop_vs_cloud_identical_frames"] = sum(same); RES["laptop_vs_cloud_differing_runs"] = runs([not x for x in same])
# PSNR laptop vs cloud on every differing run (+-5 frames)
ps = []
for a, b in RES["laptop_vs_cloud_differing_runs"]:
    a0, b0 = max(0, a - 5), min(n, b + 5)
    o = sh(f"{FF} -v error -i /tmp/a.mp4 -i /tmp/b.mp4 -lavfi \"[0:v]trim=start_frame={a0}:end_frame={b0},setpts=PTS-STARTPTS[x];[1:v]trim=start_frame={a0}:end_frame={b0},setpts=PTS-STARTPTS[y];[x][y]psnr=stats_file=/tmp/ps.txt\" -f null -")
    v = [float(l.split("psnr_avg:")[1].split()[0]) for l in open("/tmp/ps.txt") if "psnr_avg:" in l and "inf" not in l.split("psnr_avg:")[1].split()[0]]
    ps.append({"run": [a, b], "min_psnr": round(min(v), 2) if v else "inf", "mean_psnr": round(sum(v) / len(v), 2) if v else "inf"})
RES["psnr_runs"] = ps
# audio laptop vs cloud
import numpy as np
for k in ("laptop", "cloud"): sh(f"{FF} -v error -y -i {F[k]} -vn -ac 2 -ar 48000 -f f32le /tmp/{k}.f32")
x = np.fromfile("/tmp/laptop.f32", np.float32); y = np.fromfile("/tmp/cloud.f32", np.float32); m = min(len(x), len(y))
RES["audio_diff_db"] = round(float(20 * np.log10(np.sqrt(((x[:m] - y[:m]) ** 2).mean()) / (np.sqrt((x[:m] ** 2).mean()) + 1e-12) + 1e-12)), 1); RES["audio_len"] = [len(x), len(y)]
# sample descriptions / in-band parameter sets
def moov(p):
    with open(p, "rb") as f:
        sz = os.path.getsize(p); i = 0
        while i < sz:
            f.seek(i); h = f.read(16); L = int.from_bytes(h[:4], "big")
            if L == 1: L = int.from_bytes(h[8:16], "big")
            if h[4:8] == b"moov": f.seek(i); return f.read(L)
            i += L
    return b""
for k, p in F.items():
    mv = moov(p); RES[k]["avcC"] = mv.count(b"avcC")
    tr = sh(f"{FF} -v error -i {p} -map 0:v -c copy -bsf:v trace_headers -f null - 2>&1 | grep -cE 'Picture Parameter Set|Sequence Parameter Set'")
    RES[k]["inband_param_sets"] = int((tr.stdout or "0").strip() or 0)
# grabs: laptop | cloud at every bridge edge (from the laptop's not-in-master runs)
grabs = []
for a, b in RES["laptop"]["not_in_master"]:
    for fr in sorted({max(0, a - 1), a, (a + b) // 2, b - 1, b}):
        if fr >= n: continue
        p = f"/tmp/g_{fr}.jpg"
        sh(f"{FF} -v error -y -i /tmp/a.mp4 -i /tmp/b.mp4 -lavfi \"[0:v]select=eq(n\\,{fr}),scale=960:-2[x];[1:v]select=eq(n\\,{fr}),scale=960:-2[y];[x][y]hstack\" -frames:v 1 -q:v 3 {p}")
        if os.path.exists(p): S3.upload_file(p, B, f"{OUT}grab_{fr}.jpg"); grabs.append(f"grab_{fr}.jpg")
RES["grabs"] = grabs
S3.put_object(Bucket=B, Key=f"{OUT}compare.json", Body=json.dumps(RES).encode(), ContentType="application/json")
print("RESULT compare written | frames", RES["laptop"]["frames"], RES["cloud"]["frames"], "| differing runs", len(RES["laptop_vs_cloud_differing_runs"]), "| grabs", len(grabs))
