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
a representative language and performance corpus before this can serve as an
import frontend.

Accepted results now include `elaboration` with schema
`unifpga.slang-elaboration/v1`. It projects each elaborated module and
interface instance, its parent instance, definition, source location, ports,
evaluated widths and parameters, and port connection facts. Generate-array
paths retain their indices. A direct symbol reference is reported only for
simple named-value or assignment expressions; other expressions remain
unresolved. Interface connections identify the interface instance and
modport when Slang provides them. A null connection expression means an
unconnected port. Failed frontend results have `elaboration: null`.

`projection_status: complete` means this narrow fact projection found no
unhandled port kinds; `semantic_completeness: unproven` is always present.
This graph does not prove clock/reset roles, bus protocols, handshake or
timing behavior, electrical constraints, vendor-IP equivalence, or that a
design can be mapped to a virtual device. The graph is bounded by 100,000
visited symbols, 4,096 instances, 8,192 total port/parameter/connection
items, 4,096 UTF-8 bytes per fact string, and 8 MiB of serialized output.
Exceeding a bound fails the probe rather than emitting a truncated graph.
The format is experiment-owned and does not define the project configuration
schema or an import-service contract.
