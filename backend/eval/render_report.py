"""Рендерит backend/eval/last_report.json в самодостаточный HTML (без внешних
CDN/сетевых запросов — открывается локально в браузере). Вызывается из
run_eval.py автоматически; можно перерендерить отдельно:
    uv run python backend/eval/render_report.py
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "last_report.json"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "last_report.html"

# см. skill dataviz: status-палитра (фиксированная, не темизируется) и
# sequential-градиент blue 100->700 для непрерывной величины (GEval score)
_STATUS_GOOD = "#0ca30c"
_STATUS_CRITICAL = "#d03b3b"
_SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _seq_color(value: float) -> str:
    """value в [0,1] -> шаг sequential-градиента (blue, light->dark)."""
    idx = min(len(_SEQ_BLUE) - 1, max(0, round(value * (len(_SEQ_BLUE) - 1))))
    return _SEQ_BLUE[idx]


def _esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""))


def render(report: dict, output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    coverage = report.get("coverage", {})
    geval = report.get("geval", [])
    hypotheses = report.get("hypotheses", [])

    ratio = coverage.get("coverage_ratio", 0) or 0
    covered = coverage.get("covered", 0)
    total = coverage.get("total", 0)
    stat_color = _STATUS_GOOD if ratio >= 0.5 else _STATUS_CRITICAL

    coverage_rows = "\n".join(
        f"""<li class="cov-item">
            <span class="badge" style="background:{_STATUS_GOOD if d['covered'] else _STATUS_CRITICAL}">
                {"&check;" if d["covered"] else "&times;"}
            </span>
            <div>
                <div class="cov-expert">{_esc(d["expert_hypothesis"])}</div>
                {f'<div class="cov-match">&rarr; {_esc(d["matched_statement"])}</div>' if d["covered"] else ""}
                <div class="cov-reason">{_esc(d["reason"])}</div>
            </div>
        </li>"""
        for d in coverage.get("details", [])
    )

    geval_rows = "\n".join(
        f"""<li class="bar-item">
            <div class="bar-label">{_esc(r["statement"][:90])}</div>
            <div class="bar-track">
                <div class="bar-fill" style="width:{r["geval_score"] * 100:.0f}%;background:{_seq_color(r["geval_score"])}"></div>
                <span class="bar-value">{r["geval_score"]:.2f}</span>
            </div>
            <div class="bar-reason">{_esc(r["geval_reason"])}</div>
        </li>"""
        for r in geval
    )

    ranker_rows = "\n".join(
        f"""<tr>
            <td>{_esc(h["statement"][:80])}</td>
            <td class="num">{_esc(h.get("novelty"))}</td>
            <td class="num">{_esc(h.get("feasibility"))}</td>
            <td class="num">{_esc(h.get("impact"))}</td>
            <td class="num">{_esc(h.get("risk"))}</td>
            <td class="num score">{_esc(h.get("score"))}</td>
        </tr>"""
        for h in hypotheses
    )

    html_doc = f"""<meta charset="utf-8">
<title>Фабрика гипотез — отчёт eval</title>
<style>
  :root {{
    --surface-1: #fcfcfb; --page: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --grid: #e1e0d9;
    --border: rgba(11,11,11,0.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
      --border: rgba(255,255,255,0.10);
    }}
  }}
  :root[data-theme="dark"] {{
    --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff;
    --text-secondary: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
    --border: rgba(255,255,255,0.10);
  }}
  :root[data-theme="light"] {{
    --surface-1: #fcfcfb; --page: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --grid: #e1e0d9;
    --border: rgba(11,11,11,0.10);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page); color: var(--text-primary);
    margin: 0; padding: 32px 16px;
  }}
  .wrap {{ max-width: 860px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 28px; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px 24px; margin-bottom: 20px;
  }}
  .card h2 {{ font-size: 15px; margin: 0 0 14px; color: var(--text-secondary); }}
  .stat-row {{ display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }}
  .stat-value {{ font-size: 40px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .stat-sub {{ color: var(--text-secondary); font-size: 14px; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  .cov-item {{ display: flex; gap: 12px; padding: 10px 0; border-top: 1px solid var(--grid); }}
  .cov-item:first-child {{ border-top: none; }}
  .badge {{
    flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%; color: #fff;
    display: flex; align-items: center; justify-content: center; font-size: 14px;
    margin-top: 2px;
  }}
  .cov-expert {{ font-weight: 600; font-size: 14px; }}
  .cov-match {{ color: var(--text-secondary); font-size: 13px; margin-top: 2px; }}
  .cov-reason {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
  .bar-item {{ padding: 10px 0; border-top: 1px solid var(--grid); }}
  .bar-item:first-child {{ border-top: none; }}
  .bar-label {{ font-size: 13px; margin-bottom: 6px; }}
  .bar-track {{
    position: relative; height: 18px; background: var(--grid); border-radius: 4px;
    overflow: hidden;
  }}
  .bar-fill {{ height: 100%; border-radius: 4px; }}
  .bar-value {{
    position: absolute; right: 8px; top: 0; font-size: 11px; line-height: 18px;
    font-variant-numeric: tabular-nums; color: var(--text-primary); mix-blend-mode: difference;
  }}
  .bar-reason {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 6px; border-top: 1px solid var(--grid); }}
  th {{ color: var(--muted); font-weight: 500; font-size: 12px; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.score {{ font-weight: 600; }}
</style>
<div class="wrap">
  <h1>Фабрика гипотез — отчёт оценки качества</h1>
  <div class="meta">
    {_esc(report.get("generated_at", ""))} &middot;
    LLM: {_esc(report.get("llm_provider", "?"))} &middot;
    Эмбеддинги: {_esc(report.get("embedding_provider", "?"))} &middot;
    Excel: {_esc(report.get("excel_file", "?"))} &middot;
    Сгенерировано гипотез: {_esc(report.get("n_generated", "?"))}
  </div>

  <div class="card">
    <h2>Coverage vs эксперты (held-out Пример 4, ТОФ)</h2>
    <div class="stat-row">
      <div class="stat-value" style="color:{stat_color}">{covered}/{total}</div>
      <div class="stat-sub">экспертных гипотез покрыто ({ratio * 100:.0f}%)</div>
    </div>
    <ul>{coverage_rows}</ul>
  </div>

  <div class="card">
    <h2>GEval: конкретность и проверяемость гипотез (deepeval)</h2>
    <ul>{geval_rows}</ul>
  </div>

  <div class="card">
    <h2>Ranker: новизна / реализуемость / эффект / риск</h2>
    <table>
      <thead><tr><th>Гипотеза</th><th class="num">Новизна</th><th class="num">Реализуемость</th><th class="num">Эффект</th><th class="num">Риск</th><th class="num">Score</th></tr></thead>
      <tbody>{ranker_rows}</tbody>
    </table>
  </div>
</div>
"""
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT_PATH
    report_data = json.loads(path.read_text(encoding="utf-8"))
    out = render(report_data)
    print(f"Отрендерено: {out}")
