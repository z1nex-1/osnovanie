PY ?= python3

.PHONY: doctor test eval eval-all facts demo-shell

doctor:
	$(PY) bin/osnovanie doctor

test:
	uv run --with pytest python -m pytest -q

eval:
	$(PY) evals/run.py --split test2

eval-all:
	$(PY) evals/run.py --split all

facts:
	$(PY) bin/osnovanie facts build
	$(PY) bin/osnovanie facts check

# Чистый профиль Claude Code для демо-прогона: без личных CLAUDE.md, скиллов, хуков и плагинов.
# Журналы сессии ложатся в $(DEMO_HOME)/projects/, оттуда их берёт replay build.
DEMO_HOME ?= $(HOME)/.claude-osnovanie
demo-shell:
	CLAUDE_CONFIG_DIR=$(DEMO_HOME) claude
