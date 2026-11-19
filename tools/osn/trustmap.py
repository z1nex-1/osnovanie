from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

from . import checks, claims as claims_mod, cost, model, workflow
from .store import Project, atomic_write, now

DOCS = [("situation.md", "Картина ситуации"), ("product.md", "Продуктовая стратегия"), ("communication.md", "Коммуникационная стратегия"), ("validation.md", "План проверки")]
NOTICE = "Неофициальный материал участника хакатона MGIMO AI Challenge. Не является продуктом или позицией ПАО «МегаФон». Цвета ориентированы на публичный сайт компании."
TONE = {"fact": "t-fact", "calculation": "t-calc", "opinion": "t-opin", "assumption": "t-assu", "forecast": "t-fore", "hypothesis": "t-hypo", "synthetic": "t-synt", "unverified": "t-unve", "stale": "t-stal", "retracted": "t-retr"}
CSS = """
:root{--brand:#00B956;--accent:#731982;--ink:#14171a;--mute:#5b6570;--line:#dfe3e7;--paper:#fff;--soft:#f5f6f7;
--fact:#d9f4e5;--calc:#d6ecf7;--opin:#e8e4f5;--assu:#fdf0cf;--fore:#fde4cc;--hypo:#fbdde3;--synt:#ecdcef;--unve:#f4c7c7;--stal:#e3e3e3;--retr:#d0d0d0}
@media (prefers-color-scheme:dark){:root{--ink:#eef0f2;--mute:#a4adb6;--line:#343a40;--paper:#16191c;--soft:#212529;--fact:#17482f;--calc:#183f55;--opin:#352e56;--assu:#54431a;--fore:#573a1d;--hypo:#5a2a35;--synt:#4a2a50;--unve:#6a2626;--stal:#3b3b3b;--retr:#2b2b2b}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 Inter,Onest,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:28px 32px 18px;border-bottom:3px solid var(--brand)}h1{font-size:26px;margin:0 0 6px;line-height:1.2}
.q{color:var(--mute);margin:0;max-width:75ch}.notice{font-size:12px;color:var(--mute);margin:10px 0 0}
.tiles{display:flex;flex-wrap:wrap;gap:0;padding:14px 32px;border-bottom:1px solid var(--line)}.tile{padding:4px 28px 4px 0}.tile b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.tile span{font-size:13px;color:var(--mute)}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:0}article{padding:18px 32px 60px;min-width:0}aside{border-left:1px solid var(--line);padding:18px 20px;position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;background:var(--soft)}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}nav button{font:inherit;font-size:14px;padding:6px 12px;border:1px solid var(--line);background:none;color:inherit;border-radius:4px;cursor:pointer}nav button[aria-selected=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.doc{display:none}.doc.on{display:block}.doc h2{font-size:21px;margin:22px 0 8px}.doc h3{font-size:17px;margin:18px 0 6px}
.par{padding:6px 10px;margin:6px 0;border-left:4px solid transparent;border-radius:2px;max-width:75ch}.par[data-c]{cursor:pointer}.par:focus-visible{outline:2px solid var(--accent)}
.t-fact{background:var(--fact)}.t-calc{background:var(--calc)}.t-opin{background:var(--opin)}.t-assu{background:var(--assu)}.t-fore{background:var(--fore)}.t-hypo{background:var(--hypo)}.t-synt{background:var(--synt)}.t-unve{background:var(--unve)}.t-stal{background:var(--stal)}.t-retr{background:var(--retr);text-decoration:line-through}
.none{border-left-color:var(--line)}.ref{font-size:12px;color:var(--mute)}table{border-collapse:collapse;font-size:14px;margin:8px 0;max-width:100%;display:block;overflow-x:auto}td,th{border:1px solid var(--line);padding:4px 8px;text-align:left}
.legend{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;margin-bottom:14px}.legend span{padding:2px 8px;border-radius:3px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:10px 12px;margin:0 0 10px;font-size:14px}.card .k{font-size:12px;color:var(--mute)}blockquote{margin:6px 0;padding-left:8px;border-left:2px solid var(--brand)}
.ux{position:relative;display:inline-block;max-width:100%}.ux img{max-width:100%;display:block}.ux i{position:absolute;border:2px solid var(--accent);background:rgba(115,25,130,.14);cursor:pointer}
@media (max-width:860px){main{grid-template-columns:1fr}aside{position:static;border-left:0;border-top:1px solid var(--line);max-height:none}article,header,.tiles{padding-left:16px;padding-right:16px}}
.k.warn{color:#9a2a17}
"""
JS = """
const D=JSON.parse(document.getElementById('data').textContent),P=document.getElementById('panel');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function card(id){const c=D.claims[id];if(!c)return'';const s=D.sources[c.source_id];
const link=s?(/^https?:/.test(s.origin)?`<a href="${esc(s.origin)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`:esc(s.title)):'нет источника';
return`<div class=card><b>${id}</b> · ${esc(c.label)}<p>${esc(c.text)}</p>${c.quote?`<blockquote>${esc(c.quote)}</blockquote>`:''}<div class=k>Источник: ${link}${s?' · получен '+esc(s.accessed_at.slice(0,10))+' · '+esc(s.kind):''}</div><div class=k>Проверено: ${esc(c.verified_by||'—')}${c.confidence!=null?' · уверенность '+Math.round(c.confidence*100)+'%':''}${c.expires?' · действует до '+esc(c.expires):''}</div>${c.depends_on.length?`<div class=k>Зависит от: ${c.depends_on.map(esc).join(', ')}</div>`:''}${(c.flips||[]).map(f=>`<div class="k warn">Порог: ${esc(f)}</div>`).join('')}</div>`}
function show(ids){P.innerHTML=ids.map(card).join('')||'<p>Нет данных.</p>'}
document.querySelectorAll('[data-c]').forEach(e=>{const go=()=>show(e.dataset.c.split(','));e.addEventListener('click',go);e.addEventListener('keydown',k=>{if(k.key==='Enter')go()})});
const tabs=[...document.querySelectorAll('nav button')];tabs.forEach(b=>b.addEventListener('click',()=>{tabs.forEach(x=>x.setAttribute('aria-selected',x===b));document.querySelectorAll('.doc').forEach(d=>d.classList.toggle('on',d.id===b.dataset.t))}));
"""


