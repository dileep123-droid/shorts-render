"""cinekit PHOTO FETCH (runs on GitHub, see .github/workflows/photo_fetch.yml). Downloads ONLY freely licensed images from
Wikimedia Commons for a list of queries and stores them in R2 s2work/cinekit/photos/<name>/ with manifest.json + CREDITS.txt.

  python photo_fetch.py spec.json [--per 4] [--dry]        (--dry = search + licence check only, no download, no upload)

spec.json: {"name": "ic814", "queries": [{"id": "q01_a300", "q": "Indian Airlines Airbus A300", "max": 4, "people": false}, ...]}
Allowed licences: public domain (PD-*), CC0, CC BY, CC BY-SA (any version). Rejected: NC, ND, GFDL-only, fair use / non-free,
anything without a machine-readable licence. Every kept file records author, licence, licence URL and the Commons page URL.
"""
import html, json, mimetypes, os, re, sys, time
import requests

API = "https://commons.wikimedia.org/w/api.php"
UA = "cinekit-photo-fetch/1.0 (batch fetch of freely licensed Commons images for video credits; python-requests)"
S = requests.Session(); S.headers["User-Agent"] = UA
PREFIX = "s2work/cinekit/photos/"


def strip(x):
    x = re.sub(r"<[^>]+>", "", x or ""); return re.sub(r"\s+", " ", html.unescape(x)).strip()


def licence_ok(em):
    """-> (ok, short_name, reason)"""
    lic = (em.get("License", {}).get("value") or "").lower().strip()
    short = strip(em.get("LicenseShortName", {}).get("value"))
    nonfree = str(em.get("NonFree", {}).get("value", "")).lower() in ("true", "1", "yes")
    s = short.lower()
    if nonfree: return False, short, "non-free"
    if not lic and not s: return False, short, "no licence data"
    if re.search(r"\bnc\b|-nc|noncommercial|non-commercial", lic + " " + s): return False, short, "NC"
    if re.search(r"\bnd\b|-nd|noderiv", lic + " " + s): return False, short, "ND"
    if "fair use" in s or "fair-use" in lic: return False, short, "fair use"
    if lic in ("cc0",) or "cc0" in s: return True, short or "CC0", ""
    if lic.startswith("pd") or "public domain" in s or s.startswith("pd"): return True, short or "Public domain", ""
    if re.match(r"^cc-by(-sa)?(-\d(\.\d)?)?", lic) or re.match(r"^cc by(-sa)? ?\d", s): return True, short, ""
    return False, short, "licence not on the allow-list (%s)" % (lic or s)


def search(q, n=30):
    p = {"action": "query", "format": "json", "generator": "search", "gsrsearch": q + " filetype:bitmap", "gsrnamespace": 6,
         "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1920}
    for a in range(4):
        r = S.get(API, params=p, timeout=60)
        if r.status_code == 429 or r.status_code >= 500: time.sleep(5 * (a + 1)); continue
        r.raise_for_status(); pages = (r.json().get("query") or {}).get("pages") or {}
        return sorted(pages.values(), key=lambda x: x.get("index", 999))
    return []


def main():
    a = sys.argv[1:]
    spec = json.load(open(a[0], encoding="utf-8")); name = spec["name"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name): sys.exit("bad name")
    per = int(a[a.index("--per") + 1]) if "--per" in a else 4; dry = "--dry" in a
    s3 = None
    if not dry:
        import boto3
        s3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")
    out = {"name": name, "source": "Wikimedia Commons API", "allowed": "PD, CC0, CC BY, CC BY-SA", "fetched": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "items": [], "rejected_counts": {}, "queries": []}
    os.makedirs("photos", exist_ok=True)
    for q in spec["queries"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", q["id"]): print("skip bad id", q["id"]); continue
        want = int(q.get("max", per)); kept = 0; seen = 0
        for pg in search(q["q"]):
            if kept >= want: break
            ii = (pg.get("imageinfo") or [{}])[0]; em = ii.get("extmetadata") or {}
            seen += 1
            if ii.get("mime") not in ("image/jpeg", "image/png"): continue
            if (ii.get("width") or 0) < 1000: out["rejected_counts"]["too small"] = out["rejected_counts"].get("too small", 0) + 1; continue
            ok, short, why = licence_ok(em)
            if not ok: out["rejected_counts"][why] = out["rejected_counts"].get(why, 0) + 1; continue
            url = ii.get("thumburl") or ii.get("url"); ext = ".png" if ii["mime"] == "image/png" else ".jpg"
            kept += 1; fn = f"{q['id']}_{kept}{ext}"; key = f"{PREFIX}{name}/{fn}"
            item = {"query_id": q["id"], "query": q["q"], "people": bool(q.get("people")), "file": fn, "r2_key": key,
                    "title": pg.get("title"), "page_url": ii.get("descriptionurl"), "image_url": url,
                    "author": strip(em.get("Artist", {}).get("value")) or "unknown (see page)",
                    "credit": strip(em.get("Credit", {}).get("value")), "licence": short,
                    "licence_url": strip(em.get("LicenseUrl", {}).get("value")),
                    "attribution_required": str(em.get("AttributionRequired", {}).get("value", "")).lower() == "true",
                    "restrictions": strip(em.get("Restrictions", {}).get("value")),
                    "description": strip(em.get("ImageDescription", {}).get("value"))[:300],
                    "date": strip(em.get("DateTimeOriginal", {}).get("value"))[:40],
                    "width": ii.get("thumbwidth") or ii.get("width"), "height": ii.get("thumbheight") or ii.get("height")}
            if not dry:
                r = S.get(url, timeout=120); r.raise_for_status()
                loc = os.path.join("photos", fn); open(loc, "wb").write(r.content)
                s3.upload_file(loc, "eduorbex-media", key, ExtraArgs={"ContentType": mimetypes.guess_type(fn)[0] or "image/jpeg"})
                item["bytes"] = len(r.content); time.sleep(1.0)
            out["items"].append(item); print(f"  {'(dry) ' if dry else ''}{fn}  {short}  {item['author'][:50]}  {item['page_url']}", flush=True)
        out["queries"].append({"id": q["id"], "q": q["q"], "kept": kept, "checked": seen})
        print(f"{q['id']}: kept {kept} of {seen} checked", flush=True); time.sleep(1.0)
    credits = [f"{i['file']}: \"{i['title']}\" by {i['author']}, {i['licence']} ({i['licence_url']}), via Wikimedia Commons - {i['page_url']}"
               + (f" [restrictions: {i['restrictions']}]" if i["restrictions"] else "") for i in out["items"]]
    json.dump(out, open("manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open("CREDITS.txt", "w", encoding="utf-8").write("\n".join(credits) + "\n")
    print("TOTAL kept", len(out["items"]), "rejected", out["rejected_counts"])
    if not dry:
        for f, ct in (("manifest.json", "application/json"), ("CREDITS.txt", "text/plain; charset=utf-8")):
            s3.upload_file(f, "eduorbex-media", f"{PREFIX}{name}/{f}", ExtraArgs={"ContentType": ct}); print("uploaded", f"{PREFIX}{name}/{f}")


if __name__ == "__main__":
    main()
