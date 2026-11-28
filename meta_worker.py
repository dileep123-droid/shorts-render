# Owner 27 Nov 2026 ~15:50: AI lane LONG-LIVED WORKER. One GitHub run = one Meta text account (1-3, never 4), up to ~5 h.
# Keeps one logged-in browser and pulls jobs from a private R2 inbox every ~30 s, so there is no new run / install per batch.
#   python meta_worker.py <R2 key of the worker config>
#   config: {"name", "session", "ua", "tabs": 20, "dir": "s2work/ai_lane/workers/w<a>/", "max_min": 285, "hard_min": 305}
#   <dir>inbox/<item>.json : {"name", "prompt", "out", "max_min", "stable_s", "chat"?}   (lane.py writes; the worker claims by deleting)
#   SEND    : new items in groups of up to `tabs` tabs: open meta.ai, paste, send, save the chat link to <out>chat.json, CLOSE the tab.
#   COLLECT : every ~45-60 s each sent chat is reopened (groups of `tabs`); an answer is harvested the moment it is stable
#             (same length in two reads and two rounds, no Stop button) -> <out>answer.md, links.json, page_full.txt, result.json.
#   Exits after idle_min (20) with nothing to do. <dir>heartbeat.json every loop. <dir>STOP_ALL or <dir>STOP_<name> (R2) -> stop claiming, finish collecting, exit. Logged out / checkpoint / limit -> exit at once.
# This public log prints only item names, states and counts; prompts, chat links and answers live only in the private R2 bucket.
import json, os, re, sys, time, random
import boto3
from botocore.config import Config
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"
CFG = json.loads(S3.get_object(Bucket=B, Key=sys.argv[1])["Body"].read())
# owner 28 Nov: Meta 4 allowed for text too (was: assert "session_4" not in CFG["session"], "Meta 4 is never u)
T0 = time.time(); TABS = max(1, int(CFG.get("tabs") or 10)); DIR = CFG["dir"]; INBOX = DIR + "inbox/"
SOFT, HARD = 60 * CFG.get("max_min", 285), 60 * CFG.get("hard_min", 305)
ACCOUNT = CFG["session"].rsplit("/", 1)[-1]; BASE = "https://www.meta.ai"
def put(k, body, ct="text/plain; charset=utf-8"): S3.put_object(Bucket=B, Key=k, Body=body if isinstance(body, bytes) else body.encode("utf-8"), ContentType=ct)
def get(k):
    try: return S3.get_object(Bucket=B, Key=k)["Body"].read()
    except Exception: return None
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
STATE = "/tmp/meta_state.json"; open(STATE, "wb").write(get(CFG["session"]))
from playwright.sync_api import sync_playwright
LIMIT_WORDS = ["limit", "try again later", "log in", "suspicious", "confirm your identity", "checkpoint", "too many", "slow down"]
JS_PAGE = "()=>(document.querySelector('main')||document.body).innerText"
JS_ANSWER = """(snip)=>{
 const norm=s=>s.replace(/\\s+/g,' ');
 const root=document.querySelector('main')||document.body; let best=null, bl=0;
 for (const e of root.querySelectorAll('div,article,section')) {
   const t=e.innerText||''; if (t.length<=bl) continue;
   if (norm(t).includes(snip)) continue;
   best=e; bl=t.length;
 }
 if (window.__links) window.__links=best?[...best.querySelectorAll('a[href]')].map(a=>a.href):[];
 return best?best.innerText:'';
}"""
JS_BUSY = """()=>[...document.querySelectorAll('button,[role=button]')].some(b=>/^(stop|stop generating|stop responding)$/i.test((b.getAttribute('aria-label')||b.innerText||'').trim()))"""
JS_MORE = """()=>{let n=0; for (const b of document.querySelectorAll('main button, main [role=button]')) {
  const t=(b.innerText||b.getAttribute('aria-label')||'').trim();
  if (/^(continue|continue generating|keep going|show more|read more|see more)$/i.test(t)) { b.click(); n++; } } return n;}"""
