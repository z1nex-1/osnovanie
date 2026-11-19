from __future__ import annotations

import fnmatch
import os
import re
import shlex
from pathlib import Path

from . import cost, workflow
from .store import active_project, repo_root

SANITIZERS = {"source-sanitizer", "verifier"}
MATERIAL_SEARCHERS = {"researcher"}
# среды без отдельных субагентов: основной агент сам читает выжимку источника
SINGLE_AGENTS = {"cursor-main", "opencode-main"}
ALLOWED_TOOLS = {"Read", "Grep", "Glob", "Write", "Edit", "MultiEdit", "Bash", "WebSearch", "Agent", "Task", "TodoWrite", "AskUserQuestion", "Skill", "ToolSearch"}
PROJECT_MCP = "mcp__kb-state__"
SAFE_ALWAYS = {"Skill", "ToolSearch", "TodoWrite", "AskUserQuestion"}
SECRET_FILES = (".env", ".env.*", "*.pem", "*.key", "id_rsa*")
READ_SAFE_DIRS = ("instructions", "methods", "kb", "docs", "config", ".agents", ".claude/skills", ".claude/agents")
CLI = ("bin/osnovanie", "./bin/osnovanie", "osnovanie")
READ_ONLY_COMMANDS = {"pwd", "ls", "date", "wc"}
GIT_READ = {"status", "log", "diff", "show"}
HUMAN_ONLY = [("gate", "approve"), ("claim", "confirm")]
READ_ONLY_CLI = {"status", "resume", "log", "cost", "doctor", "list", "check"}
WHEN_PAUSED = {"Read", "Grep", "Glob", "TodoWrite", "AskUserQuestion", "Skill", "ToolSearch"}
OPERATORS = {"&&", "||", ";", "|", "\n"}
SHELL_FORBIDDEN = re.compile(r"[<>()&|;]")


def _allow() -> dict:
    return {"allow": True, "reason": ""}


def _deny(reason: str) -> dict:
    return {"allow": False, "reason": reason}


def _rel(path_text: str) -> str | None:
    root = repo_root().resolve()
    path = Path(path_text)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _is_secret(relative: str) -> bool:
    return any(fnmatch.fnmatch(part, pattern) for part in relative.split("/") for pattern in SECRET_FILES)


def _material_zone(relative: str) -> str | None:
    match = re.match(r"projects/[^/]+/materials/([^/]+)", relative)
    return match.group(1) if match else None


def check_read(path_text: str, agent: str | None) -> dict:
    relative = _rel(path_text)
    if relative is None:
        return _deny("Чтение вне папки агента запрещено.")
    if _is_secret(relative):
        return _deny("Файлы с секретами недоступны никому.")
    zone = _material_zone(relative)
    if zone in {"raw", "quarantine"}:
        return _deny("Сырые файлы и карантин недоступны модели: там может быть внедрённый текст.")
    if zone == "parsed" and agent not in SANITIZERS | SINGLE_AGENTS:
        return _deny("Текст источников читает только субагент source-sanitizer. Основной агент работает с его выжимкой.")
    return _allow()


def check_search(path_text: str | None, agent: str | None) -> dict:
    if agent in SANITIZERS:
        return check_read(path_text, agent) if path_text else _deny("Укажите путь внутри materials/parsed.")
    if not path_text:
        return _deny("Поиск только с явным путём: без него он заденет материалы проекта. Для базы знаний: osnovanie search.")
    relative = _rel(path_text)
    if relative is None or relative in {"", "."}:
        return _deny("Поиск по всей папке запрещён: укажите instructions, methods, kb, docs или outputs проекта.")
    if re.match(r"projects/[^/]+/outputs(/|$)", relative) or relative.startswith(READ_SAFE_DIRS):
        return _allow()
    return _deny("Поиск разрешён только в instructions, methods, kb, docs, config и outputs проекта.")


def check_write(path_text: str, agent: str | None) -> dict:
    if os.environ.get("OSNOVANIE_DEV") == "1":
        return _allow()
    relative = _rel(path_text)
    if relative is None:
        return _deny("Запись вне папки агента запрещена.")
    match = re.match(r"projects/([^/]+)/outputs/(.+)", relative)
    if not match:
        return _deny("Запись разрешена только в projects/<проект>/outputs/. Состояние меняется через osnovanie, правила и код защищены.")
    if match.group(2).startswith(("versions/", "gates/")):
        return _deny("Снимки версий и пакеты точек человека создаёт только CLI.")
    if agent in SANITIZERS:
        return _deny("Этому субагенту запись запрещена.")
    return _allow()


def _substitution(command: str) -> bool:
    single = False
    for index, char in enumerate(command):
        if char == "'":
            single = not single
        elif not single and (char == "`" or command[index:index + 2] in {"$(", "${"}):
            return True
    return False


