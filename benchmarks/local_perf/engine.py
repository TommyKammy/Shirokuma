"""Optional DuckDB backend with owned disk spill and bounded statement execution."""
from contextlib import contextmanager
import threading
import time


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def connect(path, work, threads=2, memory_gib=4, temp_bytes=256 * 1024 ** 2):
    import duckdb  # Deliberately optional for core tests.
    if duckdb.__version__ != "1.4.3":
        raise RuntimeError("install isolated requirements.txt: DuckDB 1.4.3 required")
    work.mkdir(exist_ok=True)
    con = duckdb.connect(str(path), config={"threads": str(threads), "memory_limit": f"{memory_gib}GiB",
                                          "temp_directory": str(work),
                                          "max_temp_directory_size": f"{temp_bytes}B",
                                          "allow_unsigned_extensions": "false",
                                          "allow_community_extensions": "false",
                                          "autoinstall_known_extensions": "false",
                                          "autoload_known_extensions": "false"})
    return con


@contextmanager
def bounded(con, check, deadline):
    """Interrupt long generator/query statements as well as batch-boundary checks."""
    stop, errors = threading.Event(), []

    def poll():
        while not stop.wait(.2):
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError("operation deadline")
                check()
            except BaseException as exc:
                errors.append(exc)
                con.interrupt()
                return

    check()
    if time.monotonic() >= deadline:
        raise TimeoutError("operation deadline")
    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    try:
        yield
        if errors:
            raise errors[0]
    except BaseException as exc:
        if errors and exc is not errors[0]:
            raise errors[0] from exc
        raise
    finally:
        stop.set()
        worker.join()


def load_extension(con, suite, directory):
    if suite not in ("tpch", "tpcds"):
        raise ValueError("only signed core tpch/tpcds allowed")
    installed = con.execute("SELECT installed, installed_from FROM duckdb_extensions() WHERE extension_name=?", [suite]).fetchone()
    official = ("core", "https://extensions.duckdb.org", "http://extensions.duckdb.org")
    if not installed or not installed[0] or installed[1] not in official:
        directory.mkdir(exist_ok=True)
        con.execute("SET extension_directory=" + quote(directory))
        # Built-in official core source, signature verified by DuckDB. The 1.4
        # core installer can bootstrap without an already-loaded httpfs extension.
        con.execute("INSTALL " + suite + " FROM core")
    con.execute("LOAD " + suite)
    row = con.execute("SELECT extension_version, installed_from FROM duckdb_extensions() WHERE extension_name=?", [suite]).fetchone()
    return {"extension_version": row[0], "installed_from": row[1]}
