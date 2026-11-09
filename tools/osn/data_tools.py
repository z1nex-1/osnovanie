from __future__ import annotations

import csv
import io
import json
import re
import statistics
from datetime import date

from . import claims as claims_mod, sources
from .store import OsnError, PolicyError, Project, now, sha256

DATE_RX = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?")
UNIT_RX = re.compile(r"(%|₽|руб\w*|тыс\w*|млн|млрд|шт|usd|\$|мин|час\w*)", re.I)


def load_table(project: Project, source_id: str) -> tuple[list[str], list[list[str]]]:
    text = sources.parsed_text(project, source_id)
    rows = [line.split("\t") for line in text.splitlines() if line.strip()]
    if len(rows) < 2:
        raise OsnError(f"В {source_id} нет табличных данных.")
    return rows[0], rows[1:]


def number(value: str) -> float | None:
    cleaned = re.sub(r"[\s ]", "", value).replace(",", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def profile(project: Project, source_id: str) -> dict:
    header, rows = load_table(project, source_id)
    report: dict = {"source": source_id, "rows": len(rows), "duplicates": len(rows) - len({tuple(r) for r in rows}), "columns": {}, "problems": []}
    short = sum(1 for r in rows if len(r) != len(header))
    if short:
        report["problems"].append(f"{short} строк с другим числом колонок")
    for index, name in enumerate(header):
        values = [r[index].strip() if index < len(r) else "" for r in rows]
        present = [v for v in values if v]
        info: dict = {"missing": len(values) - len(present), "missing_share": round(1 - len(present) / max(len(values), 1), 3), "unique": len(set(present))}
        numeric = [n for n in (number(v) for v in present) if n is not None and re.search(r"\d", present[0])]
        dates = [v for v in present if DATE_RX.match(v)]
        units = {m.group(1).lower() for v in present for m in [UNIT_RX.search(v)] if m}
        if len(dates) >= 0.8 * max(len(present), 1) and dates:
            info["type"] = "date"
            ordered = sorted(d[:10] for d in dates)
            info["from"], info["to"] = ordered[0], ordered[-1]
            months = sorted({d[:7] for d in dates})
            gaps = _month_gaps(months)
            if gaps:
                report["problems"].append(f"{name}: нет данных за месяцы {', '.join(gaps[:6])}")
        elif len(numeric) >= 0.8 * max(len(present), 1) and numeric:
            info["type"] = "number"
            info["min"], info["max"], info["mean"] = min(numeric), max(numeric), round(statistics.fmean(numeric), 3)
            if len(numeric) >= 8:
                q = statistics.quantiles(numeric, n=4)
                span = q[2] - q[0]
                outliers = [n for n in numeric if span and (n < q[0] - 3 * span or n > q[2] + 3 * span)]
                if outliers:
                    report["problems"].append(f"{name}: {len(outliers)} выбросов")
            if len(units) > 1:
                report["problems"].append(f"{name}: смешаны единицы {', '.join(sorted(units))}")
        else:
            info["type"] = "text"
        if info["missing_share"] > 0.2:
            report["problems"].append(f"{name}: пропуски {info['missing_share'] * 100:.0f}%")
        report["columns"][name] = info
    project.event("data_profile", source=source_id, problems=len(report["problems"]))
    return report


def _month_gaps(months: list[str]) -> list[str]:
    if len(months) < 2:
        return []
    def key(m: str) -> int:
        return int(m[:4]) * 12 + int(m[5:7]) - 1
    have = {key(m) for m in months}
    return [f"{k // 12}-{k % 12 + 1:02d}" for k in range(min(have), max(have)) if k not in have]


def _filtered(header, rows, where: dict | None):
    if not where:
        return rows
    out = []
    for row in rows:
        keep = True
        for column, condition in where.items():
            cell = row[header.index(column)] if header.index(column) < len(row) else ""
            if isinstance(condition, dict):
                if "from" in condition and cell[:10] < condition["from"]:
                    keep = False
                if "to" in condition and cell[:10] > condition["to"]:
                    keep = False
                if "contains" in condition and condition["contains"].lower() not in cell.lower():
                    keep = False
                if "in" in condition and cell not in condition["in"]:
                    keep = False
            elif cell != str(condition):
                keep = False
        if keep:
            out.append(row)
    return out


OPS = {"count", "sum", "mean", "median", "min", "max", "share", "group_count", "group_mean"}


def calc(project: Project, spec: dict) -> dict:
    for field in ("id", "source", "op", "text"):
        if not spec.get(field):
            raise OsnError(f"В спецификации расчёта нет поля {field}.")
    if spec["op"] not in OPS:
        raise OsnError(f"Операция расчёта: {', '.join(sorted(OPS))}.")
    header, rows = load_table(project, spec["source"])
    column = spec.get("column")
    for name in [column, spec.get("by"), *(spec.get("where") or {})]:
        if name and name not in header:
            raise PolicyError(f"Колонки «{name}» нет в источнике. Есть: {', '.join(header)}.")
    selected = _filtered(header, rows, spec.get("where"))
    if not selected:
        raise PolicyError("После фильтра не осталось строк.")
    def col(row, name):
        i = header.index(name)
        return row[i] if i < len(row) else ""
    values = [number(col(r, column)) for r in selected] if column else []
    missing = sum(v is None for v in values)
    numeric = [v for v in values if v is not None]
    op = spec["op"]
    if op == "count":
        result = len(selected)
    elif op == "sum":
        result = sum(numeric)
    elif op == "mean":
        result = round(statistics.fmean(numeric), 4)
    elif op == "median":
        result = statistics.median(numeric)
    elif op == "min":
        result = min(numeric)
    elif op == "max":
        result = max(numeric)
    elif op == "share":
        matched = _filtered(header, selected, spec["match"])
        result = round(len(matched) / len(selected), 4)
    elif op == "group_count":
        counts: dict[str, int] = {}
        for row in selected:
            counts[col(row, spec["by"])] = counts.get(col(row, spec["by"]), 0) + 1
        result = dict(sorted(counts.items()))
    else:
        groups: dict[str, list[float]] = {}
        for row in selected:
            value = number(col(row, column))
            if value is not None:
                groups.setdefault(col(row, spec["by"]), []).append(value)
        result = {k: round(statistics.fmean(v), 4) for k, v in sorted(groups.items())}
    data_hash = sha256(sources.parsed_text(project, spec["source"]))
    record = {"id": spec["id"], "ts": now(), "spec": spec, "result": result, "rows_used": len(selected), "missing": missing, "data_sha256": data_hash}
    project.append("calcs", record)
    numeric_value = result if isinstance(result, (int, float)) else None
    claim = claims_mod.add(project, {
        "text": f"{spec['text']}: {json.dumps(result, ensure_ascii=False)}", "type": "calculation", "calc_id": spec["id"], "source_id": spec["source"],
        "value": numeric_value, "unit": spec.get("unit"), "period": spec.get("period"), "depends_on": spec.get("depends_on", []), "locator": f"{op} по {len(selected)} строкам, пропусков {missing}",
    })
    return {"calc": record, "claim": claim["id"]}


def recompute(project: Project, calc_id: str) -> dict:
    records = [r for r in project.records("calcs") if r["id"] == calc_id]
    if not records:
        raise OsnError(f"Расчёт {calc_id} не найден.")
    stored = records[-1]
    header, _ = load_table(project, stored["spec"]["source"])
    same_data = sha256(sources.parsed_text(project, stored["spec"]["source"])) == stored["data_sha256"]
    return {"calc": calc_id, "same_data": same_data, "columns": header}


def flatten(project: Project, source_id: str, path: str, fields: dict[str, str], title: str) -> dict:
    data = json.loads(sources.parsed_text(project, source_id))
    node = data
    for part in path.split("."):
        node = node[part]
    if not isinstance(node, list):
        raise OsnError("По пути лежит не список.")
    def pluck(item, dotted):
        for part in dotted.split("."):
            if not isinstance(item, dict) or part not in item:
                return ""
            item = item[part]
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(fields))
    for item in node:
        writer.writerow([pluck(item, dotted) for dotted in fields.values()])
    return sources.ingest_bytes(project, buffer.getvalue().encode("utf-8"), ".csv", title, "reviews", f"derived:{source_id}:{path}", None)


