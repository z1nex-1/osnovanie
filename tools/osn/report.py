"""Один документ по брифу заказчика: резюме и части в порядке брифа, проверка покрытия требований, HTML и PDF для печати."""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import checks, claims as claims_mod
from .deck import Deck
from .store import OsnError, Project, atomic_write, load_toml, repo_root

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def profiles() -> list[str]:
    return sorted(p.stem.removeprefix("brief-") for p in (repo_root() / "config").glob("brief-*.toml"))


def profile(name: str) -> dict:
    if name not in profiles():
        raise OsnError(f"Нет чек-листа брифа {name}. Есть: {', '.join(profiles()) or 'нет'}.")
    return load_toml(f"brief-{name}.toml")


def set_profile(project: Project, name: str) -> dict:
    spec = profile(name)
    meta = project.meta()
    meta["report_profile"] = name
    project.save_meta(meta)
    project.event("report_profile", profile=name)
    return {"profile": name, "title": spec["title"], "file": spec["file"], "parts": len(spec["part"])}


def active(project: Project) -> dict | None:
    name = project.meta().get("report_profile")
    return profile(name) if name else None


def _sections(text: str) -> list[tuple[int, str, int, int]]:
    """Разделы второго уровня: (позиция, заголовок, начало тела, конец тела)."""
    found = [(m.start(), m.group(2), m.end()) for m in HEADING.finditer(text) if len(m.group(1)) == 2]
    return [(start, title, body, found[i + 1][0] if i + 1 < len(found) else len(text)) for i, (start, title, body) in enumerate(found)]


def _locate(spec: dict, text: str) -> tuple[dict | None, list[dict | None]]:
    sections = _sections(text)
    parts: list[dict | None] = []
    cursor = 0
    for part in spec["part"]:
        hit = next((s for s in sections if s[0] >= cursor and re.search(part["heading"], s[1], re.I)), None)
        if hit:
            cursor = hit[3]
        parts.append({"title": hit[1], "text": text[hit[2]:hit[3]], "at": hit[0]} if hit else None)
    first = next((p["at"] for p in parts if p), len(text))
    summary = next(({"title": s[1], "text": text[s[2]:s[3]], "at": s[0]} for s in sections if s[0] < first and re.search(spec["summary"]["heading"], s[1], re.I)), None)
    return summary, parts


def _table_problems(name: str, text: str) -> list[str]:
    problems = []
    blocks = checks.paragraphs(text)
    for i, block in enumerate(blocks):
        if not block.startswith("|"):
            continue
        caption = blocks[i - 1] if i and not blocks[i - 1].startswith("#") else ""
        if claims_mod.refs_in(caption):
            continue
        for row in block.splitlines()[2:]:
            if checks.NUM.search(row) and not claims_mod.refs_in(row):
                problems.append(f"{name}: строка таблицы с числом без ссылки на утверждение: «{row.strip()[:70]}…»")
    return problems


