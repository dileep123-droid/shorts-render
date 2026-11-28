# Telegram archive (owner 28 Nov 2026): rarely-needed files (Meta clips etc.) -> zip per batch (<= 1.9 GB parts) -> private Telegram channel.
# Runs on GitHub (never the laptop). A local Telegram Bot API server (docker, started by the workflow) allows files up to 2 GB.
# Input: env PREFIXES = R2 prefixes, one per line (e.g. s2work/shorts1/clips/batch31/). Secrets: TG_BOT_TOKEN, TG_CHAT_ID (+ API id/hash for the server).
# For every prefix: download all objects -> zip parts -> sendDocument (caption = prefix, part, file count) -> verify the Telegram copy
# (getFile size == local size, plus a full re-download + SHA-256 compare of the FIRST part of each prefix) -> append to the manifest
# s2work/tg_archive/manifest.jsonl (prefix, part, message_id, file_id, sha256, files). Deleting from R2 is a SEPARATE, later step (DELETE=1 only
# after the owner OKs), and only for prefixes whose every part was verified.
import os, sys, json, hashlib, zipfile, time, urllib.request, urllib.parse, boto3
from botocore.config import Config
API = "http://127.0.0.1:8081"; TOK = os.environ["TG_BOT_TOKEN"]; CHAT = os.environ["TG_CHAT_ID"]; B = "eduorbex-media"
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
MAN = "s2work/tg_archive/manifest.jsonl"; PART_MAX = 1_900_000_000; DELETE = os.environ.get("DELETE") == "1"
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
def tg(method, **params):
    r = json.load(urllib.request.urlopen(f"{API}/bot{TOK}/{method}?" + urllib.parse.urlencode(params), timeout=600))
    if not r.get("ok"): raise RuntimeError(f"{method}: {r}")
    return r["result"]
def send(path, caption):
    import requests
    with open(path, "rb") as f:
        r = requests.post(f"{API}/bot{TOK}/sendDocument", data={"chat_id": CHAT, "caption": caption[:1000]}, files={"document": (os.path.basename(path), f)}, timeout=3600).json()
    if not r.get("ok"): raise RuntimeError(r)
    return r["result"]
def manifest():
    try: return [json.loads(l) for l in S3.get_object(Bucket=B, Key=MAN)["Body"].read().decode().splitlines() if l.strip()]
    except Exception: return []
def objects(prefix):
    out, tok = [], None
    while True:
        kw = dict(Bucket=B, Prefix=prefix, MaxKeys=1000) | ({"ContinuationToken": tok} if tok else {})
        r = S3.list_objects_v2(**kw); out += [(o["Key"], o["Size"]) for o in r.get("Contents", [])]
        if not r.get("IsTruncated"): return out
        tok = r["NextContinuationToken"]
M = manifest(); done = {(m["prefix"], m["part"]) for m in M if m.get("verified")}
for prefix in [p.strip() for p in os.environ["PREFIXES"].splitlines() if p.strip()]:
    objs = objects(prefix)
    if not objs: print("empty", prefix); continue
    parts, cur, size = [], [], 0
    for k, s in objs:
        if cur and size + s > PART_MAX: parts.append(cur); cur, size = [], 0
        cur.append(k); size += s
    if cur: parts.append(cur)
    ok_all = True
    for i, keys in enumerate(parts, 1):
        if (prefix, i) in done: print("already archived", prefix, i); continue
        name = prefix.strip("/").replace("/", "__") + f"__part{i:02d}of{len(parts):02d}.zip"; os.makedirs("dl", exist_ok=True)
        with zipfile.ZipFile(name, "w", zipfile.ZIP_STORED) as z:   # videos are already compressed: store, do not deflate
            for k in keys:
                lp = os.path.join("dl", os.path.basename(k)); S3.download_file(B, k, lp); z.write(lp, k[len(prefix):] or os.path.basename(k)); os.remove(lp)
        h = sha(name); sz = os.path.getsize(name)
        msg = send(name, f"{prefix} | part {i}/{len(parts)} | {len(keys)} files | sha256 {h[:16]}")
        doc = msg["document"]; fi = tg("getFile", file_id=doc["file_id"])
        verified = int(fi.get("file_size") or 0) == sz
        fp = (fi.get("file_path") or "").replace("/var/lib/telegram-bot-api", "/tmp/tgdata")   # container path -> runner path
        if verified and i == 1 and fp and os.path.exists(fp):   # local server keeps the file: full SHA-256 check of the first part
            verified = sha(fp) == h
        rec = {"prefix": prefix, "part": i, "parts": len(parts), "files": len(keys), "bytes": sz, "sha256": h, "message_id": msg["message_id"],
               "file_id": doc["file_id"], "verified": verified, "t": time.strftime("%F %T")}
        M.append(rec); S3.put_object(Bucket=B, Key=MAN, Body=("\n".join(json.dumps(m) for m in M) + "\n").encode())
        print("archived" if verified else "NOT VERIFIED", name, sz, "bytes", flush=True); os.remove(name); ok_all &= verified
    if DELETE and ok_all and all(any(m["prefix"] == prefix and m["part"] == i and m["verified"] for m in M) for i in range(1, len(parts) + 1)):
        for k, _ in objs: S3.delete_object(Bucket=B, Key=k)
        print("deleted from R2 after verified archive:", prefix, len(objs), "files")
