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
