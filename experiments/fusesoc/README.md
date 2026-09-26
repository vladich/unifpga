# Pinned FuseSoC / Edalize setup probe

This is the first executable ECO-07 evaluation, not an accepted importer or
build backend. Its own lock installs FuseSoC 2.4.6 and Edalize 0.6.7 with
Python >=3.10, outside unifpga's Python >=3.8 runtime. The fixture contains
two independent CAPI2 cores: `composed` depends on `pulse`, supplies a
parameter, selects the Edalize generic Flow API with Icarus, and carries one
data file. The probe calls only FuseSoC's setup stage. It validates the exact
dependency closure, source order and ownership, exported bytes, core-file
provenance, top, parameter, and selected flow tool against the generated EDAM.

Use a task-owned temporary directory with a manifest and lease. Set
`UV_PROJECT_ENVIRONMENT`, `UV_CACHE_DIR`, and `TMPDIR` to paths within it,
then run:

```text
uv sync --project experiments/fusesoc --locked
python experiments/fusesoc/test_probe.py -v
python experiments/fusesoc/probe.py --work-root "$TMPDIR/probe-build"
```

The pinned setup produced EDAM version 0.2.1. It exported all three files and
retained the two exact VLNV identities and dependency edge. The `user`-typed
ROM asset was copied to the work tree but was not listed in the generated
Makefile. That observation does not prove whether every backend handles ROM
files correctly; it shows that an import cannot equate EDAM presence with
build invalidation or runtime asset placement. No simulation, synthesis, or
programming tool was invoked. This host did not have Icarus, Yosys, or
nextpnr-ice40 available for a backend-parity run.

The current unifpga `fileset.yml` only represents local source, simulation,
and asset paths. It has no identity, dependency, parameter, target, flow,
generator, license, or physical/interface metadata contract. Copying a CAPI2
core directly into that format would lose information. The intended import
record must preserve the original `.core` document and digest, the exact
resolved dependency graph, selected target/flags, exported source digests,
and a separate versioned sidecar for unifpga's physical and semantic data.

The probe accepts only local, trusted fixtures. FuseSoC setup can fetch
providers or run generators for other cores, so arbitrary external cores must
not be pointed at this in-process experiment. A production import needs an
isolated, supervised worker, immutable source snapshot, dependency pinning,
resource limits, and an explicit executable-hook policy. The full three-design
and four-flow parity matrix remains open.

For already resolved, trusted EDAM 0.2.1, `tools.edam_import_input` now checks
the exact exported file inventory, core-file digests and dependency graph,
then derives a Slang request without running a FuseSoC backend. Use the locked
Slang environment and a task-owned scratch directory:

```text
python -m tools.edam_import_input --work-root <setup-build-root> \
  --edam <setup-build-root>/<core>.eda.yml --core-root <admitted-core-root> \
  --compilation-unit separate --candidate --scratch-root <task-tmp-root>
```

The combined candidate checks the HDL/include closure against what Slang
actually read and rehashes EDAM, exported files, assets, and core files after
elaboration. It preserves each asset but marks its placement `unverified`:
EDAM presence did not put the fixture ROM into the generated Makefile. This
reader supports `systemVerilogSource`, `verilogSource`, and `user` files plus
integer `vlogparam` overrides. Unsupported file types, executable hooks,
filters, VPI inputs, extra flow options, unsafe paths, and dependency cycles
fail explicitly.
The recheck detects ordinary concurrent edits; it is not an immutable snapshot
or an untrusted-source sandbox.

References: [CAPI2 core files](https://fusesoc.readthedocs.io/en/stable/user/build_system/core_files.html),
[CAPI2 dependencies](https://fusesoc.readthedocs.io/en/stable/user/build_system/dependencies.html),
[Edalize EDAM](https://edalize.readthedocs.io/en/latest/edam/api.html), and
[Edalize Flow API extension model](https://edalize.readthedocs.io/en/stable/dev/extend.html).
