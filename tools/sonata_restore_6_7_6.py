import json, sys, time, urllib.request
sys.path.insert(0, "/data/openpilot")
from pathlib import Path
from openpilot.common.params import Params
from openpilot.starpilot.system.the_galaxy import version_install as v

repo = Path("/data/openpilot")
url = "https://github.com/raadiphone0-sketch/starpilot.git"
branch = "sonata-kor-6.7.6-recovery"
commit = "f31bcd93061d044aa0c16b621550383bbe7289cd"
api = "http://127.0.0.1:8082/api/update/"
def call(route, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(api + route, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

v.require_parked()
if v.git(repo, "rev-parse", "HEAD") != "c3e4ec630f41c4baa43254a90f718abd1bf764a1":
    raise SystemExit("STOP: installed version changed; nothing installed.")
if call("fast/status").get("running"):
    raise SystemExit("STOP: another update is running.")
remote = v.git(repo, "ls-remote", "--heads", url, "refs/heads/" + branch)
if not remote.startswith(commit + "\t"):
    raise SystemExit("STOP: recovery version could not be verified.")
with v.suspend_updater() as updater:
    v.require_parked()
    v.check_repository_idle(repo)
    saved = v._backup(repo, {"branch": branch, "commit": commit}, Path("/data"))
    (saved / "origin.txt").write_text(v.git(repo, "remote", "get-url", "origin"))
    print("BACKUP =", saved, flush=True)
    Params().put_bool("AutomaticUpdates", False)
    updater.restart_after_install()
    v.git(repo, "remote", "set-url", "origin", url)
    v._clear_staging(Path("/data"))
    v.require_parked()
    result = call("branch", {"branch": branch})
    print(result.get("message", result), flush=True)
    print("KEEP POWER AND INTERNET CONNECTED.", flush=True)
    last = None
    for _ in range(360):
        time.sleep(2)
        status = call("fast/status")
        message = status.get("progressDetail") or status.get("message")
        if message != last:
            print(message, flush=True)
            last = message
        if status.get("lastError"):
            raise SystemExit("STOP: " + status["lastError"])
        if status.get("stage") == "rebooting":
            print("REBOOTING. AGNOS installation may follow; keep power connected.", flush=True)
            break
    else:
        raise SystemExit("Installation still running. Check Galaxy before doing anything else.")
