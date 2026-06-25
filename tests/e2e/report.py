"""Render the HTML report from the runner's results list.

Self-contained HTML - no JS framework, just vanilla CSS + a tiny script for the
detail-panel toggle and run-filter. Audio replies are referenced by relative
paths so the report folder is portable.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template


_HTML_TPL = Template(r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>homeassistant-local-ai E2E - $timestamp</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }
  header { padding: 18px 24px; background: #1e293b; border-bottom: 1px solid #334155; position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 18px; }
  header .meta { font-size: 13px; color: #94a3b8; margin-top: 4px; }
  .summary { display: flex; gap: 16px; margin-top: 8px; }
  .pill { padding: 4px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; }
  .pill.pass { background: #166534; color: #dcfce7; }
  .pill.fail { background: #991b1b; color: #fee2e2; }
  .pill.runs { background: #1e40af; color: #dbeafe; }
  main { padding: 16px 24px 64px; }
  .filters { margin: 12px 0; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  input, select { background: #1e293b; color: #e5e7eb; border: 1px solid #334155; padding: 6px 10px; border-radius: 6px; }
  table.matrix { border-collapse: collapse; width: 100%; }
  table.matrix th, table.matrix td { border: 1px solid #334155; padding: 6px 8px; vertical-align: top; }
  table.matrix th { background: #1e293b; position: sticky; top: 64px; z-index: 5; font-size: 12px; }
  table.matrix td.case { white-space: nowrap; font-weight: 600; background: #1e293b; }
  table.matrix td.case .desc { font-weight: 400; color: #94a3b8; font-size: 11px; display: block; margin-top: 2px; }
  td.cell { font-size: 12px; cursor: pointer; min-width: 130px; }
  td.cell.pass { background: #064e3b; }
  td.cell.fail { background: #7f1d1d; }
  td.cell.skip { background: #334155; color: #94a3b8; }
  td.cell .label { font-weight: 600; }
  td.cell .actions { color: #cbd5e1; }
  td.cell .reply { color: #e2e8f0; font-style: italic; max-height: 32px; overflow: hidden; text-overflow: ellipsis; }
  details.detail { background: #0b1220; border: 1px solid #1e293b; border-radius: 8px; padding: 0; margin: 16px 0; }
  details.detail > summary { padding: 10px 14px; cursor: pointer; font-weight: 600; font-size: 14px; }
  details.detail > summary.fail { background: #450a0a; }
  details.detail > summary.pass { background: #052e16; }
  .detail-body { padding: 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .detail-body section { background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; padding: 10px; }
  .detail-body h3 { margin: 0 0 6px; font-size: 13px; color: #94a3b8; text-transform: uppercase; }
  pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 12px; color: #cbd5e1; max-height: 320px; overflow: auto; }
  audio { width: 100%; margin-top: 4px; }
  ul.failures { margin: 6px 0 0 18px; color: #fecaca; font-size: 12px; }
  .seq-step { color: #fbbf24; font-size: 11px; }
  .badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 10px; background: #1e293b; color: #94a3b8; margin-right: 4px; }
  .badge.fb { background: #4c1d95; color: #ddd6fe; }
  .badge.dry { background: #064e3b; color: #bbf7d0; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <h1>homeassistant-local-ai - End-to-End-Report</h1>
  <div class="meta">$timestamp - $count cases - $runs runs</div>
  <div class="summary">
    <span class="pill pass">$pass_count passed</span>
    <span class="pill fail">$fail_count failed</span>
    <span class="pill runs">$runs runs x $cases cases</span>
  </div>
</header>
<main>
  <div class="filters">
    <label><input type="checkbox" id="onlyFails"> Nur Fails anzeigen</label>
    <input type="search" id="search" placeholder="Filter case-id / transcript ...">
  </div>

  <h2>Matrix</h2>
  <table class="matrix" id="matrix">
    <thead><tr><th>Case</th>$run_headers</tr></thead>
    <tbody>
      $rows
    </tbody>
  </table>

  <h2>Details</h2>
  $details

</main>
<script>
  const cells = document.querySelectorAll('td.cell');
  cells.forEach(c => c.addEventListener('click', () => {
    const id = c.getAttribute('data-target');
    const target = document.getElementById(id);
    if (!target) return;
    target.open = true;
    target.scrollIntoView({behavior: 'smooth', block: 'center'});
  }));
  const onlyFails = document.getElementById('onlyFails');
  const search = document.getElementById('search');
  function applyFilter() {
    const f = onlyFails.checked;
    const q = search.value.trim().toLowerCase();
    document.querySelectorAll('tbody tr').forEach(tr => {
      let show = true;
      if (f && !tr.querySelector('.cell.fail')) show = false;
      if (q) {
        const hay = tr.innerText.toLowerCase();
        if (!hay.includes(q)) show = false;
      }
      tr.classList.toggle('hidden', !show);
    });
    document.querySelectorAll('details.detail').forEach(d => {
      let show = true;
      if (f && d.dataset.pass === 'true') show = false;
      if (q && !d.innerText.toLowerCase().includes(q)) show = false;
      d.classList.toggle('hidden', !show);
    });
  }
  onlyFails.addEventListener('change', applyFilter);
  search.addEventListener('input', applyFilter);
</script>
</body>
</html>
""")


