PY ?= python3

.PHONY: doctor test eval eval-all

doctor:
	$(PY) bin/osnovanie doctor

test:
	uv run --with pytest python -m pytest -q

eval:
	$(PY) evals/run.py --split test2

eval-all:
	$(PY) evals/run.py --split all