def _inline(text: str) -> str:
    safe = html.escape(text, quote=False)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    return re.sub(r"\[(c\d{3,}(?:\s*,\s*c\d{3,})*)\]", r'<span class="ref">[\1]</span>', safe)


def _weakest(refs: list[str], claims: dict) -> str | None:
    kinds = [claims_mod.effective_type(claims[r]) for r in refs if r in claims]
    return max(kinds, key=claims_mod.STRENGTH.index) if kinds else None


def _render(text: str, claims: dict) -> str:
    out = []
    for block in checks.paragraphs(text):
        if block.startswith("#"):
            level = min(len(block) - len(block.lstrip("#")) + 1, 4)
            out.append(f"<h{level}>{_inline(block.lstrip('# ').strip())}</h{level}>")
        elif block.startswith("|"):
            rows = [r for r in block.splitlines() if not re.fullmatch(r"\|[\s:|-]+\|?", r)]
            cells = ["<tr>" + "".join(f"<td>{_inline(c.strip())}</td>" for c in r.strip().strip("|").split("|")) + "</tr>" for r in rows]
            out.append("<table>" + "".join(cells) + "</table>")
        else:
            refs = claims_mod.refs_in(block)
            kind = _weakest(refs, claims)
            body = "<br>".join(_inline(line) for line in block.splitlines())
            if kind:
                out.append(f'<div class="par {TONE[kind]}" data-c="{",".join(refs)}" tabindex="0" title="Самое слабое основание: {claims_mod.LABEL[kind]}">{body}</div>')
            else:
                out.append(f'<div class="par none">{body}</div>')
    return "".join(out)


def _ux_panel(project: Project, claims: dict, sources: dict) -> str:
    findings = [c for c in claims.values() if c.get("topic") == "ux" and isinstance(c.get("locator"), dict)]
    if not findings:
        return ""
    by_image: dict[str, list[dict]] = {}
    for claim in findings:
        by_image.setdefault(claim["source_id"], []).append(claim)
    blocks = ["<h2>Разбор интерфейса</h2>"]
    for source_id, items in by_image.items():
        source = sources[source_id]
        path = project.base / source["raw"]
        data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        mime = "image/png" if path.suffix == ".png" else "image/jpeg"
        marks = ""
        for claim in items:
            x, y, w, h = claim["locator"]["bbox"]
            marks += f'<i data-c="{claim["id"]}" tabindex="0" title="{html.escape(claim["text"], quote=True)}" style="left:{x / source["width"] * 100:.2f}%;top:{y / source["height"] * 100:.2f}%;width:{w / source["width"] * 100:.2f}%;height:{h / source["height"] * 100:.2f}%"></i>'
        blocks.append(f'<div class="ux"><img alt="{html.escape(source["title"], quote=True)}" src="data:{mime};base64,{data}">{marks}</div>')
    return "".join(blocks)


def _num(value: float) -> str:
    return f"{value:,.4g}".replace(",", " ") if abs(value) >= 1000 else f"{value:.4g}"


def flip_text(flip: dict, leader: str) -> str:
    side = "опустится до" if flip["direction"] == "low" else "поднимется до"
    return f"{leader} теряет первое место, если «{flip['label']}» {side} {_num(flip['threshold'])} {flip['unit']} (сейчас {_num(flip['base'])}); лидером станет {flip['new_leader']}"


def _flip_table(run: dict) -> str:
    flips = run.get("breakeven", [])
    if not flips:
        return "<p>В пределах заданных диапазонов лидер не меняется.</p>"
    leader = run["ranking"][0]
    body = "".join(f"<tr><td>{html.escape(f['label'])}</td><td>{f.get('claim') or 'допущение'}</td><td>{_num(f['base'])} {html.escape(f['unit'])}</td><td>{_num(f['threshold'])}</td><td>{html.escape(f['new_leader'])}</td></tr>" for f in flips)
    return f"<h2>Когда {html.escape(leader)} проигрывает</h2><table><tr><th>Величина</th><th>Основание</th><th>Сейчас</th><th>Порог</th><th>Новый лидер</th></tr>{body}</table>"