def render_html(report_dir: Path, results: list[dict], matrix: dict, cases: dict) -> Path:
    runs = [r["name"] for r in matrix.get("runs", [])]
    case_order: list[str] = []
    case_meta: dict[str, dict] = {}
    for c in cases.get("single_cases", []) or []:
        case_order.append(c["id"])
        case_meta[c["id"]] = {"description": c.get("description", "")}
    for seq in cases.get("sequence_cases", []) or []:
        for idx, _ in enumerate(seq.get("steps", []), start=1):
            cid = f"{seq['id']}#{idx}"
            case_order.append(cid)
            case_meta[cid] = {"description": f"{seq.get('description', '')} (step {idx})", "sequence": seq["id"], "step": idx}

    by_run_case: dict[tuple[str, str], dict] = {(r["run_name"], r["case_id"]): r for r in results}

    pass_count = sum(1 for r in results if r.get("expect_pass"))
    fail_count = sum(1 for r in results if not r.get("expect_pass"))

    run_headers = "".join(f'<th title="{html.escape(r.get("description",""))}">{html.escape(r["name"])}</th>'
                          for r in matrix.get("runs", []))

    rows: list[str] = []
    details: list[str] = []

    for cid in case_order:
        meta = case_meta.get(cid, {})
        seq_label = ""
        if meta.get("sequence"):
            seq_label = f"<span class=\"seq-step\">step {meta['step']}</span>"
        cells_html = ""
        for run_name in runs:
            rec = by_run_case.get((run_name, cid))
            if not rec:
                cells_html += '<td class="cell skip">-</td>'
                continue
            cls = "pass" if rec.get("expect_pass") else "fail"
            tid = f"d-{_slug(run_name)}-{_slug(cid)}"
            actions = rec.get("actions_executed") or []
            short_act = ", ".join(_short_action(a) for a in actions[:3]) or "no-action"
            badges = ""
            if rec.get("fallback_used"):
                badges += f'<span class="badge fb">fb:{rec["fallback_used"]}</span>'
            if any(a.get("dry_run") for a in actions):
                badges += '<span class="badge dry">dry</span>'
            cells_html += (
                f'<td class="cell {cls}" data-target="{tid}">'
                f'<div class="label">{badges}{cls.upper()}</div>'
                f'<div class="actions">{html.escape(short_act)}</div>'
                f'<div class="reply">{html.escape((rec.get("reply") or "")[:80])}</div>'
                f'</td>'
            )
            details.append(_render_detail(tid, rec, report_dir))
        rows.append(
            f'<tr><td class="case">{html.escape(cid)} {seq_label}'
            f'<span class="desc">{html.escape(meta.get("description",""))}</span>'
            f'</td>{cells_html}</tr>'
        )

    out_html = _HTML_TPL.substitute(
        timestamp=report_dir.name,
        count=len(results),
        runs=len(runs),
        cases=len(case_order),
        pass_count=pass_count,
        fail_count=fail_count,
        run_headers=run_headers,
        rows="".join(rows),
        details="\n".join(details),
    )
    out_path = report_dir / "results.html"
    out_path.write_text(out_html, encoding="utf-8")
    return out_path


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s)[:60]


