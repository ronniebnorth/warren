"""Warren's page: a small web server on 127.0.0.1 (this computer only) with four tabs.

  Trail  where you are and the holes you left open
  Map    the month view, the tree, the timeline
  Now    the last week against your usual
  Ahead  what tends to come back, a comeback calendar, and how the forecasts have scored
"""
import json, os, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core as tt

PORT = int(os.environ.get("WARREN_PORT", "8530"))
lock = threading.Lock()
cache = {"data": None, "map": None, "at": 0, "busy": False, "error": ""}


def build():
    with lock:
        cache["busy"] = True
        try:
            data = {}
            for name, fn in (("trail", lambda: tt.where_data(float(os.environ.get("WARREN_TRAIL_DAYS", "3")))), ("now", lambda: tt.here_data(7)),
                             ("ahead", lambda: tt.next_data(save=True))):
                try:
                    data[name] = fn()
                except Exception as e:
                    traceback.print_exc()
                    data[name] = {"error": f"{type(e).__name__}: {e}"}
            cache["map"] = tt.map_html(title="Warren: the map")[0]
            cache["data"], cache["at"], cache["error"] = data, time.time(), ""
        except Exception as e:
            traceback.print_exc()
            cache["error"] = f"{type(e).__name__}: {e}"
        finally:
            cache["busy"] = False


def signature():
    """Changes when Claude Code saves anything (a cheap check; no Claude call)."""
    files = tt.transcripts() + (tt.glob.glob(os.path.join(tt.NOTES, "*")) if tt.NOTES else [])
    return sum(os.path.getmtime(x) + os.path.getsize(x) for x in files), len(files)


def watch():
    """Keep the page current: every minute, if Claude Code has saved something new, rebuild
    (at most every 4 minutes). Only new prompts cost a Claude request; an unchanged day costs nothing."""
    last = signature()
    while True:
        time.sleep(60)
        try:
            now = signature()
        except Exception:
            continue
        if now != last and not cache["busy"] and time.time() - cache["at"] > 240:
            last = now
            build()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send(PAGE, "text/html")
        if self.path == "/map":
            return self.send(cache["map"] or "<body style='background:#14161a;color:#ccc;font:14px system-ui;padding:20px'>"
                             "The map is still being built. Refresh this tab in a moment.</body>", "text/html")
        if self.path.startswith("/api/state"):
            return self.send(json.dumps({"data": cache["data"], "at": cache["at"], "busy": cache["busy"], "error": cache["error"]}))
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        if self.path == "/api/refresh":
            if not cache["busy"]:
                threading.Thread(target=build, daemon=True).start()
            return self.send(json.dumps({"ok": True}))
        self.send("not found", "text/plain", 404)


