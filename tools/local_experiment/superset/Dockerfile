FROM apache/superset@sha256:cc424513bf159c860b1db27655f40aa82fc270be35038cbcbe5272cdcaedb37d
USER root
COPY driver-requirements.txt /app/local-driver-requirements.txt
RUN uv pip install --python /app/.venv/bin/python --no-deps --only-binary=:all: --require-hashes -r /app/local-driver-requirements.txt
COPY superset_config.py bootstrap.py /app/local-experiment/
ENV SUPERSET_CONFIG_PATH=/app/local-experiment/superset_config.py
USER superset
CMD ["/app/.venv/bin/gunicorn", "--bind", "0.0.0.0:8088", "--workers", "1", "--threads", "4", "--timeout", "120", "superset.app:create_app()"]