# mode switch (owner 27 Nov 16:35): items with "mode": "thinking" get Meta's Thinking mode; best effort, the mode seen is saved in result.json
JS_MODES = """()=>[...document.querySelectorAll('button,[role=button],[role=combobox]')].map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim())
  .filter(t=>/^(thinking|think|instant|fast|auto|deep think|reasoning)\\b/i.test(t)).slice(0,6)"""
JS_CLICK_TEXT = """(rx)=>{const r=new RegExp(rx,'i'); for (const b of document.querySelectorAll('button,[role=button],[role=menuitem],[role=option],[role=menuitemradio],li,div[role]')) {
  const t=(b.innerText||b.getAttribute('aria-label')||'').trim(); if (r.test(t) && t.length<60) { b.click(); return t; } } return '';}"""
JS_BUTTONS = """()=>[...document.querySelectorAll('button,[role=button],[role=combobox],[aria-haspopup]')].filter(b=>b.offsetParent!==null)
  .map(b=>((b.innerText||'').trim()+' | '+(b.getAttribute('aria-label')||'')).slice(0,50)).filter(t=>t.length>3).slice(0,60)"""
JS_MENU = """()=>[...document.querySelectorAll('[role=menuitem],[role=option],[role=menuitemradio],[role=radio],[role=listbox] *')].filter(b=>b.offsetParent!==null)
  .map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim().slice(0,50)).filter(t=>t).slice(0,30)"""
DEBUG = {"n": 0}
def ensure_mode(pg, it):
    """switch Meta's composer mode picker to `mode` (e.g. thinking). Saves what it saw; the first 3 items also save a screenshot
    to <dir>debug/ so the real picker can be inspected."""
    want = it.get("mode")
    if not want: return
    r = R[it["name"]]; dbg = DEBUG["n"] < 3; DEBUG["n"] += 1
    try:
        seen = pg.evaluate(JS_MODES); r["mode_before"] = seen
        if dbg: r["buttons"] = pg.evaluate(JS_BUTTONS)
        if any(re.match(want, x, re.I) for x in seen): r["mode"] = want + " (already)"
        elif seen:
            pg.evaluate(JS_CLICK_TEXT, "^(" + "|".join(re.escape(x) for x in seen[:1]) + ")$"); time.sleep(1.5)
            if dbg:
                r["menu"] = pg.evaluate(JS_MENU)
                try: put(DIR + "debug/" + it["name"] + "_menu.png", pg.screenshot(full_page=False), "image/png")
                except Exception: pass
            got = pg.evaluate(JS_CLICK_TEXT, "^" + want + "\\b"); time.sleep(1.5); r["mode_clicked"] = got
            try: pg.keyboard.press("Escape")
            except Exception: pass
            after = pg.evaluate(JS_MODES); r["mode_after"] = after
            r["mode"] = want if any(re.match(want, x, re.I) for x in after) else ("clicked " + got[:30] if got else "not switched")
        else: r["mode"] = "no mode button"
        if dbg:
            try: put(DIR + "debug/" + it["name"] + "_after.png", pg.screenshot(full_page=False), "image/png")
            except Exception: pass
    except Exception as e: r["mode"] = "mode error " + type(e).__name__
    log(it["name"], "mode", r.get("mode"))
JS_SOURCES = """()=>{const out=[], seen=new Set(); for (const a of document.querySelectorAll('a[href^="http"]')) {
  const h=a.href; if (seen.has(h) || /meta[.]ai|facebook[.]com|instagram[.]com|whatsapp[.]com/.test(h)) continue; seen.add(h);
  let d=''; try { d=new URL(h).hostname.replace(/^www[.]/,''); } catch(e) {}
  out.push({domain:d, url:h, text:(a.innerText||'').trim().slice(0,120)}); } return out.slice(0,300);}"""
JS_SEND = """()=>{for (const b of document.querySelectorAll('button,[role=button]')) {
  const l=(b.getAttribute('aria-label')||'').trim(); if (/^send( message)?$/i.test(l) && !b.disabled) { b.click(); return 1; } } return 0;}"""
def split_parts(text, size=4000):   # paragraph-boundary chunks of about `size` characters
    paras = text.split("\n\n"); out = [""]
    for p_ in paras:
        if out[-1] and len(out[-1]) + len(p_) > size: out.append("")
        out[-1] += ("\n\n" if out[-1] else "") + p_
    return [x for x in out if x.strip()]