PAGE = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Warren</title>
<style>
:root{--bg:#14161a;--panel:#1e2127;--line:#33363d;--text:#e8e8e8;--dim:#b8b8b8;--accent:#7fb3ff;--warm:#f2b25c;--green:#7bc47f}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text)}
header{display:flex;align-items:center;gap:16px;padding:10px 18px;border-bottom:1px solid var(--line)}
h1{font-size:20px;margin:0;letter-spacing:.5px}h1 span{color:var(--dim);font-weight:400;font-size:13px;margin-left:8px}
nav button,.btn{background:#23262d;color:var(--text);border:1px solid #555;border-radius:6px;padding:6px 14px;cursor:pointer;font:inherit}
nav button.on{border-color:var(--accent);background:#2a3446}nav{display:flex;gap:6px}
.right{margin-left:auto;display:flex;align-items:center;gap:10px;color:var(--dim);font-size:13px}
main{padding:18px;max-width:1100px}#map{display:none;height:calc(100vh - 58px);width:100%;border:0}
.sec{margin:0 0 26px}.sec h2{font-size:15px;color:var(--dim);font-weight:600;margin:0 0 10px;text-transform:uppercase;letter-spacing:.6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:0 0 8px;cursor:pointer}
.card.cur{border-color:var(--accent);border-width:2px}.card .t{font-weight:600}.card .w{color:var(--dim);font-size:13px;margin-left:8px}
.card .s{margin-top:2px}.card .ps{display:none;margin-top:8px;border-top:1px solid var(--line);padding-top:8px}.card.open .ps{display:block}
.p{white-space:pre-wrap;word-break:break-word;margin:0 0 8px;font-size:14px}.p b{color:var(--dim);font-weight:400;font-size:12px;display:block}
.done{color:var(--dim);font-size:14px}.big{font-size:17px;margin:0 0 14px}.big b{color:var(--warm)}
.row{display:grid;grid-template-columns:260px 1fr 140px;gap:10px;align-items:center;padding:3px 0}
.bar{height:12px;border-radius:3px;background:var(--accent)}.bar.u{background:#666;height:6px;margin-top:2px}
.tag{font-size:12px;color:var(--dim)}.tag.new{color:var(--green)}
.cal{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.mo h3{margin:0 0 6px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:2px}.grid div{min-height:54px;background:var(--panel);border-radius:3px;padding:2px 3px;font-size:11px;color:var(--dim)}
.grid .hd{min-height:0;background:none;text-align:center}.grid .today{outline:2px solid var(--accent)}.grid .empty{background:none}
.ev{display:block;color:#14161a;background:var(--warm);border-radius:3px;padding:1px 3px;margin-top:2px;font-size:10px;line-height:1.2;word-break:normal;overflow-wrap:anywhere}
.grid div.win{background:#3a3320}
.note{color:var(--dim);font-size:13px;margin-top:6px}.err{color:#ff8a80}
.score{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
</style></head><body>
<header><h1>Warren<span>where the rabbit holes go</span></h1>
<nav><button data-v="trail">Trail</button><button data-v="map">Map</button><button data-v="now">Now</button><button data-v="ahead">Ahead</button></nav>
<div class="right"><span id="st"></span><button class="btn" id="rf">Refresh</button></div></header>
<main id="main"></main><iframe id="map"></iframe>
<script>
let S = null, V = localStorage.getItem("warren-view") || "trail", shownAt = 0;
const openCards = new Set();
const $ = s => document.querySelector(s), esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"})[c]);
const pct = v => Math.round(v) + "%";
document.querySelectorAll("nav button").forEach(b => b.onclick = () => show(b.dataset.v));
let timer; const again = ms => { clearTimeout(timer); timer = setTimeout(poll, ms); };
$("#rf").onclick = async () => { await fetch("/api/refresh", {method: "POST"}); again(300); };
function show(v){ V = v; localStorage.setItem("warren-view", v);
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  $("#map").style.display = v === "map" ? "block" : "none"; document.body.style.overflow = v === "map" ? "hidden" : ""; $("#main").style.display = v === "map" ? "none" : "block";
  if (v === "map" && (!$("#map").dataset.loaded || $("#map").dataset.stale)){ $("#map").src = "/map"; $("#map").dataset.loaded = 1; delete $("#map").dataset.stale; }
  render(); }
async function poll(){ const r = await (await fetch("/api/state")).json(); S = r;
  $("#st").textContent = r.busy ? "Claude is sorting your newest prompts…" : r.at ? "updated " + new Date(r.at * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}) : "";
  $("#rf").disabled = r.busy;
  if (r.at !== shownAt || !r.data){ shownAt = r.at; render();
    if ($("#map").dataset.loaded){ if (V === "map") $("#map").dataset.stale = 1; else $("#map").src = "/map"; } }
  again(r.busy || !r.data ? 2000 : 60000); }   // keeps checking, so a window left open stays current
function render(){ const m = $("#main"); if (V === "map") return;
  if (!S || !S.data){ m.innerHTML = `<p class="note">${S && S.error ? '<span class="err">' + esc(S.error) + "</span>" : "Getting everything ready. The very first time, Claude reads all your past prompts, which can take several minutes; after that it only reads new ones."}</p>`; return; }
  const d = S.data[V]; if (d.error){ m.innerHTML = `<p class="err">${esc(d.error)}</p>`; return; }
  m.innerHTML = ({trail, now, ahead})[V](d); }
function trail(d){ const [cur, ...rest] = d.trail, open = rest.filter(x => x.open), done = rest.filter(x => !x.open);
  const card = (x, c) => `<div class="card ${c} ${openCards.has(x.name) ? "open" : ""}" data-n="${esc(x.name)}" onclick="this.classList.toggle('open'); this.classList.contains('open') ? openCards.add(this.dataset.n) : openCards.delete(this.dataset.n)"><span class="t">${esc(x.name)}</span><span class="w">${esc(x.when)}</span>
    <div class="s">${esc(x.where || x.last)}</div><div class="ps">${x.prompts.map(p => `<div class="p"><b>${esc(p.when)}</b>${esc(p.text)}</div>`).join("")}</div></div>`;
  return `<div class="sec"><h2>You're in</h2>${card({...cur, where: "“" + cur.last + "”"}, "cur")}</div>
    <div class="sec"><h2>Holes you left open (newest first)</h2>${open.length ? open.map(x => card(x, "")).join("") : '<p class="note">Nothing else left open.</p>'}</div>
    ${done.length ? `<div class="sec"><h2>Finished and left behind</h2><p class="done">${done.map(x => esc(x.name)).join(" · ")}</p></div>` : ""}
    <p class="note">The last ${d.days} days. Click a hole to see its last prompts. ${esc(d.note)}</p>`; }
function now(d){ const mx = d.top[0].n, mk = Math.max(...d.kinds.map(k => Math.max(k.now, k.usual)));
  return `<p class="big">The last ${d.days} days: <b>${d.n}</b> prompts (usual ${Math.round(d.usual)}) · <b>${Math.round(d.switches[0])}</b> subject changes a working day (usual ${Math.round(d.switches[1])}) · ${pct(d.late)} between midnight and 6 am</p>
    <div class="sec"><h2>Where your time went</h2>${d.top.map(x => `<div class="row"><span>${esc(x.name)}</span><div class="bar" style="width:${100 * x.n / mx}%"></div><span class="tag ${x.tag === "new" ? "new" : ""}">${x.n} · ${esc(x.tag)}</span></div>`).join("")}
      ${d.more ? `<p class="note">…and ${d.more} more threads</p>` : ""}</div>
    ${d.quiet.length ? `<div class="sec"><h2>Gone quiet</h2>${d.quiet.map(x => `<div class="row"><span>${esc(x.name)}</span><span class="tag">${x.n} prompts in the 8 weeks before</span><span class="tag">last ${esc(x.last)}</span></div>`).join("")}</div>` : ""}
    <div class="sec"><h2>Kind of work <span class="tag">(blue = now, grey = usual; a rough guess from your words)</span></h2>${d.kinds.map(k => `<div class="row"><span>${k.kind}</span><div><div class="bar" style="width:${100 * k.now / mk}%"></div><div class="bar u" style="width:${100 * k.usual / mk}%"></div></div><span class="tag">${pct(k.now)} (usual ${pct(k.usual)})</span></div>`).join("")}</div>`; }
function ahead(d){ const sc = d.score;
  const score = sc.tries ? `Of the threads it said would come back about now, <b>${pct(100 * sc.hits / sc.tries)}</b> did within a week (${sc.hits} of ${sc.tries}). Other old threads came back ${pct(sc.base)} of the time, so the forecast is ${100 * sc.hits / sc.tries > sc.base + 5 ? "beating" : "not yet beating"} chance.`
    : `${sc.forecasts} forecast${sc.forecasts === 1 ? "" : "s"} saved since ${sc.first} (one a day, automatically). Scoring starts a week after the first.`;
  return `<p class="big">Patterns from ${d.n} prompts since ${esc(d.since)}. Not advice: just what you tend to do.</p>
    <div class="sec"><h2>Tends to come back about now</h2>${d.due.length ? d.due.map(x => `<div class="row"><span>${esc(x.name)}</span><span class="tag">usually every ~${x.typical} days; it's been ${x.since}</span><span class="tag">${x.days} active days</span></div>`).join("") : '<p class="note">Nothing is overdue on its usual rhythm.</p>'}</div>
    <div class="sec"><h2>Comeback calendar <span class="tag">(the shaded days are each guess's window)</span></h2>${calendar(d.calendar)}
      ${d.parked.length ? `<p class="note">Parked, missed several of their usual returns: ${d.parked.slice(0, 10).map(esc).join(", ")}${d.parked.length > 10 ? ` and ${d.parked.length - 10} more` : ""}</p>` : ""}</div>
    <div class="sec"><h2>Next 4 weeks by area, if your pattern holds</h2>${d.outlook.map(x => `<div class="row"><span>${esc(x.area)}</span><div class="bar" style="width:${x.pct * 2.5}%"></div><span class="tag">${pct(x.pct)} ${x.change >= 3 ? "↑" : x.change <= -3 ? "↓" : ""}</span></div>`).join("")}</div>
    <div class="sec"><h2>Right now</h2><p>You're in <b>${esc(d.area)}</b> (${esc(d.thread)}).${d.goes.length ? ` When you leave it, you've gone to ${d.goes.map(g => `${esc(g.area)} ${pct(g.pct)}`).join(", ")}${d.left < 15 ? ` <span class="tag">(only ${d.left} times, too few to trust)</span>` : ""}.` : ""}
      ${d.after_kind ? ` After ${d.after_kind.kind}, you usually switch to ${d.after_kind.next} (${pct(d.after_kind.pct)}).` : ""}</p></div>
    <div class="sec"><h2>Keeping score</h2><div class="score">${score}${sc.cal_tries ? `<br>Calendar guesses whose dates have passed: ${sc.cal_hits} of ${sc.cal_tries} came back inside their window.` : ""}</div></div>`; }
function calendar(items){ const t = new Date(); t.setHours(0, 0, 0, 0); const by = {}, win = {};
  items.forEach(x => { const d = new Date(x.date + "T00:00"); (by[d.toDateString()] ||= []).push(x);
    for (let k = -x.spread; k <= x.spread; k++){ const w = new Date(d); w.setDate(d.getDate() + k); win[w.toDateString()] = 1; } });
  let h = '<div class="cal">';
  for (let m = 0; m < 3; m++){ const first = new Date(t.getFullYear(), t.getMonth() + m, 1), days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    h += `<div class="mo"><h3>${first.toLocaleString("en-US", {month: "long", year: "numeric"})}</h3><div class="grid">` + "SMTWTFS".split("").map(c => `<div class="hd">${c}</div>`).join("");
    for (let k = 0; k < first.getDay(); k++) h += '<div class="empty"></div>';
    for (let n = 1; n <= days; n++){ const d = new Date(first.getFullYear(), first.getMonth(), n), key = d.toDateString();
      h += `<div class="${key === t.toDateString() ? "today" : ""} ${win[key] ? "win" : ""}">${n}${(by[key] || []).map(x => `<span class="ev" title="${esc(x.name)}: every ~${x.typical} days, ±${x.spread} days">${esc(x.name)}</span>`).join("")}</div>`; }
    h += "</div></div>"; }
  return h + "</div>"; }
show(V); again(0);
</script></body></html>"""


def main():
    os.makedirs(tt.OUT, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=build, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    print(f"Warren at http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
