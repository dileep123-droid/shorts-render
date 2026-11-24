# Owner 24 Nov 2026: Meta clips are stored in Google Drive (GitHub's internet, not the home connection); R2 keeps a working copy.
# Drive login comes from the DRIVE_TOKEN secret (never printed). Folder: "Dileep Explains Meta Clips/<batch>/".
import json, os, urllib.request, urllib.parse
ROOT = "Dileep Explains Meta Clips"
_tok = [None]; _fid = {}
def _token():
    if _tok[0]: return _tok[0]
    d = json.loads(os.environ["DRIVE_TOKEN"])
    body = urllib.parse.urlencode({"client_id": d["client_id"], "client_secret": d["client_secret"],
                                   "refresh_token": d["refresh_token"], "grant_type": "refresh_token"}).encode()
    _tok[0] = json.load(urllib.request.urlopen(d.get("token_uri") or "https://oauth2.googleapis.com/token", data=body, timeout=60))["access_token"]
    return _tok[0]
def _api(url, method="GET", data=None, headers=None):
    h = {"Authorization": "Bearer " + _token()}; h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=300)
def _q(q):
    u = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({"q": q, "fields": "files(id,name,size)", "pageSize": 1000})
    return json.load(_api(u)).get("files", [])
def folder(path):   # "a/b" -> id, created if missing
    if path in _fid: return _fid[path]
    pid = "root"
    for nm in path.strip("/").split("/"):
        f = _q(f"name='{nm}' and mimeType='application/vnd.google-apps.folder' and trashed=false and '{pid}' in parents")
        if f: pid = f[0]["id"]
        else:
            pid = json.load(_api("https://www.googleapis.com/drive/v3/files", "POST",
                                 json.dumps({"name": nm, "mimeType": "application/vnd.google-apps.folder", "parents": [pid]}).encode(),
                                 {"Content-Type": "application/json"}))["id"]
    _fid[path] = pid; return pid
def listing(batch):   # name -> size of the files already in the batch folder
    pid = folder(f"{ROOT}/{batch}")
    return {x["name"]: int(x.get("size", -1)) for x in _q(f"'{pid}' in parents and trashed=false")}
def put(data, batch, name):   # resumable upload of bytes; returns Drive size
    pid = folder(f"{ROOT}/{batch}")
    r = _api("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size", "POST",
             json.dumps({"name": name, "parents": [pid]}).encode(),
             {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))})
    loc = r.headers["Location"]
    out = json.load(urllib.request.urlopen(urllib.request.Request(loc, data=data, method="PUT", headers={"Content-Type": "video/mp4"}), timeout=600))
    assert int(out["size"]) == len(data), "Drive size mismatch"
    return int(out["size"])
