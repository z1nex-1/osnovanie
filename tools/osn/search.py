from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections import Counter

from . import claims as claims_mod
from .store import Project, read_json, repo_root, sha256, write_json

ENDINGS = sorted("ами ями ого его ому ему ыми ими ая яя ую юю ое ее ые ие ой ей ий ый ам ям ах ях ов ев ом ем ть ти ться тся ет ит ют ат ят а я о е ы и у ю ь й".split(), key=len, reverse=True)


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if re.search("[а-я]", word) and len(word) > 4:
        for ending in ENDINGS:
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                return word[: -len(ending)]
    return word


def tokens(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[\wёЁ]+", text)]


def corpus(project: Project, materials: bool) -> list[dict]:
    docs = []
    for claim in project.current("claims").values():
        docs.append({"id": claim["id"], "kind": "claim", "text": claim["text"] + " " + (claim.get("quote") or ""), "claim": claim})
    if materials:
        for source in project.current("sources").values():
            path = project.parsed / f"{source['id']}.txt"
            if not path.exists():
                continue
            lines = path.read_text("utf-8").splitlines()
            for start in range(0, len(lines), 6):
                chunk = "\n".join(lines[start: start + 8]).strip()
                if chunk:
                    docs.append({"id": f"{source['id']}#{start + 1}", "kind": "material", "text": chunk})
    return docs


def bm25(query: str, docs: list[dict], k1: float = 1.4, b: float = 0.75) -> dict[str, float]:
    terms = set(tokens(query))
    tokenized = {d["id"]: Counter(tokens(d["text"])) for d in docs}
    lengths = {i: sum(c.values()) for i, c in tokenized.items()}
    avg = sum(lengths.values()) / max(len(lengths), 1)
    frequency = {t: sum(1 for c in tokenized.values() if t in c) for t in terms}
    scores: dict[str, float] = {}
    for doc_id, counts in tokenized.items():
        score = 0.0
        for term in terms:
            if term in counts:
                idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * counts[term] * (k1 + 1) / (counts[term] + k1 * (1 - b + b * lengths[doc_id] / avg))
        if score:
            scores[doc_id] = score
    return scores


def _trigrams(text: str) -> Counter:
    clean = re.sub(r"\W+", " ", text.lower().replace("ё", "е"))
    return Counter(clean[i: i + 3] for i in range(len(clean) - 2))


def _cos(a: Counter, b: Counter) -> float:
    common = set(a) & set(b)
    top = sum(a[x] * b[x] for x in common)
    bottom = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return top / bottom if bottom else 0.0


def _embed(project: Project, texts: list[str]) -> list[list[float]] | None:
    if not shutil.which("uv"):
        return None
    path = project.state / "index" / "embeddings.json"
    store = read_json(path, {})
    missing = [t for t in texts if sha256(t) not in store]
    if missing:
        worker = repo_root() / "tools" / "embed_worker.py"
        try:
            done = subprocess.run(["uv", "run", "--quiet", "--python", "3.12", "--with", "fastembed==0.4.2", str(worker)], input=json.dumps(missing), capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return None
        if done.returncode:
            return None
        for text, vector in zip(missing, json.loads(done.stdout), strict=True):
            store[sha256(text)] = vector
        write_json(path, store)
    return [store[sha256(t)] for t in texts]


def _dense(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def sense(project: Project, query: str, docs: list[dict], backend: str = "auto") -> tuple[dict[str, float], str]:
    if backend in {"auto", "embed"}:
        vectors = _embed(project, [query] + [d["text"][:800] for d in docs])
        if vectors:
            return {d["id"]: _dense(vectors[0], v) for d, v in zip(docs, vectors[1:], strict=True)}, "embed"
    q = _trigrams(query)
    return {d["id"]: _cos(q, _trigrams(d["text"])) for d in docs}, "trigram"


def links(project: Project, seeds: list[str], hops: int = 2) -> dict[str, float]:
    claims = project.current("claims")
    neighbors: dict[str, set[str]] = {c: set() for c in claims}
    for claim in claims.values():
        for target in claim.get("depends_on", []) + [claim.get("supersedes"), claim.get("superseded_by")]:
            if target in claims:
                neighbors[claim["id"]].add(target)
                neighbors[target].add(claim["id"])
        for other in claims.values():
            if other["id"] != claim["id"] and claim.get("source_id") and other.get("source_id") == claim["source_id"]:
                neighbors[claim["id"]].add(other["id"])
    scores: dict[str, float] = {}
    frontier = [s for s in seeds if s in claims]
    for depth in range(1, hops + 1):
        reached: list[str] = []
        for node in frontier:
            for near in neighbors[node]:
                if near not in scores and near not in seeds:
                    scores[near] = 1 / (depth + 1)
                    reached.append(near)
        frontier = reached
    return scores


def rrf(rankings: list[dict[str, float]], k: int = 40) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for position, doc_id in enumerate(sorted(ranking, key=lambda d: -ranking[d])):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1 / (k + position + 1)
    return fused


def search(project: Project, query: str, mode: str = "hybrid", limit: int = 8, materials: bool = False, backend: str = "auto") -> list[dict]:
    docs = corpus(project, materials)
    by_id = {d["id"]: d for d in docs}
    used = None
    if mode == "word":
        scores = bm25(query, docs)
    elif mode == "sense":
        scores, used = sense(project, query, docs, backend)
    elif mode == "links":
        ids = re.findall(r"c\d{3,}", query)
        if not ids:
            ids = sorted(bm25(query, docs), key=lambda d: -bm25(query, docs)[d])[:2]
        scores = links(project, ids)
    else:
        word = bm25(query, docs)
        meaning, used = sense(project, query, docs, backend)
        seeds = sorted(word, key=lambda d: -word[d])[:3]
        scores = rrf([word, meaning, links(project, seeds)])
    out = []
    for doc_id in sorted(scores, key=lambda d: -scores[d])[:limit]:
        doc = by_id.get(doc_id)
        if not doc:
            continue
        item = {"id": doc_id, "score": round(scores[doc_id], 4), "kind": doc["kind"], "backend": used}
        if doc["kind"] == "claim":
            claim = doc["claim"]
            item.update(text=claim["text"], type=claims_mod.LABEL[claims_mod.effective_type(claim)], source=claim.get("source_id"))
        else:
            item.update(text=doc["text"][:400])
        out.append(item)
    return out
