# Owner 24 Nov 2026: copy R2 s2work -> Google Drive "BuBu TV Archive/R2/<same path>" on GitHub's internet. NOTHING is deleted from R2.
# Exact copies (same fingerprint + size) are sent ONCE (the laptop made the list). Files already in Drive with the same size are skipped.
#   python r2_drive_mirror.py folders <list key>        -> make the whole folder tree once (no races between shards), map saved to R2
#   python r2_drive_mirror.py copy <list key> <shard> <n shards>
# The lists live in the private R2 bucket; this public log prints only counts.
import json, os, sys, boto3
from botocore.config import Config
import drive_io
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"; ROOT = "BuBu TV Archive/R2"
mode, LK = sys.argv[1], sys.argv[2]
assert LK.startswith("_transfer/") and LK.endswith(".json")
L = json.loads(S3.get_object(Bucket=B, Key=LK)["Body"].read())   # [[key, size], ...]
MAPK = LK[:-5] + "_folders.json"
if mode == "folders":
    dirs = sorted({k.rsplit("/", 1)[0] for k, s in L})
    m = {d: drive_io.folder(f"{ROOT}/{d}") for d in dirs}
    S3.put_object(Bucket=B, Key=MAPK, Body=json.dumps(m).encode()); print(f"RESULT folders ready: {len(m)}"); sys.exit(0)
shard, n = int(sys.argv[3]), int(sys.argv[4])
fmap = json.loads(S3.get_object(Bucket=B, Key=MAPK)["Body"].read())
mine = [x for i, x in enumerate(sorted(L, key=lambda x: -x[1])) if i % n == shard]
have = {}; new = same = fail = 0; gb = 0
for k, s in mine:
    d, nm = k.rsplit("/", 1); pid = fmap[d]
    if d not in have: have[d] = drive_io.children(pid)
    if have[d].get(nm) == s: same += 1; continue
    for t in range(3):
        try:
            S3.download_file(B, k, "/tmp/x.bin"); drive_io.put_file("/tmp/x.bin", pid, nm); new += 1; gb += s; break
        except Exception as e:
            print("retry", type(e).__name__, flush=True); drive_io._tok[0] = None
    else: fail += 1
    if (new + same + fail) % 200 == 0: print(f"progress {new + same + fail}/{len(mine)}", flush=True)
try: os.remove("/tmp/x.bin")
except Exception: pass
print(f"RESULT shard {shard}: files {len(mine)} | copied {new} ({gb / 1e9:.1f} GB) | already in Drive {same} | failed {fail}")
