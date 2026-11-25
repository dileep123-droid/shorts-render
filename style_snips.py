# Owner 25 Nov 2026: cut short narration snippets (small mono mp3) from episode masters on GitHub, so the laptop stays light.
# Job JSON lives in the private R2 bucket: {"out": "s2work/style_check/", "jobs": [{"name", "src": {"r2": key} | {"drive": id},
#   "ss", "t", optional "fixes": [{"s","e"}], "ctx": [[s,e],...]}]}. One source file at a time is downloaded, cut, deleted.
# For snippets with "fixes" the loudness (RMS dB) of each fixed span vs the surrounding context speech is measured.
# Results: <out><name>.mp3 + <out>_manifest_<shard>.json. The public log prints counts only.
#   python style_snips.py <R2 job key> <shard> <n shards>
import os, sys, json, subprocess, boto3
import numpy as np
from botocore.config import Config
import drive_io as DI
sys.excepthook = lambda t, v, tb: print("FAILED", t.__name__)
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 8}))
B = "eduorbex-media"; key, shard, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
assert key.startswith("s2work/") and key.endswith("job.json")
J = json.loads(S3.get_object(Bucket=B, Key=key)["Body"].read()); OUT = J["out"]; assert OUT.startswith("s2work/")
FF, FP = "./ff/ffmpeg", "./ff/ffprobe"
def sh(c): return subprocess.run(c, capture_output=True, text=True)
srcs = sorted({json.dumps(j["src"], sort_keys=True) for j in J["jobs"]})
mine = [s for i, s in enumerate(srcs) if i % n == shard]
man = []; ok = bad = 0
def rms_db(x): return float(20 * np.log10(np.sqrt((x.astype(np.float64) ** 2).mean()) + 1e-9)) if len(x) else None
for sj in mine:
    src = json.loads(sj); p = "/tmp/src.mp4"
    try:
        if "r2" in src: S3.download_file(B, src["r2"], p)
        else:
            r = DI._api(f"https://www.googleapis.com/drive/v3/files/{src['drive']}?alt=media")
            with open(p, "wb") as f:
                while True:
                    b = r.read(16 << 20)
                    if not b: break
                    f.write(b)
    except Exception as e:
        print("source download failed", type(e).__name__); bad += 1; continue
    dur = float(sh([FP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout.strip() or 0)
    for j in [j for j in J["jobs"] if json.dumps(j["src"], sort_keys=True) == sj]:
        o = f"/tmp/{j['name']}.mp3"
        r = sh([FF, "-v", "error", "-y", "-ss", f"{j['ss']:.3f}", "-t", f"{j['t']:.3f}", "-i", p, "-vn", "-ac", "1", "-ar", "24000", "-b:a", "80k", o])
        if r.returncode or not os.path.exists(o): bad += 1; continue
        rec = {"name": j["name"], "key": OUT + j["name"] + ".mp3", "size": os.path.getsize(o), "src_dur": round(dur, 2)}
        if j.get("fixes"):
            sh([FF, "-v", "error", "-y", "-i", o, "-ac", "1", "-ar", "16000", "-f", "f32le", "/tmp/x.f32"])
            x = np.fromfile("/tmp/x.f32", np.float32); sr = 16000
            seg = lambda a, b: x[int(a * sr):int(b * sr)]
            c = np.concatenate([seg(a, b) for a, b in j.get("ctx", [])] or [np.zeros(0, np.float32)])
            rec["ctx_rms_db"] = rms_db(c)
            rec["fix_rms_db"] = [rms_db(seg(f["s"], f["e"])) for f in j["fixes"]]
        S3.upload_file(o, B, rec["key"], ExtraArgs={"ContentType": "audio/mpeg"}); os.remove(o); man.append(rec); ok += 1
    os.remove(p)
S3.put_object(Bucket=B, Key=f"{OUT}_manifest_{shard}.json", Body=json.dumps(man).encode())
print(f"RESULT shard {shard}: sources {len(mine)} | snippets ok {ok} | failed {bad}")