INLINE_MAX = 60000   # characters pasted straight into the message (bigger jobs still attach a file)
STOP = {"v": ""}; R = {}; ITEMS = {}; CNT = {"claimed": 0, "sent": 0, "done": 0, "failed": 0}
def snip(it): return re.sub(r"\s+", " ", it["prompt"])[:60]
def save_item(it):
    r = R[it["name"]]; r["minutes"] = round((time.time() - it["claimed_t"]) / 60, 1)
    put(it["out"] + "result.json", json.dumps(r, indent=1, ensure_ascii=False), "application/json")
def evidence(pg, it):
    try: put(it["out"] + "fail.png", pg.screenshot(full_page=False), "image/png")
    except Exception: pass
    try: put(it["out"] + "page_full.txt", pg.evaluate(JS_PAGE))
    except Exception: pass
def set_state(it, st, pg=None):
    R[it["name"]]["state"] = st; log(it["name"], "STATE", st)
    if pg is not None and not st.startswith("DONE"): evidence(pg, it)
    if re.search(r"LOGGED OUT|CHECKPOINT|NO PROMPT BOX|limit", st, re.I): STOP["v"] = st
    if not st.startswith(("sent", "started")): it["finished"] = True; CNT["done" if st.startswith("DONE") else "failed"] += 1
    save_item(it)
def close(pg):
    try: pg and pg.close()
    except Exception: pass
def heartbeat(extra=None):
    hb = {"t": time.time(), "at": time.strftime("%F %T"), "minutes": round((time.time() - T0) / 60, 1), "account": ACCOUNT, "tabs": TABS,
          "open": sum(1 for it in ITEMS.values() if not it.get("finished")), **CNT, "account_stop": STOP["v"], "claiming": claiming()}
    hb["name"] = CFG.get("name")
    if extra: hb.update(extra)
    put(DIR + "hb_" + CFG.get("name", "x") + ".json", json.dumps(hb), "application/json")   # one heartbeat file per worker run
def claiming(): return not STOP["v"] and time.time() - T0 < SOFT and get(DIR + "STOP_ALL") is None and get(DIR + "STOP_" + CFG.get("name", "")) is None

