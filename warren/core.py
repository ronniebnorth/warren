"""Warren core: read your prompts to Claude Code, sort them into threads (Claude names them),
and work out the trail back, where you are, and where you might be heading.

Everything here runs on your computer. The only thing sent anywhere is each new prompt, once,
to Claude through your own `claude` command (the same place you typed it in the first place).
"""
import glob, html, json, math, os, re, sys, time, webbrowser
from collections import Counter
from datetime import datetime, timezone

CLAUDE_DIR = os.path.expanduser(os.environ.get("WARREN_CLAUDE_DIR", "~/.claude/projects"))
OUT = os.path.expanduser(os.environ.get("WARREN_HOME", "~/.local/share/warren"))   # names, forecasts, the map
NOTES = os.path.expanduser(os.environ["WARREN_NOTES"]) if os.environ.get("WARREN_NOTES") else ""
DEMO = bool(os.environ.get("WARREN_DEMO"))
MODEL = os.environ.get("WARREN_MODEL", "sonnet")


def transcripts():
    """Every Claude Code conversation saved on this computer (all projects), oldest first."""
    return sorted(glob.glob(os.path.join(CLAUDE_DIR, "*", "*.jsonl")), key=os.path.getmtime)

STOP = set("""a about above after again all also am an and any are aren as at be because been before being below
between both but by can can't could did didn do does doing don done down during each few for from further get got
had has have having he her here hers him his how i i'd i'll i'm i've if in into is isn it it's its itself just
know let like make me more most much my myself need no nor not now of off on once only or other our out over own
really right same see she should so some something such sure than that that's the their them then there these they
thing things think this those through to too under until up us very want was wasn way we well were what when where
which while who why will with would yes yeah you your yours ok okay thanks thank please maybe still even
going gonna one two lot little bit back also new use used using give take put try look looks show tell said says
say ask asked again first last next time good great nice cool wow hmm just actually though""".split())

SHORT = 4        # prompts with this few telling words stay in the current thread
STAY = 0.10      # how much a prompt must share with the current thread to stay
RETURN = 0.16    # ...and with an earlier thread to count as going back to it


# ------------------------------------------------------------- reading prompts
def prompt_text(d):
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary") or d.get("isSidechain"):
        return None
    c = d.get("message", {}).get("content")
    if isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
            return None
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = re.sub(r"<system-reminder>.*?</system-reminder>", "", c, flags=re.S).strip()
    if not c or c.startswith(("<command-", "<local-command", "[Request interrupted", "Caveat:", "<bash-",
                              "This session is being continued", "<task-notification")):
        return None
    return c


def context(d):
    """What Claude did in answer: files it touched (strong hints) and words it wrote."""
    paths, text = [], []
    for b in d.get("message", {}).get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            inp = b.get("input") or {}
            v = inp.get("file_path") or inp.get("path")
            if isinstance(v, str) and "/" in v:
                parts = v.rstrip("/").split("/")
                paths.append((parts[-2] if len(parts) > 1 else "", os.path.splitext(parts[-1])[0]))
    return paths, " ".join(text)


def load(files):
    out = []
    for f in files:
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "assistant" and out:
                paths, text = context(d)
                out[-1]["ctx"] += [f"@{a}" for a, _ in paths] + [f"@{b}" for _, b in paths if b] * 1
                out[-1]["reply"] += " " + text
                continue
            t = prompt_text(d)
            if t is None:
                continue
            ts = d.get("timestamp")
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                when = os.path.getmtime(f)
            sess = os.path.basename(f)[:8]
            out.append({"t": when, "text": t, "session": sess, "ctx": [], "reply": "",
                        "key": f"{sess}:{d['uuid']}" if d.get("uuid") else f"{sess}:{when:.3f}"})
    out.sort(key=lambda p: p["t"])
    if DEMO and out:                            # the demo's made-up history always ends an hour ago
        shift = time.time() - 3600 - out[-1]["t"]
        for p in out:
            p["t"] += shift
    return out




def norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()[:100]


def load_notes(skip=()):
    """Optional (WARREN_NOTES=folder): older Claude Code sessions you saved as text or Markdown notes by
    copying the terminal (each prompt starts with '❯ ', wrapped lines are indented). Notes carry no time
    per prompt: each note is placed at the time it was saved, a minute apart, so dates are approximate."""
    out, seen = [], set(skip)
    if not NOTES:
        return out
    files = glob.glob(os.path.join(NOTES, "*.md")) + glob.glob(os.path.join(NOTES, "*.txt"))
    for f in sorted(files, key=os.path.getmtime):
        got, cur = [], None
        for line in open(f, errors="replace"):
            line = line.rstrip()
            if line.startswith("❯ "):
                cur = [line[2:].strip()]; got.append(cur)
            elif cur is not None and line.startswith("  ") and not line.lstrip().startswith(("●", "⎿")):
                cur.append(line.strip())
            else:
                cur = None
        texts = []
        for g in got:
            t = re.sub(r"\s+", " ", " ".join(g)).strip()
            n = norm(t)
            if len(t) < 2 or t.startswith("/") or n in seen:   # slash commands, and prompts already seen elsewhere
                continue
            seen.add(n); texts.append(t)
        end = os.path.getmtime(f)
        name = os.path.splitext(os.path.basename(f))[0]
        for k, t in enumerate(texts):
            out.append({"t": end - 60 * (len(texts) - k), "text": t, "session": name[:24], "approx": True,
                        "key": f"notes:{name}:{k}", "ctx": [], "reply": ""})
    return out


def words(text):
    text = re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " ", text, flags=re.S)   # pasted text is someone else's
    text = re.sub(r"https?://\S+", " ", text)
    return [w for w in re.findall(r"[a-z][a-z0-9']+", text.lower()) if len(w) > 2 and w not in STOP]


def display(text):
    """What the page shows: pasted blocks shortened to a note."""
    return re.sub(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>",
                  lambda m: f"[pasted: {m.group(1).strip()[:160]}…]", text, flags=re.S)


