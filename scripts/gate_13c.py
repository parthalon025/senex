import subprocess, sys, time, os, shutil, signal, threading, glob, json
from pathlib import Path

REPO    = r"E:\senex\tests\fixtures\repos\tiny_python"
PYTHON  = r"E:\senex\.venv\Scripts\python.exe"
CONFIG  = r"E:\senex\senex.gates-test.config.toml"
EVIDENCE = Path(r"E:\senex\docs\validation\evidence")
EVIDENCE.mkdir(parents=True, exist_ok=True)

print("=== GATE 13c: Resume mid-run ===")

# Phase 1: start audit and interrupt after 2 files complete
cmd = [PYTHON, "-m", "senex", "audit", REPO,
       "--config", CONFIG, "--no-tui", "--no-unload", "--no-wizard"]
print(f"Starting: {' '.join(cmd)}")

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
    cwd=r"E:\senex",
    env=env,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
)

output_lines = []
files_completed = 0
did_interrupt = False
lock = threading.Lock()

def read_stdout():
    global files_completed, did_interrupt
    for line in proc.stdout:
        stripped = line.rstrip()
        output_lines.append(stripped)
        print(f"  | {stripped}")
        # Format: "[N/4] auditing X.py ..."
        # "[1/4]" means file index 1 completed (0-based), so after [1/4] we've done 2
        import re
        m = re.search(r'\[(\d+)/4\]', stripped)
        if m:
            idx = int(m.group(1))
            with lock:
                files_completed = idx + 1  # [1/4] = 2nd file completed
                current_count = files_completed
            print(f"  >>> file index {idx} reported, completed={current_count}")
        with lock:
            should_interrupt = files_completed >= 2 and not did_interrupt
        if should_interrupt:
            with lock:
                did_interrupt = True
            print(f"  >>> Interrupting after {files_completed} files...")
            time.sleep(1)
            print("  >>> sending CTRL_BREAK...")
            try:
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            except Exception as e:
                print(f"  CTRL_BREAK failed: {e}, using terminate")
                proc.terminate()

t = threading.Thread(target=read_stdout, daemon=True)
t.start()

# Wait up to 3 min
for _ in range(180):
    if proc.poll() is not None:
        break
    time.sleep(1)
if proc.poll() is None:
    print("  Timeout — force terminating")
    proc.terminate()
    proc.wait(5)

t.join(timeout=5)
exit1 = proc.returncode
print(f"\nPhase 1 done. exit={exit1}, files_completed={files_completed}, lines={len(output_lines)}")
(EVIDENCE / "13c-phase1-output.txt").write_text("\n".join(output_lines), encoding="utf-8")

# Find the newest audit dir
dirs = sorted(glob.glob(r"E:\senex-audits\tiny_python\*"), key=os.path.getmtime, reverse=True)
if not dirs:
    print("ERROR: no audit dir found")
    sys.exit(1)
audit_dir = Path(dirs[0])
print(f"Audit dir: {audit_dir}")
contents = [f.name for f in audit_dir.iterdir()]
print(f"Contents: {contents}")

cp = audit_dir / "checkpoint.json"
ev = audit_dir / "events.jsonl"
if cp.exists():
    shutil.copy(str(cp), str(EVIDENCE / "13c-checkpoint-pre.json"))
    print(f"checkpoint.json: {cp.stat().st_size} bytes — saved")
    print(f"  preview: {cp.read_text(encoding='utf-8')[:500]}")
else:
    print("WARNING: checkpoint.json missing")
if ev.exists():
    shutil.copy(str(ev), str(EVIDENCE / "13c-events-pre.jsonl"))
    lines_ev = ev.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"events.jsonl: {ev.stat().st_size} bytes, {len(lines_ev)} events")
    for l in lines_ev[-8:]:
        try:
            e = json.loads(l)
            print(f"  event: {e.get('type', e.get('event_type','?'))}")
        except:
            print(f"  raw: {l[:80]}")

# Phase 2: resume
print("\n=== Phase 2: Resume ===")
r = subprocess.run(
    [PYTHON, "-m", "senex", "audit", REPO,
     "--config", CONFIG, "--no-tui", "--resume", "--no-unload", "--no-wizard"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=r"E:\senex", env=env, timeout=600
)
print(f"Resume exit={r.returncode}")
for ln in r.stdout.splitlines()[-80:]:
    print(f"  {ln}")
if r.stderr:
    print("STDERR (last 20):")
    for ln in r.stderr.splitlines()[-20:]:
        print(f"  ERR: {ln}")
(EVIDENCE / "13c-resume-output.txt").write_text(r.stdout + "\n---STDERR---\n" + r.stderr, encoding="utf-8")

# Newest audit dir after resume
dirs2 = sorted(glob.glob(r"E:\senex-audits\tiny_python\*"), key=os.path.getmtime, reverse=True)
final_dir = Path(dirs2[0])
print(f"\nFinal audit dir: {final_dir}")
print(f"Contents: {[f.name for f in final_dir.iterdir()]}")

cp2 = final_dir / "checkpoint.json"
if cp2.exists():
    shutil.copy(str(cp2), str(EVIDENCE / "13c-checkpoint-post.json"))
    print(f"checkpoint-post saved: {cp2.stat().st_size} bytes")
    print(f"  preview: {cp2.read_text(encoding='utf-8')[:400]}")

# Check findings.json covers all 4 files
fj = final_dir / "findings.json"
total_files = 0
if fj.exists():
    data = json.loads(fj.read_text(encoding="utf-8"))
    total_files = data.get("totals", {}).get("files", 0)
    print(f"findings.json totals.files = {total_files}")
    print(f"  totals: {data.get('totals', {})}")
else:
    print("WARNING: findings.json missing — checking per-file MDs")
    md_files = [f for f in final_dir.glob("*.md") if f.name not in ("combined.md","claude-handoff.md","README.md")]
    print(f"  Per-file MDs: {[f.name for f in md_files]}")
    total_files = len(md_files)

checkpoint_ok = (EVIDENCE / "13c-checkpoint-pre.json").exists()
resume_ok = r.returncode == 0
files_ok = total_files >= 4
verdict = "PASS" if (checkpoint_ok and resume_ok and files_ok) else "FAIL"
(EVIDENCE / "13c-verdict.txt").write_text(
    f"{verdict}\ncheckpoint_found={checkpoint_ok}\nresume_exit={r.returncode}\nfiles_in_findings={total_files}",
    encoding="utf-8"
)
print(f"\nGate 13c: {verdict}")
print(f"  checkpoint_ok={checkpoint_ok}, resume_ok={resume_ok}, files_ok={files_ok} (need>=4, got {total_files})")
