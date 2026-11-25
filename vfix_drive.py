# Owner 25 Nov 2026: Mahabharatam voice-fix masters R2 -> Google Drive on GitHub's internet (the laptop stays light).
# Same folder logic as voice_fix/tools/drive_up.py: "BuBu TV Archive/R2/<R2 prefix parts>/ship/<name>", next to the orchestrator's master.
# Folders are never created (missing = stop). The original master on Drive must have the size of the R2 master we edited. A file with the
# same name and size is skipped; an older _fixed of the same name is replaced (never the original). Prints counts only; the link goes to R2.
#   python vfix_drive.py <R2 key of the job JSON: {"src", "pre", "name", "master", "result"}>
import os, sys, json, urllib.request, urllib.parse, boto3
from botocore.config import Config
import drive_io as DI
sys.excepthook = lambda t, v, tb: print("FAILED", t.__name__)   # public log: no paths, names or ids
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 8}))
B = "eduorbex-media"; key = sys.argv[1]
assert key.startswith("s2work/voicefix/") and key.endswith("/drive_job.json"), "bad job key"
J = json.loads(S3.get_object(Bucket=B, Key=key)["Body"].read())
def out(d):
    S3.put_object(Bucket=B, Key=J["result"], Body=json.dumps(d).encode(), ContentType="application/json")
    print("RESULT", d.get("status"), "| copied", int(d.get("status") == "uploaded"), "| already there", int(d.get("status") == "exists"), "| failed", int(d.get("status") not in ("uploaded", "exists")))
    sys.exit(0 if d.get("status") in ("uploaded", "exists") else 1)
def q(qs):
    u = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({"q": qs, "fields": "files(id,name,size)", "pageSize": 1000})
    return json.load(DI._api(u)).get("files", [])
pid = None
for nm in ["BuBu TV Archive", "R2"] + J["pre"].strip("/").split("/") + ["ship"]:
    qn = nm.replace("\\", "\\\\").replace("'", "\\'")
    f = q(f"name='{qn}' and mimeType='application/vnd.google-apps.folder' and trashed=false and '{pid or 'root'}' in parents")
    if not f: out({"status": "stop", "why": "Drive folder missing: " + nm})
    pid = f[0]["id"]
old = q(f"'{pid}' in parents and trashed=false")
msize = S3.head_object(Bucket=B, Key=J["master"])["ContentLength"]; ssize = S3.head_object(Bucket=B, Key=J["src"])["ContentLength"]
orig = [x for x in old if not x["name"].endswith("_fixed.mp4")]
if not any(int(x.get("size", -1)) == msize for x in orig): out({"status": "stop", "why": "the edited master is not the master on Drive (size differs)"})
have = [x for x in old if x["name"] == J["name"] and int(x.get("size", -1)) == ssize]
if have: out({"status": "exists", "id": have[0]["id"], "link": f"https://drive.google.com/file/d/{have[0]['id']}/view", "size": ssize})
assert J["name"].endswith("_fixed.mp4")
for x in old:
    if x["name"] == J["name"]: DI._api(f"https://www.googleapis.com/drive/v3/files/{x['id']}", "DELETE"); print("replaced an older copy of the same name")
loc_ = "/tmp/vfix.mp4"; S3.download_file(B, J["src"], loc_); assert os.path.getsize(loc_) == ssize
r = DI._api("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size", "POST", json.dumps({"name": J["name"], "parents": [pid]}).encode(),
            {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(ssize)})
loc = r.headers["Location"]; CH = 64 << 20; pos = 0; res = None
with open(loc_, "rb") as f:
    while pos < ssize:
        blk = f.read(CH); end = pos + len(blk) - 1
        req = urllib.request.Request(loc, data=blk, method="PUT", headers={"Content-Range": f"bytes {pos}-{end}/{ssize}"})
        try: res = json.load(urllib.request.urlopen(req, timeout=600)); break
        except urllib.error.HTTPError as e:
            if e.code != 308: raise
        pos = end + 1
os.remove(loc_)
if not res or int(res.get("size", 0)) != ssize: out({"status": "failed", "why": "Drive size mismatch"})
out({"status": "uploaded", "id": res["id"], "link": f"https://drive.google.com/file/d/{res['id']}/view", "size": ssize})
