# Pinned Slang frontend experiment

This is a local, trusted-source evaluation harness for ECO-06. It is not the
accepted SystemVerilog parser, an import service, or a sandbox for untrusted
repositories. The [Slang 11 release](https://github.com/MikePopoloski/slang/releases/tag/v11.0)
changed Python binding namespaces, and this experiment uses its new API. The
separate `pyproject.toml` and `uv.lock` keep that dependency outside the main
project's Python >=3.8 runtime.

Run `uv sync --project experiments/slang --locked` with
`UV_PROJECT_ENVIRONMENT` and `UV_CACHE_DIR` set to a manifested task-scoped
temporary directory. Then use that environment's Python to run
`experiments/slang/test_probe.py` or:

```text
python experiments/slang/probe.py \
  --root experiments/slang/fixtures \
  --request experiments/slang/fixtures/good.json
```

The versioned JSON request names ordered source files, ordered include
directories, macro definitions, an exact top, and either separate or single
compilation-unit policy. The result records input digests, every source or
include file Slang read, parse and semantic diagnostic codes with source
locations, and elaborated top names. Errors return nonzero. The request has
basic path and size bounds, but the parser still runs in-process and can read
an include before the harness validates it. External imports require an
isolated worker, admission of all referenced files, timeout/cancellation, and
the full language and performance corpus from `ECOSYSTEM_PLAN.md`.
