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
    path = path.strip("/")
    if path in _fid: return _fid[path]
    pid = "root"; parts = path.split("/")
    for i, nm in enumerate(parts):
        pre = "/".join(parts[:i + 1])
        if pre in _fid: pid = _fid[pre]; continue
        qn = nm.replace("\\", "\\\\").replace("'", "\\'")
        f = _q(f"name='{qn}' and mimeType='application/vnd.google-apps.folder' and trashed=false and '{pid}' in parents")
        if f: pid = f[0]["id"]
        else:
            pid = json.load(_api("https://www.googleapis.com/drive/v3/files", "POST",
                                 json.dumps({"name": nm, "mimeType": "application/vnd.google-apps.folder", "parents": [pid]}).encode(),
                                 {"Content-Type": "application/json"}))["id"]
        _fid[pre] = pid
    return pid
def listing(batch):   # name -> size of the files already in the batch folder
    pid = folder(f"{ROOT}/{batch}")
    return {x["name"]: int(x.get("size", -1)) for x in _q(f"'{pid}' in parents and trashed=false")}
def children(pid):   # name -> size of files in a folder (all pages)
    out, tok = {}, None
    while True:
        u = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(dict(
            {"q": f"'{pid}' in parents and trashed=false", "fields": "nextPageToken,files(name,size)", "pageSize": 1000}, **({"pageToken": tok} if tok else {})))
        r = json.load(_api(u)); out.update({x["name"]: int(x.get("size", -1)) for x in r.get("files", [])}); tok = r.get("nextPageToken")
        if not tok: return out
def put_file(path, pid, name, mime="application/octet-stream"):   # chunked resumable upload of a local file (big masters)
    size = os.path.getsize(path)
    r = _api("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size", "POST",
             json.dumps({"name": name, "parents": [pid]}).encode(),
             {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
    loc = r.headers["Location"]; CH = 64 << 20; pos = 0
    with open(path, "rb") as f:
        while pos < size or size == 0:
            blk = f.read(CH); end = pos + len(blk) - 1
            req = urllib.request.Request(loc, data=blk, method="PUT", headers={"Content-Range": f"bytes {pos}-{end}/{size}" if size else "bytes */0"})
            try:
                out = json.load(urllib.request.urlopen(req, timeout=600)); break
            except urllib.error.HTTPError as e:
                if e.code != 308: raise
            pos = end + 1
    assert int(out.get("size", 0)) == size, "Drive size mismatch"
    return size
def put(data, batch, name):   # resumable upload of bytes; returns Drive size
    pid = folder(f"{ROOT}/{batch}")
    r = _api("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size", "POST",
             json.dumps({"name": name, "parents": [pid]}).encode(),
             {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))})
    loc = r.headers["Location"]
    out = json.load(urllib.request.urlopen(urllib.request.Request(loc, data=data, method="PUT", headers={"Content-Type": "video/mp4"}), timeout=600))
    assert int(out["size"]) == len(data), "Drive size mismatch"
    return int(out["size"])
