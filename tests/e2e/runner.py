"""End-to-end test runner for homeassistant-ai-gateway.

For each settings variant in matrix.yaml the runner:
  1. Builds an isolated .env (root .env + common_env + matrix env) and writes
     it under _runs/<run>/.env.
  2. Optionally rebuilds the RAG index against the test fixtures.
  3. Spawns services/voice_gateway/main.py as a subprocess on GATEWAY_PORT.
  4. Streams stdout/stderr line-by-line into _runs/<run>/log.txt.
  5. POSTs each case from cases.yaml to /audio (or /text) and saves the WAV
     reply under reports/<ts>/audio/<run>/<case>.wav.
  6. Slices the captured log between request-start and reply-done into a
     per-request structured step using log_parser.py.
  7. Evaluates expect / expect_per_run rules.
  8. Writes results.json and renders results.html via report.py.

Usage:
  python -m tests.e2e.runner [--only RUN] [--case CASE] [--live] [--open]
                             [--rebuild-rag] [--keep] [--port 8799]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import requests
import yaml

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _resolve_gateway_python() -> str:
    """Find the Python that has uvicorn available."""
    import importlib.util
    if importlib.util.find_spec("uvicorn") is not None:
        return sys.executable

    uvicorn_bin = shutil.which("uvicorn")
    if uvicorn_bin:
        candidate = Path(uvicorn_bin).parent / "python"
        if candidate.is_file():
            return str(candidate)
        candidate3 = Path(uvicorn_bin).parent / "python3"
        if candidate3.is_file():
            return str(candidate3)

    print("WARNING: uvicorn not found in current Python or PATH.", file=sys.stderr)
    return sys.executable


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _build_env(local_env: dict, common: dict, run_env: dict, run_name: str) -> dict[str, str]:
    """Merge: root .env + common_env + run-specific env. {repo_root}/{run} substituted."""
    merged: dict[str, str] = {}
    for src in (local_env, common or {}, run_env or {}):
        for k, v in src.items():
            merged[k] = str(v)
    for k, v in list(merged.items()):
        merged[k] = v.replace("{repo_root}", str(REPO_ROOT)).replace("{run}", run_name)
    return merged


def _write_env_file(env: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in env.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------

def _wait_port_free(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
            except OSError:
                return
        time.sleep(0.2)


def _wait_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


class GatewaySubprocess:
    """Runs services/voice_gateway/main.py with an isolated env + log capture."""

    def __init__(self, env_file: Path, log_path: Path, port: int, python_exe: str | None = None):
        self.env_file = env_file
        self.log_path = log_path
        self.port = port
        self.python_exe = python_exe or sys.executable
        self.proc: subprocess.Popen | None = None
        self.log_buf: deque[tuple[float, str]] = deque(maxlen=20000)
        self._reader: threading.Thread | None = None
        self._log_fp: io.TextIOWrapper | None = None
        self._started_at: float = 0.0

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fp = open(self.log_path, "w", encoding="utf-8", buffering=1)

        env = os.environ.copy()
        env["DOTENV_PATH"] = str(self.env_file)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        env["GATEWAY_PORT"] = str(self.port)

        cmd = [
            self.python_exe, "-m", "uvicorn",
            "services.voice_gateway.main:app",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--log-level", "info",
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._started_at = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            ts = time.time()
            self.log_buf.append((ts, line.rstrip("\n")))
            if self._log_fp:
                self._log_fp.write(line)

    def lines_between(self, start_ts: float, end_ts: float) -> list[str]:
        return [line for ts, line in self.log_buf if start_ts - 0.05 <= ts <= end_ts + 0.05]

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            if os.name == "nt":
                self.proc.terminate()
            else:
                self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        except Exception:
            pass
        if self._log_fp:
            try:
                self._log_fp.close()
            except Exception:
                pass
        _wait_port_free(self.port)


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------

_AUDIO_MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}

def _post_audio(port: int, audio_path: Path, device_id: str, tts: bool, api_key: str) -> requests.Response:
    mime = _AUDIO_MIME.get(audio_path.suffix.lower(), "audio/wav")
    files = {"file": (audio_path.name, audio_path.read_bytes(), mime)}
    data = {"device_id": device_id, "tts": "true" if tts else "false"}
    headers = {"X-Api-Key": api_key} if api_key else {}
    return requests.post(
        f"http://127.0.0.1:{port}/audio",
        files=files, data=data, headers=headers, timeout=120,
    )


def _post_text(port: int, text: str, device_id: str, tts: bool, api_key: str) -> requests.Response:
    headers = {"X-Api-Key": api_key} if api_key else {}
    return requests.post(
        f"http://127.0.0.1:{port}/text",
        json={"text": text, "device_id": device_id, "tts": tts},
        headers=headers, timeout=120,
    )


# ---------------------------------------------------------------------------
# Expect / Assert engine
# ---------------------------------------------------------------------------

def _match_per_run(per_run: dict, run_name: str) -> dict | None:
    """Pick the most specific fnmatch entry for this run_name, or None."""
    best: tuple[int, dict] | None = None
    for pattern, expect in (per_run or {}).items():
        if fnmatch.fnmatchcase(run_name, pattern):
            specificity = sum(1 for c in pattern if c not in "*?")
            if best is None or specificity > best[0]:
                best = (specificity, expect)
    return best[1] if best else None


def _evaluate(expect: dict, result: dict, transcript: str | None) -> tuple[bool, list[str]]:
    failures: list[str] = []
    actions = result.get("actions_executed", []) or []
    reply = (result.get("reply") or "")
    error = result.get("error")
    fallback = result.get("fallback_used")

    if expect.get("no_error") is True and error:
        failures.append(f"expected no_error, got error={error!r}")
    if "error" in expect and expect["error"] != error:
        failures.append(f"expected error={expect['error']!r}, got {error!r}")
    if "fallback_used" in expect and expect["fallback_used"] != fallback:
        failures.append(f"expected fallback_used={expect['fallback_used']!r}, got {fallback!r}")

    if expect.get("no_actions") is True and actions:
        failures.append(f"expected no actions, got {len(actions)}")
    if "actions_count_min" in expect and len(actions) < expect["actions_count_min"]:
        failures.append(f"expected >= {expect['actions_count_min']} actions, got {len(actions)}")
    if "actions_count_max" in expect and len(actions) > expect["actions_count_max"]:
        failures.append(f"expected <= {expect['actions_count_max']} actions, got {len(actions)}")

    if "action_includes" in expect:
        wanted = expect["action_includes"]
        if isinstance(wanted, dict):
            wanted = [wanted]
        for w in wanted:
            ok = any(
                a.get("entity_id") == w.get("entity_id") and a.get("action") == w.get("action")
                for a in actions
            )
            if not ok:
                failures.append(f"expected action {w} in actions_executed")

    if "actions_executed" in expect:
        ex = expect["actions_executed"]
        got = [{"entity_id": a.get("entity_id"), "action": a.get("action")} for a in actions]
        if sorted(map(json.dumps, ex), key=str) != sorted(map(json.dumps, got), key=str):
            failures.append(f"actions mismatch: expected {ex}, got {got}")

    if "reply_regex" in expect:
        if not re.search(expect["reply_regex"], reply, re.IGNORECASE | re.DOTALL):
            failures.append(f"reply does not match /{expect['reply_regex']}/i ({reply!r})")
    if "reply_min_chars" in expect and len(reply.strip()) < expect["reply_min_chars"]:
        failures.append(f"reply too short: {len(reply.strip())} < {expect['reply_min_chars']}")

    if "transcript_contains" in expect:
        if not transcript or expect["transcript_contains"].lower() not in transcript.lower():
            failures.append(f"transcript missing substring {expect['transcript_contains']!r}")

    return (not failures), failures


# ---------------------------------------------------------------------------
# RAG rebuild (uses the production code path)
# ---------------------------------------------------------------------------

def _rebuild_rag(env: dict[str, str]) -> int:
    """Spawn a tiny child process that imports core.rag.index.build with the run's env.
    We do it as a subprocess so that the env_file is honoured cleanly via DOTENV_PATH.
    """
    cmd = [sys.executable, "-c",
           "import logging, sys; logging.basicConfig(level=logging.INFO);"
           " from core.rag.index import build, status;"
           " n = build(); s = status();"
           " print(f'RAG_BUILD_OK count={n} last={s.get(\"last_indexed\")}')"]
    sub_env = os.environ.copy()
    sub_env.update(env)
    sub_env["PYTHONPATH"] = str(REPO_ROOT)
    sub_env["PYTHONUNBUFFERED"] = "1"
    out = subprocess.run(cmd, cwd=str(REPO_ROOT), env=sub_env,
                         capture_output=True, text=True, timeout=600)
    print(out.stdout)
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        raise RuntimeError(f"RAG rebuild failed (exit {out.returncode})")
    m = re.search(r"RAG_BUILD_OK count=(\d+)", out.stdout)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Run only this matrix entry (fnmatch)")
    ap.add_argument("--case", help="Run only this case id (fnmatch)")
    ap.add_argument("--live", action="store_true", help="Disable HA_DRY_RUN, send real service calls")
    ap.add_argument("--open", action="store_true", help="Open the report in the browser when done")
    ap.add_argument("--rebuild-rag", action="store_true", help="Force RAG rebuild before each RAG run")
    ap.add_argument("--keep", action="store_true", help="Keep _runs/ folder after the test")
    ap.add_argument("--port", type=int, default=None, help="Override gateway port")
    args = ap.parse_args()

    matrix = _load_yaml(THIS_DIR / "matrix.yaml")
    cases = _load_yaml(THIS_DIR / "cases.yaml")
    local_env = _load_env_file(REPO_ROOT / ".env")
    if not local_env:
        print("ERROR: root .env missing. Copy .env.example and fill in values.", file=sys.stderr)
        return 2

    gateway_python = _resolve_gateway_python()
    print(f"Gateway Python: {gateway_python}")

    runs_all = matrix.get("runs", [])
    if args.only:
        runs = [r for r in runs_all if fnmatch.fnmatchcase(r["name"], args.only)]
    else:
        runs = runs_all
    if not runs:
        print("No runs matched.", file=sys.stderr)
        return 2

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = THIS_DIR / "reports" / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = THIS_DIR / "_runs"

    all_results: list[dict] = []

    from tests.e2e.log_parser import extract_steps  # local import after sys.path setup

    for run in runs:
        run_name = run["name"]
        run_dir = runs_dir / run_name
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        env = _build_env(local_env, matrix.get("common_env", {}), run.get("env", {}), run_name)
        if args.live:
            env["HA_DRY_RUN"] = "false"
        if args.port:
            env["GATEWAY_PORT"] = str(args.port)
        env_file = run_dir / ".env"
        _write_env_file(env, env_file)

        port = int(env.get("GATEWAY_PORT", "8799"))
        _wait_port_free(port, timeout=5)

        rag_count = None
        if env.get("RAG_ENABLED", "false").lower() == "true" and (args.rebuild_rag or not Path(env["RAG_DB_PATH"]).exists()):
            print(f"[{run_name}] Rebuilding RAG index ...")
            rag_count = _rebuild_rag(env)
            print(f"[{run_name}] RAG built: {rag_count} entities")

        gateway = GatewaySubprocess(env_file, run_dir / "log.txt", port, python_exe=gateway_python)
        print(f"[{run_name}] starting subprocess on port {port} ...")
        gateway.start()
        try:
            if not _wait_health(port, timeout=30):
                print(f"[{run_name}] /health did not respond within 30s - dumping log")
                print((run_dir / "log.txt").read_text(encoding="utf-8", errors="replace")[:3000])
                continue

            run_lang = (env.get("LANGUAGE") or "de").lower()

            # ---- Single cases ----
            for case in cases.get("single_cases", []):
                if args.case and not fnmatch.fnmatchcase(case["id"], args.case):
                    continue
                if any(fnmatch.fnmatchcase(run_name, p) for p in case.get("skip_runs", [])):
                    continue
                if (case.get("language") or "de").lower() != run_lang:
                    continue
                rec = _execute_case(gateway, run_name, run, case, port, env, report_dir, extract_steps)
                all_results.append(rec)

            # ---- Sequence cases ----
            for seq in cases.get("sequence_cases", []):
                if args.case and not fnmatch.fnmatchcase(seq["id"], args.case):
                    continue
                if any(fnmatch.fnmatchcase(run_name, p) for p in seq.get("skip_runs", [])):
                    continue
                if (seq.get("language") or "de").lower() != run_lang:
                    continue
                base = int(seq.get("chat_id_base", 999000))
                run_hash = abs(hash(run_name)) % 1000
                chat_id = base + run_hash
                for idx, step in enumerate(seq.get("steps", []), start=1):
                    step_case = {
                        **step,
                        "id": f"{seq['id']}#{idx}",
                        "description": f"{seq.get('description', '')} (step {idx})",
                    }
                    rec = _execute_case(
                        gateway, run_name, run, step_case, port, env, report_dir, extract_steps,
                        chat_id_override=chat_id,
                        sequence_id=seq["id"],
                        sequence_step=idx,
                    )
                    all_results.append(rec)
        finally:
            print(f"[{run_name}] stopping subprocess ...")
            gateway.stop()

    # ---- Persist + render ----
    (report_dir / "results.json").write_text(
        json.dumps({
            "timestamp": timestamp,
            "matrix": matrix,
            "results": all_results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    from tests.e2e.report import render_html
    html_path = render_html(report_dir, all_results, matrix, cases)
    print(f"\nReport: {html_path}")

    if not args.keep:
        # _runs/ wird belassen falls --keep, ansonsten loeschen wir die DB-Files (env bleibt)
        for r in runs_dir.glob("*/rag.sqlite"):
            try:
                r.unlink()
            except OSError:
                pass

    if args.open:
        try:
            import webbrowser
            webbrowser.open(html_path.as_uri())
        except Exception:
            pass

    fails = sum(1 for r in all_results if not r.get("expect_pass", True))
    print(f"\nSummary: {len(all_results)} cases, {fails} failed.")
    return 0 if fails == 0 else 1


def _execute_case(
    gateway: GatewaySubprocess,
    run_name: str,
    run: dict,
    case: dict,
    port: int,
    env: dict,
    report_dir: Path,
    extract_steps,
    chat_id_override: int | None = None,
    sequence_id: str | None = None,
    sequence_step: int | None = None,
) -> dict:
    case_id = case["id"]
    print(f"  -> [{run_name}] {case_id}")
    audio = case.get("audio")
    text = case.get("text")
    tts = case.get("tts", True if audio else False)

    api_key = env.get("GATEWAY_API_KEY", "")
    if chat_id_override is not None:
        device_id = str(chat_id_override)
    else:
        device_id = f"e2e-{run_name}-{re.sub(r'[^a-z0-9]+', '-', case_id.lower())}"

    audio_dir = report_dir / "audio" / run_name
    audio_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", case_id)
    audio_out = audio_dir / f"{safe_id}.wav"

    started = time.time()
    err_msg: str | None = None
    response_json: dict | None = None
    audio_path: Path | None = None
    try:
        if audio:
            wav_path = THIS_DIR / "fixtures" / "audio" / audio
            if not wav_path.is_file():
                # try alternative extensions (.m4a, .mp3, .ogg) before failing
                stem = wav_path.stem
                alt = next(
                    (wav_path.with_name(stem + ext) for ext in _AUDIO_MIME
                     if ext != wav_path.suffix.lower()
                     and wav_path.with_name(stem + ext).is_file()),
                    None,
                )
                if alt:
                    wav_path = alt
                else:
                    err_msg = f"missing fixture audio: {wav_path}"
                    resp = None
            if not err_msg:
                resp = _post_audio(port, wav_path, device_id, tts, api_key)
        elif text is not None:
            resp = _post_text(port, text, device_id, tts, api_key)
        else:
            err_msg = "case has neither audio nor text"
            resp = None

        if resp is not None:
            ct = resp.headers.get("content-type", "")
            if ct.startswith("audio/"):
                audio_out.write_bytes(resp.content)
                audio_path = audio_out
                # Reply text is in the captured log; JSON not provided when WAV is returned.
                response_json = {"reply": "<audio reply>", "actions_executed": [], "error": None,
                                  "fallback_used": None, "tts": True}
            else:
                try:
                    response_json = resp.json()
                except Exception:
                    response_json = {"_raw": resp.text}
        else:
            response_json = {}
    except Exception as e:
        err_msg = f"request failed: {e}"
        response_json = {}
    ended = time.time()

    # Background HA execution may continue after the response - give it ~2s to finish so
    # log captures the [HA DRY-RUN] / call_service lines for our parser.
    time.sleep(2.0)
    log_lines = gateway.lines_between(started, time.time())
    steps = extract_steps(log_lines)

    # Reconstruct an effective result dict from log + response (response_json may be sparse
    # when WAV is returned). We treat the parsed LLM output + ha_calls as the source of truth.
    effective = _reconstruct_result(response_json, steps)

    # Evaluate expectations
    expect = dict(case.get("expect") or {})
    per = _match_per_run(case.get("expect_per_run") or {}, run_name)
    if per:
        expect.update(per)
    transcript = effective.get("transcript") or steps.transcript
    passed, failures = (True, []) if not expect else _evaluate(expect, effective, transcript)
    if err_msg:
        passed, failures = False, [err_msg, *failures]

    return {
        "run_name": run_name,
        "run_description": run.get("description", ""),
        "case_id": case_id,
        "case_description": case.get("description", ""),
        "sequence_id": sequence_id,
        "sequence_step": sequence_step,
        "device_id": device_id,
        "audio_input": audio,
        "text_input": text,
        "started_at": started,
        "ended_at": ended,
        "latency_ms": int((ended - started) * 1000),
        "tts_wav_path": audio_path.relative_to(report_dir).as_posix() if audio_path else None,
        "transcript": transcript,
        "intent": steps.intent,
        "rewritten_query": steps.rewritten_query,
        "rag_top": steps.rag_top,
        "rag_pre_filter": steps.rag_pre_filter,
        "rag_post_filter": steps.rag_post_filter,
        "rag_threshold": steps.rag_threshold,
        "llm_path": steps.llm_path,
        "llm_raw": steps.llm_raw,
        "llm_parsed": steps.llm_parsed,
        "smalltalk_reply": steps.smalltalk_reply,
        "ha_calls_log": steps.ha_calls,
        "fallback_used": effective.get("fallback_used"),
        "error": effective.get("error"),
        "actions_executed": effective.get("actions_executed", []),
        "reply": effective.get("reply", ""),
        "expect": expect,
        "expect_pass": passed,
        "expect_failures": failures,
    }


def _reconstruct_result(resp: dict | None, steps) -> dict:
    """Fold log evidence into the response dict.

    When TTS=True, the gateway returns WAV without JSON. We still reconstruct
    a result-like dict from parsed log evidence so asserts work either way.
    """
    out: dict = {
        "transcript": (resp or {}).get("transcript") or steps.transcript or "",
        "reply": (resp or {}).get("reply") or "",
        "error": (resp or {}).get("error"),
        "fallback_used": (resp or {}).get("fallback_used") or steps.fallback_used_log,
        "actions_executed": list((resp or {}).get("actions_executed") or []),
    }
    # If we don't have actions from JSON but the log has [HA DRY-RUN] lines, use those.
    if not out["actions_executed"] and steps.ha_calls:
        out["actions_executed"] = [
            {
                "entity_id": c.get("entity_id"),
                "action": c.get("action"),
                "success": True,
                "status": "ok",
                "dry_run": c.get("dry_run", False),
            }
            for c in steps.ha_calls if c.get("entity_id")
        ]
    # If reply is empty or the audio placeholder, reconstruct from log evidence.
    if not out["reply"] or out["reply"] == "<audio reply>":
        if steps.llm_parsed and isinstance(steps.llm_parsed, dict):
            out["reply"] = (steps.llm_parsed.get("reply") or "").strip()
        if not out["reply"] and steps.smalltalk_reply:
            out["reply"] = steps.smalltalk_reply
    return out


if __name__ == "__main__":
    sys.exit(main())
