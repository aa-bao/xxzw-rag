"""HTML 报告渲染：内建通用模板（晨报头版风格，自包含 base64 关键帧）。

移植自 quick-watch 的 render_report.py + render_templates/general.py +
_shared.py（首版只移植通用模板；专家模板后续迭代）。
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

# ── 类型别名 ──
Section = tuple[str, str, str, str, ...]

_SEGMENT_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$")
_CAPTION_MATCH_TOLERANCE = 1.5  # seconds


# ── 工具 ──

def ts_to_seconds(ts: str) -> float:
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return float(parts[0])


def fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def parse_transcript(text: str) -> list[dict]:
    segments: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _SEGMENT_RE.match(line)
        if match:
            ts = match.group(1)
            body = match.group(2).strip()
            segments.append({"ts": ts, "seconds": ts_to_seconds(ts), "text": body})
        elif segments:
            segments[-1]["text"] += " " + line.strip()
        else:
            segments.append({"ts": "", "seconds": -1.0, "text": line.strip()})
    return segments


def embed_image(path: str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    mime = "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def match_caption(ts: float, captions_by_sec: dict[float, str]) -> str:
    if not captions_by_sec:
        return ""
    best, best_diff = "", _CAPTION_MATCH_TOLERANCE
    for sec, text in captions_by_sec.items():
        diff = abs(sec - ts)
        if diff <= best_diff:
            best_diff, best = diff, text
    return best


# ── HTML 构件 ──

def build_abstract(summary: dict) -> str:
    text = str(summary.get("summary") or "")
    if not text.strip():
        return ""
    return f'<section class="abstract"><p>{html.escape(text)}</p></section>'


def build_keypoints(summary: dict) -> Section | None:
    items = summary.get("keypoints") or []
    if not items:
        return None
    lis = "".join(f"<li>{html.escape(str(k))}</li>" for k in items)
    return ("关键要点", "Key Points", "keypoints", f'<ol class="keypoints">{lis}</ol>')


def build_visual(summary: dict) -> Section | None:
    notes = summary.get("visual_notes") or []
    if not notes:
        return None
    lis = "".join(f"<li>{html.escape(str(n))}</li>" for n in notes)
    return ("画面洞察", "Visual Insight", "visual", f'<ul class="bullets">{lis}</ul>')


def section_block(
    num: str, zh: str, en: str, sid: str, inner: str, outer: str = ""
) -> str:
    if outer:
        return f'<section class="{outer}" id="{sid}">{inner}</section>'
    return (
        f'<section class="section" id="{sid}">'
        f'<div class="sec-head"><span class="sec-num">{num}</span>'
        f'<span class="sec-title">{html.escape(zh)}</span>'
        f'<span class="sec-en">{html.escape(en)}</span></div>'
        f"{inner}</section>"
    )


def _build_newspaper_keyframes(
    keyframes: list[dict], captions_by_sec: dict[float, str]
) -> Section | None:
    if not keyframes:
        return None
    cards = []
    for f in keyframes:
        src = embed_image(str(f.get("path") or ""))
        if not src:
            continue
        ts = float(f.get("timestamp_seconds") or 0)
        cap = match_caption(ts, captions_by_sec)
        cap_html = f'<div class="kf-cap">{html.escape(cap)}</div>' if cap else ""
        cards.append(
            f'<figure class="kf"><div class="kf-img">'
            f'<img src="{src}" alt="关键帧 {fmt_time(ts)}" />'
            f'<span class="kf-ts">{fmt_time(ts)}</span></div>{cap_html}</figure>'
        )
    if not cards:
        return None
    grid = '<div class="kf-grid">' + "".join(cards) + "</div>"
    head = (
        '<div class="sec-head"><span class="sec-num">01</span>'
        '<span class="sec-title">关键帧</span>'
        '<span class="sec-en">Key Frames</span></div>'
    )
    return ("关键帧", "Key Frames", "keyframes", head + grid, "kf-section")


def _build_appendix_transcript(segments: list[dict]) -> Section | None:
    if not segments:
        return None
    rows = []
    for seg in segments:
        ts = html.escape(seg["ts"]) if seg["ts"] else "--:--"
        rows.append(
            f'<div class="seg"><span class="seg-ts">{ts}</span>'
            f'<span class="seg-text">{html.escape(seg["text"])}</span></div>'
        )
    head = (
        '<div class="sec-head"><span class="sec-num">04</span>'
        '<span class="sec-title">完整转写</span>'
        '<span class="sec-en">Transcript</span></div>'
    )
    return (
        "完整转写",
        "Transcript",
        "transcript",
        head + f'<div class="seg-list">{"".join(rows)}</div>',
        "appendix",
    )


def assemble_sections(
    summary: dict,
    report: dict,
    keyframes: list[dict],
    segments: list[dict],
    captions_by_sec: dict[float, str],
) -> list[Section]:
    """晨报头版章节：照片墙 → 要点 → 画面洞察 → 附录转写。"""
    sections: list[Section] = []
    kf = _build_newspaper_keyframes(keyframes, captions_by_sec)
    if kf is not None:
        sections.append(kf)
    kp = build_keypoints(summary)
    if kp is not None:
        sections.append(kp)
    vi_ = build_visual(summary)
    if vi_ is not None:
        sections.append(vi_)
    tr = _build_appendix_transcript(segments)
    if tr is not None:
        sections.append(tr)
    return sections


def build_meta(report: dict, fmt) -> str:
    """报眉日期行：左=来源·时长，右=生成时间。"""
    left: list[str] = []
    src = report.get("source")
    if src:
        left.append(fmt(src))
    dur = report.get("duration_seconds")
    if dur not in (None, "unavailable"):
        left.append(fmt_time(float(dur)))
    right: list[str] = []
    gen = report.get("generated_at")
    if gen:
        right.append(fmt(gen))
    rid = report.get("report_id")
    if rid:
        right.append(fmt(rid))
    left_html = " · ".join(left)
    right_html = " · ".join(right)
    return (
        '<div class="dateline"><span class="dt-left">'
        f"{left_html}</span><span class=\"dt-right\">{right_html}</span></div>"
    )


GENERAL_CSS = r"""
/* ── 重置 ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ── 主题:报纸白 / 墨黑 / 油墨红 ── */
:root {
  --paper:   #ffffff;
  --ink:     #111111;
  --ink-2:   #262626;
  --muted:   #3a3a36;
  --rule:    #111111;
  --rule-2:  #b9b7ae;
  --accent:  #b02a2a;
  --mono:  'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace;
  --serif: 'Songti SC', 'Noto Serif SC', 'STSong', SimSun, Georgia, 'Times New Roman', serif;
  --sans:  'Noto Sans SC', -apple-system, 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
}
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--serif); font-size: 15px; line-height: 1.85;
  color: var(--ink); background: var(--paper); padding: 26px 0 56px;
}
.doc { max-width: 780px; margin: 0 auto; padding: 0 22px; background: var(--paper); }
.cover { text-align: center; padding: 0 0 6px; }
.masthead { border-top: 3px solid var(--ink); border-bottom: 2px solid var(--ink); padding-bottom: 12px; }
.dateline {
  display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  padding: 9px 2px 0; font-family: var(--mono); font-size: 11px; letter-spacing: .05em; color: var(--ink-2);
}
.dateline .dt-right { margin-left: auto; color: var(--muted); }
.banner {
  display: flex; align-items: center; gap: 14px;
  font-family: var(--serif); font-weight: 900;
  font-size: 38px; line-height: 1.15; color: var(--ink);
  letter-spacing: .12em; text-transform: uppercase; padding: 12px 0 2px;
}
.banner::before, .banner::after { content: ''; flex: 1 1 0; min-width: 0; height: 1px; background: var(--ink); }
.banner .b-red { color: var(--accent); }
.tagline {
  font-family: var(--serif); font-weight: 700;
  font-size: 12px; letter-spacing: .4em; text-indent: .4em;
  color: var(--ink-2); padding: 3px 0 2px;
}
.cover h1 {
  position: relative;
  font-family: var(--serif); font-weight: 900;
  font-size: 25px; line-height: 1.5; color: var(--ink);
  letter-spacing: .01em; margin: 22px 0 0; padding: 0 0 18px;
  border-bottom: 2px solid var(--ink);
}
.cover h1::after {
  content: ''; position: absolute; left: 50%; bottom: -2px; transform: translateX(-50%);
  width: 96px; height: 4px; background: var(--accent);
}
.lead {
  margin: 20px 0 4px; border: 1px solid var(--ink); border-top-width: 3px;
  padding: 13px 16px 12px;
}
.lead-head {
  display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  border-bottom: 1px solid var(--rule-2); padding-bottom: 8px; margin-bottom: 12px;
}
.lead-rubric {
  font-family: var(--serif); font-weight: 900; font-size: 15px;
  letter-spacing: .32em; color: var(--accent);
}
.lead-en {
  font-family: var(--mono); font-size: 9.5px; letter-spacing: .14em;
  color: var(--ink-2); text-transform: uppercase;
}
.lead .abstract { margin: 0; padding: 0; text-align: left; }
.lead .abstract p { font-size: 14.5px; line-height: 1.95; text-align: justify; }
.outline {
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 18px;
  padding: 11px 1px; margin: 18px 0 2px;
  border-top: 1px solid var(--ink); border-bottom: 1px solid var(--ink);
  font-family: var(--mono); font-size: 12px;
}
.outline a { color: var(--ink); text-decoration: none; }
.section { margin-top: 42px; }
.sec-head {
  display: flex; align-items: baseline; gap: 12px;
  border-bottom: 2px solid var(--ink); padding-bottom: 9px; margin-bottom: 16px;
}
.sec-num {
  font-family: var(--mono); font-weight: 700; font-size: 13px;
  color: var(--accent); letter-spacing: .04em; flex: 0 0 auto;
}
.sec-title { font-family: var(--serif); font-weight: 900; font-size: 20px; color: var(--ink); letter-spacing: .02em; }
.sec-en { margin-left: auto; font-family: var(--mono); font-size: 10px; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.kf-section { margin-top: 42px; }
.kf-section .sec-head { margin-bottom: 18px; }
.kf-grid {
  display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 14px; border-top: 1px solid var(--ink); padding-top: 16px;
}
.kf { background: var(--paper); border: 1px solid var(--ink); overflow: hidden; }
.kf-img { position: relative; aspect-ratio: 16 / 10; background: #e6e4de; border-bottom: 1px solid var(--ink); overflow: hidden; }
.kf-img img { width: 100%; height: 100%; object-fit: cover; display: block; }
.kf-ts {
  position: absolute; left: 0; top: 0;
  font-family: var(--mono); font-size: 11px; font-weight: 600;
  color: #fff; background: var(--accent); padding: 3px 8px;
}
.kf-cap { font-size: 11.5px; line-height: 1.6; color: var(--ink-2); padding: 7px 9px 8px; }
.keypoints { list-style: none; counter-reset: kp; }
.keypoints li {
  counter-increment: kp; position: relative;
  padding: 12px 0 12px 46px; border-bottom: 1px solid var(--rule-2);
  font-size: 14.5px; line-height: 1.8; color: var(--ink-2);
}
.keypoints li:last-child { border-bottom: none; }
.keypoints li::before {
  content: counter(kp, decimal-leading-zero);
  position: absolute; left: 0; top: 13px;
  width: 30px; height: 30px; line-height: 30px; text-align: center;
  font-family: var(--mono); font-weight: 700; font-size: 13px;
  color: #fff; background: var(--ink); border-radius: 0;
}
.bullets { list-style: none; }
.bullets li {
  position: relative; padding: 10px 0 10px 24px;
  border-bottom: 1px solid var(--rule-2);
  font-size: 14.5px; line-height: 1.8; color: var(--ink-2);
}
.bullets li:last-child { border-bottom: none; }
.bullets li::before {
  content: ''; position: absolute; left: 2px; top: 18px;
  width: 7px; height: 7px; background: var(--accent); border-radius: 0;
}
.appendix { margin-top: 42px; }
.appendix .sec-head { border-bottom: 1px solid var(--rule-2); padding-bottom: 8px; margin-bottom: 12px; }
.appendix .sec-title { font-size: 17px; }
.appendix .sec-en { font-size: 9px; }
.appendix .seg-list { font-size: 13px; }
.appendix .seg { display: flex; align-items: flex-start; gap: 0; padding: 8px 0; border-bottom: 1px dotted var(--rule-2); }
.appendix .seg:last-child { border-bottom: none; }
.appendix .seg-ts {
  flex: 0 0 52px; font-family: var(--mono); font-size: 11px; font-weight: 700;
  letter-spacing: .06em; font-variant-numeric: tabular-nums;
  color: var(--accent); padding: 1px 10px 0 0; border-right: 1px solid var(--rule-2);
}
.appendix .seg-text { flex: 1; padding-left: 12px; font-size: 13px; line-height: 1.7; color: var(--ink-2); }
.endmark {
  display: flex; align-items: center; gap: 16px; margin-top: 46px;
  font-family: var(--serif); font-weight: 700; font-size: 12px; letter-spacing: .4em; color: var(--ink-2);
}
.endmark::before, .endmark::after { content: ''; flex: 1; height: 1px; background: var(--rule-2); }
.footer {
  margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--ink);
  font-family: var(--mono); font-size: 10px; letter-spacing: .04em;
  color: var(--muted); text-align: justify; text-align-last: justify;
}
@media (min-width: 780px) {
  body { padding: 40px 0 72px; }
  .doc { padding: 0 40px; }
  .banner { font-size: 64px; }
  .cover h1 { font-size: 32px; }
  .lead { padding: 16px 20px 14px; }
  .kf-grid { grid-template-columns: repeat(4, 1fr); gap: 16px; }
  .kf-cap { font-size: 12px; }
  .keypoints, .bullets, .appendix .seg-list { column-count: 2; column-gap: 40px; column-rule: 1px solid var(--rule-2); }
  .keypoints li, .bullets li, .appendix .seg { break-inside: avoid; }
}
@media print {
  body { background: #fff; padding: 0; }
  .doc { max-width: none; padding: 0; }
}
"""

PAGE = (
    '<!doctype html>\n'
    '<html lang="zh-CN">\n'
    '<head>\n'
    '<meta charset="utf-8" />\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
    '<title>__TITLE__ · 视频解析报告</title>\n'
    '<style>\n'
    + GENERAL_CSS +
    '</style>\n'
    '</head>\n'
    '<body>\n'
    '<main class="doc">\n'
    '  <header class="cover">\n'
    '    <div class="masthead">\n'
    '      <div class="banner">QUICK<b class="b-red">WATCH</b></div>\n'
    '      __DATELINE__\n'
    '      <div class="tagline">视频解析</div>\n'
    '    </div>\n'
    '    <h1>__TITLE__</h1>\n'
    '  </header>\n'
    '  <div class="lead">\n'
    '    <div class="lead-head"><span class="lead-rubric">内容简介</span>'
    '<span class="lead-en">摘要</span></div>\n'
    '    __ABSTRACT__\n'
    '  </div>\n'
    '  <nav class="outline">__NAV__</nav>\n'
    '  __BODY__\n'
    '  <div class="endmark"><span>完</span></div>\n'
    '  <footer class="footer"><span>__FOOTER__</span>'
    '<span>__REPORT_ID__</span></footer>\n'
    '</main>\n'
    '</body>\n'
    '</html>\n'
)


def render_report_html(
    *,
    output_path: Path,
    report: dict,
    transcript: str,
    keyframes: list[dict],
    summary: dict,
) -> Path:
    """渲染自包含 HTML 报告并写入 output_path。"""
    segments = parse_transcript(transcript)
    captions_by_sec: dict[float, str] = {}
    captions_raw = summary.get("keyframe_captions")
    if isinstance(captions_raw, dict):
        for key, val in captions_raw.items():
            text = str(val).strip()
            if not text:
                continue
            try:
                captions_by_sec[ts_to_seconds(str(key))] = text
            except (ValueError, IndexError):
                continue

    title = str(summary.get("title") or report.get("title") or "视频解析报告")
    status = str(report.get("status") or "complete")
    source = str(report.get("source") or "")
    generated_at = str(report.get("generated_at") or "")
    report_id = str(report.get("report_id") or "")

    meta_rows: list[tuple[str, str]] = []
    if source:
        meta_rows.append(("来源", source))
    dur = report.get("duration_seconds")
    if dur not in (None, "unavailable"):
        meta_rows.append(("时长", fmt_time(float(dur))))
    if generated_at:
        meta_rows.append(("生成时间", generated_at))
    meta_rows.append(("解析状态", status))
    meta_html = '<div class="meta">' + "".join(
        f'<div class="meta-row"><span class="meta-key">{html.escape(k)}</span>'
        f'<span class="meta-val{" accent" if k == "解析状态" else ""}">{html.escape(str(v))}</span></div>'
        for k, v in meta_rows
    ) + "</div>"
    meta_css = (
        ".meta { display:grid; grid-template-columns:1fr 1fr; gap:0 32px; "
        "background:#f6f4ef; border:1px solid #b9b7ae; padding:14px 20px; margin-top:18px; text-align:left; }"
        ".meta-row { display:flex; align-items:baseline; gap:8px; padding:5px 0; border-bottom:1px dotted #b9b7ae; font-size:13px; }"
        ".meta-row:last-child { border-bottom:none; }"
        ".meta-key { color:#3a3a36; font-weight:500; white-space:nowrap; }"
        ".meta-val { margin-left:auto; color:#111; font-weight:500; font-family:var(--mono); font-size:12.5px; }"
        ".meta-val.accent { color:var(--accent); }"
    )

    abstract_html = build_abstract(summary)
    section_blocks = assemble_sections(summary, report, keyframes, segments, captions_by_sec)
    body_parts: list[str] = []
    nav_items: list[str] = []
    for i, sec in enumerate(section_blocks, start=1):
        num = f"{i:02d}"
        zh, en, sid, inner, *rest = sec
        outer = rest[0] if rest else ""
        body_parts.append(section_block(num, zh, en, sid, inner, outer))
        nav_items.append(f'<a href="#{sid}">{num} {html.escape(zh)}</a>')
    body_html = "".join(body_parts)
    nav_html = "".join(nav_items) if nav_items else ""

    footer_parts: list[str] = []
    if dur not in (None, "unavailable"):
        footer_parts.append(f"时长 {fmt_time(float(dur))}")
    cost = report.get("cost") or {}
    if cost.get("estimated_asr_cny") is not None:
        footer_parts.append(f"ASR ¥{cost['estimated_asr_cny']}")
    usage = report.get("usage") or {}
    if usage.get("transcript_characters") is not None:
        footer_parts.append(f"{usage['transcript_characters']} 字")
    timings = report.get("timings_seconds") or {}
    if timings.get("total") is not None:
        footer_parts.append(f"{timings['total']}s")
    footer_html = "由 rag-service 视频解析 agent 生成" + (
        " · " + " · ".join(footer_parts) if footer_parts else ""
    )
    dateline_html = build_meta(report, fmt=lambda v: html.escape(str(v)))

    page = (
        PAGE.replace("__TITLE__", html.escape(title))
        .replace("__META__", meta_html)
        .replace("__DATELINE__", dateline_html)
        .replace("__ABSTRACT__", abstract_html)
        .replace("__NAV__", nav_html)
        .replace("__BODY__", body_html)
        .replace("__REPORT_ID__", html.escape(report_id))
        .replace("__FOOTER__", html.escape(footer_html))
    )
    # 注入 meta 样式（通用模板无 __META__ 占位，追加到 <style>）
    page = page.replace("</style>", meta_css + "</style>", 1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return output_path
