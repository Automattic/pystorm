.PHONY: default clean test publish requirements install install-dev lint format check docs build
.SUFFIXES:
.SECONDARY:

################################################################################
## Special Make Targets
################################################################################

default: build

clean:
	rm -rf dist pystorm.egg-info .venv .pytest_cache .coverage htmlcov

test:
	uv run pytest

test-cov:
	uv run pytest --cov=pystorm --cov-report=html --cov-report=term

publish: clean build
	#
	# Be sure to set TWINE_REPOSITORY_URL to the repo you want to upload to!!
	#
	uv run twine upload --verbose dist/*

install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[test,docs,lint]"

lint:
	uv run pep8 pystorm/ test/
	uv run pyflakes pystorm/ test/

format:
	uv run black pystorm/ test/

check: lint test

docs:
	uv run sphinx-build -b html doc/source doc/build/html

requirements: sync
	@echo "Generating requirements files..."
	uv pip compile pyproject.toml --output-file requirements.txt
	uv pip compile pyproject.toml --extra test --output-file test-requirements.txt
	@echo "Requirements files generated:"
	@echo "  - requirements.txt (main dependencies)"
	@echo "  - test-requirements.txt (test dependencies)"
	@echo "  - uv.lock (locked versions - managed by uv)"

################################################################################
## Physical Targets
################################################################################

build: pystorm/*.py pystorm/serializers/*.py
	uv build

################################################################################
## Development Helpers
################################################################################

# Quick development setup
dev-setup: install-dev
	@echo "\n\033[1m\033[32mDevelopment environment ready!\033[39m\033[0m"
	@echo "Run 'make test' to run tests"
	@echo "Run 'make lint' to check code style"
	@echo "Run 'make format' to format code"
	@echo "Run 'make docs' to build documentation"

# Run tests with coverage and generate report
coverage: test-cov
	@echo "\n\033[1m\033[34mCoverage report generated in htmlcov/index.html\033[39m\033[0m"

# Clean everything and start fresh
reset: clean
	uv sync --reinstall

sync:
	uv sync

# Show project info
info:
	@echo "\033[1m\033[34mProject Information:\033[39m\033[0m"
	@echo "Name: pystorm"
	@echo "Description: Battle-tested Apache Storm Multi-Lang implementation for Python"
	@echo "Current version: $(shell uv run python -c "from pystorm import __version__; print(__version__)")"
	@echo "Python version: $(shell uv run python --version)"
	@echo "UV version: $(shell uv --version)"