def _short_action(a: dict) -> str:
    eid = a.get("entity_id") or "?"
    act = a.get("action") or "?"
    return f"{act}->{eid}"


def _render_detail(tid: str, rec: dict, report_dir: Path) -> str:
    audio_html = ""
    if rec.get("tts_wav_path"):
        audio_html = f'<audio controls src="{html.escape(rec["tts_wav_path"])}"></audio>'

    rag_html = ""
    if rec.get("rag_top"):
        rag_html = "<ul style='margin:0;padding-left:18px'>" + "".join(
            f"<li>{html.escape(eid)} <code>dist={d:.3f}</code></li>"
            for eid, d in rec["rag_top"]
        ) + "</ul>"
        if rec.get("rag_pre_filter") is not None:
            rag_html += f'<small>pre={rec["rag_pre_filter"]} -> post={rec["rag_post_filter"]} (threshold={rec.get("rag_threshold")})</small>'

    failures_html = ""
    if rec.get("expect_failures"):
        failures_html = "<ul class='failures'>" + "".join(f"<li>{html.escape(f)}</li>" for f in rec["expect_failures"]) + "</ul>"

    pass_cls = "pass" if rec.get("expect_pass") else "fail"

    return f"""
<details class="detail" id="{tid}" data-pass="{str(bool(rec.get('expect_pass'))).lower()}">
  <summary class="{pass_cls}">[{html.escape(rec['run_name'])}] {html.escape(rec['case_id'])} - {pass_cls.upper()} ({rec.get('latency_ms','?')}ms){failures_html}</summary>
  <div class="detail-body">
    <section>
      <h3>Input</h3>
      <pre>{html.escape(rec.get('audio_input') or rec.get('text_input') or '')}</pre>
      <h3>Transcript</h3>
      <pre>{html.escape(rec.get('transcript') or '(none)')}</pre>
      <h3>Rewriter</h3>
      <pre>intent={html.escape(str(rec.get('intent')))}\nquery={html.escape(str(rec.get('rewritten_query')))}</pre>
      <h3>RAG Top</h3>
      {rag_html or '<pre>(no rag)</pre>'}
    </section>
    <section>
      <h3>LLM Path</h3>
      <pre>{html.escape(str(rec.get('llm_path')))}</pre>
      <h3>LLM Raw</h3>
      <pre>{html.escape(rec.get('llm_raw') or '')}</pre>
      <h3>LLM Parsed</h3>
      <pre>{html.escape(json.dumps(rec.get('llm_parsed'), indent=2, ensure_ascii=False) if rec.get('llm_parsed') else '')}</pre>
      <h3>Smalltalk Reply</h3>
      <pre>{html.escape(rec.get('smalltalk_reply') or '')}</pre>
    </section>
    <section>
      <h3>HA Calls (log evidence)</h3>
      <pre>{html.escape(json.dumps(rec.get('ha_calls_log') or [], indent=2, ensure_ascii=False))}</pre>
      <h3>Actions Executed</h3>
      <pre>{html.escape(json.dumps(rec.get('actions_executed') or [], indent=2, ensure_ascii=False))}</pre>
      <h3>Final Reply</h3>
      <pre>{html.escape(rec.get('reply') or '')}</pre>
      {audio_html}
    </section>
    <section>
      <h3>Expect</h3>
      <pre>{html.escape(json.dumps(rec.get('expect') or {}, indent=2, ensure_ascii=False))}</pre>
      <h3>Error / Fallback</h3>
      <pre>error={html.escape(str(rec.get('error')))}\nfallback={html.escape(str(rec.get('fallback_used')))}</pre>
    </section>
  </div>
</details>
"""
