"""Laptop side: rebuild the TRANSITIONS BOOK folder from book_spec.json + downloaded results.
  python build_book.py <book dir>   (default E:/mb_builds/shorts_v1/listen/transitions_book)
Writes transitions_book.json (manifest), index.html (grid of looping previews, filter by category) and
book_reel.mp4 (all ok previews in id order, stream copy)."""
import html, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "book_spec.json").read_text(encoding="utf-8"))
book = Path(sys.argv[1] if len(sys.argv) > 1 else r"E:/mb_builds/shorts_v1/listen/transitions_book")
res = {}
for f in sorted((book / "results").glob("*.json")) if (book / "results").exists() else []:
    for r in json.loads(f.read_text(encoding="utf-8")):
        if r["status"] == "ok" or r["id"] not in res or res[r["id"]]["status"] != "ok":
            res[r["id"]] = r

items = []
for it in SPEC["items"]:
    r = res.get(it["id"], {})
    fn = f"previews/{r['file']}" if r.get("status") == "ok" and (book / "previews" / r["file"]).exists() else None
    items.append({"id": it["id"], "name": it["name"], "category": it["category"], "kind": it["kind"], "source": it["source"],
                  "licence": it["licence"], "duration_frames": it["frames"], "duration_s": round(it["frames"] / SPEC["fps"], 2),
                  "recommended_use": it["use"], "creator_match": it["creator_match"], "batch": it["batch"],
                  "params": it.get("params") or ({k: it[k] for k in ("mode", "thr", "url") if k in it} or None),
                  "status": r.get("status", "not rendered yet"), "error": r.get("error"), "preview": fn})
man = {"what": "TRANSITIONS BOOK: 3 s 640x360 previews (Kathmandu valley -> Kandahar apron, our ic814 Meta clips)",
       "fps": SPEC["fps"], "gl_transitions_version": SPEC["gl_version"],
       "renderer": "cinekit/transitions/render_book.py (GitHub cinekit_transitions.yml)", "items": items}
(book / "transitions_book.json").write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")

cats = sorted({i["category"] for i in items})
cards = []
for i in items:
    media = (f'<video src="{i["preview"]}" muted loop autoplay playsinline preload="metadata"></video>' if i["preview"]
             else f'<div class="missing">{html.escape(i["status"])}</div>')
    cards.append(f'<figure data-cat="{html.escape(i["category"])}">{media}<figcaption><b>{i["id"]:03d}</b> {html.escape(i["name"])}'
                 f'<br><small>{html.escape(i["category"])} · {html.escape(i["kind"])} · {i["duration_s"]} s · '
                 f'{html.escape(", ".join(i["recommended_use"]))}</small>'
                 + (f'<br><small class="m">{html.escape(i["creator_match"])}</small>' if i["creator_match"] else "") + "</figcaption></figure>")
page = f"""<!doctype html><meta charset="utf-8"><title>Transitions Book</title>
<style>body{{background:#111;color:#eee;font:14px system-ui,sans-serif;margin:16px}}
.bar button{{background:#333;color:#eee;border:0;padding:6px 10px;margin:2px;border-radius:4px;cursor:pointer}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
figure{{margin:0;background:#1c1c1c;border-radius:6px;overflow:hidden}} video{{width:100%;display:block}}
figcaption{{padding:6px 8px}} small{{color:#aaa}} .m{{color:#d9a54a}} .missing{{aspect-ratio:16/9;display:grid;place-items:center;color:#777}}</style>
<h1>Transitions Book</h1><p>{sum(1 for i in items if i['preview'])} of {len(items)} rendered. Pick favourites by number.</p>
<div class="bar"><button onclick="f('')">all</button>{''.join(f'<button onclick="f(this.textContent)">{html.escape(c)}</button>' for c in cats)}</div>
<div class="grid">{''.join(cards)}</div>
<script>function f(c){{document.querySelectorAll('figure').forEach(e=>e.style.display=(!c||e.dataset.cat==c)?'':'none')}}</script>"""
(book / "index.html").write_text(page, encoding="utf-8")

ok = [i for i in items if i["preview"]]
ff = shutil.which("ffmpeg") or r"C:/ffmpeg-master-latest-win64-gpl-shared/bin/ffmpeg.exe"
if ok and Path(ff).exists():
    lst = book / "_reel_list.txt"
    lst.write_text("".join(f"file '{(book / i['preview']).as_posix()}'\n" for i in ok), encoding="utf-8")
    subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
                    str(book / "book_reel.mp4")])
    lst.unlink()
print(f"book: {len(ok)}/{len(items)} previews; manifest, index.html, book_reel.mp4 in {book}")
