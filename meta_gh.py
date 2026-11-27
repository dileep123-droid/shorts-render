# Owner 23 Nov: Meta AI clips made on GitHub, while Meta sees the owner's laptop internet (tunnel test).
# Owner 23 Nov: only Meta's pages/requests (meta.ai, facebook.com) go through the laptop; ALL downloads (page code, pictures, videos
# from Meta's CDN *.fbcdn.net) come straight from GitHub's data centre, so the laptop's home internet stays light.
# Runs on a GitHub runner:
#   1. a local SOCKS5 proxy (127.0.0.1:1080) turns every Chrome connection into a WebSocket through the owner's
#      Cloudflare quick tunnel -> laptop_bridge.py -> the real Meta site, from the laptop's home connection
#   2. Chromium (Playwright) with the owner's saved Meta session sends the prompts, waits, reads the video links
#   3. the videos are downloaded DIRECTLY (no tunnel) and stored in R2
# Everything private (tunnel address, session, prompts, chat and video links) lives only in the private R2 bucket.
# The public log prints only counts and states.   python meta_gh.py <R2 job key>
import asyncio, json, os, sys, threading, time, urllib.request
import boto3, websockets
from botocore.config import Config
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"
def r2json(k): return json.loads(S3.get_object(Bucket=B, Key=k)["Body"].read())
EP = {} if json.loads(S3.get_object(Bucket="eduorbex-media", Key=sys.argv[1])["Body"].read()).get("direct") else r2json("s2work/meta_tunnel/endpoint.json")   # 27 Nov owner: "direct" = GitHub internet, no laptop tunnel            # {"url": "wss://...trycloudflare.com/<secret>"}
JOB = r2json(sys.argv[1])                                   # {"out": "<r2 prefix>/", "items": [{"id","prompt"}], "wait_min": 15}
# 24 Nov: "session" = which Meta account (R2 key): session.json = Meta 3, session_1.json = Meta 1, session_2.json = Meta 2
STATE = "/tmp/meta_state.json"; open(STATE, "wb").write(S3.get_object(Bucket=B, Key=JOB.get("session") or "s2work/meta_tunnel/session.json")["Body"].read())
stats = {"conn": 0, "fail": 0}

# ---------- 1) SOCKS5 -> WebSocket tunnel ----------
async def pipe_client(reader, writer):
    try:
        v, n = await reader.readexactly(2); await reader.readexactly(n); writer.write(b"\x05\x00"); await writer.drain()
        h = await reader.readexactly(4); at = h[3]
        if at == 3: host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        elif at == 1: host = ".".join(map(str, await reader.readexactly(4)))
        else: await reader.readexactly(16); host = "ipv6"
        port = int.from_bytes(await reader.readexactly(2), "big")
        ws = await websockets.connect(EP["url"], max_size=None, open_timeout=30, ping_interval=20)
        await ws.send(f"{host}:{port}"); ok = await ws.recv()
        if ok != "OK":
            stats["fail"] += 1; writer.write(b"\x05\x02\x00\x01" + bytes(6)); await writer.drain(); writer.close(); await ws.close(); return
        stats["conn"] += 1; writer.write(b"\x05\x00\x00\x01" + bytes(6)); await writer.drain()
        async def up():
            while True:
                d = await reader.read(65536)
                if not d: break
                await ws.send(d)
            await ws.close()
        async def down():
            async for m in ws:
                writer.write(m if isinstance(m, bytes) else m.encode()); await writer.drain()
            writer.close()
        await asyncio.gather(up(), down(), return_exceptions=True)
    except Exception:
        stats["fail"] += 1
        try: writer.close()
        except Exception: pass
def socks_thread():
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio.start_server(pipe_client, "127.0.0.1", 1080)); loop.run_forever()
if not JOB.get("direct"): threading.Thread(target=socks_thread, daemon=True).start(); time.sleep(1)

# ---------- 2) Chromium on meta.ai through the tunnel ----------
from playwright.sync_api import sync_playwright
res = {"t0": time.strftime("%H:%M:%S"), "items": {}}
RES = JOB.get("res") or JOB["out"] + "result.json"   # private R2 report (the feeder reads "done")
def save_res(): S3.put_object(Bucket=B, Key=RES, Body=json.dumps(res, indent=1).encode())
def shot(pg, name):
    try: S3.put_object(Bucket=B, Key=RES.rsplit("/", 1)[0] + f"/debug_{name}.png", Body=pg.screenshot())
    except Exception: pass
