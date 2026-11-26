"""warren: where the rabbit holes go.

    warren              open Warren in your browser (starts it if needed)
    warren stop         stop it
    warren where [DAYS] the trail back: the thread you're in, then the ones you left open (last 3 days)
    warren here [DAYS]  where you are: the last 7 days against your usual
    warren next         where you might be heading: comebacks due, a comeback calendar, the score
    warren map          just the map, as a page
    warren schedule     save a forecast once a day by itself (adds a line to your crontab; asks first)
    warren autostart    start Warren when the computer starts, so a window left open just works (asks first)
    warren demo         try it on a made-up person's history (nothing of yours is read)
    warren help         this

Everything stays on your computer except each new prompt, which goes once to Claude through your own
`claude` command so it can name the threads. Settings (environment variables):
    WARREN_HOME         where Warren keeps its files (default ~/.local/share/warren)
    WARREN_CLAUDE_DIR   where Claude Code keeps conversations (default ~/.claude/projects)
    WARREN_NOTES        optional folder of older sessions saved as notes (copied terminal text)
    WARREN_PORT         the page's port (default 8530)
    WARREN_MODEL        the Claude model used for naming (default sonnet)
"""
import os, shutil, subprocess, sys, tempfile, time, urllib.request, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


def up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1)
        return True
    except Exception:
        return False


def start(env=None, port=None, foreground=False, open_page=True):
    from . import core
    port = port or int(os.environ.get("WARREN_PORT", "8530"))
    url = f"http://127.0.0.1:{port}"
    if not shutil.which("claude"):
        print("Warren needs Claude Code's `claude` command (https://claude.com/claude-code) to name your threads.")
        return
    if foreground:
        webbrowser.open(url)
        os.environ.update(env or {})
        os.environ["WARREN_PORT"] = str(port)
        from . import server
        server.PORT = port
        print(f"Warren (demo) at {url}. Ctrl+C to stop.")
        try:
            server.main()
        except KeyboardInterrupt:
            pass
        return
    if not up(port):
        os.makedirs(core.OUT, exist_ok=True)
        log = open(os.path.join(core.OUT, "warren.log"), "a")
        p = subprocess.Popen([sys.executable, "-m", "warren.server"], stdout=log, stderr=log,
                             env={**os.environ, **(env or {}), "WARREN_PORT": str(port)},
                             cwd=os.path.dirname(HERE), start_new_session=True)
        open(os.path.join(core.OUT, "warren.pid"), "w").write(str(p.pid))
        for _ in range(30):
            if up(port):
                break
            time.sleep(0.3)
        print(f"Warren started at {url} (`warren stop` to stop it).")
    if open_page:
        webbrowser.open(url)


def stop():
    from . import core
    pidf = os.path.join(core.OUT, "warren.pid")
    try:
        os.kill(int(open(pidf).read()), 15)
        os.remove(pidf)
        print("Warren stopped.")
    except Exception:
        print("Warren wasn't running.")


def schedule():
    line = f"17 */2 * * * {sys.executable} -m warren.cli forecast >> {os.path.join(__import__('warren.core').core.OUT, 'forecast.log')} 2>&1  # warren"
    if not shutil.which("crontab"):
        print("No crontab here. To keep score, run `warren next` once a day, or schedule `warren forecast` yourself.")
        return
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "# warren" in cur:
        print("Already scheduled:\n  " + next(l for l in cur.splitlines() if "# warren" in l)); return
    print("This adds one line to your crontab. Every 2 hours it checks whether today's forecast is saved;\n"
          "the first check each day saves one (one short Claude request), the rest do nothing:\n  " + line)
    if input("Add it? [y/N] ").strip().lower() != "y":
        print("Not added."); return
    path = os.environ.get("PATH", "")
    subprocess.run(["crontab", "-"], input=cur + f"PATH={path}\n" * ("PATH=" not in cur) + line + "\n", text=True, check=True)
    print("Added. `crontab -e` to remove it.")


def autostart():
    line = f"@reboot sleep 45 && {sys.executable} -m warren.cli start  # warren-autostart"
    if not shutil.which("crontab"):
        print("No crontab here. Start Warren with `warren` after each restart, or add `warren start` to your startup apps.")
        return
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "# warren-autostart" in cur:
        print("Already set to start at boot."); return
    print("This adds one line to your crontab, so Warren starts (without opening a window) when the computer does:\n  " + line)
    if input("Add it? [y/N] ").strip().lower() != "y":
        print("Not added."); return
    path = os.environ.get("PATH", "")
    subprocess.run(["crontab", "-"], input=cur + f"PATH={path}\n" * ("PATH=" not in cur) + line + "\n", text=True, check=True)
    print("Added. `crontab -e` to remove it.")


def demo():
    src = os.path.join(HERE, "demo")
    home = tempfile.mkdtemp(prefix="warren-demo-")
    shutil.copytree(os.path.join(src, "home"), home, dirs_exist_ok=True)
    env = {"WARREN_HOME": home, "WARREN_CLAUDE_DIR": os.path.join(src, "claude"), "WARREN_DEMO": "1", "WARREN_TRAIL_DAYS": "12"}
    os.environ.update(env)
    start(env, port=int(os.environ.get("WARREN_DEMO_PORT", "8531")), foreground=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args.pop(0) if args else "open"
    if cmd in ("help", "-h", "--help"):
        print(__doc__); return
    if cmd == "demo":
        return demo()
    from . import core
    if cmd == "open":
        start()
    elif cmd == "start":                       # start without opening a window (used at boot)
        start(open_page=False)
    elif cmd == "autostart":
        autostart()
    elif cmd == "stop":
        stop()
    elif cmd == "where":
        core.where(args)
    elif cmd == "here":
        core.here(args)
    elif cmd == "next":
        core.next_(args)
    elif cmd == "forecast":
        core.next_(["--auto"])
    elif cmd == "map":
        html, n, k = core.map_html()
        if not html:
            print("No prompts found."); return
        os.makedirs(core.OUT, exist_ok=True)
        out = os.path.join(core.OUT, "map.html")
        open(out, "w").write(html)
        print(f"{n} prompts, {k} threads: {out}")
        webbrowser.open("file://" + out)
    elif cmd == "schedule":
        schedule()
    else:
        print(f"Unknown command: {cmd}\n"); print(__doc__)


if __name__ == "__main__":
    main()