# ------------------------------------------------------------- threading
def thread(prompts):
    df = Counter()
    for p in prompts:
        p["w"] = words(p["text"])
        p["c"] = p.pop("ctx") + words(p.pop("reply"))[:300]
        df.update(set(p["w"]) | set(p["c"]))
    n = max(1, len(prompts))
    idf = {w: math.log(1 + n / c) for w, c in df.items()}

    def vec(ws, ctx=()):
        v = Counter()
        for w in ws:
            v[w] += 3 * idf.get(w, 1)          # your own words count most
        for w in ctx:
            v[w] += (2 if w[0] == "@" else 0.3) * idf.get(w, 1)   # then the files touched, then Claude's words
        return v

    def sim(a, b):
        dot = sum(a[w] * b[w] for w in a if w in b)
        na = math.sqrt(sum(x * x for x in a.values())); nb = math.sqrt(sum(x * x for x in b.values()))
        return dot / (na * nb) if na and nb else 0

    threads, cur = [], None
    for i, p in enumerate(prompts):
        v = vec(p["w"], p["c"])
        gap = cur is not None and p["t"] - prompts[i - 1]["t"] > 3 * 3600
        p["gap"] = gap
        kind = "same"
        if cur is None:
            kind = "start"
        elif len(set(p["w"])) <= SHORT and not gap:
            pass
        else:
            scores = [(sim(v, th["vec"]), j) for j, th in enumerate(threads)]
            here = sim(v, threads[cur]["vec"])
            best, bj = max(scores)
            if here >= STAY and (here >= best * 0.7 or best < RETURN):
                pass
            elif best >= RETURN and bj != cur:
                kind, cur = "back", bj
            else:
                kind = "new"
        if kind in ("start", "new"):
            threads.append({"id": len(threads), "parent": cur, "vec": Counter(), "words": Counter(), "items": [],
                            "from_prompt": i - 1 if cur is not None else None})
            cur = len(threads) - 1
        th = threads[cur]
        # the thread remembers mostly its recent prompts, so it can drift as the subject does
        for w in list(th["vec"]):
            th["vec"][w] *= 0.75
        th["vec"].update(v)
        th["words"].update(p["w"])
        th["items"].append(i)
        p["thread"], p["kind"] = cur, kind
    # names: words this thread uses much more than the rest
    total = Counter()
    for th in threads:
        total.update(th["words"])
    for th in threads:
        sc = {w: c * math.log(1 + n / df[w]) * (c / total[w]) for w, c in th["words"].items()}
        top = [w for w, _ in sorted(sc.items(), key=lambda x: -x[1])[:3]]
        th["name"] = " · ".join(top) if top else display(prompts[th["items"][0]]["text"])[:30]
        del th["vec"], th["words"]
    return threads


# ------------------------------------------------------------- Claude names the threads
# Each new prompt is shown to Claude (the `claude` command, headless, on your own plan:
# no API key, no extra cost) with the threads so far; it says which thread each prompt
# belongs to, or starts a new one with a short name and the thread it branched off.
# Answers are kept in names.json (in WARREN_HOME), so each prompt is only ever sent once.
SYSTEM = """You map one person's train of thought. You get the threads so far and a numbered list of
their new prompts (what they typed to an AI coding assistant), oldest first. Put every prompt in a thread.
- Short follow-ups ("yes", "push it", "build it", "try again") belong to the thread just before them.
- A prompt on a different subject starts a new thread, OR goes back to an earlier thread if it is that subject again.
- A new thread's parent is the thread they were in when the new idea came (null only if unrelated to everything).
- Thread names: 2 to 5 plain words, what the subject is, like "Garden watering timer",
  "Novel chapter outline", "Kitchen shelf build", "Learning Spanish verbs". Not generic ("Misc", "Questions").
- You may rename an existing thread if its subject has grown (keep it short).
Reply with JSON only, no other text:
{"new_threads":[{"id":"n1","name":"...","parent":<thread id or "n.." or null>}],
 "assign":[<thread id for prompt 1>, <for prompt 2>, ...],
 "rename":{"<thread id>":"new name"}}"""
BATCH = 70


def key(p):
    if "key" in p:
        return p["key"]
    return f"{p['session']}:{p['t']:.3f}"


def ask_claude(text, system=None):
    import subprocess
    os.makedirs(OUT, exist_ok=True)
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json", "--no-session-persistence",
                        "--tools", "", "--strict-mcp-config", "--system-prompt", system or SYSTEM],
                       input=text, capture_output=True, text=True, timeout=600, cwd=OUT)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    reply = json.loads(r.stdout)["result"]
    return json.JSONDecoder().raw_decode(reply[reply.index("{"):])[0]


def claude_names(prompts):
    path = os.path.join(OUT, "names.json")
    db = json.load(open(path)) if os.path.exists(path) else {"threads": {}, "assign": {}}
    todo = [i for i, p in enumerate(prompts) if key(p) not in db["assign"]]
    if todo:
        print(f"Claude is naming the threads for {len(todo)} new prompt{'s' * (len(todo) > 1)}…", flush=True)
    for b in range(0, len(todo), BATCH):
        chunk = todo[b:b + BATCH]
        th = db["threads"]
        kind = prompts[chunk[0]].get("approx", False)
        same = [p for p in prompts[:chunk[0]] if p.get("approx", False) == kind]   # notes' dates are rough: don't mix them with exact ones
        last = {}
        for p in same:
            if key(p) in db["assign"]:
                last[db["assign"][key(p)]] = display(p["text"])[:120]
        lines = ["Threads so far (id | name | parent | last prompt):"]
        recent = list(dict.fromkeys(reversed(list(last))))[:120]   # the 120 threads used most recently
        lines += [f"{k} | {th[k]['name']} | {th[k]['parent']} | {last.get(k, '')!r}" for k in recent] or ["(none yet)"]
        prev = [i for i in range(chunk[0]) if prompts[i].get("approx", False) == kind and key(prompts[i]) in db["assign"]][-3:]
        if prev:
            lines.append("\nThe last prompts before these, for context:")
            lines += [f"[{db['assign'][key(prompts[i])]}] {display(prompts[i]['text'])[:200]!r}" for i in prev]
        lines.append(f"\nNew prompts ({len(chunk)}; give exactly {len(chunk)} entries in assign):")
        for n, i in enumerate(chunk, 1):
            gap = " (after a break of hours)" if i and prompts[i]["t"] - prompts[i - 1]["t"] > 3 * 3600 else ""
            lines.append(f"{n}.{gap} {display(prompts[i]['text'])[:400]!r}")
        try:
            ans = ask_claude("\n".join(lines))
        except Exception as e:
            print(f"  Couldn't get names from Claude ({e}); using word matching instead.")
            return None
        if len(ans.get("assign", [])) != len(chunk):
            print("  Claude's answer didn't line up with the prompts; using word matching instead.")
            return None
        ids = {}
        for nt in ans.get("new_threads", []):
            real = f"t{len(th) + 1}"
            ids[nt["id"]] = real
            th[real] = {"name": nt["name"], "parent": nt.get("parent")}
        for v in th.values():
            v["parent"] = ids.get(v["parent"], v["parent"])
            if v["parent"] not in th:
                v["parent"] = None
        for k, name in (ans.get("rename") or {}).items():
            if ids.get(k, k) in th:
                th[ids.get(k, k)]["name"] = name
        for i, a in zip(chunk, ans["assign"]):
            a = ids.get(str(a), str(a))
            if a not in th:   # an unknown id: keep it with the thread before
                a = db["assign"].get(key(prompts[i - 1])) if i else None
                if a is None:
                    a = f"t{len(th) + 1}"; th[a] = {"name": "Start", "parent": None}
            db["assign"][key(prompts[i])] = a
        json.dump(db, open(path, "w"), indent=1)
        print(f"  {min(b + BATCH, len(todo))}/{len(todo)}", flush=True)
    return db


