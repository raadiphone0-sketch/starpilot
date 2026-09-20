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


def backup_current(repo, target, data_root):
    """Save local edits and link entries without following or changing them."""
    import hashlib, os, shutil, sqlite3, stat, tarfile, tempfile
    from datetime import datetime, timezone
    from pathlib import PurePosixPath

    repo, data_root = Path(repo).resolve(), Path(data_root)
    index_patch = v.git(repo, "diff", "--cached", "--binary", "HEAD", binary=True)
    working_patch = v.git(repo, "diff", "--binary", binary=True)
    names = [os.fsdecode(name) for name in
             v.git(repo, "ls-files", "--others", "--exclude-standard", "-z", binary=True).split(b"\0")
             if name]
    entries = []
    size = len(index_patch) + len(working_patch)
    for name in names:
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or ".git" in rel.parts:
            raise RuntimeError("Unsafe backup path: " + name)
        path = repo / name
        for parent in path.parents:
            if parent == repo:
                break
            if parent.is_symlink():
                raise RuntimeError("Backup parent is a symlink: " + name)
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise RuntimeError("Unsupported backup entry: " + name)
        size += path.lstat().st_size
        entries.append({"path": name, "link": os.readlink(path) if stat.S_ISLNK(mode) else None})
    if size > 128 * 1024 * 1024:
        raise RuntimeError("Local source changes exceed 128 MiB; stopped before installation.")
    if shutil.disk_usage(data_root).free < size + 512 * 1024 * 1024:
        raise RuntimeError("Not enough free space for a recovery backup.")
    current = v.git(repo, "rev-parse", "HEAD")
    root = data_root / "starpilot/version-backups"
    root.mkdir(parents=True, exist_ok=True)
    saved = Path(tempfile.mkdtemp(prefix="raad-v2-", dir=root))
    (saved / "index.patch").write_bytes(index_patch)
    (saved / "working.patch").write_bytes(working_patch)
    with tarfile.open(saved / "untracked.tar", "w", dereference=False) as archive:
        for entry in entries:
            archive.add(repo / entry["path"], arcname=entry["path"], recursive=False)
    with tarfile.open(saved / "untracked.tar", "r") as archive:
        members = {member.name: member for member in archive.getmembers()}
        if set(members) != set(names):
            raise RuntimeError("Untracked backup verification failed.")
        for entry in entries:
            if entry["link"] is not None:
                member = members[entry["path"]]
                if not member.issym() or member.linkname != entry["link"]:
                    raise RuntimeError("Symlink backup verification failed.")
    shutil.copytree(data_root / "params/d", saved / "params", symlinks=True)
    stats = data_root / "starpilot/model_stats.sqlite"
    if stats.is_file():
        deadline = time.monotonic() + 60
        def progress(*_):
            if time.monotonic() > deadline:
                raise RuntimeError("Model statistics backup timed out.")
        with sqlite3.connect(stats.as_uri() + "?mode=ro", uri=True, timeout=10) as source:
            with sqlite3.connect(saved / "model_stats.sqlite") as destination:
                source.backup(destination, pages=256, progress=progress)
    info = {
        "format": "raad-source-changes-v2", "repo": str(repo), "commit": current,
        "branch": v.git(repo, "branch", "--show-current"), "target": target,
        "origin": v.git(repo, "remote", "get-url", "origin"),
        "createdAt": datetime.now(timezone.utc).isoformat(), "untrackedEntries": entries,
        "indexPatchSha256": hashlib.sha256(index_patch).hexdigest(),
        "workingPatchSha256": hashlib.sha256(working_patch).hexdigest(),
    }
    version_file = data_root.parent / "VERSION"
    info["agnos"] = version_file.read_text().strip() if version_file.is_file() else None
    (saved / "manifest.json").write_text(json.dumps(info, indent=2))
    (saved / "README.txt").write_text(
        "Source changes and params snapshot; not an AGNOS disk image.\n"
        "Symlinks were archived as links without reading their targets.\n"
        "Keep this folder for reviewed recovery; do not blindly extract untracked.tar.\n")
    v.git(repo, "update-ref", "refs/starpilot/version-backups/" + saved.name, current)
    os.sync()
    return saved


def main():
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
        saved = backup_current(repo, {"branch": branch, "commit": commit}, Path("/data"))
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

if __name__ == "__main__":
    main()