def _split(command: str) -> list[list[str]] | None:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    pieces: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token in OPERATORS:
                pieces.append([])
            elif SHELL_FORBIDDEN.fullmatch(token[0]) and set(token) <= set("<>()&|;"):
                return None
            else:
                pieces[-1].append(token)
    except ValueError:
        return None
    return [p for p in pieces if p]


def _check_cli(args: list[str], agent: str | None) -> dict:
    words = [a for a in args if a != "--json"]
    if len(words) >= 2 and (words[0], words[1]) in HUMAN_ONLY:
        return _deny("Это решение принимает только человек: в терминале или сообщением в чате.")
    if "--materials" in words and agent not in MATERIAL_SEARCHERS:
        return _deny("Поиск по материалам доступен только субагенту researcher.")
    if words[:2] == ["source", "read"] and agent not in SANITIZERS | MATERIAL_SEARCHERS | SINGLE_AGENTS:
        return _deny("Текст источника читают только субагенты.")
    if words[:1] == ["serve"]:
        return _deny("HTTP-сервис запускает человек.")
    if words[:1] == ["import"]:
        return _deny("Импорт запускает человек.")
    return _allow()


def check_bash(command: str, agent: str | None) -> dict:
    if agent in SANITIZERS:
        return _deny("Этому субагенту shell запрещён.")
    if _substitution(command):
        return _deny("Подстановка команд запрещена.")
    pieces = _split(command)
    if pieces is None:
        return _deny("Перенаправления и фоновые запуски запрещены. Передавайте данные через --file из outputs/.")
    if not pieces:
        return _deny("Пустая команда.")
    for piece in pieces:
        head, args = piece[0], piece[1:]
        if head in {"python3", "python"} and args[:1] and args[0] in CLI:
            head, args = args[0], args[1:]
        if head in CLI:
            verdict = _check_cli(args, agent)
            if not verdict["allow"]:
                return verdict
        elif head in READ_ONLY_COMMANDS:
            if any(_material_zone(_rel(a) or "") or _is_secret(a) for a in args if not a.startswith("-")):
                return _deny("Эта команда не должна касаться материалов и секретов.")
        elif head == "git" and args[:1] and args[0] in GIT_READ:
            if any("materials" in a or ".env" in a for a in args):
                return _deny("Эта команда не должна касаться материалов и секретов.")
        else:
            return _deny(f"Команда {head} вне разрешённого списка. Рабочий инструмент агента: bin/osnovanie.")
    return _allow()


def _project_limits(tool: str, tool_input: dict) -> dict:
    project = active_project(required=False)
    if not project:
        return _allow()
    state = workflow.load(project)
    is_cli = tool == "Bash"
    if is_cli:
        pieces = _split(tool_input.get("command", "")) or []
        first = [a for a in (pieces[0][1:] if pieces else []) if a != "--json"][:1]
        read_only = bool(first) and first[0] in READ_ONLY_CLI
    if state.get("paused") and tool not in WHEN_PAUSED and not (is_cli and read_only):
        return _deny("Работа остановлена человеком. Дождитесь сообщения «продолжить».")
    if tool in SAFE_ALWAYS or tool in {"Read", "Grep", "Glob"}:
        return _allow()
    if cost.over_budget(project) and not (is_cli and read_only):
        return _deny("Бюджет проекта исчерпан. Сообщите человеку: он может поднять бюджет в точке G2 или командой budget set в терминале.")
    return _allow()


def decide(tool: str, tool_input: dict, agent: str | None = None) -> dict:
    if tool.startswith(PROJECT_MCP):
        if agent in SANITIZERS:
            return _deny("Этому субагенту инструменты базы запрещены.")
        return _project_limits(tool, tool_input)
    if tool == "WebFetch":
        return _deny("Прямое чтение страниц запрещено. Страницы загружает osnovanie source fetch: там allowlist, разбор и карантин.")
    if tool not in ALLOWED_TOOLS:
        return _deny(f"Инструмент {tool} не разрешён политикой агента.")
    if tool == "WebSearch" and agent != "researcher":
        return _deny("Веб-поиск доступен только субагенту researcher.")
    verdict = _allow()
    if tool == "Read":
        verdict = check_read(tool_input.get("file_path", ""), agent)
    elif tool in {"Grep", "Glob"}:
        verdict = check_search(tool_input.get("path"), agent)
    elif tool in {"Write", "Edit", "MultiEdit"}:
        verdict = check_write(tool_input.get("file_path", ""), agent)
    elif tool == "Bash":
        verdict = check_bash(tool_input.get("command", ""), agent)
    if not verdict["allow"]:
        return verdict
    return _project_limits(tool, tool_input)
