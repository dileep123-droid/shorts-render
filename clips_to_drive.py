# Owner 24 Nov 2026: copy Meta clips R2 -> Google Drive on GitHub (GitHub's internet). Skips files already in Drive with the same size.
# R2 copy is KEPT (owner: Drive main store, R2 working copy for the assembler).   python clips_to_drive.py <batch, e.g. ic814>
import os, sys, boto3
from botocore.config import Config
import drive_io
b = sys.argv[1]
assert b.replace("_", "").isalnum(), "bad batch"
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"; pre = f"s2work/shorts1/clips/{b}/"
keys = []
for pg in S3.get_paginator("list_objects_v2").paginate(Bucket=B, Prefix=pre):
    keys += [(o["Key"], o["Size"]) for o in pg.get("Contents", []) if o["Key"].endswith(".mp4")]
have = drive_io.listing(b); new = same = fail = 0
for k, sz in keys:
    nm = k.rsplit("/", 1)[1]
    if have.get(nm) == sz: same += 1; continue
    try: drive_io.put(S3.get_object(Bucket=B, Key=k)["Body"].read(), b, nm); new += 1
    except Exception as e: fail += 1; print("fail", type(e).__name__, flush=True)
print(f"RESULT {b}: R2 {len(keys)} | copied {new} | already in Drive {same} | failed {fail}")