with sync_playwright() as p:
    br = p.chromium.launch(headless=True, **({} if JOB.get("direct") else {"proxy": {"server": "socks5://127.0.0.1:1080", "bypass": "*.fbcdn.net,fbcdn.net,*.fbsbx.com,*.cdninstagram.com"}}), args=["--disable-blink-features=AutomationControlled"])
    ctx = br.new_context(storage_state=STATE, viewport={"width": 1280, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                         user_agent=JOB.get("ua") or None)
    ctx.route("**/*.{mp4,webm,m4s,woff,woff2,ttf}", lambda r: r.abort())   # never pull video through the tunnel
    chats = {}
    def in_r2(iid):
        try: S3.head_object(Bucket=B, Key=JOB["out"] + f"{iid}_1.mp4"); return True
        except Exception: return False
    # character pictures (23 Nov): laptop picture from R2, or made here (image prompt -> picture link -> direct download)
    REFF = {}
    def open_chat(prompt, files=()):
        pg = ctx.new_page(); pg.set_default_timeout(90000)
        pg.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=120000)
        ed = pg.locator("[data-lexical-editor], [contenteditable='true']").first; ed.wait_for(state="visible", timeout=45000); time.sleep(1.5)
        if files: pg.locator("input[type=file]").first.set_input_files(list(files)); time.sleep(6 + 6 * len(files))   # same waits as meta_send.py
        ed.click(); pg.keyboard.insert_text(prompt); time.sleep(1); pg.keyboard.press("Enter")
        for _ in range(40):
            time.sleep(1)
            if pg.url.rstrip("/") != "https://www.meta.ai": return pg, pg.url
        return pg, None
    for c, r in (JOB.get("refs") or {}).items():
        f = f"/tmp/ref_{c}.jpg"
        try: S3.download_file(B, r["key"], f); REFF[c] = f; continue
        except Exception: pass
        if not r.get("prompt"): continue
        try:
            pg, url = open_chat(r["prompt"])
            for _ in range(36):                                # up to ~6 min for the picture
                time.sleep(10)
                im = pg.evaluate("()=>[...document.querySelectorAll('main img')].filter(e=>e.naturalWidth>=400 && /fbcdn|scontent/.test(e.currentSrc||e.src)).map(e=>e.currentSrc||e.src)")
                if im:
                    open(f, "wb").write(urllib.request.urlopen(urllib.request.Request(im[0], headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read())
                    S3.upload_file(f, B, r["key"]); REFF[c] = f; break
            pg.close()
        except Exception as e: print("char", c, "failed", type(e).__name__, flush=True)
    print("character pictures ready:", len(REFF), "of", len(JOB.get("refs") or {}), flush=True)
    # 23 Nov: ONE tab for all sends - "New chat" inside the page instead of reloading meta.ai each time (the page code is the big
    # data that goes through the laptop); slow loads get 90 s and a second try
    main = ctx.new_page(); main.set_default_timeout(90000)
    def fresh():
        try:
            if main.url.startswith("https://www.meta.ai"):
                main.get_by_text("New chat", exact=True).first.click(timeout=8000)
                main.wait_for_url(lambda u: u.rstrip("/") == "https://www.meta.ai", timeout=15000)
            else: raise Exception("first load")
        except Exception: main.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=120000)
        ed = main.locator("[data-lexical-editor], [contenteditable='true']").first; ed.wait_for(state="visible", timeout=90000); return ed
    def send_one(it):
        ed = fresh(); time.sleep(1.5)
        fl = [REFF[c] for c in it.get("chars", []) if c in REFF]
        if fl: main.locator("input[type=file]").first.set_input_files(fl); time.sleep(6 + 6 * len(fl))
        ed.click(); main.keyboard.insert_text(it["prompt"]); time.sleep(1); main.keyboard.press("Enter")
        for _ in range(40):
            time.sleep(1)
            if main.url.rstrip("/") != "https://www.meta.ai": return main.url
        return None
    todo = [it for it in JOB["items"] if not in_r2(it["id"])]
    if JOB.get("collect"):   # owner 24 Nov: collect-only run - open already-sent chats, no new prompts
        for iid, url in JOB["collect"].items():
            if not in_r2(iid): chats[iid] = url; res["items"][iid] = {"send": "SENT EARLIER", "chat": url}
        todo = []; print("collect only:", len(chats), "chats", flush=True)
    for it in JOB["items"]:
        if it not in todo: res["items"][it["id"]] = {"send": "ALREADY IN R2", "video": "kept"}
    stop = False
    TABS = int(JOB.get("tabs") or 1)
    # owner 24 Nov: every video also goes to Google Drive "Dileep Explains Meta Clips/<batch>" (GitHub's internet); R2 keeps a working copy
    DBATCH = JOB["out"].rstrip("/").rsplit("/", 1)[-1]; DHAVE = {}
    def to_drive(data, iid):
        if not os.environ.get("DRIVE_TOKEN"): return
        try:
            import drive_io
            if "have" not in DHAVE: DHAVE["have"] = drive_io.listing(DBATCH)
            if DHAVE["have"].get(f"{iid}_1.mp4") != len(data): drive_io.put(data, DBATCH, f"{iid}_1.mp4")
            res["items"][iid]["drive"] = True
        except Exception as e: print(iid, "drive retry later", type(e).__name__, flush=True)
    # owner 24 Nov: "use a few more tabs, tab-closing method; send ALL at once, then collect ALL at once" (Meta 3 only).
    # TABS tabs open together, each types its prompt, waits for its chat link, then the tab is CLOSED; next group of TABS.
    LIMIT_WORDS = ["limit", "try again later", "log in", "suspicious", "confirm"]
    def send_group(group):
        global stop
        pages = []
        for it in group:   # open + type in every tab of the group
            pg = None
            try:
                pg = ctx.new_page(); pg.set_default_timeout(90000)
                pg.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=120000)
                ed = pg.locator("[data-lexical-editor], [contenteditable='true']").first; ed.wait_for(state="visible", timeout=90000); time.sleep(1.5)
                fl = [REFF[c] for c in it.get("chars", []) if c in REFF]
                if fl: pg.locator("input[type=file]").first.set_input_files(fl); time.sleep(6 + 6 * len(fl))
                ed.click(); pg.keyboard.insert_text(it["prompt"]); time.sleep(1); pg.keyboard.press("Enter")
                pages.append((it, pg))
            except Exception as e:
                res["items"][it["id"]] = {"send": "NO PROMPT BOX / " + type(e).__name__, "chat": ""}; print(it["id"], "NO PROMPT BOX", flush=True)
                if pg: shot(pg, it["id"] + "_nobox")
                try: pg and pg.close()
                except Exception: pass
        for _ in range(45):   # wait until every tab has its chat link
            if all(pg.url.rstrip("/") != "https://www.meta.ai" for _, pg in pages): break
            time.sleep(1)
        for it, pg in pages:
            url = pg.url if pg.url.rstrip("/") != "https://www.meta.ai" else None
            if url: chats[it["id"]] = url; st = "SENT"
            else:
                st = "NOT SENT"; shot(pg, it["id"] + "_notsent")
                try: body = pg.evaluate("()=>(document.querySelector('main')||document.body).innerText.slice(-300)").lower()
                except Exception: body = ""
                if any(k in body for k in LIMIT_WORDS): st = "NOT SENT (limit/login/check)"; stop = True
            res["items"][it["id"]] = {"send": st, "chat": chats.get(it["id"], "")}; print(it["id"], st.split(" (")[0], flush=True)
            try: pg.close()   # tab-closing method: the tab is closed as soon as its prompt is sent
            except Exception: pass
        save_res()
    if TABS > 1:
        for attempt in (1, 2):
            for i in range(0, len(todo), TABS):
                send_group(todo[i:i + TABS])
                if stop: print("STOPPING: Meta limit/login/check page", flush=True); break
            again = [it for it in todo if str(res["items"].get(it["id"], {}).get("send", "")).startswith("NO PROMPT BOX")]
            if stop or not again: break
            todo = again; print("second try for", len(again), flush=True)
        print("all sent:", len(chats), flush=True)
        deadline = time.time() + 60 * JOB.get("wait_min", 15); left = dict(chats)
        while left and time.time() < deadline:   # collect ALL at once: TABS chat tabs together, read the video link, close
            time.sleep(45)
            ids = list(left)
            for i in range(0, len(ids), TABS):
                grp = []
                for iid in ids[i:i + TABS]:
                    pg = None
                    try:
                        pg = ctx.new_page(); pg.set_default_timeout(90000); pg.goto(left[iid], wait_until="domcontentloaded", timeout=120000); grp.append((iid, pg))
                    except Exception as e:
                        print(iid, "collect retry", type(e).__name__, flush=True)
                        try: pg and pg.close()
                        except Exception: pass
                got = {}
                for _ in range(20):
                    for iid, pg in grp:
                        if iid in got: continue
                        try:
                            v = pg.evaluate("()=>[...document.querySelectorAll('video')].map(e=>e.currentSrc||e.src||(e.querySelector('source')||{}).src).filter(x=>x && /^https?:/.test(x))")
                            if v: got[iid] = v[0]
                        except Exception: pass
                    if len(got) == len(grp): break
                    time.sleep(1)
                for iid, pg in grp:
                    try: pg.close()
                    except Exception: pass
                for iid, link in got.items():   # direct download (no tunnel) -> R2
                    try:
                        data = urllib.request.urlopen(urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read()
                        S3.put_object(Bucket=B, Key=JOB["out"] + f"{iid}_1.mp4", Body=data, ContentType="video/mp4")
                        res["items"][iid]["video"] = len(data); left.pop(iid); print(iid, "VIDEO", len(data) // 1000, "KB", flush=True)
                        to_drive(data, iid)
                    except Exception as e: print(iid, "download retry", type(e).__name__, flush=True)
                save_res()
        for iid in left: res["items"][iid]["video"] = "NOT READY in time"
        res["tunnel"] = dict(stats); res["t1"] = time.strftime("%H:%M:%S"); res["done"] = True; save_res(); br.close()
        print("tunnel connections", stats, "| videos", sum(1 for x in res["items"].values() if isinstance(x.get("video"), int)), "of", len(JOB["items"]))
        sys.exit(0)
    for attempt in (1, 2):
        again = []
        for it in todo:
            st = "?"
            try:
                url = send_one(it)
                if url: chats[it["id"]] = url; st = "SENT"
                else:
                    st = "NOT SENT"; shot(main, it["id"] + "_notsent")
                    body = main.evaluate("()=>(document.querySelector('main')||document.body).innerText.slice(-300)").lower()
                    if any(k in body for k in ["limit", "try again later", "log in", "suspicious", "confirm"]): st = "NOT SENT (limit/login/check)"
            except Exception as e:
                st = "NO PROMPT BOX / " + type(e).__name__; shot(main, it["id"] + "_nobox"); again.append(it)
            res["items"][it["id"]] = {"send": st, "chat": chats.get(it["id"], "")}; print(it["id"], st.split(" (")[0].split(" /")[0], flush=True); save_res()
            if st.startswith("NOT SENT (limit"): print("STOPPING: Meta limit/login/check page", flush=True); stop = True; break
        if stop or not again: break
        todo = again; print("second try for", len(again), flush=True)
    deadline = time.time() + 60 * JOB.get("wait_min", 15); left = dict(chats)
    while left and time.time() < deadline:                 # collect: reopen each chat, read the video link only
        time.sleep(60)
        for iid, url in list(left.items()):
            pg = main
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=120000); v = []
                for _ in range(15):
                    time.sleep(1)
                    v = pg.evaluate("()=>[...document.querySelectorAll('video')].map(e=>e.currentSrc||e.src||(e.querySelector('source')||{}).src).filter(x=>x && /^https?:/.test(x))")
                    if v: break
                if v:   # 3) direct download (no tunnel) -> R2
                    data = urllib.request.urlopen(urllib.request.Request(v[0], headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read()
                    S3.put_object(Bucket=B, Key=JOB["out"] + f"{iid}_1.mp4", Body=data, ContentType="video/mp4")
                    res["items"][iid]["video"] = len(data); left.pop(iid); print(iid, "VIDEO", len(data) // 1000, "KB", flush=True); to_drive(data, iid); save_res()
            except Exception as e: print(iid, "collect retry", type(e).__name__, flush=True)
    for iid in left: res["items"][iid]["video"] = "NOT READY in time"
    res["tunnel"] = dict(stats); res["t1"] = time.strftime("%H:%M:%S"); res["done"] = True; save_res(); br.close()
print("tunnel connections", stats, "| videos", sum(1 for x in res["items"].values() if isinstance(x.get("video"), int)), "of", len(JOB["items"]))
