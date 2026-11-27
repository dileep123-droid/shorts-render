# Owner 27 Nov 2026: AI lane - Meta AI chat as a TEXT draft writer / translator / researcher, on GitHub's own internet (no tunnel).
# Job, session, prompts, chat links and answers live only in the private R2 bucket; this public log prints only names/states/counts.
#   python meta_text.py <R2 job key>
# Single job (pilot format): {"name", "session", "ua", "prompt", "out", "max_min": 15, "stable_s": 60}  -> run as a batch of one.
# Batch job (owner 27 Nov 12:05: SAME SEND / COLLECT method as meta_gh.py clips). One run = one Meta account (1-3, never 4):
#   {"name", "session", "ua", "tabs": 10, "res": "<R2 key of the batch result>", "max_run_min": 75, "first_wait_s": 120,
#    "items": [{"name", "prompt", "out", "max_min", "stable_s", "chat"?}, ...]}
#   SEND    : groups of `tabs` tabs: open meta.ai, paste the prompt, send, wait only until the chat URL appears, save the chat link
#             to <out>chat.json (R2), CLOSE the tab. Items that already carry "chat" (collect-only) are not sent again.
#   COLLECT : rounds; each round reopens the chat links in groups of `tabs`, reads the answer, waits stable_s-ish inside the tab,
#             and if it stopped growing (and no Stop button) clicks continue/show-more if offered, saves answer.md etc. and CLOSES
#             the tab. Still growing -> next round. Past max_min after sending -> saved as "DONE (hit max time, may be cut)".
#   Per item: <out>answer.md, page_full.txt, links.json, result.json, chat.json (+ a screenshot on failure).
#   Logged out / checkpoint / limit page -> no more sends (account stop); unsent items get "NOT STARTED (...)".
#   Items still not finished when the run time is used up get "SENT, NOT COLLECTED" (chat.json kept for a collect-only run).
import json, os, re, sys, time, random
import boto3
from botocore.config import Config
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"
JOB = json.loads(S3.get_object(Bucket=B, Key=sys.argv[1])["Body"].read())
# owner 28 Nov: Meta 4 allowed for text too (was: assert "session_4" not in JOB["session"], "Meta 4 is never u)
T0 = time.time()
if "items" not in JOB:   # pilot single-job format -> a batch of one
    JOB = {**JOB, "tabs": 1, "items": [{"name": JOB["name"], "prompt": JOB["prompt"], "out": JOB["out"],
                                        "max_min": JOB.get("max_min", 15), "stable_s": JOB.get("stable_s", 60)}]}
ITEMS = JOB["items"]; TABS = max(1, int(JOB.get("tabs") or 1)); MAX_RUN = 60 * JOB.get("max_run_min", 75)
ACCOUNT = JOB.get("session", "").rsplit("/", 1)[-1]
BASE = "https://www.meta.ai"
def put(k, body, ct="text/plain; charset=utf-8"): S3.put_object(Bucket=B, Key=k, Body=body if isinstance(body, bytes) else body.encode("utf-8"), ContentType=ct)
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
BRES = {"name": JOB.get("name"), "account": ACCOUNT, "tabs": TABS, "items": {}, "t0": time.strftime("%F %T")}
def save_batch():
    BRES["minutes"] = round((time.time() - T0) / 60, 1)
    if JOB.get("res"): put(JOB["res"], json.dumps(BRES, indent=1, ensure_ascii=False), "application/json")
STATE = "/tmp/meta_state.json"; open(STATE, "wb").write(S3.get_object(Bucket=B, Key=JOB["session"])["Body"].read())
from playwright.sync_api import sync_playwright
LIMIT_WORDS = ["limit", "try again later", "log in", "suspicious", "confirm your identity", "checkpoint", "too many"]
JS_PAGE = "()=>(document.querySelector('main')||document.body).innerText"
# largest element under <main> whose text does NOT contain the user's prompt snippet = the assistant answer container
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
JS_SEND = """()=>{for (const b of document.querySelectorAll('button,[role=button]')) {
  const l=(b.getAttribute('aria-label')||'').trim(); if (/^send( message)?$/i.test(l) && !b.disabled) { b.click(); return 1; } } return 0;}"""