def build(project: Project) -> Path:
    claims = project.current("claims")
    sources = project.current("sources")
    meta = project.meta()
    spent = cost.summary(project)
    state = workflow.load(project)
    counts: dict[str, int] = {}
    for claim in claims.values():
        kind = claims_mod.effective_type(claim)
        counts[kind] = counts.get(kind, 0) + 1
    legend = "".join(f'<span class="{TONE[k]}">{claims_mod.LABEL[k]}: {counts.get(k, 0)}</span>' for k in claims_mod.STRENGTH if k in TONE)
    tabs, docs = [], []
    first = True
    for name, title in DOCS:
        path = project.outputs / name
        if not path.exists():
            continue
        slug = name.split(".")[0]
        tabs.append(f'<button role="tab" aria-selected="{str(first).lower()}" data-t="{slug}">{title}</button>')
        docs.append(f'<section class="doc{" on" if first else ""}" id="{slug}">{_render(path.read_text("utf-8"), claims)}</section>')
        first = False
    extra = _ux_panel(project, claims, sources)
    if extra:
        tabs.append('<button role="tab" aria-selected="false" data-t="ux">Интерфейс</button>')
        docs.append(f'<section class="doc" id="ux">{extra}</section>')
    gates = "".join(f"<tr><td>{g}</td><td>{html.escape(workflow.GATE_LABEL[g])}</td><td>{html.escape(v['status'])}</td><td>{html.escape(v.get('channel', '—'))}</td><td>{html.escape((v.get('closed_at') or '—')[:16])}</td><td>{html.escape(v.get('comment') or '')}</td></tr>" for g, v in state["gates"].items())
    tabs.append('<button role="tab" aria-selected="false" data-t="audit">Ход работы</button>')
    run = model.last_run(project)
    rows = ""
    flips_by_claim: dict[str, list[str]] = {}
    if run:
        for flip in run.get("breakeven", []):
            if flip.get("claim"):
                flips_by_claim.setdefault(flip["claim"], []).append(flip_text(flip, run["ranking"][0]))
        rows = "<h2>Расчёт вариантов</h2><table><tr><th>Вариант</th><th>База</th><th>P10</th><th>P90</th><th>Лидирует</th></tr>" + "".join(f"<tr><td>{o}</td><td>{r['base']:,.0f}</td><td>{r['p10']:,.0f}</td><td>{r['p90']:,.0f}</td><td>{r['p_best'] * 100:.0f}%</td></tr>" for o, r in run["results"].items()) + f"</table><p>Единица: {html.escape(run['output_unit'])}. Доля допущений в величинах: {run['assumption_share'] * 100:.0f}%.</p>" + _flip_table(run)
    docs.append(f'<section class="doc" id="audit"><h2>Точки человека</h2><table><tr><th>Точка</th><th>Смысл</th><th>Статус</th><th>Канал</th><th>Когда</th><th>Комментарий</th></tr>{gates}</table>{rows}</section>')
    payload = {
        "claims": {cid: {"label": claims_mod.LABEL[claims_mod.effective_type(c)], "text": c["text"], "quote": c.get("quote"), "source_id": c.get("source_id"), "verified_by": c.get("verified_by"), "depends_on": c.get("depends_on", []), "confidence": c.get("confidence"), "expires": c.get("expires"), "flips": flips_by_claim.get(cid, [])} for cid, c in claims.items()},
        "sources": {sid: {"title": s["title"], "origin": s["origin"], "accessed_at": s["accessed_at"], "kind": s["kind"]} for sid, s in sources.items()},
    }
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    tiles = [(f"{len(sources)}", "источников"), (f"{len(claims)}", "утверждений"), (f"{counts.get('fact', 0)}", "проверенных фактов"), (f"${spent['llm_usd']:.2f}", "модели и инструменты"), (f"{spent['human_minutes']:.0f} мин", "время человека"), (workflow.STAGE_LABEL[state["stage"]], "этап")]
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Карта доверия</title><style>{CSS}</style></head><body>
<header><h1>Карта доверия: {html.escape(meta.get('title', project.slug))}</h1><p class="q">{html.escape(meta.get('question') or '')}</p><p class="notice">{NOTICE} Собрано {now()[:16]}.</p></header>
<div class="tiles">{''.join(f'<div class=tile><b>{html.escape(a)}</b><span>{html.escape(b)}</span></div>' for a, b in tiles)}</div>
<main><article><div class="legend">{legend}</div><nav role="tablist">{''.join(tabs)}</nav>{''.join(docs)}</article><aside aria-live="polite"><h2 style="font-size:17px;margin:0 0 8px">Основания</h2><div id="panel"><p>Абзац окрашен по самому слабому основанию. Нажмите на него, чтобы увидеть цитату и источник.</p></div></aside></main>
<script type="application/json" id="data">{blob}</script><script>{JS}</script></body></html>"""
    target = project.outputs / "trust-map.html"
    atomic_write(target, page)
    project.event("map_built")
    return target