def thread_named(prompts, db):
    order, threads, cur = {}, [], None
    for i, p in enumerate(prompts):
        tid = db["assign"][key(p)]
        p["gap"] = cur is not None and p["t"] - prompts[i - 1]["t"] > 3 * 3600
        if tid not in order:
            info = db["threads"][tid]
            par = order.get(info["parent"]) if info["parent"] else None
            if par is None and cur is not None and info["parent"]:
                par = cur
            order[tid] = len(threads)
            threads.append({"id": len(threads), "parent": par, "name": info["name"], "items": [],
                            "from_prompt": i - 1 if (par is not None and i) else None})
            kind = "start" if cur is None else "new"
        else:
            kind = "same" if order[tid] == cur else "back"
        cur = order[tid]
        threads[cur]["items"].append(i)
        p["thread"], p["kind"] = cur, kind
        p.pop("ctx", None)
        p["reply"] = p.get("reply", "")[-700:]
    return threads


# ------------------------------------------------------------- where was I?
WHERE_SYSTEM = """For each thread of someone's work below (their last prompts to an AI assistant, and the end of
the assistant's last reply), say whether it was left OPEN (something still to do, test, answer or decide) or DONE
(finished, nothing waiting). For open ones, say in at most 12 plain words where it stood or what's next.
Reply with JSON only: {"<thread id>": {"open": true, "where": "..."}, ...}"""


def when_short(t):
    d, now = datetime.fromtimestamp(t), datetime.now()
    days = (now.date() - d.date()).days
    hm = d.strftime("%I:%M %p").lstrip("0").lower()
    return hm if days == 0 else f"yesterday {hm}" if days == 1 else d.strftime("%a %b %d").replace(" 0", " ")


def recent(days):
    files = [f for f in transcripts() if DEMO or os.path.getmtime(f) > time.time() - (days + 1) * 86400]
    return [p for p in load(files) if p["t"] > time.time() - days * 86400]


