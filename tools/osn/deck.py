"""Презентация проекта одним HTML-файлом: слайды 16:9, стрелки, печать в PDF по слайду на страницу, без внешних ресурсов."""
from __future__ import annotations

import html
import re

from . import checks, claims as claims_mod, model, workflow
from .store import Project, atomic_write

REF = claims_mod.REF
MAX_APPENDIX = 15
KIND = {"keep": "оставить", "change": "изменить", "close": "закрыть"}


class Deck:
    def __init__(self, project: Project):
        self.project = project
        self.claims = project.current("claims")
        self.meta = project.meta()
        self.used: dict[str, None] = {}
        self.slides: list[str] = []

    def live(self, claim_id: str) -> dict:
        return claims_mod.resolve_chain(self.claims, claim_id) if claim_id in self.claims else {"id": claim_id}

    def sup(self, ids: list[str], raw_ids: bool = False) -> str:
        out = []
        for raw in ids:
            current = raw if raw_ids else self.live(raw)["id"]
            self.used[current] = None
            out.append(f'<a class="ref" href="#note-{current}" data-note="{current}">{current}</a>')
        return "<sup>[" + ", ".join(out) + "]</sup>" if out else ""

    def inline(self, text: str) -> str:
        parts, last = [], 0
        for match in REF.finditer(text):
            parts.append(html.escape(text[last:match.start()]))
            parts.append(self.sup(re.findall(r"c\d{3,}", match.group(1))))
            last = match.end()
        parts.append(html.escape(text[last:]))
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", "".join(parts))

    def add(self, title: str, body: str, kicker: str = "", cls: str = "") -> None:
        number = len(self.slides) + 1
        head = f'<div class="kicker">{html.escape(kicker)}</div>' if kicker else ""
        self.slides.append(f'<section class="slide {cls}" id="s{number}" data-n="{number}">{head}<h2>{html.escape(title)}</h2>{body}<div class="foot"><span>{html.escape(self.meta.get("title", self.project.slug))}</span><span>{number}</span></div></section>')

    def doc(self, name: str) -> str:
        path = self.project.outputs / name
        return path.read_text("utf-8") if path.exists() else ""

    def alive(self, kinds: set[str] | None = None, topic: str | None = None, skip_topic: set[str] = frozenset()) -> list[dict]:
        out = []
        for claim in self.claims.values():
            if claim.get("superseded_by") or claim["status"] == "retracted":
                continue
            if kinds and claim["type"] not in kinds:
                continue
            if topic is not None and claim.get("topic") != topic:
                continue
            if claim.get("topic") in skip_topic:
                continue
            out.append(claim)
        return sorted(out, key=lambda c: c["id"])


def _short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _k(value: float) -> str:
    return f"{value / 1000:,.0f}".replace(",", " ")


def _bullets(deck: Deck, text: str, limit: int = 6, cap: int = 190) -> str:
    rows = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line) for line in checks.items(text) if not line.strip().startswith("|")]
    return "<ul>" + "".join(f"<li>{deck.inline(_short(row, cap))}</li>" for row in rows[:limit]) + "</ul>"


def _range_chart(run: dict) -> str:
    results, unit = run["results"], run["output_unit"]
    low = min(r["p10"] for r in results.values())
    high = max(r["p90"] for r in results.values())
    pad = (high - low) * 0.05 or 1
    low, high = low - pad, high + pad
    width, left, row = 1000, 190, 62

    def x(value: float) -> float:
        return left + (value - low) / (high - low) * (width - left - 20)

    shapes = [f'<line x1="{x(0):.0f}" x2="{x(0):.0f}" y1="8" y2="{row * len(results) + 8}" stroke="#4A6358" stroke-dasharray="4 4"/><text x="{x(0):.0f}" y="{row * len(results) + 30}" fill="#8FA89C" font-size="14" text-anchor="middle">0</text>']
    for index, option in enumerate(run["ranking"]):
        r = results[option]
        y = 20 + index * row
        lead = index == 0
        color = "#0CDC78" if lead else "#8FA89C"
        shapes.append(
            f'<text x="0" y="{y + 22}" fill="#E7F2EC" font-size="20">{html.escape(option)}</text>'
            f'<text x="44" y="{y + 22}" fill="#8FA89C" font-size="15">лидер в {r["p_best"] * 100:.0f}%</text>'
            f'<rect x="{x(r["p10"]):.0f}" y="{y}" width="{max(x(r["p90"]) - x(r["p10"]), 2):.0f}" height="14" rx="3" fill="{color}" opacity=".35"/>'
            f'<rect x="{x(r["base"]) - 3:.0f}" y="{y - 4}" width="6" height="22" fill="{color}"/>'
            f'<text x="{x(r["base"]):.0f}" y="{y + 40}" fill="{color}" font-size="15" text-anchor="middle">{_k(r["base"])}</text>'
            f'<text x="{x(r["p10"]):.0f}" y="{y + 12}" fill="#8FA89C" font-size="12" text-anchor="end" dx="-6">{_k(r["p10"])}</text>'
            f'<text x="{x(r["p90"]):.0f}" y="{y + 12}" fill="#8FA89C" font-size="12" dx="6">{_k(r["p90"])}</text>'
        )
    return f'<svg viewBox="0 0 {width} {row * len(results) + 44}" class="chart" role="img" aria-label="Диапазоны результата по вариантам, тыс. {html.escape(unit)}">{"".join(shapes)}</svg>'


