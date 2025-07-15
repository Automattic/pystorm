.PHONY: default clean test publish requirements install lint lint-fix format check docs build dev-setup reset sync sync-dev info requirements.txt test-requirements.txt
.SUFFIXES:
.SECONDARY:

################################################################################
## Special Make Targets
################################################################################

default: build

build: pyproject.toml pystorm/*.py pystorm/serializers/*.py
	uv build

info:
	@echo "\033[1m\033[34mProject Information:\033[39m\033[0m"
	@echo "Name: pystorm"
	@echo "Description: Battle-tested Apache Storm Multi-Lang implementation for Python"
	@echo "Current version: $(shell uv run python -c "from pystorm import __version__; print(__version__)")"
	@echo "Python version: $(shell uv run python --version)"
	@echo "UV version: $(shell uv --version)"

clean:
	rm -rf dist pystorm.egg-info .venv .pytest_cache .coverage htmlcov

test:
	uv run --dev pytest

test-cov:
	uv run --dev pytest --cov=pystorm --cov-report=html --cov-report=term
	@echo "\n\033[1m\033[34mCoverage report generated in htmlcov/index.html\033[39m\033[0m"

publish: clean build
	#
	# Be sure to set TWINE_REPOSITORY_URL to the repo you want to upload to!!
	#
	uv run twine upload --verbose dist/*

lint:
	uv run --dev ruff check pystorm/ test/

lint-fix:
	uv run --dev ruff check --fix pystorm/ test/

format:
	uv run --dev ruff format pystorm/ test/

check: lint test

docs:
	uv run --dev sphinx-build -b html doc/source doc/build/html

# Generate requirements.txt for publishing
requirements.txt:
	uv pip compile pyproject.toml --output-file requirements.txt

# Generate test-requirements.txt for publishing
test-requirements.txt:
	uv pip compile pyproject.toml --group dev --output-file test-requirements.txt

# Generate both requirements files
requirements: requirements.txt test-requirements.txt

# Clean everything and start fresh
reset: clean
	uv sync --reinstall --dev

sync:
	uv sync

sync-dev:
	uv sync --dev