PII_PATTERNS = [
    ("телефон", re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")),
    ("почта", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("паспорт", re.compile(r"\b\d{2}\s?\d{2}\s?\d{6}\b")),
    ("имя", re.compile(r"(?:меня зовут|моя фамилия|я\s*[-—]\s*)\s*[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?")),
]


def add_interview(project: Project, path, consent: str, title: str | None) -> dict:
    if not consent.strip():
        raise PolicyError("Реальное интервью загружается только с указанием согласия (номер записи, без ПДн).")
    if not path.exists():
        raise OsnError(f"Файл не найден: {path}")
    text = path.read_text("utf-8")
    found = [name for name, rx in PII_PATTERNS if rx.search(text)]
    if found:
        raise PolicyError(f"В расшифровке похоже есть персональные данные ({', '.join(found)}). Обезличьте её и загрузите снова.")
    record = sources.ingest_bytes(project, text.encode("utf-8"), ".txt", title or path.name, "interview", f"interview:{consent}", None)
    project.append("consents", {"ts": now(), "source": record["id"], "consent": consent})
    return record


HEURISTICS = {
    "H1": "Видимость состояния системы", "H2": "Соответствие миру пользователя", "H3": "Контроль и свобода", "H4": "Единообразие и стандарты", "H5": "Предотвращение ошибок",
    "H6": "Узнавание вместо припоминания", "H7": "Гибкость и эффективность", "H8": "Эстетика и минимализм", "H9": "Помощь при ошибках", "H10": "Справка и документация",
}


def ux_add(project: Project, items: list[dict]) -> list[str]:
    known = project.current("sources")
    created = []
    for item in items:
        source = known.get(item.get("source_id"))
        if not source or "width" not in source:
            raise PolicyError("Находка UX должна ссылаться на загруженное изображение.")
        if item.get("heuristic") not in HEURISTICS:
            raise OsnError("Эвристика: H1…H10.")
        box = item.get("bbox")
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            raise OsnError("bbox задаётся как [x, y, ширина, высота] в пикселях.")
        x, y, w, h = box
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > source["width"] or y + h > source["height"]:
            raise PolicyError(f"bbox {box} выходит за пределы изображения {source['width']}×{source['height']}: находка не привязана к видимому.")
        claim = claims_mod.add(project, {
            "text": f"{HEURISTICS[item['heuristic']]}: {item['finding']}", "type": "hypothesis", "source_id": item["source_id"], "locator": {"bbox": box, "heuristic": item["heuristic"], "severity": item.get("severity")},
            "topic": "ux",
        })
        created.append(claim["id"])
    project.event("ux_findings", count=len(created))
    return created


def days_until(due: str) -> int:
    return (date.fromisoformat(due) - date.today()).days
