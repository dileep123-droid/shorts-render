# Owner 27 Nov 2026: AI-lane pilot - Meta AI chat as a TEXT draft writer / researcher, on GitHub's own internet (no laptop tunnel).
# One job = one prompt on one Meta account. Job, session, prompt and answer live only in the private R2 bucket;
# this public log prints only states and counts (never prompt/answer text or chat links).
#   python meta_text.py <R2 job key>
#   job: {"name", "session": "s2work/meta_tunnel/session_N.json", "ua", "prompt", "out": "s2work/ai_lane/<name>/",
#         "max_min": 15, "stable_s": 60}
# Writes <out>answer.md (best-guess last assistant message), <out>page_full.txt (whole page text, for local salvage),
# <out>result.json (account, chars, minutes, state); a screenshot on any failure. Logged out / checkpoint -> stop, never log in.
import json, os, re, sys, time
import boto3
from botocore.config import Config
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(signature_version="s3v4"))
B = "eduorbex-media"
JOB = json.loads(S3.get_object(Bucket=B, Key=sys.argv[1])["Body"].read())
OUT = JOB["out"]; T0 = time.time()
res = {"name": JOB.get("name"), "account": JOB.get("session", "").rsplit("/", 1)[-1], "state": "started", "t0": time.strftime("%F %T")}
def put(k, body, ct="text/plain; charset=utf-8"): S3.put_object(Bucket=B, Key=OUT + k, Body=body if isinstance(body, bytes) else body.encode("utf-8"), ContentType=ct)
def save():
    res["minutes"] = round((time.time() - T0) / 60, 1); put("result.json", json.dumps(res, indent=1, ensure_ascii=False), "application/json")
def log(*a): print(*a, flush=True)
STATE = "/tmp/meta_state.json"; open(STATE, "wb").write(S3.get_object(Bucket=B, Key=JOB["session"])["Body"].read())
PROMPT = JOB["prompt"]
# a snippet of the prompt to recognise the user's own message on the page (whitespace-normalised)
SNIP = re.sub(r"\s+", " ", PROMPT)[:60]
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
with sync_playwright() as p:
    br = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = br.new_context(storage_state=STATE, viewport={"width": 1280, "height": 900}, locale="en-IN", timezone_id="Asia/Kolkata",
                         user_agent=JOB.get("ua") or None)
    ctx.route("**/*.{mp4,webm,m4s}", lambda r: r.abort())
    pg = ctx.new_page(); pg.set_default_timeout(90000)
    def fail(state):
        res["state"] = state; log("STATE", state)
        try: put("fail.png", pg.screenshot(full_page=False), "image/png"); res["screenshot"] = OUT + "fail.png"
        except Exception: pass
        try: put("page_full.txt", pg.evaluate(JS_PAGE))
        except Exception: pass
        save(); br.close(); sys.exit(0)
    try: pg.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=120000)
    except Exception as e: fail("LOAD FAILED " + type(e).__name__)
    time.sleep(6)
    if re.search(r"login|checkpoint|two_step|recover", pg.url, re.I): fail("LOGGED OUT / CHECKPOINT (url)")
    ed = pg.locator("[data-lexical-editor], [contenteditable='true'], textarea").first
    try: ed.wait_for(state="visible", timeout=60000)
    except Exception: fail("NO PROMPT BOX (logged out or checkpoint?)")
    # logged-in check: a logged-out meta.ai still shows a box, but also a prominent "Log in" button
    try:
        li = pg.evaluate("()=>[...document.querySelectorAll('a,button,[role=button]')].some(b=>/^(log in|login|sign in)$/i.test((b.innerText||'').trim()))")
    except Exception: li = False
    res["login_button_seen"] = bool(li)
    if li: fail("LOGGED OUT (log-in button on page)")
    time.sleep(1.5); ed.click(); pg.keyboard.insert_text(PROMPT); time.sleep(2); pg.keyboard.press("Enter")
    sent = False
    for _ in range(60):
        time.sleep(1)
        if pg.url.rstrip("/") != "https://www.meta.ai": sent = True; break
    if not sent:
        body = pg.evaluate(JS_PAGE).lower()[-600:]
        fail("NOT SENT" + (" (limit/login/check words on page)" if any(k in body for k in LIMIT_WORDS) else ""))
    res["state"] = "sent"; res["sent_at_min"] = round((time.time() - T0) / 60, 1); save(); log("SENT")
    deadline = time.time() + 60 * JOB.get("max_min", 15); last, last_t, clicks, ans = -1, time.time(), 0, ""
    while time.time() < deadline:
        time.sleep(10)
        try: ans = pg.evaluate(JS_ANSWER, SNIP); busy = pg.evaluate(JS_BUSY)
        except Exception: continue
        n = len(ans)
        if n != last: last, last_t = n, time.time()
        elif n > 200 and not busy and time.time() - last_t >= JOB.get("stable_s", 60):
            more = pg.evaluate(JS_MORE)
            if more and clicks < 5: clicks += more; last_t = time.time(); log("clicked continue/show-more", more); continue
            break
        if int(time.time() - T0) % 120 < 10: log("answer chars", n, "busy", busy)
    res["stable"] = time.time() < deadline; res["continue_clicks"] = clicks
    time.sleep(2)
    try: full = pg.evaluate(JS_PAGE)
    except Exception: full = ""
    try: ans = pg.evaluate(JS_ANSWER, SNIP) or ans
    except Exception: pass
    try: links = pg.evaluate("(snip)=>{window.__links=[1]; (" + JS_ANSWER + ")(snip); return window.__links;}", SNIP)
    except Exception: links = []
    put("page_full.txt", full); put("answer.md", ans); put("links.json", json.dumps(links, ensure_ascii=False), "application/json")
    res["anchor_links"] = len(links)
    res["chars"] = len(ans); res["page_chars"] = len(full)
    low = ans.lower()
    if len(ans) < 300:
        res["state"] = "SHORT/NO ANSWER" + (" (limit/login words)" if any(k in (full.lower()[-1500:]) for k in LIMIT_WORDS) else "")
        try: put("fail.png", pg.screenshot(full_page=False), "image/png"); res["screenshot"] = OUT + "fail.png"
        except Exception: pass
    else: res["state"] = "DONE" if res["stable"] else "DONE (hit max time, may be cut)"
    res["links_in_answer"] = len(re.findall(r"https?://", ans))
    try: put("end.png", pg.screenshot(full_page=False), "image/png")
    except Exception: pass
    save(); log("STATE", res["state"], "chars", res["chars"], "minutes", res["minutes"]); br.close()
