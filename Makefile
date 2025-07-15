.PHONY: default clean test publish requirements install lint lint-fix format check docs build dev-setup reset sync sync-dev info
.SUFFIXES:
.SECONDARY:

################################################################################
## Special Make Targets
################################################################################

default: build

clean:
	rm -rf dist pystorm.egg-info .venv .pytest_cache .coverage htmlcov

test:
	uv run --dev pytest

test-cov:
	uv run --dev pytest --cov=pystorm --cov-report=html --cov-report=term
	@echo "\n\033[1m\033[34mCoverage report generated in htmlcov/index.html\033[39m\033[0m"

publish: clean build sync
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

requirements: sync
	@echo "Requirements managed by uv:"
	@echo "  - uv.lock (locked versions - managed by uv)"
	@echo "  - pyproject.toml (dependency specifications)"
	@echo "  - Use 'uv sync' to install base dependencies"
	@echo "  - Use 'uv sync --dev' to install dev dependencies"

################################################################################
## Physical Targets
################################################################################

build: pystorm/*.py pystorm/serializers/*.py
	uv build

################################################################################
## Development Helpers
################################################################################

# Quick development setup
dev-setup: sync-dev
	@echo "\n\033[1m\033[32mDevelopment environment ready!\033[39m\033[0m"
	@echo "Dependencies installed via uv sync"
	@echo "Run 'make test' to run tests"
	@echo "Run 'make lint' to check code style"
	@echo "Run 'make lint-fix' to automatically fix linting issues"
	@echo "Run 'make format' to format code"
	@echo "Run 'make docs' to build documentation"

# Clean everything and start fresh
reset: clean
	uv sync --reinstall --dev

sync:
	uv sync

sync-dev:
	uv sync --dev

# Show project info
info:
	@echo "\033[1m\033[34mProject Information:\033[39m\033[0m"
	@echo "Name: pystorm"
	@echo "Description: Battle-tested Apache Storm Multi-Lang implementation for Python"
	@echo "Current version: $(shell uv run python -c "from pystorm import __version__; print(__version__)")"
	@echo "Python version: $(shell uv run python --version)"
	@echo "UV version: $(shell uv --version)"
