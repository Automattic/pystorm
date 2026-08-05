PYTHON_VERSIONS := 3.9 3.10 3.11 3.12 3.13 3.14

# black's output depends on the interpreter it runs under -- it formats some
# constructs differently on 3.9 than on 3.12+ -- so lint and fmt pin one, or
# the tree is "correctly formatted" only for whichever Python you happen to
# have. The newest supported version, so the formatting matches the newest
# syntax the package may use.
LINT_PYTHON := $(lastword $(PYTHON_VERSIONS))

.PHONY: test test-all lint fmt dist publish clean

test:
	uv run --extra test pytest

test-all:
	@for v in $(PYTHON_VERSIONS); do \
		echo "=== Python $$v ==="; \
		uv run --python $$v --extra test pytest -q || exit 1; \
	done

lint:
	uv run --python $(LINT_PYTHON) --extra lint black --check pystorm_a8c test

fmt:
	uv run --python $(LINT_PYTHON) --extra lint black pystorm_a8c test

dist: clean
	uv build

# Requires TWINE_REPOSITORY_URL=https://<internal-index>/
publish: dist
	@test -n "$$TWINE_REPOSITORY_URL" || { echo "TWINE_REPOSITORY_URL is not set; refusing to publish"; exit 1; }
	uv publish --publish-url "$$TWINE_REPOSITORY_URL"

clean:
	rm -rf dist build *.egg-info .pytest_cache
