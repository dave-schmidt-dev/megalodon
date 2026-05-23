#!/usr/bin/env python3
"""Benchmark small local models on the lane-narrative task — on REAL session data.

For each candidate GGUF under ``--models-dir`` (one model per subdir), this:
  1. launches ``llama-server`` on a local port and waits for ``/health``,
  2. sends a warmup request (excluded from timing),
  3. for each fixture (a real digested agent session) sends the production
     narrator prompt and records {output, latency, word/sentence count},
  4. stops the server and moves to the next model.

Fixtures are built straight from captured Claude session transcripts via the
production digest layer (``megalodon_ui.narrator``), so we score models on the
exact input they'll see in production — not synthetic prompts.

Quality is judged by a human from the side-by-side report this writes; the
machine metrics (latency, format compliance) are the objective first gate.

Usage:
    uv run python scripts/narrator_bench.py \
        --models-dir ~/models/narrator-bench \
        --transcripts '~/.claude/projects/*v94h*/*.jsonl' \
        --out benchmarks/narrator/results.md

Stdlib only (urllib + subprocess) so it runs without extra deps.
"""

from __future__ import annotations

import argparse
import glob
import html as _html
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Allow running as a plain script (add repo root to sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from megalodon_ui.narrator.digest import parse_session, render_for_prompt  # noqa: E402
from megalodon_ui.narrator.prompt import build_messages  # noqa: E402

_LANE_RE = re.compile(r"launch-([A-Z]+)\.md")


@dataclass
class Fixture:
    lane: str
    session_id: str
    messages: list[dict]
    n_events: int
    digest_text: str  # the "blurp" each model worked from (render_for_prompt output)
    full_session: str = ""  # readable full transcript (ground truth, for human judging)


@dataclass
class Result:
    model: str
    lane: str
    output: str
    latency_s: float
    words: int
    sentences: int
    tok_s: float = 0.0  # generation tokens/sec (llama.cpp eval rate)
    completion_tokens: int = 0


@dataclass
class ModelStats:
    """Per-model performance, captured during the run."""

    model: str
    load_s: float = 0.0  # launch → /health ready
    infer_wall_s: float = 0.0  # total wall time for all fixtures (excl. load/warmup)
    avg_tok_s: float = 0.0  # mean generation rate across fixtures
    peak_gpu_util: int = 0  # max "Device Utilization %" sampled during inference
    avg_gpu_util: float = 0.0
    peak_mem_mb: float = 0.0  # peak llama-server RSS (unified memory footprint)


def render_full_session(path: Path, *, max_event_chars: int = 700) -> str:
    """Readable, near-complete transcript for human judging (ground truth).

    Unlike the model's compacted digest (``render_for_prompt``), this shows ALL
    events with generous truncation, so the reader can verify a narrative against
    what actually happened — not just against the lossy model input.
    """
    lines: list[str] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(transcript unavailable)"

    def clip(s: str, n: int = max_event_chars) -> str:
        s = " ".join(str(s).split())
        return s if len(s) <= n else s[: n - 1] + "…"

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if etype == "assistant":
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and b.get("text", "").strip():
                        lines.append(f"assistant: {clip(b['text'])}")
                    elif bt == "thinking":
                        lines.append("assistant [thinking]: …")
                    elif bt == "tool_use":
                        inp = b.get("input") or {}
                        detail = ", ".join(
                            f"{k}={clip(v, 200)}" for k, v in inp.items()
                        )
                        lines.append(
                            f"assistant → {b.get('name', '?')}({clip(detail, 300)})"
                        )
            elif isinstance(content, str) and content.strip():
                lines.append(f"assistant: {clip(content)}")
        elif etype == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        rc = b.get("content")
                        lines.append(
                            f"  result: {clip(rc if isinstance(rc, str) else json.dumps(rc))}"
                        )
            elif isinstance(content, str) and content.strip():
                lines.append(f"user: {clip(content)}")
    return "\n".join(lines) or "(no events)"


def _lane_of(digest) -> str:
    """Infer the lane label from the first prompt mentioning launch-<LANE>.md."""
    for ev in digest.events:
        m = _LANE_RE.search(ev.text)
        if m:
            return m.group(1)
    return digest.session_id[:8]


def build_fixtures(glob_pat: str, max_fixtures: int) -> list[Fixture]:
    """One richest transcript per lane, from real captured sessions."""
    paths = [Path(p) for p in glob.glob(os.path.expanduser(glob_pat))]
    by_lane: dict[str, Fixture] = {}
    for p in paths:
        digest = parse_session(p)
        if len(digest.events) < 3:
            continue  # too little activity to narrate
        lane = _lane_of(digest)
        digest_text = render_for_prompt(digest)
        fx = Fixture(
            lane=lane,
            session_id=digest.session_id,
            messages=build_messages(lane, digest_text),
            n_events=len(digest.events),
            digest_text=digest_text,
            full_session=render_full_session(p),
        )
        # Keep the transcript with the most events per lane.
        if lane not in by_lane or fx.n_events > by_lane[lane].n_events:
            by_lane[lane] = fx
    fixtures = sorted(by_lane.values(), key=lambda f: f.lane)
    return fixtures[:max_fixtures]


def discover_models(models_dir: Path) -> list[tuple[str, Path]]:
    models: list[tuple[str, Path]] = []
    for sub in sorted(models_dir.iterdir()):
        if not sub.is_dir():
            continue
        ggufs = sorted(sub.glob("*.gguf"))
        if ggufs:
            models.append((sub.name, ggufs[0]))
    return models


def _wait_health(port: int, timeout: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _chat(
    port: int, messages: list[dict], *, max_tokens: int = 120
) -> tuple[str, float, float, int]:
    """Return (text, wall_latency_s, gen_tok_s, completion_tokens).

    ``gen_tok_s`` comes from llama.cpp's own ``timings`` block (its measured eval
    rate), falling back to completion_tokens / wall time.
    """
    body = json.dumps(
        {
            "model": "narrator",
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": False,
            "timings_per_token": True,
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    dt = time.monotonic() - t0
    text = data["choices"][0]["message"]["content"].strip()
    timings = data.get("timings") or {}
    usage = data.get("usage") or {}
    ctoks = int(timings.get("predicted_n") or usage.get("completion_tokens") or 0)
    tok_s = float(timings.get("predicted_per_second") or 0.0)
    if not tok_s and ctoks and dt > 0:
        tok_s = ctoks / dt
    return text, dt, tok_s, ctoks


def _count_sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+", text) if s.strip()])


_GPU_UTIL_RE = re.compile(r'"Device Utilization %"=(\d+)')


def _gpu_util_pct() -> int | None:
    """Current Apple-Silicon GPU utilization % via ioreg (no sudo)."""
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = _GPU_UTIL_RE.search(out)
    return int(m.group(1)) if m else None


def _proc_rss_mb(pid: int) -> float:
    """Resident memory (MB) of a process — unified-memory footprint on Apple Silicon."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        return int(out) / 1024.0 if out else 0.0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


class _ResourceSampler(threading.Thread):
    """Background sampler of GPU utilization % and process RSS during a run."""

    def __init__(self, pid: int, interval: float = 0.3) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self._stop = threading.Event()
        self.gpu_samples: list[int] = []
        self.peak_mem_mb: float = 0.0

    def run(self) -> None:
        while not self._stop.is_set():
            u = _gpu_util_pct()
            if u is not None:
                self.gpu_samples.append(u)
            self.peak_mem_mb = max(self.peak_mem_mb, _proc_rss_mb(self.pid))
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2)


def run_model(
    name: str, gguf: Path, fixtures: list[Fixture], port: int, ctx: int
) -> tuple[list[Result], ModelStats]:
    stats = ModelStats(model=name)
    t_launch = time.monotonic()
    proc = subprocess.Popen(
        [
            "llama-server",
            "-m",
            str(gguf),
            "--port",
            str(port),
            "--alias",
            "narrator",
            "-ngl",
            "99",
            "-c",
            str(ctx),
            "--jinja",
            # Disable thinking at the template level. Gemma 4 (and other hybrid
            # models) otherwise burn the token budget on a reasoning trace that
            # never reaches `content` — empty/partial narratives. `--reasoning-
            # budget 0` does NOT suppress it for Gemma's template; this kwarg
            # does. Harmless for non-thinking models (the var is simply unused).
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[Result] = []
    sampler: _ResourceSampler | None = None
    try:
        if not _wait_health(port):
            print(
                f"  !! {name}: server never became healthy; skipping", file=sys.stderr
            )
            return results, stats
        stats.load_s = time.monotonic() - t_launch
        # Warmup (excluded from timing).
        try:
            _chat(port, fixtures[0].messages)
        except Exception:
            pass
        # Sample GPU util + RSS only during the timed inference window.
        sampler = _ResourceSampler(proc.pid)
        sampler.start()
        t_infer = time.monotonic()
        for fx in fixtures:
            try:
                out, dt, tok_s, ctoks = _chat(port, fx.messages)
            except Exception as exc:  # noqa: BLE001
                out, dt, tok_s, ctoks = f"<error: {exc}>", float("nan"), 0.0, 0
            results.append(
                Result(
                    name,
                    fx.lane,
                    out,
                    dt,
                    len(out.split()),
                    _count_sentences(out),
                    tok_s=tok_s,
                    completion_tokens=ctoks,
                )
            )
            print(f"  [{name}] {fx.lane}: {dt:.2f}s {tok_s:.0f}tok/s — {out[:70]}")
        stats.infer_wall_s = time.monotonic() - t_infer
    finally:
        if sampler is not None:
            sampler.stop()
            if sampler.gpu_samples:
                stats.peak_gpu_util = max(sampler.gpu_samples)
                stats.avg_gpu_util = sum(sampler.gpu_samples) / len(sampler.gpu_samples)
            stats.peak_mem_mb = sampler.peak_mem_mb
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    tok_rates = [r.tok_s for r in results if r.tok_s > 0]
    stats.avg_tok_s = sum(tok_rates) / len(tok_rates) if tok_rates else 0.0
    return results, stats


def write_report(
    out_path: Path,
    fixtures: list[Fixture],
    results: list[Result],
    models: list[str],
    stats: dict[str, "ModelStats"],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Narrator model benchmark — lane status summarization\n")
    lines.append(f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}_\n")
    lines.append(
        "Task: compress a real agent's digested session into a 1-line advisory "
        "status. Inputs are real captured transcripts; the prompt is the "
        "production narrator prompt.\n"
    )

    # Per-model performance (measured during the run).
    lines.append("## Performance (per model)\n")
    lines.append(
        "| Model | load | infer wall | tok/s | peak GPU | avg GPU | peak mem | max sent | avg words |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in models:
        rs = [r for r in results if r.model == m and r.latency_s == r.latency_s]
        st = stats.get(m)
        if not rs or st is None:
            lines.append(f"| {m} | (no data) | | | | | | | |")
            continue
        max_sent = max(r.sentences for r in rs)
        avg_words = sum(r.words for r in rs) / len(rs)
        flag = " ⚠" if max_sent > 3 else ""
        lines.append(
            f"| {m} | {st.load_s:.1f}s | {st.infer_wall_s:.2f}s | {st.avg_tok_s:.0f} "
            f"| {st.peak_gpu_util}% | {st.avg_gpu_util:.0f}% | {st.peak_mem_mb:.0f} MB "
            f"| {max_sent}{flag} | {avg_words:.0f} |"
        )
    lines.append("")
    lines.append(
        "_Wall = total time for all fixtures (excl. load + warmup). tok/s = llama.cpp "
        "eval rate. GPU via ioreg Device Utilization %. mem = peak llama-server RSS "
        "(unified memory). Sampled solo (no competing load)._\n"
    )

    # Side-by-side outputs per fixture (for blind quality ranking).
    lines.append("## Narratives per lane (judge fluency + fidelity)\n")
    for fx in fixtures:
        lines.append(
            f"### Lane {fx.lane}  ·  {fx.n_events} events  ·  session {fx.session_id[:8]}\n"
        )
        for m in models:
            r = next((x for x in results if x.model == m and x.lane == fx.lane), None)
            if r:
                lines.append(f"- **{m}** ({r.latency_s:.2f}s): {r.output}")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written: {out_path}")


# Markers of a degraded output: empty, or the model echoed the prompt/event list
# back instead of summarizing it.
_ECHO_MARKERS = ("Recent activity", "ASKED:", "ASKed:", "- SAID:", "- TOOL:", "- ASKED")


def _degraded(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("<error"):
        return True
    if t.startswith("Agent lane:") and "\n" in t:
        return True
    return any(mark in t for mark in _ECHO_MARKERS)


def _model_verdict(rs: list[Result]) -> tuple[str, str]:
    """Return (css_class, label) from the degraded count across a model's outputs."""
    if not rs:
        return "bad", "no data"
    bad = sum(1 for r in rs if _degraded(r.output))
    if bad == 0:
        return "good", "all clean"
    if bad <= 2:
        return "warn", f"{bad}/{len(rs)} degraded"
    return "bad", f"{bad}/{len(rs)} degraded"


def write_html_report(
    out_path: Path,
    fixtures: list[Fixture],
    results: list[Result],
    models: list[str],
    stats: dict[str, "ModelStats"],
) -> None:
    """Render a self-contained dark-mode HTML page (no external assets)."""
    esc = _html.escape
    gen = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    rows = []
    for m in models:
        rs = [r for r in results if r.model == m and r.latency_s == r.latency_s]
        cls, label = _model_verdict([r for r in results if r.model == m])
        st = stats.get(m)
        if rs and st:
            avg_lat = sum(r.latency_s for r in rs) / len(rs)
            max_sent = max(r.sentences for r in rs)
            avg_words = sum(r.words for r in rs) / len(rs)
            cells = (
                f"<td>{avg_lat:.2f}s</td><td>{st.avg_tok_s:.0f}</td>"
                f"<td>{st.peak_gpu_util}%</td><td>{st.peak_mem_mb:.0f} MB</td>"
                f"<td>{max_sent}</td><td>{avg_words:.0f}</td>"
            )
        else:
            cells = "<td>—</td>" * 6
        rows.append(
            f'<tr><td class="mono">{esc(m)}</td>{cells}'
            f'<td><span class="badge {cls}">{esc(label)}</span></td></tr>'
        )
    summary_rows = "\n".join(rows)

    lane_blocks = []
    for fx in fixtures:
        cards = []
        for m in models:
            r = next((x for x in results if x.model == m and x.lane == fx.lane), None)
            if not r:
                continue
            cls = "bad" if _degraded(r.output) else "good"
            out = esc(r.output) if r.output.strip() else "<em>(empty)</em>"
            cards.append(
                f'<div class="card {cls}"><div class="card-h">'
                f'<span class="mono model">{esc(m)}</span>'
                f'<span class="lat">{r.latency_s:.2f}s</span></div>'
                f'<div class="narr">{out}</div></div>'
            )
        lane_blocks.append(
            f'<section class="lane"><h3>{esc(fx.lane)} '
            f'<span class="meta">· {fx.n_events} events · session {esc(fx.session_id[:8])}</span></h3>'
            f'<div class="cards">{"".join(cards)}</div></section>'
        )
    lanes_html = "\n".join(lane_blocks)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Narrator model benchmark</title>
<style>
  :root {{
    --bg:#0b0d10; --panel:#14171c; --border:#262b33; --text:#d7dbe0;
    --muted:#8b939e; --accent:#6ea8fe; --good:#3fb950; --warn:#d29922; --bad:#f85149;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:32px 20px 80px; }}
  h1 {{ font-size:24px; margin:0 0 4px; }}
  h3 {{ font-size:16px; margin:28px 0 12px; border-bottom:1px solid var(--border); padding-bottom:8px; }}
  .sub {{ color:var(--muted); margin:0 0 24px; font-size:13px; }}
  .mono {{ font-family:var(--mono); }}
  .meta {{ color:var(--muted); font-weight:400; font-size:13px; }}
  .callout {{ background:var(--panel); border:1px solid var(--border); border-left:3px solid var(--accent);
    border-radius:8px; padding:14px 16px; margin:0 0 28px; font-size:14px; }}
  .callout b {{ color:var(--accent); }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel);
    border:1px solid var(--border); border-radius:8px; overflow:hidden; }}
  th,td {{ text-align:left; padding:10px 14px; border-bottom:1px solid var(--border); font-size:14px; }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  tr:last-child td {{ border-bottom:none; }}
  .badge {{ font-size:12px; padding:2px 9px; border-radius:20px; font-weight:600; }}
  .badge.good {{ background:rgba(63,185,80,.15); color:var(--good); }}
  .badge.warn {{ background:rgba(210,153,34,.15); color:var(--warn); }}
  .badge.bad {{ background:rgba(248,81,73,.15); color:var(--bad); }}
  .cards {{ display:flex; flex-direction:column; gap:8px; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-left:3px solid var(--border);
    border-radius:8px; padding:10px 14px; }}
  .card.good {{ border-left-color:var(--good); }}
  .card.bad {{ border-left-color:var(--bad); }}
  .card-h {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:5px; }}
  .model {{ font-size:13px; color:var(--accent); }}
  .lat {{ font-size:12px; color:var(--muted); }}
  .narr {{ font-size:14px; white-space:pre-wrap; word-break:break-word; }}
  footer {{ margin-top:40px; color:var(--muted); font-size:13px; border-top:1px solid var(--border); padding-top:16px; }}
</style></head>
<body><div class="wrap">
  <h1>Narrator model benchmark — lane status summarization</h1>
  <p class="sub">Generated {esc(gen)} · {len(models)} models × {len(fixtures)} real captured agent sessions ·
     Apple M5 Max · llama-server GGUF Q4_K_M</p>
  <div class="callout">
    <b>Task:</b> compress a pre-digested agent session into a 1-line advisory status, judged on
    fidelity + format stability (latency is not a differentiator — all sub-second).
    <br><b>Serving note:</b> Gemma 4 is a hybrid thinking model; served with
    <span class="mono">--chat-template-kwargs '{{"enable_thinking":false}}'</span> to suppress the
    reasoning channel (<span class="mono">--reasoning-budget 0</span> alone does not).
  </div>
  <table>
    <thead><tr><th>Model</th><th>Avg latency</th><th>tok/s</th><th>Peak GPU</th><th>Peak mem</th><th>Max sent.</th><th>Avg words</th><th>Verdict</th></tr></thead>
    <tbody>
{summary_rows}
    </tbody>
  </table>
  <h3 style="margin-top:36px">Narratives per lane <span class="meta">— judge fluency + fidelity</span></h3>
{lanes_html}
  <footer>Green = clean narrative · Red = empty or echoed the prompt.
    Source: <span class="mono">scripts/narrator_bench.py</span> on real
    <span class="mono">~/.claude/projects/*/*.jsonl</span> transcripts.</footer>
</div></body></html>
"""
    out_path.write_text(doc, encoding="utf-8")
    print(f"HTML report written: {out_path}")


def write_json(
    out_path: Path,
    fixtures: list[Fixture],
    results: list[Result],
    stats: dict[str, "ModelStats"],
) -> None:
    """Persist structured results so reports can be rebuilt without re-inference."""
    data = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixtures": [
            {
                "lane": f.lane,
                "session_id": f.session_id,
                "n_events": f.n_events,
                "digest_text": f.digest_text,
            }
            for f in fixtures
        ],
        "results": [
            {
                "model": r.model,
                "lane": r.lane,
                "output": r.output,
                "latency_s": r.latency_s,
                "words": r.words,
                "sentences": r.sentences,
                "tok_s": r.tok_s,
                "completion_tokens": r.completion_tokens,
            }
            for r in results
        ],
        "stats": [
            {
                "model": s.model,
                "load_s": s.load_s,
                "infer_wall_s": s.infer_wall_s,
                "avg_tok_s": s.avg_tok_s,
                "peak_gpu_util": s.peak_gpu_util,
                "avg_gpu_util": s.avg_gpu_util,
                "peak_mem_mb": s.peak_mem_mb,
            }
            for s in stats.values()
        ],
    }
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"JSON results written: {out_path}")


def write_blinded_eval_html(
    out_path: Path, fixtures: list[Fixture], results: list[Result], models: list[str]
) -> None:
    """Interactive blinded A/B/C page: pick the best narrative per lane, then reveal.

    Per lane: shows the source digest ("the blurp" each model worked from) and the
    candidate narratives in RANDOMIZED order as Option A/B/C… with model identity
    hidden. The reader picks one per lane; a Reveal button unmasks the models and
    tallies which model the reader preferred. Model→option mapping is embedded but
    only exposed on reveal (honest self-eval).
    """
    lanes_data = []
    for fx in fixtures:
        opts = []
        for m in models:
            r = next((x for x in results if x.model == m and x.lane == fx.lane), None)
            if r is None:
                continue
            opts.append({"model": m, "text": r.output.strip() or "(no output)"})
        random.shuffle(opts)  # blind: order carries no model signal
        lanes_data.append(
            {
                "lane": fx.lane,
                "n_events": fx.n_events,
                "digest": fx.digest_text,
                "full_session": fx.full_session,
                "options": opts,
            }
        )
    data_json = json.dumps(lanes_data)
    gen = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    doc = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Narrator — blinded eval</title>
<style>
  :root {
    --bg:#0b0d10; --panel:#14171c; --border:#262b33; --text:#d7dbe0; --muted:#8b939e;
    --accent:#6ea8fe; --good:#3fb950; --pick:#6ea8fe; --mono:ui-monospace,Menlo,monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  .wrap { max-width:980px; margin:0 auto; padding:28px 20px 140px; }
  h1 { font-size:23px; margin:0 0 6px; }
  .sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
  .lane { margin:0 0 30px; border:1px solid var(--border); border-radius:10px; background:var(--panel); overflow:hidden; }
  .lane h2 { font-size:15px; margin:0; padding:12px 16px; border-bottom:1px solid var(--border);
    background:#10131a; }
  .lane h2 .meta { color:var(--muted); font-weight:400; font-size:12px; }
  .digest { font-family:var(--mono); font-size:12.5px; white-space:pre-wrap; color:#aeb6c0;
    background:#0d1014; border-bottom:1px solid var(--border); padding:12px 16px; max-height:200px; overflow:auto; }
  .digest .lbl { color:var(--muted); text-transform:uppercase; letter-spacing:.05em; font-size:11px; display:block; margin-bottom:6px; }
  .digest-det { border-bottom:1px solid var(--border); }
  .digest-det summary { cursor:pointer; color:var(--muted); font-size:12px; padding:8px 16px; user-select:none; }
  .digest-det summary:hover { color:var(--accent); }
  .digest.mini { max-height:160px; border-bottom:none; color:#7d8590; }
  .opts { padding:12px 16px; display:flex; flex-direction:column; gap:8px; }
  .opt { display:flex; gap:12px; align-items:flex-start; padding:11px 14px; border:1px solid var(--border);
    border-radius:8px; cursor:pointer; transition:border-color .12s, background .12s; }
  .opt:hover { border-color:var(--accent); }
  .opt.picked { border-color:var(--pick); background:rgba(110,168,254,.10); }
  .opt .tag { font-family:var(--mono); font-weight:700; color:var(--accent); min-width:22px; }
  .opt .body { flex:1; }
  .opt .reveal { display:none; font-family:var(--mono); font-size:12px; color:var(--good); margin-top:5px; }
  body.revealed .opt .reveal { display:block; }
  .bar { position:fixed; left:0; right:0; bottom:0; background:#0d1014; border-top:1px solid var(--border);
    padding:12px 20px; display:flex; gap:16px; align-items:center; justify-content:space-between; }
  .bar .status { color:var(--muted); font-size:13px; }
  button { background:var(--accent); color:#06223f; border:0; border-radius:7px; padding:9px 18px;
    font-size:14px; font-weight:650; cursor:pointer; }
  button:disabled { opacity:.4; cursor:not-allowed; }
  #tally { max-width:980px; margin:0 auto 20px; }
  #tally .card { background:var(--panel); border:1px solid var(--border); border-left:3px solid var(--good);
    border-radius:10px; padding:16px; display:none; }
  body.revealed #tally .card { display:block; }
  #tally h3 { margin:0 0 8px; }
  #tally .row { display:flex; justify-content:space-between; padding:3px 0; font-size:14px; }
  #tally .mono { font-family:var(--mono); }
</style></head>
<body><div class="wrap">
  <h1>Narrator — blinded eval</h1>
  <p class="sub">For each lane, read the <b>source activity</b>, then pick the status line you'd want on the dashboard. Model names are hidden until you reveal. __GEN__</p>
  <div id="tally"><div class="card"></div></div>
  <div id="lanes"></div>
</div>
<div class="bar">
  <span class="status" id="status"></span>
  <button id="revealBtn" disabled>Reveal models</button>
</div>
<script>
const DATA = __DATA__;
const picks = {};
const TAGS = ["A","B","C","D","E","F","G"];

function el(t, props, ...kids){ const e=document.createElement(t); Object.assign(e,props||{});
  for(const k of kids){ if(k!=null) e.append(k.nodeType?k:document.createTextNode(k)); } return e; }

function updateStatus(){
  const done=Object.keys(picks).length, total=DATA.length;
  document.getElementById("status").textContent = done + " / " + total + " lanes picked";
  document.getElementById("revealBtn").disabled = done < total;
}

const lanesEl=document.getElementById("lanes");
DATA.forEach((lane, li)=>{
  const sec=el("section",{className:"lane"});
  sec.append(el("h2",{},lane.lane+" ", el("span",{className:"meta"},"· "+lane.n_events+" events")));
  // Primary source = the FULL readable session (ground truth) — judge fidelity against this.
  const full=el("div",{className:"digest"},
    el("span",{className:"lbl"},"Full session — what the agent actually did (ground truth)"),
    lane.full_session);
  sec.append(full);
  // Secondary, collapsible: the compacted digest the models actually received.
  const det=el("details",{className:"digest-det"});
  det.append(el("summary",{},"Show the compacted digest the models received"));
  det.append(el("div",{className:"digest mini"}, lane.digest));
  sec.append(det);
  const opts=el("div",{className:"opts"});
  lane.options.forEach((opt,oi)=>{
    const card=el("div",{className:"opt"});
    card.append(el("span",{className:"tag"},TAGS[oi]));
    const body=el("div",{className:"body"}, el("div",{},opt.text),
      el("div",{className:"reveal"}, "→ "+opt.model));
    card.append(body);
    card.onclick=()=>{
      picks[li]=oi;
      [...opts.children].forEach(c=>c.classList.remove("picked"));
      card.classList.add("picked");
      updateStatus();
    };
    opts.append(card);
  });
  sec.append(opts);
  lanesEl.append(sec);
});
updateStatus();

document.getElementById("revealBtn").onclick=()=>{
  document.body.classList.add("revealed");
  const counts={};
  DATA.forEach((lane,li)=>{ const oi=picks[li]; if(oi==null) return;
    const m=lane.options[oi].model; counts[m]=(counts[m]||0)+1; });
  const sorted=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  const card=document.querySelector("#tally .card");
  while(card.firstChild) card.removeChild(card.firstChild);
  card.append(el("h3",{},"Your preferences"));
  sorted.forEach(([m,n])=>card.append(el("div",{className:"row"},
    el("span",{className:"mono"},m), el("span",{},n+" / "+DATA.length+" lanes"))));
  if(sorted.length){ card.append(el("div",{className:"row",style:"margin-top:8px;color:var(--good);font-weight:650"},
    el("span",{},"Your favorite"), el("span",{className:"mono"},sorted[0][0]))); }
  window.scrollTo({top:0,behavior:"smooth"});
  document.getElementById("revealBtn").disabled=true;
};
</script>
</body></html>
"""
    doc = doc.replace("__DATA__", data_json).replace("__GEN__", f"Generated {gen}.")
    out_path.write_text(doc, encoding="utf-8")
    print(f"Blinded eval written: {out_path}")

    # Persist the option→model key so picks can be decoded either way: the page's
    # own Reveal button, OR the reader relaying letters ("ARCHITECT: B") to the
    # agent, who reads key[lane][1]. TAGS order = A,B,C… = option index.
    key = {ld["lane"]: [o["model"] for o in ld["options"]] for ld in lanes_data}
    key_path = out_path.with_name("blinded_eval_key.json")
    key_path.write_text(json.dumps(key, indent=2), encoding="utf-8")
    print(f"Blinded eval key written: {key_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models-dir", default="~/models/narrator-bench")
    ap.add_argument("--transcripts", default="~/.claude/projects/*v94h*/*.jsonl")
    ap.add_argument("--out", default="benchmarks/narrator/results.md")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--max-fixtures", type=int, default=6)
    args = ap.parse_args(argv)

    models_dir = Path(os.path.expanduser(args.models_dir))
    models = discover_models(models_dir)
    if not models:
        print(f"no models found under {models_dir}", file=sys.stderr)
        return 1
    fixtures = build_fixtures(args.transcripts, args.max_fixtures)
    if not fixtures:
        print(f"no fixtures from {args.transcripts}", file=sys.stderr)
        return 1

    print(f"Models: {[m for m, _ in models]}")
    print(f"Fixtures (lanes): {[f.lane for f in fixtures]}\n")

    all_results: list[Result] = []
    all_stats: dict[str, ModelStats] = {}
    for name, gguf in models:
        print(f"== {name} ({gguf.name}) ==")
        rs, st = run_model(name, gguf, fixtures, args.port, args.ctx)
        all_results.extend(rs)
        all_stats[name] = st

    model_names = [m for m, _ in models]
    out = Path(args.out)
    write_report(out, fixtures, all_results, model_names, all_stats)
    write_html_report(
        out.with_suffix(".html"), fixtures, all_results, model_names, all_stats
    )
    write_json(out.with_suffix(".json"), fixtures, all_results, all_stats)
    write_blinded_eval_html(
        out.with_name("blinded_eval.html"), fixtures, all_results, model_names
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
