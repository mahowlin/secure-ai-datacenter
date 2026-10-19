#!/usr/bin/env python3
"""Bounded operator-only guest metrics evidence collector."""
from collections import deque
import datetime
import errno
import json
import math
import os
import pathlib
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import query

CLUSTER = os.environ["CLUSTER_NAME"]
INTERVAL = int(os.environ["COLLECTION_INTERVAL_SECONDS"])
MAX_RECORDS = int(os.environ["MAX_HISTORY_RECORDS"])
HISTORY = pathlib.Path("/var/lib/saif-observability/history.jsonl")
MAX_LINE_BYTES = 65536
MAX_HISTORY_BYTES = 67108864
MAX_SCAN_BYTES = 134217728
state = {"last": None, "attempt": 0, "records": 0, "write_ok": 0,
         "write_error": "not-started", "last_persist": 0,
         "traffic": None, "traffic_error": "not-started", "traffic_write_ok": 0,
         "traffic_last_persist": 0}
lock = threading.Lock()


class HistoryError(ValueError):
    pass


def record_failure(exc):
    reason = "http" if isinstance(exc, query.urllib.error.HTTPError) else type(exc).__name__
    if isinstance(exc, query.urllib.error.HTTPError):
        exc.close()
    return {"cluster": CLUSTER, "observedAt": datetime.datetime.now(
        datetime.timezone.utc).isoformat(), "status": "ERROR", "reason": reason}


def scan_history(incoming_bytes=0):
    """Validate old rows and retain only offsets that fit count and byte limits."""
    if not HISTORY.exists():
        return deque()
    if HISTORY.stat().st_size > MAX_SCAN_BYTES:
        raise HistoryError("history-over-limit")
    entries = deque()
    total = 0
    offset = 0
    count_limit = MAX_RECORDS - (1 if incoming_bytes else 0)
    with HISTORY.open("rb") as source:
        while True:
            line = source.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                raise HistoryError("history-corrupt")
            try:
                receipt = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HistoryError("history-corrupt") from None
            if not isinstance(receipt, dict) or receipt.get("status") not in ("PASS", "FAIL", "ERROR", "INCOMPLETE"):
                raise HistoryError("history-corrupt")
            entries.append((offset, len(line)))
            total += len(line)
            offset += len(line)
            while entries and (len(entries) > count_limit or total + incoming_bytes > MAX_HISTORY_BYTES):
                _, old_size = entries.popleft()
                total -= old_size
    return entries


