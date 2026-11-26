# Cooking series -> Google Drive (runs in cooking_drive.yml). Job file (private, via presigned link):
#   {"root": "Chinni Diaries - Cooking Series", "files": [{"get": <presigned GET>, "folder": "With music", "name": "x.mp4", "size": n}], "put": <presigned PUT for result>}
# Files already in the Drive folder with the same name and size are skipped. Result: [{name, folder, id, size, state}]. Prints counts only.
import json, os, time, urllib.request, urllib.parse, urllib.error
import drive_io

def fetch(url, path=None):
    for t in range(5):
        try:
            d = urllib.request.urlopen(url, timeout=600).read()
            if path: open(path, "wb").write(d); return path
            return d
        except Exception as e:
            err = e; time.sleep(5 * (t + 1))
    raise SystemExit(f"fetch failed: {type(err).__name__}")

def ids_in(pid):   # name -> (id, size), all pages
    out, tok = {}, None
    while True:
        u = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(dict(
            {"q": f"'{pid}' in parents and trashed=false", "fields": "nextPageToken,files(id,name,size)", "pageSize": 1000}, **({"pageToken": tok} if tok else {})))
        r = json.load(drive_io._api(u))
        out.update({x["name"]: (x["id"], int(x.get("size", -1))) for x in r.get("files", [])}); tok = r.get("nextPageToken")
        if not tok: return out

def update_file(path, fid, mime="video/mp4"):   # replace the content of an existing Drive file (same id, same name)
    size = os.path.getsize(path)
    r = drive_io._api(f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=resumable&fields=id,size", "PATCH", b"{}",
                      {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
    loc = r.headers["Location"]; CH = 64 << 20; pos = 0
    with open(path, "rb") as f:
        while True:
            blk = f.read(CH); end = pos + len(blk) - 1
            try:
                out = json.load(urllib.request.urlopen(urllib.request.Request(loc, data=blk, method="PUT",
                                headers={"Content-Range": f"bytes {pos}-{end}/{size}"}), timeout=600)); break
            except urllib.error.HTTPError as e:
                if e.code != 308: raise
            pos = end + 1
    assert int(out.get("size", 0)) == size, "Drive size mismatch"

ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))["inputs"]
job = json.loads(fetch(ev["job"]))
res = []; new = same = fail = 0
folders = {f: drive_io.folder(f"{job['root']}/{f}") for f in sorted({x["folder"] for x in job["files"]})}
have = {f: ids_in(pid) for f, pid in folders.items()}
for x in job["files"]:
    pid = folders[x["folder"]]; cur = have[x["folder"]].get(x["name"])
    if cur and cur[1] == x["size"]:
        same += 1; res.append(dict(name=x["name"], folder=x["folder"], id=cur[0], size=cur[1], state="already")); continue
    for t in range(3):
        try:
            fetch(x["get"], "/tmp/x.mp4")
            if cur: update_file("/tmp/x.mp4", cur[0])          # same name, new content -> replace in place (keeps the file id)
            else: drive_io.put_file("/tmp/x.mp4", pid, x["name"], "video/mp4")
            new += 1; break
        except Exception as e:
            print("retry", type(e).__name__, flush=True); drive_io._tok[0] = None
    else:
        fail += 1; res.append(dict(name=x["name"], folder=x["folder"], state="FAILED")); continue
    res.append(dict(name=x["name"], folder=x["folder"], state="copied"))
# verify from Drive itself: ids + sizes
have = {f: ids_in(pid) for f, pid in folders.items()}
for r in res:
    cur = have[r["folder"]].get(r["name"])
    if cur: r["id"], r["size"] = cur
want = {(x["folder"], x["name"]): x["size"] for x in job["files"]}
ok = sum(1 for r in res if r.get("size") == want[(r["folder"], r["name"])])
body = json.dumps({"files": res, "folders": folders}).encode()
urllib.request.urlopen(urllib.request.Request(job["put"], data=body, method="PUT", headers={"Content-Type": "application/json"}), timeout=120)
try: os.remove("/tmp/x.mp4")
except Exception: pass
print(f"RESULT files {len(job['files'])} | copied {new} | already {same} | failed {fail} | size-verified {ok}")
