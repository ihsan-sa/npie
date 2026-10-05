# make check: the test suite against the simulated bench only.
# NPIE_BENCH_HOST is cleared, so no test can open a real instrument; real
# instruments stay on the bench host. The suite needs no network; only
# building .venv (pip install) does, once.
PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: check
check: $(VENV)/.installed
	env -u NPIE_BENCH_HOST $(PY) -m pytest -q tests

$(VENV)/.installed: requirements.txt
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt
	touch $@
