# Releasing `pystorm-a8c`

One repository, one publish. The two-repo version-bump dance that `pystorm` and
`streamparse` needed is gone.

Version scheme is in the [README](../README.md#versioning): `MAJOR` tracks the
Apache Storm major line, so a breaking Python-API change is a **minor** bump.

> This repository is public. Internal hostnames, index URLs and credentials
> belong in the internal release runbook, never in a file here. Placeholders
> below are written as `<internal-index>`.

---

## 1. Prepare

```bash
make lint          # black --check across pystorm_a8c, test
make test-all      # full suite on every supported Python, not just the default
make coverage      # terminal + htmlcov/ report, fails under 90%
```

Bump `version` in `pyproject.toml`. Grep for the old version and update anything
that pins it — the README's install line pins an exact version on purpose, so it
will not update itself.

> **Cutting the first release.** While this work is in review, `pyproject.toml`
> carries a pre-release version (`1.0.0b1`) but the documentation already
> describes the package as `1.0.0`. That divergence is deliberate: the docs are
> written for what ships. Releasing means **dropping the `b1`** so
> `pyproject.toml` reads `1.0.0`, at which point the docs need no edit. Write
> pre-releases in PEP 440 canonical form (`1.0.0b1`, not `1.0.0-beta1`) — the
> raw string in `pyproject.toml` is compared against the normalized value in
> installed metadata, and a test enforces it.

```bash
grep -rn "<old-version>" README.md doc/ pyproject.toml
```

Commit the bump on its own.

## 2. Build and check

```bash
make dist                 # clean, then sdist + wheel via the uv_build backend
uvx twine check dist/*    # both artifacts must report PASSED
```

`make dist` depends on `clean`, so `dist/` never carries a stale artifact from a
previous version into a publish.

## 3. Verify the artifact before it leaves the machine

Install the built wheel into a throwaway environment and confirm the things a
worker depends on. This catches a broken wheel while it is still local, which is
the only point at which the fix is cheap.

```bash
V=$(mktemp -d)/venv && uv venv -q "$V"
uv pip install -q --python "$V/bin/python" dist/pystorm_a8c-<version>-py3-none-any.whl

"$V/bin/python" - <<'EOF'
import importlib.metadata as md
print("version:", md.version("pystorm-a8c"))

import pystorm_a8c
for name in ("Bolt", "Spout", "Topology", "Grouping",
             "BatchingBolt", "TicklessBatchingBolt"):
    assert hasattr(pystorm_a8c, name), name

print("ok")
EOF

# The name Storm invokes on the worker, and the CLI the deployer calls.
"$V/bin/pystorm-a8c-run" --help >/dev/null && echo "pystorm-a8c-run ok"
"$V/bin/pystorm-a8c" --help >/dev/null && echo "pystorm-a8c ok"
```

## 4. Publish

Requires the internal network and the upload credential, which lives in the
secret store — see the internal release runbook. Do not put it in a file, a
command line that reaches a shell history, or a commit.

```bash
TWINE_REPOSITORY_URL=<internal-index> make publish
```

`make publish` refuses to run when `TWINE_REPOSITORY_URL` is unset, so a bare
`make publish` cannot reach the default public index by accident.

Then confirm it resolves from the index rather than from the local `dist/`:

```bash
V=$(mktemp -d)/venv && uv venv -q "$V"
uv pip install -q --python "$V/bin/python" \
  --index-url <internal-index> "pystorm-a8c==<version>"
"$V/bin/python" -c "import importlib.metadata as m; print(m.version('pystorm-a8c'))"
```

## 5. Tag

```bash
git tag pystorm-a8c-v<version>
git push origin pystorm-a8c-v<version>
```

The `v3.1.x` tags are the old `pystorm` fork's and are unrelated to this
package's numbering.

## 6. Roll out to consumers

Update the pin in `casterisk-realtime/requirements.txt` and open a PR there.
Deploying it is a separate, human step: `casterisk-realtime/AGENTS.md` says
agents must never deploy that project, and submitting a topology counts as a
deploy. See that project's README for the deployer-container procedure.
