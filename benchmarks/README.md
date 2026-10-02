# Orientation rebuild benchmark

Measure the orientation build before and after the fork optimization on the
same project tree:

```powershell
python benchmarks/benchmark_orientation_index.py --root "C:\path\to\cpp-project" --runs 4
```

The command is read-only. It runs the discovery code from before commit
`6afc953` and the current code, then builds the complete orientation result
with each implementation. The JSON report contains every duration in
milliseconds, median durations, result equivalence, node counts, and the
median speedup. Run counts greater than one alternate execution order to
reduce filesystem-cache bias.

`equivalent=false` means the outputs differ, for example because a document
inside a newly excluded generated directory was previously indexed. In that
case `speedup` is null and the command exits with status 2.

This measures the `rebuild orientation docs` portion of an index update. It
does not measure watcher polling, C++ scanning, SQLite writes, or the optional
module-map rebuild. The updater's `incrementalAggregationTimings` report can
be used alongside this benchmark to inspect the full update.

## Incremental source update with synthetic documents

Use this when the target project has no orientation documents:

```powershell
python benchmarks/benchmark_orientation_incremental.py --documents 200 --runs 3
```

The script creates a temporary C++ project with the requested number of valid
orientation documents. For every run it copies the same baseline index twice,
changes the same C++ source, and executes the real incremental updater. One
copy reuses orientation; the other has its saved document stamps invalidated
so the updater rebuilds orientation. Copying and invalidation are outside the
timed section. The final manifest and orientation results must be equal.

`wallMs` includes Python process startup and source indexing. `aggregationMs`
comes from the updater's phase timings and is the better measure of the cache
optimization. This is a local synthetic workload; a network share or a
different project layout can produce different timings.