def check(project: Project) -> dict:
    spec = active(project)
    if not spec:
        raise OsnError("У проекта не выбран чек-лист брифа: osnovanie report profile <имя>.")
    name = spec["file"]
    path = project.outputs / name
    if not path.exists():
        return {"profile": spec["id"], "file": name, "coverage": [], "covered": 0, "total": 0, "problems": [f"нет файла outputs/{name}: один документ в структуре брифа"]}
    text = path.read_text("utf-8")
    problems = []
    if not re.match(r"\s*#\s", text):
        problems.append(f"{name}: документ начинается не с заголовка первого уровня")
    summary, parts = _locate(spec, text)
    coverage = []

    def mark(where: str, block: dict | None, needs: list[dict]) -> None:
        for need in needs:
            ok = bool(block and re.search(need["pattern"], block["text"], re.I))
            coverage.append({"part": where, "label": need["label"], "ok": ok})

    if not summary:
        problems.append(f"{name}: нет резюме перед первой частью (заголовок второго уровня «Резюме»)")
    else:
        words = len(re.findall(r"\w+", re.sub(r"\[c\d+(?:,\s*c\d+)*\]", "", summary["text"])))
        if words > spec["summary"].get("max_words", 10_000):
            problems.append(f"{name}: резюме {words} слов, предел {spec['summary']['max_words']}")
    mark("резюме", summary, spec["summary"]["need"])
    for number, (part, block) in enumerate(zip(spec["part"], parts), 1):
        if not block:
            problems.append(f"{name}: нет части {number} (заголовок второго уровня по шаблону «{part['heading']}»)")
        mark(f"часть {number}", block, part["need"])
    problems += [f"{name}: не закрыт пункт брифа — {c['part']}: {c['label']}" for c in coverage if not c["ok"]]
    problems += checks.doc_problems(project, [name]) + _table_problems(name, text)
    for paragraph in checks.paragraphs(text):
        word = checks.WEASEL.search(paragraph)
        if word and not checks.NUM.search(paragraph):
            problems.append(f"{name}: оценочное слово «{word.group(0)}» без числа: «{paragraph[:60]}…»")
    covered = sum(c["ok"] for c in coverage)
    return {"profile": spec["id"], "file": name, "coverage": coverage, "covered": covered, "total": len(coverage), "problems": problems}