with sync_playwright() as p:
    br = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = br.new_context(storage_state=STATE, viewport={"width": 1280, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                         user_agent=CFG.get("ua") or None)
    ctx.route("**/*.{mp4,webm,m4s}", lambda r: r.abort())

    def open_and_type(it):
        pg = None
        try:
            pg = ctx.new_page(); pg.set_default_timeout(90000)
            pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=120000)
        except Exception as e: set_state(it, "LOAD FAILED " + type(e).__name__, pg); close(pg); return None
        time.sleep(4)
        if re.search(r"login|checkpoint|two_step|recover", pg.url, re.I): set_state(it, "LOGGED OUT / CHECKPOINT (url)", pg); close(pg); return None
        ed = pg.locator("[data-lexical-editor]:visible, [contenteditable='true']:visible, textarea:visible").first
        try: ed.wait_for(state="visible", timeout=60000)
        except Exception: set_state(it, "NO PROMPT BOX (logged out or checkpoint?)", pg); close(pg); return None
        try: li = pg.evaluate("()=>[...document.querySelectorAll('a,button,[role=button]')].some(b=>/^(log in|login|sign in)$/i.test((b.innerText||'').trim()))")
        except Exception: li = False
        if li: set_state(it, "LOGGED OUT (log-in button on page)", pg); close(pg); return None
        ensure_mode(pg, it)
        if it.get("attach") and it.get("method") == "link" and not it.get("inline_done"):   # owner 28 Nov 20:50: laptop test - Meta opened a private 2-h link and answered in 40 s
            try:
                urls = [S3.generate_presigned_url("get_object", Params={"Bucket": B, "Key": a_["key"], "ResponseContentType": "text/plain; charset=utf-8"}, ExpiresIn=7200) for a_ in it["attach"]]
                it["prompt"] = ("TEXT ONLY. Do not create any image or video. Reply in plain text only.\nOpen and read " + ("this text file" if len(urls) == 1 else "these text files") +
                                " completely: " + " ".join(urls) + "\nIt contains your full task, its rules and the material. Follow it exactly and reply in the format it asks for.")
                it["attach"] = []; it["inline_done"] = True; R[it["name"]]["link"] = len(urls)
            except Exception as e: log(it["name"], "link failed", type(e).__name__)
        if it.get("attach") and not it.get("inline_done") and it.get("method") != "attach":   # owner 28 Nov 20:05 laptop test: attached file = NO answer, same text PASTED = answer in ~30 s
            texts = []
            for a_ in it["attach"]:
                b_ = get(a_["key"])
                if b_: texts.append(b_.decode("utf-8", "replace"))
            if texts and sum(len(x) for x in texts) <= INLINE_MAX:
                it["prompt"] = "\n\n".join(texts); it["attach"] = []; it["inline_done"] = True; R[it["name"]]["inline"] = len(it["prompt"])
        if it.get("method") == "parts" and not it.get("parts"):
            it["parts"] = split_parts(it["prompt"])
        if it.get("attach"):   # owner 27 Nov 20:10 test: bulk material as attached files, short typed instruction
            paths = []
            for a in it["attach"]:
                b = get(a["key"])
                if b:
                    fp = "/tmp/attach_" + re.sub(r"[^A-Za-z0-9_.-]", "_", a.get("name", "file.txt")); open(fp, "wb").write(b); paths.append(fp)
            try:
                pg.locator("input[type=file]").first.set_input_files(paths); time.sleep(6)
                R[it["name"]]["attached"] = [os.path.basename(x) for x in paths]
            except Exception as e:
                R[it["name"]]["attach_error"] = type(e).__name__; log(it["name"], "attach failed", type(e).__name__)
        if it.get("parts") and len(it["parts"]) > 1:   # owner 28 Nov 20:35: send in parts, Meta answers OK k/N, full answer after the last
            P = it["parts"]; n = len(P)
            for k, part in enumerate(P, 1):
                head = (f"I will send one task in {n} parts. After each part reply only: OK {k}/{n}. After part {n}, do the whole task.\n\n" if k == 1 else "")
                tail = (f"\n\nThat was part {n}/{n}, the last part. Now do the whole task exactly as instructed." if k == n else f"\n\n(part {k}/{n} - reply only OK {k}/{n})")
                time.sleep(1.5); ed.click(); pg.keyboard.insert_text(head + part + tail); time.sleep(2)
                try:
                    if not pg.evaluate(JS_SEND): pg.keyboard.press("Enter")
                except Exception: pg.keyboard.press("Enter")
                if k < n:
                    t0 = time.time()
                    while time.time() - t0 < 90:
                        time.sleep(4)
                        try:
                            if f"OK {k}/{n}" in pg.evaluate("()=>document.body.innerText"): break
                        except Exception: pass
                    ed = pg.locator("[data-lexical-editor]:visible, [contenteditable='true']:visible, textarea:visible").first
            it["prompt"] = P[-1]; R[it["name"]]["parts"] = n
            return pg
        time.sleep(1.5); ed.click(); pg.keyboard.insert_text(it["prompt"]); time.sleep(2.5); pg.keyboard.press("Enter")
        return pg

    def send_group(group):
        pages = []
        for it in group:
            if STOP["v"]: break
            pg = open_and_type(it)
            if pg: pages.append((it, pg))
            time.sleep(random.uniform(1, 2))
        t = time.time(); resend = 0
        while time.time() - t < 60:
            waiting = [(it, pg) for it, pg in pages if pg.url.rstrip("/") == BASE]
            if not waiting: break
            if time.time() - t > 12 * (resend + 1) and resend < 3:
                resend += 1
                for it, pg in waiting:
                    try:
                        if not pg.evaluate(JS_SEND): pg.keyboard.press("Enter")
                    except Exception: pass
            time.sleep(1)
        for it, pg in pages:
            url = pg.url if pg.url.rstrip("/") != BASE else None
            if url:
                it["chat"] = url; it["sent_t"] = time.time(); R[it["name"]].update({"state": "sent", "sent_after_min": round((time.time() - it["claimed_t"]) / 60, 1)})
                put(it["out"] + "chat.json", json.dumps({"chat": url, "account": ACCOUNT}), "application/json"); save_item(it); CNT["sent"] += 1; log(it["name"], "SENT")
            else:
                try: body = pg.evaluate(JS_PAGE).lower()[-600:]
                except Exception: body = ""
                set_state(it, "NOT SENT" + (" (limit/login/check words on page)" if any(k in body for k in LIMIT_WORDS) else ""), pg)
            close(pg)

    def finish(it, pg, stable, ans):
        r = R[it["name"]]; r["stable"] = stable
        try: full = pg.evaluate(JS_PAGE)
        except Exception: full = ""
        try: links = pg.evaluate("(snip)=>{window.__links=[1]; (" + JS_ANSWER + ")(snip); return window.__links;}", snip(it))
        except Exception: links = []
        put(it["out"] + "page_full.txt", full); put(it["out"] + "answer.md", ans); put(it["out"] + "links.json", json.dumps(links, ensure_ascii=False), "application/json")
        m = re.search(r"Answered with (\d+) sub-?agents", full or "", re.I)
        try:   # every link on the page outside the answer text = the Steps / Sources panel; saved for fact-checking
            src = pg.evaluate(JS_SOURCES)
            put(it["out"] + "sources.json", json.dumps(src, ensure_ascii=False), "application/json"); r["sources"] = len(src)
        except Exception: pass
        r.update({"anchor_links": len(links), "chars": len(ans), "answer_after_min": round((time.time() - it["sent_t"]) / 60, 1),
                  "subagents": int(m.group(1)) if m else 0, "thinking_shown": bool(re.search(r"Show thinking|Thought for", full or ""))})
        if len(ans) < 300:
            # look for limit words OUTSIDE our own prompt text (27 Nov 18:25: the prompt itself contains "limit" / "log in" -> false account stops)
            page = (full or "").lower()
            for chunk in re.split(r"\n\s*\n", it["prompt"].lower()):
                if len(chunk) > 40: page = page.replace(chunk.strip(), " ")
            lim = any(k in page[-1500:] for k in LIMIT_WORDS)
            set_state(it, "SHORT/NO ANSWER" + (" (limit/login words)" if lim else " (no answer yet; prompt shown, nothing written)"), pg)
        else: set_state(it, "DONE" if stable else "DONE (hit max time, may be cut)")

    def collect_group(group):
        tabs = []
        for it in group:
            pg = None
            try:
                pg = ctx.new_page(); pg.set_default_timeout(90000); pg.goto(it["chat"], wait_until="domcontentloaded", timeout=120000)
                if re.search(r"login|checkpoint|two_step|recover", pg.url, re.I): set_state(it, "LOGGED OUT / CHECKPOINT (url)", pg); close(pg); continue
                tabs.append((it, pg))
            except Exception as e: log(it["name"], "collect retry", type(e).__name__); close(pg)
        time.sleep(6)
        first = {}
        for it, pg in tabs:
            try: first[it["name"]] = (pg.evaluate(JS_ANSWER, snip(it)), pg.evaluate(JS_BUSY))
            except Exception: first[it["name"]] = ("", True)
        time.sleep(15)
        for it, pg in tabs:
            try: ans = pg.evaluate(JS_ANSWER, snip(it)); busy = pg.evaluate(JS_BUSY)
            except Exception: ans, busy = "", True
            a0, b0 = first[it["name"]]; n, prev = len(ans), it.get("prev_len", -1)
            stable = n > 200 and n == len(a0) and not busy and not b0   # owner 28 Nov 21:00 faster: two identical reads 15 s apart (was: also the previous pass)
            if stable:
                try: more = pg.evaluate(JS_MORE)
                except Exception: more = 0
                if more and it.get("clicks", 0) < 5: it["clicks"] = it.get("clicks", 0) + more; stable = False
            it["prev_len"] = n
            # owner 28 Nov 17:20: Meta sometimes shows only the spinning logo and never answers -> ask once more IN THE SAME CHAT before giving up
            if not stable and n < 50 and not it.get("nudged") and time.time() - it["sent_t"] > 3 * 60:   # owner 28 Nov 18:13: a short follow-up makes Meta answer at once (spinner counts as busy, so no busy check)
                try:
                    ed = pg.locator("[data-lexical-editor]:visible, [contenteditable='true']:visible, textarea:visible").first
                    ed.click(); time.sleep(0.8); pg.keyboard.insert_text("Could you please reply"); time.sleep(1.5)
                    sent = False
                    for _ in range(3):   # 28 Nov 19:20: Enter alone did not send -> click Meta's Send button, then check the text is in the chat
                        try:
                            if not pg.evaluate(JS_SEND): pg.keyboard.press("Enter")
                        except Exception: pg.keyboard.press("Enter")
                        time.sleep(4)
                        try: sent = pg.evaluate("()=>[...document.querySelectorAll('div,span,p')].some(e=>e.children.length==0 && (e.innerText||'').trim()==='Could you please reply')")
                        except Exception: sent = False
                        if sent: break
                    it["nudged"] = time.time(); R[it["name"]]["nudged"] = "sent" if sent else "typed, not confirmed"; log(it["name"], "NUDGED", "sent" if sent else "NOT CONFIRMED")
                except Exception as e: log(it["name"], "nudge failed", type(e).__name__)
            if it.get("nudged") and not stable and time.time() - it["nudged"] < 8 * 60 and time.time() - it["sent_t"] > 60 * it.get("max_min", 15):
                close(pg); continue   # give the re-asked chat up to 8 more minutes
            if stable: finish(it, pg, True, ans)
            elif time.time() - it["sent_t"] > 60 * it.get("max_min", 15): finish(it, pg, False, ans)
            close(pg)

    def claim():
        new = []
        try: objs = S3.list_objects_v2(Bucket=B, Prefix=INBOX).get("Contents", [])
        except Exception: objs = []
        for o in sorted(objs, key=lambda o: o["Key"]):
            b = get(o["Key"])
            try: S3.delete_object(Bucket=B, Key=o["Key"])
            except Exception: continue
            if not b: continue
            it = json.loads(b); it["claimed_t"] = time.time()
            R[it["name"]] = {"name": it["name"], "account": ACCOUNT, "state": "started", "worker": CFG.get("name")}
            if it.get("chat"): it["sent_t"] = time.time() - 120; R[it["name"]]["state"] = "sent"   # collect-only item
            ITEMS[it["name"]] = it; new.append(it); CNT["claimed"] += 1
        return new

    log("worker", CFG.get("name"), "account", ACCOUNT, "tabs", TABS); heartbeat({"phase": "start"})
    last_collect = 0; last_act = time.time(); IDLE = 60 * CFG.get("idle_min", 20)
    while True:
        if claiming():
            got = claim()
            if got: last_act = time.time()
            fresh = [it for it in got if not it.get("chat")]
            for i in range(0, len(fresh), TABS):
                if STOP["v"]: break
                send_group(fresh[i:i + TABS]); heartbeat({"phase": "sending"})
            for it in fresh:
                if not it.get("chat") and R[it["name"]]["state"] == "started": set_state(it, "NOT STARTED (account stop: " + STOP["v"] + ")")
        if STOP["v"]: break
        due = [it for it in ITEMS.values() if it.get("chat") and not it.get("finished") and time.time() - it["sent_t"] > 25]
        if due and time.time() - last_collect > 15:
            last_collect = time.time()
            for i in range(0, len(due), TABS):
                collect_group(due[i:i + TABS])
                if STOP["v"]: break
        heartbeat()
        open_ = [it for it in ITEMS.values() if not it.get("finished")]
        if open_: last_act = time.time()
        if not claiming() and not open_: break
        if not open_ and time.time() - last_act > IDLE: log("idle", CFG.get("idle_min", 20), "min: exit"); break
        if time.time() - T0 > HARD: break
        time.sleep(random.uniform(5, 10))
    for it in ITEMS.values():
        if not it.get("finished"):
            R[it["name"]]["state"] = "SENT, NOT COLLECTED" if it.get("chat") else "NOT STARTED (worker exit)"; save_item(it)
    heartbeat({"exited": True})
    log("worker end", CNT, "minutes", round((time.time() - T0) / 60, 1), "stop", STOP["v"][:60])
    br.close()
