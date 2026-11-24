"""R2 helper for the episode assembler. READ from s2work/ (shots clips, kit_meta elements, sfx, cinekit renders); WRITE only under
s2work/cinekit/assemble/. Never deletes anything outside s2work/cinekit/assemble/<episode>/.

  python r2io_assemble.py fetch plan.json            # runner: every plan asset -> $ASM_ASSETS/<key with / -> __>, SFX -> $ASM_ASSETS/sfx/
  python r2io_assemble.py fetch-chunk plan.json K    # runner: only what chunk K needs
  python r2io_assemble.py get <key> <file> | put <file> <key> | ls <prefix>
  python r2io_assemble.py stage plan.json <episode>  # laptop: upload plan + local (non-R2) assets to s2work/cinekit/assemble/<ep>/in/, rewrite plan
  python r2io_assemble.py clean <episode>            # laptop: delete s2work/cinekit/assemble/<ep>/ work files (chunks, mix, inputs), keep master
"""
import json, os, sys

BUCKET = "eduorbex-media"
READ_OK = ("s2work/",)
WRITE_OK = "s2work/cinekit/assemble/"
ASSETS = os.environ.get("ASM_ASSETS", "assets")
_S3 = None

def s3():
    global _S3
    if _S3 is None:
        import boto3
        e = dict(os.environ)
        envf = "C:/Users/goddu/.gemini/antigravity/scratch/eduorbex/.env"
        if "CF_R2_ENDPOINT" not in e and os.path.exists(envf):   # laptop: read keys from .env (never printed)
            for line in open(envf, encoding="utf-8"):
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1); e.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        _S3 = boto3.client("s3", endpoint_url=e["CF_R2_ENDPOINT"], aws_access_key_id=e["CF_R2_ACCESS_KEY_ID"],
                           aws_secret_access_key=e["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")
    return _S3

def chk_read(k):
    if not k.startswith(READ_OK) or ".." in k: sys.exit(f"refused read outside s2work/: {k}")
def chk_write(k):
    if not k.startswith(WRITE_OK) or ".." in k: sys.exit(f"refused write outside {WRITE_OK}: {k}")

def get(k, f):
    chk_read(k); os.makedirs(os.path.dirname(os.path.abspath(f)), exist_ok=True); s3().download_file(BUCKET, k, f)
def put(f, k):
    chk_write(k)
    ct = {"mp4": "video/mp4", "json": "application/json", "wav": "audio/wav", "mov": "video/quicktime"}.get(f.rsplit(".", 1)[-1], "application/octet-stream")
    s3().upload_file(f, BUCKET, k, ExtraArgs={"ContentType": ct}); print("uploaded", k, os.path.getsize(f), "bytes")
def ls(prefix):
    chk_read(prefix); out, tok = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix, **({"ContinuationToken": tok} if tok else {})}
        r = s3().list_objects_v2(**kw); out += [o["Key"] for o in r.get("Contents", [])]
        if not r.get("IsTruncated"): return out
        tok = r["NextContinuationToken"]

def local(k): return os.path.join(ASSETS, k.replace("/", "__"))

def fetch_keys(keys):
    n = 0
    for k in keys:
        if not k or os.path.exists(k) or not k.startswith("s2work/"): continue
        if os.path.exists(local(k)): continue
        try: get(k, local(k)); n += 1
        except Exception as ex: print("  missing in R2:", k, type(ex).__name__)
    os.makedirs(os.path.join(ASSETS, "sfx"), exist_ok=True)
    for k in ls("s2work/shorts1/sfx/"):
        f = os.path.join(ASSETS, "sfx", os.path.basename(k))
        if not os.path.exists(f): get(k, f)
    print("fetched", n, "assets into", ASSETS)

def chunk_keys(plan, k):
    c = plan["chunks"][int(k)]; segs = plan["segments"][c["i0"]:c["i1"]]; t0, t1 = c["t0"], c["t1"]
    keys = [s.get("src") for s in segs]
    keys += [o.get("src") for o in plan["overlays"] if o["t0"] < t1 and o["t0"] + o["dur"] > t0]
    keys += [o.get("key") for o in plan["overlays"] if o["kind"] == "element" and o["t0"] < t1 and o["t0"] + o["dur"] > t0]
    keys += [f["src"] for f in plan["face"] if f["t0"] < t1 and f["t1"] > t0]
    if any(s["type"] == "title" or s.get("stype") == "plate" for s in segs): keys.append((plan.get("elements") or {}).get("grunge_title_bg"))
    return keys

def stage(plan_json, ep):
    """upload local files referenced by the plan to s2work/cinekit/assemble/<ep>/in/ and rewrite their keys; then the plan itself."""
    P = json.load(open(plan_json, encoding="utf-8")); base = f"{WRITE_OK}{ep}/in/"; m = {}
    def up(p):
        if not p or p.startswith("s2work/"): return p
        if p not in m:
            k = base + os.path.basename(p); put(p, k); m[p] = k
        return m[p]
    for s in P["segments"]: s["src"] = up(s.get("src"))
    for o in P["overlays"]: o["src"] = up(o.get("src"))
    for f in P["face"]: f["src"] = up(f["src"])
    for b in P["audio"]["music"]: b["src"] = up(b["src"])
    P["audio"]["voice"] = up(P["audio"]["voice"])
    P["assets"] = sorted(set(up(a) for a in P["assets"]))
    out = plan_json.replace(".json", "_r2.json"); json.dump(P, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    put(out, f"{WRITE_OK}{ep}/plan.json"); print("staged", len(m), "files; plan ->", f"{WRITE_OK}{ep}/plan.json")

def clean(ep):
    pre = f"{WRITE_OK}{ep}/"; ks = [k for k in ls(pre) if not k.endswith(("_master.mp4", "_master_720.mp4"))]
    for k in ks: chk_write(k); s3().delete_object(Bucket=BUCKET, Key=k)
    print("deleted", len(ks), "work objects under", pre, "(masters kept)")

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "fetch": fetch_keys(json.load(open(a[1], encoding="utf-8"))["assets"])
    elif a[0] == "check":   # which plan assets are not in R2 yet (Meta clips still queued, cards not rendered, maps pending)
        P = json.load(open(a[1], encoding="utf-8")); have = set()
        for pre in sorted({k.rsplit("/", 1)[0] + "/" for k in P["assets"] if k and k.startswith("s2work/")}): have |= set(ls(pre))
        miss = [k for k in P["assets"] if k and k.startswith("s2work/") and k not in have]
        print(f"{len(P['assets']) - len(miss)}/{len(P['assets'])} assets in R2; missing {len(miss)}:"); print("\n".join(miss))
    elif a[0] == "fetch-chunk": fetch_keys(chunk_keys(json.load(open(a[1], encoding="utf-8")), a[2]))
    elif a[0] == "get": get(a[1], a[2])
    elif a[0] == "put": put(a[1], a[2])
    elif a[0] == "ls": print("\n".join(ls(a[1])))
    elif a[0] == "stage": stage(a[1], a[2])
    elif a[0] == "clean": clean(a[1])
    else: sys.exit(__doc__)