def problems(project: Project) -> list[str]:
    return check(project)["problems"] if project.meta().get("report_profile") else []


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _render(deck: Deck, text: str) -> str:
    out = []
    for block in checks.paragraphs(text):
        heading = HEADING.fullmatch(block)
        if heading:
            level = min(len(heading.group(1)), 4)
            anchor = f' id="part-{len(out)}"' if level == 2 else ""
            out.append(f"<h{level}{anchor}>{deck.inline(heading.group(2))}</h{level}>")
        elif block.startswith("|"):
            rows = [r for r in block.splitlines() if r.strip().startswith("|")]
            head, body = rows[0], [r for r in rows[1:] if not re.fullmatch(r"\|[\s:|-]+\|?", r.strip())]
            out.append("<table><thead><tr>" + "".join(f"<th>{deck.inline(c)}</th>" for c in _cells(head)) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{deck.inline(c)}</td>" for c in _cells(r)) + "</tr>" for r in body) + "</tbody></table>")
        elif re.match(r"(?:[-*]|\d+[.)])\s", block):
            tag = "ol" if re.match(r"\d", block) else "ul"
            items = re.split(r"\n(?=(?:[-*]|\d+[.)])\s)", block)
            out.append(f"<{tag}>" + "".join(f"<li>{deck.inline(re.sub(r'^(?:[-*]|\d+[.)])\s+', '', i).replace(chr(10), ' '))}</li>" for i in items) + f"</{tag}>")
        elif block.startswith(">"):
            out.append(f"<blockquote>{deck.inline(re.sub(r'^>\s?', '', block, flags=re.M).replace(chr(10), ' '))}</blockquote>")
        else:
            out.append(f"<p>{deck.inline(block.replace(chr(10), ' '))}</p>")
    return "\n".join(out)


def _notes(deck: Deck) -> str:
    sources = deck.project.current("sources")
    rows = []
    for claim_id in sorted(deck.used):
        claim = deck.claims.get(claim_id)
        if not claim:
            continue
        label = claims_mod.LABEL[claims_mod.effective_type(claim)]
        source = sources.get(claim.get("source_id") or "", {})
        if claim.get("quote"):
            where = html.escape(source.get("title", "") + (f", {source['published_at']}" if source.get("published_at") else ""))
            body = f"<q>{html.escape(claim['quote'][:260])}</q> <span class=\"src\">{where}</span>"
        else:
            deps = ", ".join(claim.get("depends_on", [])[:5])
            body = f"{html.escape(claim['text'][:220])} <span class=\"src\">опора: {html.escape(deps) or 'нет'}</span>"
        rows.append(f'<li id="note-{claim_id}"><b>{claim_id}</b> <i>{html.escape(label)}</i> {body}</li>')
    return f'<section class="notes"><h2>Основания чисел</h2><p class="muted">Каждая ссылка в тексте ведёт сюда: цитата из источника или расчёт с опорой.</p><ol>{"".join(rows)}</ol></section>' if rows else ""


def _coverage(result: dict) -> str:
    rows = "".join(f'<tr><td>{html.escape(c["part"])}</td><td>{html.escape(c["label"])}</td><td class="{"ok" if c["ok"] else "miss"}">{"есть" if c["ok"] else "нет"}</td></tr>' for c in result["coverage"])
    return f'<section class="coverage"><h2>Сверка с брифом</h2><p class="muted">Закрыто {result["covered"]} из {result["total"]} требований брифа. Сверка автоматическая: <code>osnovanie report check</code>.</p><table><thead><tr><th>Где</th><th>Требование брифа</th><th></th></tr></thead><tbody>{rows}</tbody></table></section>'


def _chrome() -> str | None:
    for candidate in (os.environ.get("OSN_CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium", shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def build(project: Project, pdf: bool = False) -> dict:
    result = check(project)
    path = project.outputs / result["file"]
    if not path.exists():
        raise OsnError(result["problems"][0])
    deck = Deck(project)
    body = _render(deck, path.read_text("utf-8"))
    page = TEMPLATE.replace("__TITLE__", html.escape(deck.meta.get("title", project.slug))).replace("__BODY__", body + _coverage(result) + _notes(deck))
    out = project.outputs / "report.html"
    atomic_write(out, page)
    info = {"path": str(out.relative_to(project.base)), "covered": result["covered"], "total": result["total"], "problems": len(result["problems"]), "footnotes": len(deck.used), "pdf": None}
    if pdf:
        browser = _chrome()
        if not browser:
            raise OsnError("Для PDF нужен Chrome или Chromium (путь можно задать в OSN_CHROME). HTML собран: откройте его и напечатайте в PDF.")
        target = project.outputs / "report.pdf"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={target}", out.resolve().as_uri()], check=True, capture_output=True, timeout=120)
        info["pdf"] = str(target.relative_to(project.base))
    project.event("report_build", covered=result["covered"], total=result["total"])
    return info


TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{--ink:#07100C;--muted:#5B6F65;--line:#D5DED9;--accent:#0B8F52;--warn:#C26A00;--bad:#C8323C;--paper:#FFFFFF}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:780px;margin:0 auto;padding:48px 20px 64px}
h1{font-size:30px;line-height:1.15;margin:0 0 24px;letter-spacing:-.01em}
h2{font-size:21px;margin:40px 0 12px;padding-top:12px;border-top:2px solid var(--ink)}
h3{font-size:16px;margin:24px 0 8px}
h4{font-size:15px;margin:18px 0 6px;color:var(--muted)}
p,li{max-width:70ch}
table{width:100%;border-collapse:collapse;margin:12px 0 18px;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;background:#F2F6F4}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--accent);background:#F2F6F4}
sup{font-size:10.5px;line-height:0}
a.ref{color:var(--accent);text-decoration:none}
.muted,.src{color:var(--muted)}
.notes ol{padding-left:0;list-style:none;font-size:12.5px}
.notes li{padding:5px 0;border-bottom:1px solid var(--line)}
.notes q{font-style:italic}
.coverage td.ok{color:var(--accent);font-weight:600}
.coverage td.miss{color:var(--bad);font-weight:600}
code{font-size:13px}
@media print{@page{size:A4;margin:16mm 14mm}main{padding:0;max-width:none}h2,h3,h4{break-after:avoid}tr,blockquote{break-inside:avoid}thead{display:table-header-group}.coverage,.notes{break-before:page}a{color:inherit}}
</style></head><body><main>
__BODY__
</main></body></html>
"""