def where_data(days=3):
    """The trail back: the thread you're in, then the ones you left (newest first), each marked open or done."""
    prompts = recent(days)
    if not prompts:
        return {"error": f"No prompts in the last {days:g} days."}
    db = claude_names(all_prompts(notes=False))  # name in time order, history first, so names come out right
    if not db:
        return {"error": "Couldn't reach Claude to sort the threads."}
    threads = thread_named(prompts, db)
    stack = []                                  # most recently visited last
    for p in prompts:
        if stack and stack[-1] == p["thread"]:
            continue
        if p["thread"] in stack:
            stack.remove(p["thread"])
        stack.append(p["thread"])
    stack = stack[::-1][:12]
    # which are still open: one question to Claude, remembered until the thread moves on
    path = os.path.join(OUT, "where.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    stamp = {i: key(prompts[threads[i]["items"][-1]]) for i in stack}
    ask = [i for i in stack[1:] if cache.get(stamp[i]) is None]
    note = ""
    if ask:
        lines = []
        for i in ask:
            th = threads[i]
            lines.append(f"\nThread {i}: {th['name']}")
            lines += [f"  you: {display(prompts[j]['text'])[:250]!r}" for j in th["items"][-3:]]
            lines.append(f"  assistant's last reply ended: {prompts[th['items'][-1]]['reply'][-500:]!r}")
        try:
            ans = ask_claude("\n".join(lines), WHERE_SYSTEM)
            for i in ask:
                if str(i) in ans:
                    cache[stamp[i]] = ans[str(i)]
            json.dump(cache, open(path, "w"), indent=1)
        except Exception as e:
            note = f"Couldn't ask Claude which are still open: {e}"
    out = []
    for n, i in enumerate(stack):
        th, st = threads[i], cache.get(stamp[i]) or {}
        lp = prompts[th["items"][-1]]
        out.append({"name": th["name"], "when": when_short(lp["t"]), "t": lp["t"], "current": n == 0,
                    "open": n == 0 or st.get("open", True), "where": st.get("where") or "",
                    "last": display(lp["text"])[:300].strip(),
                    "prompts": [{"when": when_short(prompts[j]["t"]), "text": display(prompts[j]["text"])} for j in th["items"][-8:]]})
    return {"days": days, "trail": out, "note": note}


def where(args):
    """`warren where [DAYS]`: print the trail back."""
    d = where_data(float(args[0]) if args else 3)
    if "error" in d:
        print(d["error"]); return
    if d["note"]:
        print(f"({d['note']})")
    cur, rest = d["trail"][0], d["trail"][1:]
    print(f"\n  You're in:  {cur['name']}   ({cur['when']}, \"{cur['last'][:60]}\")\n")
    left = [x for x in rest if x["open"]]
    for n, x in enumerate(left):
        print(f"{'  You left:  ' if n == 0 else '             '}{x['name']:<34} {x['when']:<19} {x['where'] or x['last'][:60]}")
    if not left:
        print("  Nothing else left open.")
    done = [x["name"] for x in rest if not x["open"]]
    if done:
        print(f"\n  Finished and left behind: {', '.join(done)}")
    print()


# ------------------------------------------------------------- where am I?
MODES = [("building", r"\b(build|add|make|fix|change|set up|wire|implement|create|update|rename|move)\b"),
         ("shipping", r"\b(push|commit|deploy|upload|release|post|fb)\b"),
         ("writing", r"\b(doc|docs|document|write|writeup|letter|note|desc|explain)\b"),
         ("testing", r"\b(works?|worked|doesn'?t|didn'?t|error|broken|test|try|tried|still|crash|hear|sounds?)\b"),
         ("exploring", r"(\?$|\bi wonder|\bwhat if|\bjust thought|\bidea\b|\bcould\b|\bwhy\b)")]


def mode(text):
    t = display(text).lower().strip()
    for name, rx in MODES:
        if re.search(rx, t):
            return name
    return "other"


def here_data(days=7):
    """The last DAYS against your usual (the 8 weeks before)."""
    from collections import Counter
    now = time.time()
    prompts = recent(days + 56)
    db = claude_names(all_prompts(notes=False)) if prompts else None
    if not db:
        return {"error": "Couldn't reach Claude to sort the threads." if prompts else "No prompts yet."}
    threads = thread_named(prompts, db)
    cut = now - days * 86400
    week = [p for p in prompts if p["t"] >= cut]
    before = [p for p in prompts if p["t"] < cut]
    if not week:
        return {"error": f"Nothing in the last {days:g} days."}
    span_b = max(1.0, (cut - before[0]["t"]) / 86400) if before else 1.0
    scale = days / span_b                       # "usual" = the earlier weeks, scaled to the same length
    wk, bf = Counter(p["thread"] for p in week), Counter(p["thread"] for p in before)
    first_seen = {th["id"]: prompts[th["items"][0]]["t"] for th in threads}
    top = [{"name": threads[t]["name"], "n": n,
            "tag": "new" if first_seen[t] >= cut else (f"usually {bf[t] * scale:.0f}" if bf[t] else "back after a break")}
           for t, n in wk.most_common(12)]
    quiet = [{"name": threads[t]["name"], "n": n, "last": when_short(prompts[threads[t]["items"][-1]]["t"])}
             for t, n in bf.most_common() if n >= 15 and wk[t] == 0][:8]
    mw, mb = Counter(mode(p["text"]) for p in week), Counter(mode(p["text"]) for p in before)
    kinds = [{"kind": k, "now": 100 * mw[k] / len(week), "usual": 100 * mb[k] / max(1, len(before))}
             for k, _ in MODES + [("other", "")]]

    def switches(ps):
        by_day = {}
        for a, b in zip(ps, ps[1:]):
            if b["t"] - a["t"] < 3 * 3600:
                d = datetime.fromtimestamp(b["t"]).date()
                by_day.setdefault(d, [0, set()])
                by_day[d][0] += a["thread"] != b["thread"]
                by_day[d][1].add(b["thread"])
        if not by_day:
            return 0, 0
        return (sum(v[0] for v in by_day.values()) / len(by_day), sum(len(v[1]) for v in by_day.values()) / len(by_day))
    sw, tw = switches(week); sb, tb = switches(before)
    late = sum(1 for p in week if datetime.fromtimestamp(p["t"]).hour < 6)
    return {"days": days, "n": len(week), "usual": len(before) * scale, "more": max(0, len(wk) - 12), "top": top,
            "quiet": quiet, "kinds": kinds, "switches": [sw, sb], "threads": [tw, tb], "late": 100 * late / len(week)}


def here(args):
    """`warren here [DAYS]`: print the last 7 days against your usual."""
    d = here_data(float(args[0]) if args else 7)
    if "error" in d:
        print(d["error"]); return
    days = d["days"]
    print(f"\n  Where you are: the last {days:g} days ({d['n']} prompts; usual for {days:g} days: {d['usual']:.0f})\n")
    print("  Your time went to:")
    mx = d["top"][0]["n"]
    for x in d["top"][:8]:
        print(f"    {x['name']:<34} {x['n']:>4}  {'█' * max(1, round(20 * x['n'] / mx)):<20}  ({x['tag']})")
    if len(d["top"]) > 8 or d["more"]:
        print(f"    …and {len(d['top']) - 8 + d['more']} more threads")
    if d["quiet"]:
        print("\n  Gone quiet (big before, nothing this week):")
        for x in d["quiet"][:6]:
            print(f"    {x['name']:<34} {x['n']:>4} prompts before, last {x['last']}")
    print("\n  What kind of work (a rough guess from the words you use):")
    for k in d["kinds"]:
        arrow = "  ↑" if k["now"] - k["usual"] >= 5 else "  ↓" if k["usual"] - k["now"] >= 5 else ""
        print(f"    {k['kind']:<10} {k['now']:>3.0f}%   (usual {k['usual']:.0f}%){arrow}")
    print(f"\n  Jumping around: {d['switches'][0]:.0f} subject changes and {d['threads'][0]:.0f} threads on a working day "
          f"(usual {d['switches'][1]:.0f} and {d['threads'][1]:.0f})")
    print(f"  Between midnight and 6 am: {d['late']:.0f}% of this week's prompts\n")


# ------------------------------------------------------------- where will I be?
AREA_SYSTEM = """Group someone's work threads into broad AREAS of their life and work (about 10 to 15 areas in total,
2 to 4 words each, like "Music app", "Home electronics", "Writing",
"3D printing", "Garden", "Health", "Family", "Learning"). You get the areas so far (keep using them) and threads to place.
Reply with JSON only: {"<thread id>": "<area name>", ...} covering every thread listed."""


def areas_for(db):
    """Broad areas for every thread (asked once per new thread, kept in names.json)."""
    amap = db.setdefault("areas", {})
    todo = [k for k in db["threads"] if k not in amap]
    for b in range(0, len(todo), 150):
        part = todo[b:b + 150]
        known = sorted(set(amap.values()))
        text = "Areas so far: " + (", ".join(known) or "(none yet)") + "\n\nThreads:\n" + \
               "\n".join(f"{k} | {db['threads'][k]['name']}" for k in part)
        try:
            ans = ask_claude(text, AREA_SYSTEM)
        except Exception as e:
            print(f"(Couldn't sort threads into areas: {e})"); break
        for k in part:
            amap[k] = ans.get(k) or "Other"
    json.dump(db, open(os.path.join(OUT, "names.json"), "w"), indent=1)
    return amap


def next_data(save=True):
    """Patterns, not advice: threads due back on their rhythm, what follows what, a calendar of comebacks,
    the next 4 weeks by area, and how earlier forecasts scored. Saves at most one forecast a day."""
    from collections import Counter, defaultdict
    from statistics import median
    import datetime as _dt
    now = time.time()
    prompts = load(transcripts())              # exact times only; notes' dates are too rough for rhythms
    db = claude_names(prompts) if prompts else None
    if not db:
        return {"error": "Couldn't reach Claude to sort the threads." if prompts else "No prompts yet."}
    amap = areas_for(db)
    threads = thread_named(prompts, db)
    tid_of = {p["thread"]: db["assign"][key(p)] for p in prompts}   # page thread number -> names.json id
    area = lambda i: amap.get(tid_of[i], "Other")
    day = lambda t: datetime.fromtimestamp(t).date()
    today = day(now)

    # comes back on a rhythm
    active = defaultdict(set)
    for p in prompts:
        active[p["thread"]].add(day(p["t"]))
    due = []
    for i, ds in active.items():
        ds = sorted(ds)
        if len(ds) < 4:
            continue
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        typical = max(1, median(gaps))
        since = (today - ds[-1]).days
        if since >= 2 and since >= 0.8 * typical and since <= 4 * typical + 7:
            due.append((since / typical, i, typical, since, len(ds)))
    due = sorted(due, reverse=True)[:7]

    # what follows what (areas and kinds of work, from every subject change)
    trans, kinds = defaultdict(Counter), defaultdict(Counter)
    for a, b in zip(prompts, prompts[1:]):
        if b["t"] - a["t"] > 6 * 3600:
            continue
        if area(a["thread"]) != area(b["thread"]):
            trans[area(a["thread"])][area(b["thread"])] += 1
        ka, kb = mode(a["text"]), mode(b["text"])
        if ka != kb and "other" not in (ka, kb):
            kinds[ka][kb] += 1
    cur_p = prompts[-1]
    cur_area, cur_kind = area(cur_p["thread"]), mode(cur_p["text"])
    tot = sum(trans[cur_area].values())
    after_kind = None
    if kinds[cur_kind]:
        k2, n = kinds[cur_kind].most_common(1)[0]
        after_kind = {"kind": cur_kind, "next": k2, "pct": 100 * n / sum(kinds[cur_kind].values())}
    big = Counter(area(p["thread"]) for p in prompts if p["t"] > now - 28 * 86400)

    # further out: a calendar of comebacks (a run of days close together counts as one burst)
    visits = {}
    for i, ds in active.items():
        ds = sorted(ds); runs = [[ds[0], ds[0]]]
        for d in ds[1:]:
            if (d - runs[-1][1]).days <= 2:
                runs[-1][1] = d
            else:
                runs.append([d, d])
        visits[i] = runs
    soon = {d[1] for d in due}
    cal, parked = [], []
    for i, runs in visits.items():
        if len(runs) < 3 or i in soon:
            continue
        typ = median([(b[0] - a[1]).days for a, b in zip(runs, runs[1:])])
        if (today - runs[-1][1]).days > 3 * typ + 7:   # missed several of its usual returns: finished or parked
            parked.append(i); continue
        when = runs[-1][1] + _dt.timedelta(days=round(typ))
        while when < today + _dt.timedelta(days=3):    # overdue: next likely turn after a full gap more
            when += _dt.timedelta(days=round(typ))
        spread = max(3, round(0.4 * typ))
        if (when - today).days <= 92:
            cal.append((when, i, typ, spread, len(runs)))
    cal.sort()
    wkc = lambda a: Counter(area(p["thread"]) for p in prompts if now - a * 86400 <= p["t"])
    r4, r12 = wkc(28), wkc(84)
    n4, n12 = max(1, sum(r4.values())), max(1, sum(r12.values()))
    blend = {a: 0.6 * r4[a] / n4 + 0.4 * r12[a] / n12 for a in set(r4) | set(r12)}

    # keep score: forecasts are saved, and checked once their time has passed
    path = os.path.join(OUT, "forecasts.jsonl")
    old = [json.loads(l) for l in open(path)] if os.path.exists(path) else []
    hits = tries = base_hits = base_tries = ch = ct = 0
    for fc in old:
        if now - fc["t"] >= 7 * 86400:
            came = {db["assign"].get(key(p)) for p in prompts if fc["t"] <= p["t"] < fc["t"] + 7 * 86400}
            for k in fc["due"]:
                tries += 1; hits += k in came
            for k in fc["others"]:
                base_tries += 1; base_hits += k in came
        for c in fc.get("calendar", []):
            d = datetime.fromisoformat(c["date"]).timestamp(); w = c["spread"] * 86400
            if now >= d + w:
                ct += 1
                ch += any(db["assign"].get(key(p)) == c["tid"] and d - w <= p["t"] <= d + w + 86400 for p in prompts)
    saved = False
    if save and (not old or now - old[-1]["t"] > 20 * 3600):     # at most one saved forecast a day
        others = [tid_of[i] for i, ds in active.items() if len(ds) >= 4 and (today - max(ds)).days >= 2 and i not in soon]
        with open(path, "a") as fh:
            fh.write(json.dumps({"t": now, "due": [tid_of[d[1]] for d in due], "others": others, "area": cur_area,
                                 "calendar": [{"tid": tid_of[i], "date": w.isoformat(), "spread": sp}
                                              for w, i, _, sp, _ in cal]}) + "\n")
        saved = True
    return {
        "n": len(prompts), "since": f"{day(prompts[0]['t']):%b %d}",
        "due": [{"name": threads[i]["name"], "typical": typ, "since": since, "days": n} for _, i, typ, since, n in due],
        "area": cur_area, "thread": threads[cur_p["thread"]]["name"], "left": tot,
        "goes": [{"area": a2, "pct": 100 * n / tot} for a2, n in trans[cur_area].most_common(4)] if tot else [],
        "after_kind": after_kind,
        "last4": [{"area": a, "pct": 100 * n / sum(big.values())} for a, n in big.most_common(8)],
        "calendar": [{"date": w.isoformat(), "label": f"{w:%b %d}", "month": f"{w:%B}", "spread": sp,
                      "name": threads[i]["name"], "typical": typ, "bursts": n} for w, i, typ, sp, n in cal[:20]],
        "parked": [threads[i]["name"] for i in sorted(parked, key=lambda i: -len(visits[i]))],
        "outlook": [{"area": a, "pct": 100 * v, "change": 100 * (v - r4[a] / n4)}
                    for a, v in sorted(blend.items(), key=lambda x: -x[1])[:8]],
        "score": {"tries": tries, "hits": hits, "base": 100 * base_hits / max(1, base_tries), "cal_tries": ct, "cal_hits": ch,
                  "forecasts": len(old) + saved, "first": datetime.fromtimestamp((old[0] if old else {"t": now})["t"]).strftime("%b %d")},
    }


def next_(args):
    """`warren next`: print where you might be heading."""
    now = time.time()
    if "--auto" in args:                        # the daily job: skip unless today's forecast isn't saved yet
        path = os.path.join(OUT, "forecasts.jsonl")
        if os.path.exists(path):
            lines = open(path).read().splitlines()
            if lines and now - json.loads(lines[-1])["t"] < 20 * 3600:
                return
        print(f"--- {datetime.now():%Y-%m-%d %H:%M}")
    d = next_data()
    if "error" in d:
        print(d["error"]); return
    print(f"\n  Where you might be heading (patterns from {d['n']} prompts since {d['since']}; not advice)\n")
    print("  Tends to come back about now:")
    if not d["due"]:
        print("    nothing is overdue on its usual rhythm")
    for x in d["due"]:
        print(f"    {x['name']:<34} usually every ~{x['typical']:g} day{'s' * (x['typical'] != 1)}; it's been {x['since']}   ({x['days']} active days)")
    print(f"\n  You're in {d['area']} ({d['thread']}). When you leave {d['area']}, you've gone to:")
    if d["left"]:
        print(f"    (from {d['left']} times you left it{'; too few to trust' if d['left'] < 15 else ''})")
    for x in d["goes"]:
        print(f"    {x['area']:<30} {x['pct']:>3.0f}%")
    if not d["left"]:
        print("    (not enough history yet)")
    if d["after_kind"]:
        k = d["after_kind"]
        print(f"\n  After {k['kind']}, you usually switch to {k['next']} next ({k['pct']:.0f}% of the time).")
    print("\n  The last 4 weeks by area: " + ", ".join(f"{x['area']} {x['pct']:.0f}%" for x in d["last4"][:6]))
    print("\n  Further out: when threads that come in bursts are due back (rougher the further out)")
    if not d["calendar"]:
        print("    no thread has come back in enough separate bursts yet")
    month = None
    for x in d["calendar"][:14]:
        if x["month"] != month:
            print(f"    {x['month']}"); month = x["month"]
        print(f"      around {x['label']} (±{x['spread']} days)  {x['name']:<32} every ~{x['typical']:g} days, {x['bursts']} bursts so far")
    if d["parked"]:
        print(f"    Parked (missed several of their usual returns, so no date): {', '.join(d['parked'][:6])}"
              + (f" and {len(d['parked']) - 6} more" if len(d["parked"]) > 6 else ""))
    print("\n  The next 4 weeks by area, if your recent pattern holds (vs the last 4 weeks):")
    for x in d["outlook"][:7]:
        print(f"    {x['area']:<30} {x['pct']:>3.0f}%  {'↑' if x['change'] >= 3 else '↓' if x['change'] <= -3 else ' '}")
    sc = d["score"]
    if sc["cal_tries"]:
        print(f"  Calendar guesses whose dates have passed: {sc['cal_hits']} of {sc['cal_tries']} came back inside their window.")
    if sc["tries"]:
        print(f"\n  How the forecasts have done: {100 * sc['hits'] / sc['tries']:.0f}% of the 'comes back about now' threads came back "
              f"within a week ({sc['hits']} of {sc['tries']}). Other old threads came back {sc['base']:.0f}% of the time.")
    else:
        print("\n  Keeping score: forecasts are saved daily; from a week after the first, each run says how many came true.")
    print()


# ------------------------------------------------------------- page
COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#bab0ac"]


def page(prompts, threads, title):
    for p in prompts:
        p.pop("w", None); p.pop("c", None)
        p["show"] = display(p["text"])
        p["when"] = (("about " + datetime.fromtimestamp(p["t"]).strftime("%a %b %d").replace(" 0", " ") + " · note: " + p["session"])
                     if p.pop("approx", False) else datetime.fromtimestamp(p["t"]).strftime("%a %b %d, %I:%M %p").replace(" 0", " "))
        p.pop("key", None)
        del p["text"]
    data = json.dumps({"prompts": prompts, "threads": threads, "colors": COLORS})
    return TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data.replace("</", "<\\/"))


TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#14161a;color:#e8e8e8;display:flex;height:100vh}
#tree{width:340px;overflow:auto;padding:14px;flex:none;box-sizing:border-box}
#time{flex:1;overflow:auto;position:relative}
#read{width:380px;overflow:auto;padding:14px;flex:none;box-sizing:border-box}
.split{width:6px;flex:none;cursor:col-resize;background:#2a2d33;border-left:1px solid #3a3d44;border-right:1px solid #3a3d44}.split:hover,.split.drag{background:#4a6fa5}
h1{font-size:16px;margin:0 0 4px}.sub{color:#b8b8b8;font-size:12px;margin-bottom:12px}
.th{cursor:pointer;padding:3px 6px;border-radius:4px;margin:1px 0;white-space:nowrap}
.th:hover,.th.on{background:#2a2e36}.n{color:#b8b8b8;font-size:12px}
.views{margin:6px 0 10px}.views button{background:#23262d;color:#e8e8e8;border:1px solid #555;border-radius:4px;padding:4px 10px;margin-right:6px;cursor:pointer;font:inherit}
.views button.on{border-color:#7fb3ff;background:#2a3446}
.mo{margin:0 0 14px}.mh{cursor:pointer;font-weight:600;padding:4px 6px;border-radius:4px;background:#1e2127}.mh:hover{background:#2a2e36}
.mh .n{font-weight:400}.mr{display:flex;align-items:center;gap:6px;padding:2px 6px;cursor:pointer;border-radius:4px}.mr:hover{background:#2a2e36}
.mr .nm{flex:1;min-width:0}.mr .fr{display:block;color:#b8b8b8;font-size:11px}.bar{height:6px;border-radius:3px;flex:none}
.more{color:#9cc2ff;cursor:pointer;font-size:12px;padding:2px 6px}
.tg{display:inline-block;width:14px;color:#b8b8b8;cursor:pointer}.kids{margin-left:8px;border-left:1px dashed #555;padding-left:4px}
.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.p{padding:8px 10px;border-radius:6px;background:#1e2127;margin:0 0 8px;border-left:4px solid;white-space:pre-wrap;word-break:break-word}
.p .w{color:#b8b8b8;font-size:12px;display:block;margin-bottom:3px}.p.on{outline:2px solid #fff}
.tag{font-size:11px;padding:0 5px;border-radius:3px;background:#333;margin-left:4px}
svg text{fill:#e8e8e8;font-size:12px;paint-order:stroke;stroke:#14161a;stroke-width:4px}
</style></head><body>
<div id="tree"><h1>__TITLE__</h1>
<div class="views"><button id="vm">By month</button><button id="vt">Tree</button></div>
<div id="tv"><div class="sub">Each line is a thread; indented threads branched off the one above.
Click one to read it. ↩ = times you came back to it. ▾ folds a branch.</div><div id="t"></div></div>
<div id="mv"><div class="sub">Each month: what you spent it on, biggest first. "new" = started that month,
with the thread it grew out of. Click a month to jump the timeline there; click a thread to read it.</div><div id="m"></div></div></div>
<div class="split" data-for="tree"></div><div id="time"><svg id="g"></svg></div><div class="split" data-for="read"></div>
<div id="read"><div class="sub">Click a dot or a thread.</div></div>
<script>
const D = __DATA__, P = D.prompts, T = D.threads, C = i => D.colors[i % D.colors.length];
const backs = T.map(() => 0); P.forEach(p => { if (p.kind === "back") backs[p.thread]++; });
// tree
function node(th){ const d = document.createElement("div");
  const r = document.createElement("div"); r.className = "th"; r.dataset.id = th.id;
  r.innerHTML = `<span class="sw" style="background:${C(th.id)}"></span>${esc(th.name)} <span class="n">${th.items.length}${backs[th.id] ? " ↩" + backs[th.id] : ""}</span>`;
  r.onclick = () => showThread(th.id); d.appendChild(r);
  const kids = T.filter(k => k.parent === th.id);
  if (kids.length){ const k = document.createElement("div"); k.className = "kids"; kids.forEach(x => k.appendChild(node(x))); d.appendChild(k);
    const tg = document.createElement("span"); tg.className = "tg"; tg.textContent = "▾";
    tg.onclick = e => { e.stopPropagation(); const shut = k.style.display !== "none"; k.style.display = shut ? "none" : ""; tg.textContent = shut ? "▸" : "▾"; };
    r.prepend(tg); }
  return d; }
T.filter(t => t.parent === null).forEach(t => document.getElementById("t").appendChild(node(t)));
// drag the bars between panels to resize them (remembered in this browser)
document.querySelectorAll(".split").forEach(bar => { const el = document.getElementById(bar.dataset.for), left = bar.dataset.for === "tree";
  const k = "thoughts-w-" + bar.dataset.for, saved = +localStorage.getItem(k); if (saved) el.style.width = saved + "px";
  bar.onmousedown = e => { e.preventDefault(); bar.classList.add("drag"); const x0 = e.clientX, w0 = el.offsetWidth;
    const mv = ev => { const w = Math.max(160, Math.min(window.innerWidth - 300, w0 + (left ? 1 : -1) * (ev.clientX - x0))); el.style.width = w + "px"; };
    const up = () => { bar.classList.remove("drag"); localStorage.setItem(k, el.offsetWidth); removeEventListener("mousemove", mv); removeEventListener("mouseup", up); };
    addEventListener("mousemove", mv); addEventListener("mouseup", up); };
  bar.ondblclick = () => { el.style.width = ""; localStorage.removeItem(k); }; });
// zoom-out: by month
const MONTHS = new Map();
P.forEach((p, i) => { const d = new Date(p.t * 1000), k = d.getFullYear() * 100 + d.getMonth();
  if (!MONTHS.has(k)) MONTHS.set(k, {label: d.toLocaleString("en-US", {month: "long", year: "numeric"}), first: i, n: 0, th: new Map()});
  const m = MONTHS.get(k); m.n++; const e = m.th.get(p.thread) || {n: 0, first: i}; e.n++; m.th.set(p.thread, e); });
const born = T.map(t => { const d = new Date(P[t.items[0]].t * 1000); return d.getFullYear() * 100 + d.getMonth(); });
function monthView(){ const box = document.getElementById("m"); box.innerHTML = "";
  for (const [k, m] of MONTHS){ const rows = [...m.th.entries()].sort((a, b) => b[1].n - a[1].n);
    const nw = rows.filter(([id]) => born[id] === k).length, max = rows[0][1].n;
    const d = document.createElement("div"); d.className = "mo";
    d.innerHTML = `<div class="mh">${m.label} <span class="n">· ${m.n} prompts · ${rows.length} threads, ${nw} new</span></div>`;
    d.firstChild.onclick = () => { document.getElementById("time").scrollTop = Y0 + ROW * m.first - 40; };
    const list = document.createElement("div"); d.appendChild(list);
    const add = ([id, e]) => { const th = T[id], par = th.parent !== null ? T[th.parent] : null, isNew = born[id] === k;
      const r = document.createElement("div"); r.className = "mr";
      r.innerHTML = `<span class="sw" style="background:${C(id)}"></span><span class="nm">${esc(th.name)}${isNew ? '<span class="tag">new</span>' : ""}`
        + (isNew && par ? `<span class="fr">from ${esc(par.name)}</span>` : "") + `</span>`
        + `<span class="bar" style="width:${Math.max(3, 60 * e.n / max)}px;background:${C(id)}"></span><span class="n">${e.n}</span>`;
      r.onclick = () => showThread(id, e.first, true); list.appendChild(r); };
    rows.slice(0, 10).forEach(add);
    if (rows.length > 10){ const mo = document.createElement("div"); mo.className = "more"; mo.textContent = `+ ${rows.length - 10} more`;
      mo.onclick = () => { mo.remove(); rows.slice(10).forEach(add); }; d.appendChild(mo); }
    box.appendChild(d); } }
monthView();
function view(v){ document.getElementById("tv").style.display = v === "tree" ? "" : "none";
  document.getElementById("mv").style.display = v === "month" ? "" : "none";
  document.getElementById("vt").classList.toggle("on", v === "tree"); document.getElementById("vm").classList.toggle("on", v === "month"); }
document.getElementById("vt").onclick = () => view("tree"); document.getElementById("vm").onclick = () => view("month");
view(P.length && (P[P.length - 1].t - P[0].t) > 40 * 86400 ? "month" : "tree");
function esc(s){ return s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"})[c]); }
// timeline: one row per prompt, one column per thread
const ROW = 22, COL = 22, X0 = 20, Y0 = 20, g = document.getElementById("g");
// columns are reused: a thread gets the leftmost column free from where it branched off to its last prompt
const lane = [], busyTo = [];
T.forEach(th => { const a = th.from_prompt ?? th.items[0], b = th.items[th.items.length - 1];
  let c = busyTo.findIndex(e => e < a); if (c < 0) { c = busyTo.length; busyTo.push(0); }
  busyTo[c] = b; lane[th.id] = c; });
const NC = busyTo.length;
const W = X0 * 2 + COL * NC + 520, H = Y0 * 2 + ROW * P.length;
g.setAttribute("width", W); g.setAttribute("height", H);
let s = "";
const x = i => X0 + COL * lane[i], y = i => Y0 + ROW * i;
T.forEach(th => { const a = th.items[0], b = th.items[th.items.length - 1];
  s += `<line x1="${x(th.id)}" y1="${y(a)}" x2="${x(th.id)}" y2="${y(b)}" stroke="${C(th.id)}" stroke-width="2" opacity=".35"/>`;
  if (th.parent !== null && th.from_prompt !== null){ const fy = y(th.from_prompt), px = x(th.parent);
    s += `<path d="M${px},${fy} C${px},${y(a)} ${x(th.id)},${fy} ${x(th.id)},${y(a)}" stroke="${C(th.id)}" fill="none" stroke-width="2"/>`; } });
P.forEach((p, i) => { if (p.kind === "back" && i > 0){ const q = P[i - 1];
  s += `<path d="M${x(q.thread)},${y(i - 1)} C${x(q.thread)},${y(i)} ${x(p.thread)},${y(i - 1)} ${x(p.thread)},${y(i)}" stroke="#fff" fill="none" stroke-dasharray="3 3" opacity=".7"/>`; } });
P.forEach((p, i) => {
  if (p.gap || i === 0) s += `<line x1="0" x2="${W}" y1="${y(i) - ROW / 2}" y2="${y(i) - ROW / 2}" stroke="#555" stroke-dasharray="6 4"/>`
    + `<text x="${W - 8}" y="${y(i) - ROW / 2 - 3}" text-anchor="end" style="fill:#b8b8b8;font-size:11px">${esc(p.when.split(",")[0].replace(/ · note.*/, ""))}</text>`;
  s += `<circle cx="${x(p.thread)}" cy="${y(i)}" r="${p.kind === "new" || p.kind === "start" ? 7 : 5}" fill="${C(p.thread)}" stroke="${p.kind === "back" ? "#fff" : "none"}" stroke-width="2" data-i="${i}" style="cursor:pointer"/>`;
  const one = p.show.replace(/\s+/g, " ");
  s += `<text x="${x(p.thread) + 12}" y="${y(i) + 4}" data-i="${i}" style="cursor:pointer">${esc(one.length > 70 ? one.slice(0, 70) + "…" : one)}</text>`; });
g.innerHTML = s;
g.onclick = e => { const i = e.target.dataset?.i; if (i !== undefined) showThread(P[+i].thread, +i); };
function showThread(id, focus, jump){
  document.querySelectorAll(".th").forEach(r => r.classList.toggle("on", +r.dataset.id === id));
  const th = T[id], par = th.parent !== null ? T[th.parent] : null;
  let h = `<h1><span class="sw" style="background:${C(id)}"></span>${esc(th.name)}</h1><div class="sub">${th.items.length} prompts`
    + (par ? ` · branched off <a href="#" onclick="showThread(${par.id});return false" style="color:${C(par.id)}">${esc(par.name)}</a>` : "") + `</div>`;
  th.items.forEach(i => { const p = P[i];
    const tag = p.kind === "new" ? "branch" : p.kind === "back" ? "came back" : "";
    h += `<div class="p${i === focus ? " on" : ""}" id="p${i}" style="border-color:${C(id)}"><span class="w">${p.when}${tag ? `<span class="tag">${tag}</span>` : ""}</span>${esc(p.show)}</div>`; });
  const r = document.getElementById("read"); r.innerHTML = h;
  if (focus !== undefined) document.getElementById("p" + focus)?.scrollIntoView({block: "center"});
  const tm = document.getElementById("time");
  if (focus === undefined) tm.scrollTop = Math.max(0, Y0 + ROW * th.items[0] - 60);
  else if (jump) tm.scrollTop = Math.max(0, Y0 + ROW * focus - tm.clientHeight / 2);
}
</script></body></html>"""


def all_prompts(notes=True):
    """Every prompt: the Claude Code logs, plus saved notes if WARREN_NOTES is set."""
    prompts = load(transcripts())
    if notes and NOTES:
        prompts = sorted(load_notes(skip={norm(p["text"]) for p in prompts}) + prompts, key=lambda p: p["t"])
    return prompts


def map_html(prompts=None, title="Warren: the map"):
    prompts = all_prompts() if prompts is None else prompts
    if not prompts:
        return None, 0, 0
    db = claude_names(prompts)
    threads = thread_named(prompts, db) if db else thread(prompts)
    return page(prompts, threads, title), len(prompts), len(threads)