def persist(receipt):
    line = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(line) > MAX_LINE_BYTES:
        raise HistoryError("receipt-over-limit")
    entries = scan_history(len(line))
    fd, name = tempfile.mkstemp(prefix=".history-", dir=HISTORY.parent)
    try:
        with os.fdopen(fd, "wb") as dest:
            if entries:
                with HISTORY.open("rb") as source:
                    for offset, length in entries:
                        source.seek(offset)
                        remaining = length
                        while remaining:
                            chunk = source.read(min(65536, remaining))
                            if not chunk:
                                raise HistoryError("history-corrupt")
                            dest.write(chunk)
                            remaining -= len(chunk)
            dest.write(line)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(name, HISTORY)
        directory = os.open(HISTORY.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return len(entries) + 1


def persisted_traffic_execution(receipt):
    """Find an execution already durably recorded, including after restart."""
    entries = scan_history()
    if not entries:
        return None, 0
    with HISTORY.open("rb") as source:
        for offset, length in entries:
            source.seek(offset)
            old = json.loads(source.read(length))
            if (old.get("schemaVersion") == receipt.get("schemaVersion")
                    and old.get("executionId") == receipt.get("executionId")
                    and old.get("clusterIncarnation") == receipt.get("clusterIncarnation")):
                return old, len(entries)
    return None, len(entries)


def persist_traffic_once(receipt):
    old, count = persisted_traffic_execution(receipt)
    if old is not None:
        return old, count, False
    stored = {**receipt, "persistedAt": datetime.datetime.now(
        datetime.timezone.utc).isoformat()}
    return stored, persist(stored), True


def write_error(exc):
    if isinstance(exc, HistoryError):
        return str(exc)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "disk-full"
    return "filesystem-error"


def label(value):
    return json.dumps(str(value), ensure_ascii=True)


def traffic_selection():
    path = getattr(query, "QUERY_CONFIG", None)
    return json.loads(pathlib.Path(path).read_text()).get("trafficPublication") if path else None


def metrics():
    with lock:
        receipt = state["last"]
        attempt = state["attempt"]
        count = state["records"]
        write_ok = state["write_ok"]
        last_persist = state["last_persist"]
        error = state["write_error"]
        traffic = state["traffic"]
        traffic_error = state["traffic_error"]
        traffic_write_ok = state["traffic_write_ok"]
        traffic_last_persist = state["traffic_last_persist"]
    lines = [
        "# TYPE saif_observability_collector_last_attempt_seconds gauge",
        "saif_observability_collector_last_attempt_seconds{cluster=" + label(CLUSTER) + "} " + str(attempt),
        "# TYPE saif_observability_collector_last_persist_seconds gauge",
        "saif_observability_collector_last_persist_seconds{cluster=" + label(CLUSTER) + "} " + str(last_persist),
        "# TYPE saif_observability_collector_history_records gauge",
        "saif_observability_collector_history_records{cluster=" + label(CLUSTER) + "} " + str(count),
        "# TYPE saif_observability_collector_history_write_ok gauge",
        "saif_observability_collector_history_write_ok{cluster=" + label(CLUSTER) + "} " + str(write_ok),
        "saif_observability_collector_history_write_error{cluster=" + label(CLUSTER) +
        ",reason=" + label(error) + "} 1",
    ]
    if receipt:
        passed = 1 if receipt.get("status") == "PASS" else 0
        lines.append("saif_observability_collector_query_pass{cluster=" + label(CLUSTER) + "} " + str(passed))
        for item in receipt.get("queries", []):
            for sample in item["samples"][:20]:
                try:
                    value = float(sample["value"])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):
                    continue
                labels = sample.get("labels", {})
                keys = {"cluster": CLUSTER, "query": item["id"],
                        "instance": labels.get("instance", ""), "node": labels.get("node", "")}
                attrs = ",".join(k + "=" + label(v) for k, v in sorted(keys.items()))
                lines.append("saif_observability_query_sample_value{" + attrs + "} " + str(value))
    traffic_expected = traffic_selection()
    if traffic_expected:
        age = (time.time() - datetime.datetime.fromisoformat(traffic["observedAt"]).timestamp()) if traffic else None
        fresh = traffic_write_ok and traffic_error == "none" and age is not None and -30 <= age <= (
            traffic_expected["cadenceSeconds"] + traffic_expected["graceSeconds"])
        lines.extend([
            "saif_traffic_publication_fresh{cluster=" + label(CLUSTER) + "} " + str(int(bool(fresh))),
            "saif_traffic_publication_history_write_ok{cluster=" + label(CLUSTER) + "} " + str(traffic_write_ok),
            "saif_traffic_publication_last_persist_seconds{cluster=" + label(CLUSTER) + "} " + str(traffic_last_persist),
            "saif_traffic_publication_error{cluster=" + label(CLUSTER) +
            ",reason=" + label(traffic_error) + "} 1",
        ])
        if fresh:
            lines.append("saif_traffic_http_client_success{cluster=" + label(CLUSTER) + "} " +
                         str(int(traffic["httpClientSuccess"])))
            lines.append("saif_traffic_control_success{cluster=" + label(CLUSTER) + "} " +
                         str(int(traffic["controlSuccess"])))
            if traffic["diagnosticSuccess"] is not None:
                lines.append("saif_traffic_diagnostic_success{cluster=" + label(CLUSTER) + "} " +
                             str(int(traffic["diagnosticSuccess"])))
            lines.append("saif_traffic_publication_observed_seconds{cluster=" + label(CLUSTER) + "} " +
                         str(datetime.datetime.fromisoformat(traffic["observedAt"]).timestamp()))
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            # Process liveness is separate from query, storage and scrape health.
            self.send_response(200)
            self.end_headers()
        elif self.path == "/metrics":
            body = metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def main():
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    try:
        old_records = len(scan_history())
        with lock:
            state["records"] = old_records
    except (HistoryError, OSError) as exc:
        with lock:
            state["write_error"] = write_error(exc)
    server = ThreadingHTTPServer(("0.0.0.0", 9099), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    traffic_expected = traffic_selection()
    if traffic_expected:
        import traffic_publication
    while True:
        started = time.time()
        try:
            receipt = query.collect()
        except Exception as exc:
            receipt = record_failure(exc)
        try:
            count = persist(receipt)
        except (HistoryError, OSError) as exc:
            with lock:
                state.update(last=receipt, attempt=started, write_ok=0,
                             write_error=write_error(exc))
        else:
            with lock:
                state.update(last=receipt, attempt=started, records=count, write_ok=1,
                             write_error="none", last_persist=time.time())
        if traffic_expected:
            try:
                observed = traffic_publication.collect(traffic_expected, query.TOKEN,
                    "/var/run/saif/kube-api-ca/ca.crt")
            except Exception as exc:
                # Avoid leaking API response contents or credentials through metrics.
                reason = str(exc) if isinstance(exc, traffic_publication.PublicationError) else type(exc).__name__
                with lock:
                    state["traffic_error"] = reason
            else:
                with lock:
                    same = state["traffic"] and state["traffic"]["executionId"] == observed["executionId"]
                if not same:
                    try:
                        stored, count, _new = persist_traffic_once(observed)
                    except (HistoryError, OSError) as exc:
                        with lock:
                            state.update(traffic_write_ok=0, traffic_error=write_error(exc))
                    else:
                        with lock:
                            state.update(traffic=stored, traffic_write_ok=1, traffic_error="none",
                                         traffic_last_persist=datetime.datetime.fromisoformat(
                                             stored["persistedAt"]).timestamp(), records=count)
                else:
                    with lock:
                        state["traffic_error"] = "none"
        time.sleep(max(1, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
