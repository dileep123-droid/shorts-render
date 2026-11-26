# Cooking series -> Google Drive (runs in cooking_drive.yml). Job file (private, via presigned link):
#   {"root": "Chinni Diaries - Cooking Series", "files": [{"get": <presigned GET>, "folder": "With music", "name": "x.mp4", "size": n}], "put": <presigned PUT for result>}
# Files already in the Drive folder with the same name and size are skipped. Result: [{name, folder, id, size, state}]. Prints counts only.
import json, os, time, urllib.request, urllib.parse
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
            fetch(x["get"], "/tmp/x.mp4"); drive_io.put_file("/tmp/x.mp4", pid, x["name"], "video/mp4"); new += 1; break
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
