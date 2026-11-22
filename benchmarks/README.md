# Benchmarks

Explicitly invoked benchmark workloads, including Doris experiments, belong here.

[Local performance preparation](local_perf/README.md) provides isolated P0/P1
protocol/capacity planning, TPC-derived Parquet generation, reference preparation,
typed full-result comparison and sequential DuckDB/loopback Trino qualification.
Run `rtk make test-local-perf` for stdlib checks; `rtk make qualify-local-perf`
explicitly opts into the dedicated venv and signed core extension smoke.
