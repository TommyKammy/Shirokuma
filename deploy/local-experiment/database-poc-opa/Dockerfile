FROM shirokuma.local/trino-opa@sha256:420d88cf037c400e23ed42a319f0683d6753a525dc94d8f46bfdd57d86aacaa3 AS opa_source
FROM shirokuma.local/trino@sha256:7347d14eb3a3017c351c0220c811d50b556e5c8596564d8b6195276600064670
COPY --from=opa_source --chown=10000:10000 /opt/trino/plugin/opa/ /opt/trino/plugin/opa/