STOP = {"v": ""}
R = {}   # item name -> per-item result dict
def snip(it): return re.sub(r"\s+", " ", it["prompt"])[:60]
def save_item(it):
    r = R[it["name"]]; r["minutes"] = round((time.time() - T0) / 60, 1)
    put(it["out"] + "result.json", json.dumps(r, indent=1, ensure_ascii=False), "application/json")
    BRES["items"][it["name"]] = {"state": r["state"], "minutes": r["minutes"], "chars": r.get("chars")}
def evidence(pg, it):
    try: put(it["out"] + "fail.png", pg.screenshot(full_page=False), "image/png"); R[it["name"]]["screenshot"] = it["out"] + "fail.png"
    except Exception: pass
    try: put(it["out"] + "page_full.txt", pg.evaluate(JS_PAGE))
    except Exception: pass
def set_state(it, st, pg=None):
    R[it["name"]]["state"] = st; log(it["name"], "STATE", st)
    if pg is not None and not st.startswith("DONE"): evidence(pg, it)
    if re.search(r"LOGGED OUT|CHECKPOINT|NO PROMPT BOX|limit", st, re.I): STOP["v"] = st
    save_item(it)
def close(pg):
    try: pg and pg.close()
    except Exception: pass

with sync_playwright() as p:
    br = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = br.new_context(storage_state=STATE, viewport={"width": 1280, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                         user_agent=JOB.get("ua") or None)
    ctx.route("**/*.{mp4,webm,m4s}", lambda r: r.abort())

    def open_and_type(it):
        """SEND step 1 for one item: new tab, login check, paste, Enter. Returns the page or None (failure recorded)."""
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
        R[it["name"]]["login_button_seen"] = bool(li)
        if li: set_state(it, "LOGGED OUT (log-in button on page)", pg); close(pg); return None
        time.sleep(1.5); ed.click(); pg.keyboard.insert_text(it["prompt"]); time.sleep(2.5); pg.keyboard.press("Enter")
        return pg

    def send_group(group):
        pages = []
        for it in group:
            if STOP["v"]: break
            pg = open_and_type(it)
            if pg: pages.append((it, pg))
            time.sleep(random.uniform(2, 5))
        t = time.time(); resend = 0
        while time.time() - t < 60:   # wait only until every tab has its chat link; re-press send if Enter was swallowed
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
                it["chat"] = url; it["sent_t"] = time.time(); R[it["name"]].update({"state": "sent", "sent_at_min": round((time.time() - T0) / 60, 1)})
                put(it["out"] + "chat.json", json.dumps({"chat": url, "account": ACCOUNT}), "application/json"); save_item(it); log(it["name"], "SENT")
            else:
                try: body = pg.evaluate(JS_PAGE).lower()[-600:]
                except Exception: body = ""
                set_state(it, "NOT SENT" + (" (limit/login/check words on page)" if any(k in body for k in LIMIT_WORDS) else ""), pg)
            close(pg)   # tab-closing method: closed as soon as its prompt is sent
        save_batch()

    def finish(it, pg, stable, ans):
        r = R[it["name"]]; r["stable"] = stable
        try: full = pg.evaluate(JS_PAGE)
        except Exception: full = ""
        try: links = pg.evaluate("(snip)=>{window.__links=[1]; (" + JS_ANSWER + ")(snip); return window.__links;}", snip(it))
        except Exception: links = []
        put(it["out"] + "page_full.txt", full); put(it["out"] + "answer.md", ans); put(it["out"] + "links.json", json.dumps(links, ensure_ascii=False), "application/json")
        r.update({"anchor_links": len(links), "chars": len(ans), "page_chars": len(full), "links_in_answer": len(re.findall(r"https?://", ans))})
        if len(ans) < 300:
            lim = any(k in full.lower()[-1500:] for k in LIMIT_WORDS)
            set_state(it, "SHORT/NO ANSWER" + (" (limit/login words)" if lim else ""), pg)
        else:
            try: put(it["out"] + "end.png", pg.screenshot(full_page=False), "image/png")
            except Exception: pass
            set_state(it, "DONE" if stable else "DONE (hit max time, may be cut)")
        it["finished"] = True

    def collect_group(group):
        """COLLECT: reopen each chat, read twice ~20-30 s apart; finished = not growing and no Stop button."""
        tabs = []
        for it in group:
            pg = None
            try:
                pg = ctx.new_page(); pg.set_default_timeout(90000); pg.goto(it["chat"], wait_until="domcontentloaded", timeout=120000)
                if re.search(r"login|checkpoint|two_step|recover", pg.url, re.I): set_state(it, "LOGGED OUT / CHECKPOINT (url)", pg); close(pg); continue
                tabs.append((it, pg))
            except Exception as e: log(it["name"], "collect retry", type(e).__name__); close(pg)
        time.sleep(10)
        first = {}
        for it, pg in tabs:
            try: first[it["name"]] = (pg.evaluate(JS_ANSWER, snip(it)), pg.evaluate(JS_BUSY))
            except Exception: first[it["name"]] = ("", True)
        time.sleep(25)
        for it, pg in tabs:
            try: ans = pg.evaluate(JS_ANSWER, snip(it)); busy = pg.evaluate(JS_BUSY)
            except Exception: ans, busy = "", True
            a0, b0 = first[it["name"]]
            n, prev = len(ans), it.get("prev_len", -1)
            # the same length in two collect rounds (a reopened chat may show a frozen partial answer while Meta still writes)
            stable = n > 200 and n == len(a0) and n == prev and not busy and not b0
            if stable:
                try: more = pg.evaluate(JS_MORE)
                except Exception: more = 0
                if more and it.get("clicks", 0) < 5:
                    it["clicks"] = it.get("clicks", 0) + more; log(it["name"], "clicked continue/show-more", more); stable = False
            it["prev_len"] = n
            if stable: finish(it, pg, True, ans)
            elif time.time() - it["sent_t"] > 60 * it.get("max_min", 15): finish(it, pg, False, ans)
            else: log(it["name"], "still growing", n)
            close(pg)
        save_batch()

    for it in ITEMS:
        R[it["name"]] = {"name": it["name"], "account": ACCOUNT, "state": "started", "batch": JOB.get("name")}
        if it.get("chat"): it["sent_t"] = time.time() - 60 * 3; R[it["name"]]["state"] = "sent"   # collect-only item
    log("batch", JOB.get("name"), "items", len(ITEMS), "tabs", TABS, "collect-only", sum(1 for it in ITEMS if it.get("chat")))
    # ---- SEND ----
    todo = [it for it in ITEMS if not it.get("chat")]
    for i in range(0, len(todo), TABS):
        if STOP["v"]: break
        send_group(todo[i:i + TABS])
    for it in todo:
        if not it.get("chat") and R[it["name"]]["state"] == "started":
            R[it["name"]]["state"] = "NOT STARTED (account stop: " + STOP["v"] + ")"; save_item(it)
    log("sent", sum(1 for it in ITEMS if it.get("chat")), "of", len(ITEMS))
    # ---- COLLECT ----
    if any(it.get("chat") for it in todo): time.sleep(JOB.get("first_wait_s", 120))
    while time.time() - T0 < MAX_RUN:
        left = [it for it in ITEMS if it.get("chat") and not it.get("finished") and R[it["name"]]["state"] == "sent"]
        if not left: break
        for i in range(0, len(left), TABS): collect_group(left[i:i + TABS])
        if any(it.get("chat") and not it.get("finished") and R[it["name"]]["state"] == "sent" for it in ITEMS): time.sleep(45)
    for it in ITEMS:
        if it.get("chat") and not it.get("finished") and R[it["name"]]["state"] == "sent":
            R[it["name"]]["state"] = "SENT, NOT COLLECTED"; save_item(it)
    BRES["account_stop"] = STOP["v"]; save_batch()
    log("batch end", {k: sum(1 for v in BRES["items"].values() if v["state"].startswith(k)) for k in ("DONE", "NOT", "SENT", "SHORT", "LOG", "NO ")},
        "minutes", BRES["minutes"])
    br.close()
