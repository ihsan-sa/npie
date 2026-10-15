# make check: the test suite against the simulated bench only.
# NPIE_BENCH_HOST is cleared, so no test can open a real instrument; real
# instruments stay on the bench host. The suite needs no network; only
# building .venv (pip install) does, once. Report PDFs are built where
# lualatex is on PATH; NPIE_REQUIRE_PDF=1 (CI sets it) fails without it.
PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: check lint
lint:
	$(PYTHON) -m compileall -q skill tests

check: lint $(VENV)/.installed
	env -u NPIE_BENCH_HOST $(PY) -m pytest -q tests

$(VENV)/.installed: requirements.txt
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt
	touch $@