def build(project: Project) -> dict:
    deck = Deck(project)
    meta, claims = deck.meta, deck.claims
    options = project.current("options")
    runs = project.records("model_runs")
    run = runs[-1] if runs else None
    brief = (project.state / "brief.json")
    from .store import read_json

    brief = read_json(brief, {})
    state = workflow.load(project)
    decision = meta.get("decision") or {}
    note = f'<p class="stamp">{html.escape(meta["note"])}</p>' if meta.get("reference") and meta.get("note") else ""

    deck.add(meta.get("title", project.slug), f'<p class="lead">{html.escape(meta.get("question", ""))}</p>{note}', "Основание")

    rows = "".join(f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(brief.get(key, '')))}</td></tr>" for key, label in (("goal", "Цель"), ("recipient", "Кому"), ("constraints", "Ограничения"), ("data", "Данные")) if brief.get(key))
    deck.add("Задача", f'<p class="lead">{html.escape(meta.get("question", ""))}</p><table class="plain">{rows}</table>', "Что решаем")

    facts = [c for c in deck.alive({"fact", "calculation", "opinion"}) if c["status"] == "verified"]
    facts.sort(key=lambda c: (c.get("value") is None, c["id"]))
    shown = facts[:5]
    deck.add("Что знаем", "<ul>" + "".join(f'<li>{html.escape(_short(c["text"], 170))} {deck.sup([c["id"]])}</li>' for c in shown) + f'</ul><p class="muted">Показано {len(shown)} из {len(facts)} проверенных утверждений: у каждого цитата из источника в приложении.</p>', "Проверено по тексту источника")

    gaps = [q for q in project.current("questions").values() if q["status"] == "gap"]
    hyps = [c for c in deck.alive({"hypothesis"}, skip_topic={"conflict", "conclusion"})]
    gap_html = "".join(f'<li>{html.escape(_short(q["text"], 130))}<span class="muted"> {html.escape(_short(q.get("why", ""), 110))}</span></li>' for q in gaps[:4])
    hyp_html = "".join(f'<div class="hyp"><div class="bar"><i style="width:{int(round((c.get("confidence") or 0) * 100))}%"></i></div><span>{html.escape(_short(c["text"], 120))} {deck.sup([c["id"]])} <b>{int(round((c.get("confidence") or 0) * 100))}%</b></span></div>' for c in hyps[:4])
    deck.add("Чего не знаем", f'<div class="cols"><div><h3>Пробелы в данных</h3><ul class="gap">{gap_html}</ul></div><div><h3>Гипотезы и уверенность</h3>{hyp_html}</div></div>', "Гипотезы названы гипотезами")

    conflicts = deck.alive(topic="conflict")
    deck.add("Источники расходятся", "".join(f'<div class="card"><p>{html.escape(_short(re.sub(r"^Расхождение[^:]*:\s*", "", c["text"]), 210))} {deck.sup([c["id"]])}</p><p class="muted">{html.escape(c.get("resolution", ""))}</p></div>' for c in conflicts[:4]) or '<p class="muted">Расхождений не записано.</p>', "Что не сходится")

    cards = []
    for option in sorted(options.values(), key=lambda o: o["id"]):
        objections = "".join(f'<li class="obj">{html.escape(_short(o["text"], 120))} {deck.sup(o.get("claims", []))}</li>' for o in option.get("objections", [])[:2])
        cards.append(f'<div class="card"><div class="tag">{html.escape(option["id"])} · {KIND.get(option.get("kind"), "")}</div><h3>{html.escape(option["title"])}</h3><p>{html.escape(_short(option.get("thesis", ""), 150))}</p><ul>{objections}</ul></div>')
    deck.add("Варианты и возражения", f'<div class="grid4">{"".join(cards)}</div>', "Красным — что против")

    if run:
        calc = [c for c in deck.alive({"calculation"}) if str(c.get("calc_id", "")).startswith("model-")]
        link = deck.sup([calc[-1]["id"]]) if calc else ""
        leader = run["ranking"][0]
        deck.add("Модель: диапазоны", f'{_range_chart(run)}<p class="muted">Прибыль в месяц, тыс. {html.escape(run["output_unit"])}: полоса P10-P90 из 4 000 прогонов, метка база. Лидер {html.escape(leader)}. {link} Без проверенной опоры {run["assumption_share"] * 100:.0f}% величин.</p>', "Что показывает расчёт")
        flips = run.get("breakeven", [])
        flip_rows = "".join(f'<tr><td>{html.escape(f["label"])}</td><td>{f["base"]:g} {html.escape(f["unit"])}</td><td class="warn">{f["threshold"]:g}</td><td>{html.escape(f["new_leader"])}</td></tr>' for f in flips[:8])
        param_refs = deck.sup([p["claim"] for p in run["params"].values() if p.get("claim")][:3])
        body = f'<table class="plain"><tr><th>Величина</th><th>База</th><th>Лидер меняется при</th><th>Новый лидер</th></tr>{flip_rows}</table>' if flips else '<p class="lead">В пределах заданных диапазонов лидер не меняется.</p>'
        deck.add("Точки перелома", body + f'<p class="muted">Значения величин опираются на утверждения {param_refs}</p>', "Где решение разворачивается")

    chosen = options.get(decision.get("option", ""), {})
    strategy = checks.paragraphs(deck.doc("strategy.md"))
    summary = "".join(f"<p>{deck.inline(re.sub(r'^#+\s.*$', '', p, flags=re.M).strip())}</p>" for p in strategy[1:4] if p.strip())
    head = f'<div class="pick">{html.escape(chosen.get("id", ""))} · {html.escape(chosen.get("title", "решение не принято"))}</div>'
    if decision.get("owner"):
        head += f'<p class="muted">Ответственный: {html.escape(decision["owner"])}</p>'
    deck.add("Рекомендация", head + summary, "Что предлагаем")

    commits = list(project.current("commitments").values())
    commit_rows = "".join(f'<tr><td>{html.escape(_short(c["what"], 90))}</td><td>{html.escape(c.get("check_at", ""))}</td><td>{html.escape(str(c.get("threshold", "")))}</td><td>{deck.sup(c.get("claims", [])[:2])}</td></tr>' for c in commits[:5])
    validation = deck.doc("validation.md")
    expected = checks.paragraphs(checks.section(validation, "Ожидаемое изменение"))
    deck.add("План проверки", (f"<p>{deck.inline(expected[0])}</p>" if expected else "") + f'<table class="plain"><tr><th>Шаг</th><th>Проверить к</th><th>Порог</th><th>Опора</th></tr>{commit_rows}</table>', "Как узнаем, что прав")

    stops = checks.section(validation, "Пороги остановки")
    review_if = "".join(f'<li>{html.escape(_short(c["review_if"], 150))}</li>' for c in commits if c.get("review_if"))
    deck.add("Пороги остановки", (_bullets(deck, stops) if stops.strip() else "") + (f'<h3>Пересматриваем, если</h3><ul>{review_if}</ul>' if review_if else ""), "Когда выходим")

    reviews = project.records("reviews")
    if reviews:
        last = reviews[-1]
        changed = [claims[c] for c in last["changed"] if c in claims]
        lines = "".join(f'<li>{html.escape(_short(c["text"], 120))} {deck.sup([c["id"]], True)}<span class="muted"> → {html.escape(_short(claims[c["superseded_by"]]["text"], 120)) if c.get("superseded_by") else "отозвано"}</span> {deck.sup([c["superseded_by"]] if c.get("superseded_by") else [])}</li>' for c in changed)
        verdict = f'Лидер сменился: {last["leader_before"]} → {last["leader_after"]}.' if last["leader_changed"] else f'Лидер прежний: {last["leader_after"]}.'
        pair = [r for r in runs[-2:]] if len(runs) >= 2 else []
        delta = ""
        if len(pair) == 2 and pair[0]["results"].keys() == pair[1]["results"].keys():
            delta = "<table class='plain'><tr><th>Вариант</th><th>База до</th><th>База после</th></tr>" + "".join(f"<tr><td>{o}</td><td>{_k(pair[0]['results'][o]['base'])}</td><td>{_k(pair[1]['results'][o]['base'])}</td></tr>" for o in pair[1]["ranking"]) + "</table>"
        deck.add("Что изменилось при пересмотре", f'<p class="lead">{html.escape(last.get("note", ""))}</p><ul>{lines}</ul><p class="pick small">{html.escape(verdict)}</p>{delta}', "Пересмотр " + last["id"])

    appendix = sorted((claims[c] for c in deck.used if c in claims), key=lambda c: c['id'])
    appendix = [c for c in appendix if c["id"] in claims]
    sources = project.current("sources")
    entries = []
    for claim in appendix:
        source = sources.get(claim.get("source_id") or "", {})
        label = claims_mod.LABEL[claims_mod.effective_type(claim)]
        host = re.sub(r"^https?://(www\.)?", "", source.get("origin", "")).split("/")[0]
        if claim.get("quote"):
            body = f'<q>{html.escape(_short(claim["quote"], 170))}</q><div class="src">{html.escape(_short(source.get("title", ""), 46))} · {html.escape(host)} · {html.escape(source.get("published_at") or "без даты")}</div>'
            text = ""
        else:
            deps = ", ".join(claim.get("depends_on", [])[:4])
            conf = f", {int(round(claim['confidence'] * 100))}%" if claim.get("confidence") is not None else ""
            text = f'<p>{html.escape(_short(claim["text"], 110))}</p>'
            body = f'<div class="src">{html.escape(label)}{conf}; опора: {html.escape(deps) or "нет"}</div>'
        entries.append(f'<div class="note" id="note-{claim["id"]}"><b>{claim["id"]}</b> <span class="muted">{html.escape(label)}</span>{text}{body}</div>')
    pages = [entries[i:i + MAX_APPENDIX] for i in range(0, len(entries), MAX_APPENDIX)] or [[]]
    for number, page in enumerate(pages[:2], 1):
        deck.add(f"Приложение: сноски ({number} из {min(len(pages), 2)})", f'<div class="notes">{"".join(page)}</div>', "Каждое число стоит на цитате", "dense")
    if len(pages) > 2:
        deck.slides[-1] = deck.slides[-1].replace("</div><div class=\"foot\">", f'<p class="muted">Ещё сносок: {sum(len(p) for p in pages[2:])}, полный список в project.json пакета сайта.</p></div><div class="foot">', 1)

    out = project.outputs / "deck.html"
    page = TEMPLATE.replace("__TITLE__", html.escape(meta.get("title", project.slug))).replace("__SLIDES__", "\n".join(deck.slides))
    atomic_write(out, page)
    return {"path": str(out.relative_to(project.base)), "slides": len(deck.slides), "footnotes": len(entries), "bytes": len(page.encode("utf-8"))}


TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#07100C;--panel:#0D1B14;--line:#1B3025;--text:#E7F2EC;--muted:#8FA89C;--accent:#0CDC78;--hyp:#FFA717;--obj:#FF5A64}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,"Helvetica Neue",Arial,sans-serif}
.stage{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden}
.slide{width:1280px;height:720px;padding:64px 72px 56px;background:var(--bg);position:relative;display:none;transform-origin:center;flex:none;overflow:hidden}
.slide.on{display:block}
.kicker{color:var(--accent);font-size:15px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:10px}
h2{font-size:42px;line-height:1.1;margin:0 0 26px;font-weight:700;letter-spacing:-.01em}
h3{font-size:20px;margin:0 0 10px;color:var(--text)}
p,li,td,th{font-size:21px;line-height:1.38}
ul{margin:0;padding-left:22px}li{margin-bottom:10px}
.lead{font-size:28px;line-height:1.3;max-width:1000px}.muted{color:var(--muted);font-size:17px}.small{font-size:19px}
.stamp{margin-top:44px;padding:14px 18px;border:1px solid var(--hyp);color:var(--hyp);font-size:17px;max-width:900px}
.foot{position:absolute;left:72px;right:72px;bottom:24px;display:flex;justify-content:space-between;color:#4A6358;font-size:13px}
sup{font-size:12px;color:var(--accent);margin-left:2px;white-space:nowrap}sup a,.ref{color:var(--accent);text-decoration:none}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:40px}.gap li{font-size:19px}
.hyp{display:grid;grid-template-columns:70px 1fr;gap:14px;align-items:center;margin-bottom:14px;font-size:18px}.hyp b{color:var(--hyp)}
.bar{height:8px;background:var(--line);border-radius:4px}.bar i{display:block;height:8px;background:var(--hyp);border-radius:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin-bottom:14px}.card p{margin:0 0 6px;font-size:19px}
.grid4{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid4 .card{margin:0;padding:14px 18px}.grid4 h3{font-size:19px;margin-bottom:4px}.grid4 p{font-size:16px}
.tag{color:var(--accent);font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px}
.obj{color:var(--obj);font-size:15px;margin-bottom:4px}.obj sup,.obj sup a{color:var(--obj)}
table.plain{border-collapse:collapse;width:100%}.plain th{color:var(--muted);text-align:left;font-weight:500;font-size:16px;padding:8px 14px 8px 0;border-bottom:1px solid var(--line)}.plain td{padding:9px 14px 9px 0;border-bottom:1px solid var(--line);font-size:18px;vertical-align:top}.warn{color:var(--hyp)}
.pick{font-size:34px;color:var(--accent);margin-bottom:12px;font-weight:700}.pick.small{font-size:22px}
.chart{width:100%;max-height:430px}
.notes{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px 14px}.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px}.note p{margin:2px 0;font-size:13px;line-height:1.3}.note b{color:var(--accent);font-size:13px}.note .muted{font-size:12px}.note q{display:block;font-size:12px;line-height:1.3;color:var(--text);quotes:"«" "»"}.src{font-size:11px;color:var(--muted);word-break:break-all}
.dense{padding-top:40px}.dense h2{font-size:30px;margin-bottom:12px}.dense .kicker{margin-bottom:4px}.note:target{border-color:var(--accent)}
.hint{position:fixed;right:14px;top:8px;color:#4A6358;font-size:12px}
@page{size:1280px 720px;margin:0}
@media print{html,body{height:auto;background:var(--bg)}.stage{position:static;display:block}.slide{display:block!important;page-break-after:always;break-after:page;transform:none!important}.hint{display:none}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
</style></head><body>
<div class="stage" id="stage">
__SLIDES__
</div>
<div class="hint">стрелки и пробел листают, P печатает в PDF</div>
<script>
(function(){
var slides=[].slice.call(document.querySelectorAll('.slide')),at=0;
function fit(){var k=Math.min(innerWidth/1280,innerHeight/720);slides.forEach(function(s){s.style.transform='scale('+k+')'})}
function show(n){at=Math.max(0,Math.min(slides.length-1,n));slides.forEach(function(s,i){s.classList.toggle('on',i===at)});try{history.replaceState(null,'','#s'+(at+1))}catch(e){}}
document.addEventListener('keydown',function(e){
 if(['ArrowRight','ArrowDown','PageDown',' ','Enter'].indexOf(e.key)>-1){e.preventDefault();show(at+1)}
 else if(['ArrowLeft','ArrowUp','PageUp','Backspace'].indexOf(e.key)>-1){e.preventDefault();show(at-1)}
 else if(e.key==='Home')show(0);else if(e.key==='End')show(slides.length-1);
 else if(e.key==='p'||e.key==='P'||e.key==='з')print();
});
document.addEventListener('click',function(e){
 var a=e.target.closest&&e.target.closest('a.ref');
 if(a){e.preventDefault();var t=document.getElementById('note-'+a.getAttribute('data-note'));if(t){show(slides.indexOf(t.closest('.slide')));t.scrollIntoView({block:'nearest'});location.hash='note-'+a.getAttribute('data-note')}return}
 if(e.clientX>innerWidth/2)show(at+1);else show(at-1);
});
addEventListener('resize',fit);fit();
var m=/^#s(\\d+)$/.exec(location.hash);show(m?+m[1]-1:0);
})();
</script></body></html>
"""
