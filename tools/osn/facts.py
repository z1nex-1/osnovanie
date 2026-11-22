"""Единый источник чисел для README, PDF, сайта и видео.

Числа в текстах сдаются руками и расходятся с кодом через день. Поэтому все счётчики
считаются здесь из репозитория, пишутся в docs/facts.json, а docs/facts-map.toml
говорит, где в текстах стоит каждое число. facts check падает при любом расхождении.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from . import claims as claims_mod, cost, workflow
from .store import OsnError, Project, now, read_json


def _toml(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text("utf-8")) if path.exists() else {}


def _project_facts(project: Project) -> dict:
    claims = project.current("claims")
    kinds = Counter(claims_mod.effective_type(c) for c in claims.values())
    options = project.current("options")
    state = workflow.load(project)
    spent = cost.summary(project)
    commitments = project.current("commitments")
    return {
        "stage": state["stage"],
        "sources": len(project.current("sources")),
        "claims": len(claims),
        "claims_by_type": dict(sorted(kinds.items())),
        "facts_verified": kinds.get("fact", 0),
        "options": len(options),
        "objections": sum(len(o.get("objections", [])) for o in options.values()),
        "gates": {g: v["status"] for g, v in state["gates"].items()},
        "reviews": len(project.records("reviews")) if (project.state / "reviews.jsonl").exists() else 0,
        "commitments": len(commitments),
        "llm_usd": spent["llm_usd"],
        "llm_rub": spent["llm_rub"],
        "human_minutes": spent["human_minutes"],
        "total_rub": spent["total_rub"],
        "elapsed": spent["elapsed"],
    }


def build(root: Path) -> dict:
    from .cli import RULE_FILES, _rules_hashes, parser

    commands = next(a for a in parser()._actions if a.dest == "command").choices or {}
    tests = sum(len(re.findall(r"^[ \t]*def test_", p.read_text("utf-8"), re.M)) for p in (root / "tests").glob("test_*.py"))
    results = read_json(root / "evals/results.json", {}) or {}
    inj = results.get("injection", {})
    channels = results.get("channels", {})
    defects = results.get("defects", {}).get("rows", [])
    tasks = _toml(root / "evals/tasks.toml")
    projects = {}
    for base in sorted((root / "projects").glob("*/state")):
        project = Project(base.parent.name, root)
        projects[project.slug] = _project_facts(project)
    return {
        "generated_at": now(),
        "agent": {
            "cli_commands": len(commands),
            "methods": len(list((root / "methods").glob("*.md"))),
            "skills": len([p for p in (root / ".agents/skills").iterdir() if (p / "SKILL.md").exists()]),
            "subagents": len(list((root / ".claude/agents").glob("*.md"))),
            "rule_files": len(_rules_hashes(root)),
            "rule_patterns": len(RULE_FILES),
            "tests": tests,
        },
        "evals": {
            "split": results.get("split"),
            "attacks": inj.get("attacks"),
            "attacks_caught": inj.get("caught"),
            "benign": inj.get("benign"),
            "false_alarms": inj.get("false_alarms"),
            "hidden_leaks": len(channels.get("hidden_reached_agent", [])),
            "g1_after_attacks": channels.get("g1_after"),
            "facts_after_attacks": channels.get("facts_after"),
            # Смысловые дефекты (аудитории расходятся, «понедельник» без действий) скрипт не ловит, их ловит судья.
            "defects_total": sum(1 for r in defects if r.get("expected")),
            "defects_blocked": sum(1 for r in defects if r.get("expected") and r.get("caught") and r.get("blocked")),
            "defects_semantic": sum(1 for r in defects if not r.get("expected")),
            "tasks": len(tasks.get("task", [])),
        },
        "projects": projects,
    }


def _lookup(facts: dict, key: str):
    value = facts
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise OsnError(f"В facts.json нет ключа {key}.")
        value = value[part]
    return value


def check(root: Path, facts: dict | None = None) -> list[str]:
    facts = facts or build(root)
    rules = _toml(root / "docs/facts-map.toml").get("number", [])
    problems = []
    for rule in rules:
        path = root / rule["file"]
        if not path.exists():
            problems.append(f"{rule['file']}: файла нет")
            continue
        found = re.findall(rule["pattern"], path.read_text("utf-8"))
        if not found:
            problems.append(f"{rule['file']}: не найдено «{rule['pattern']}» ({rule['key']})")
            continue
        expected = str(_lookup(facts, rule["key"]))
        for value in found:
            value = value if isinstance(value, str) else value[0]
            if value != expected:
                problems.append(f"{rule['file']}: {rule['key']} = {value}, а в репозитории {expected}")
    return problems


def write(root: Path) -> Path:
    facts = build(root)
    target = root / "docs/facts.json"
    old = read_json(target, None)
    if old and {k: v for k, v in old.items() if k != "generated_at"} == {k: v for k, v in facts.items() if k != "generated_at"}:
        return target
    target.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return target
