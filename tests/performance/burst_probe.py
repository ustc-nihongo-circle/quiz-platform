"""Sustained synchronized quiz rounds against a labelled, loopback-only synthetic lab."""
import argparse
import concurrent.futures
import http.client
import json
import math
import socket
import ssl
import time
from http.cookies import SimpleCookie
from pathlib import Path


class LocalTLS(http.client.HTTPSConnection):
    def connect(self):
        self.sock = self._context.wrap_socket(
            socket.create_connection(("127.0.0.1", self.port), timeout=20),
            server_hostname=self.host,
        )


class Client:
    def __init__(self, fixture, index):
        self.fixture, self.index = fixture, index
        self.http = LocalTLS(fixture["host"], fixture["port"], timeout=20,
                             context=ssl.create_default_context())
        self.cookies = {}
        self.attempt = None

    def request(self, method, path, payload=None, allowed=(200,)):
        origin = f"https://{self.fixture['host']}:{self.fixture['port']}"
        headers = {"Host": f"{self.fixture['host']}:{self.fixture['port']}", "Origin": origin,
                   "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        if payload is not None:
            headers.update({"Content-Type": "application/json",
                            "X-CSRFToken": self.cookies.get("csrftoken", "")})
        begin = time.perf_counter()
        try:
            self.http.request(method, path,
                              body=json.dumps(payload).encode() if payload is not None else None,
                              headers=headers)
            response = self.http.getresponse()
            raw = response.read()
            if response.getheader("X-Quiz-Synthetic-Lab") != self.fixture["lab_id"]:
                raise RuntimeError("Target is not the approved synthetic lab")
            for key, value in response.getheaders():
                if key.lower() == "set-cookie":
                    cookie = SimpleCookie()
                    cookie.load(value)
                    self.cookies.update({k: v.value for k, v in cookie.items()})
            data = json.loads(raw) if "application/json" in response.getheader(
                "Content-Type", "") else None
            return {"seconds": time.perf_counter() - begin, "status": response.status,
                    "ok": response.status in allowed,
                    "error_code": (data or {}).get("error", {}).get("code")}, data
        except (OSError, http.client.HTTPException) as error:
            self.http.close()
            return {"seconds": time.perf_counter() - begin, "status": 0, "ok": False,
                    "error_type": type(error).__name__}, None


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def measure(fixture, workers, duration, period, think):
    clients = [Client(fixture, i) for i in range(workers)]
    metrics = {name: [] for name in ("read", "register", "start", "image", "submit", "retry")}
    rounds = 0
    scores_ok = True
    begin = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        def register(client):
            first, _ = client.request("GET", "/api/v1/activity")
            if not first["ok"]:
                return first
            result, _ = client.request("POST", "/api/v1/participant-session", {
                "display_name": "Synthetic capacity participant",
                "identifier": f"SYNTHETIC-CAPACITY-{client.index:04d}",
                "contact": f"capacity{client.index}@example.invalid",
            }, allowed=(200, 201))
            return result

        metrics["register"].extend(pool.map(register, clients))
        if any(not r["ok"] for r in metrics["register"]):
            print(json.dumps({"registration_failures": [r for r in metrics["register"]
                                                        if not r["ok"]][:5]}), flush=True)
        while time.monotonic() - begin < duration and all(r["ok"] for r in metrics["register"]):
            round_started = time.monotonic()

            def start(client, round_index=rounds):
                result, data = client.request("POST", "/api/v1/attempts", {
                    "category_code": fixture["categories"][(round_index + client.index) % 7],
                }, allowed=(200, 201))
                client.attempt = data.get("attempt") if result["ok"] and data else None
                return result

            metrics["start"].extend(pool.map(start, clients))
            if any(not r["ok"] for r in metrics["start"]):
                print(json.dumps({"start_failures": [r for r in metrics["start"]
                                                     if not r["ok"]][:5]}), flush=True)

            def images(client):
                if not client.attempt:
                    return []
                return [client.request("GET", q["image_url"])[0]
                        for q in client.attempt["questions"] if q.get("image_url")]

            for group in pool.map(images, clients):
                metrics["image"].extend(group)
            time.sleep(think)

            def submit(client):
                if not client.attempt:
                    return None
                answers = [{"item_id": q["id"],
                            "answer": "テスト" if q["type"] == "fill_blank" else "A"}
                           for q in client.attempt["questions"]]
                return client.request("PUT", f"/api/v1/attempts/{client.attempt['id']}/submission",
                                      {"answers": answers})

            for row in pool.map(submit, clients):
                if row is not None:
                    result, data = row
                    metrics["submit"].append(result)
                    scores_ok = scores_ok and bool(
                        data and data.get("attempt", {}).get("score") == 15)
            for row in pool.map(submit, clients):
                if row is not None:
                    result, data = row
                    metrics["retry"].append(result)
                    scores_ok = scores_ok and bool(
                        data and data.get("attempt", {}).get("score") == 15)
            metrics["read"].extend(pool.map(
                lambda c: c.request("GET", "/api/v1/attempts/history?page=1")[0], clients))
            rounds += 1
            failures = sum(not r["ok"] for rows in metrics.values() for r in rows)
            print(json.dumps({"workers": workers, "round": rounds, "failures": failures,
                              "scores_ok": scores_ok}), flush=True)
            # Rejected admissions do not count as successfully supported users.
            if failures or not scores_ok:
                break
            latency_failed = any(
                percentile([r["seconds"] for r in metrics[k]], .95) > limit
                for k, limit in (("start", 2), ("submit", 3), ("read", 2))
            )
            if latency_failed:
                break
            remaining = min(period - (time.monotonic() - round_started),
                            duration - (time.monotonic() - begin))
            if remaining > 0:
                time.sleep(remaining)
    for client in clients:
        client.http.close()
    elapsed = time.monotonic() - begin
    summaries = {}
    for name, rows in metrics.items():
        values = [r["seconds"] for r in rows]
        summaries[name] = {"requests": len(rows), "failures": sum(not r["ok"] for r in rows),
                           "rate_limited": sum(r["status"] == 429 for r in rows),
                           "p95": percentile(values, .95), "p99": percentile(values, .99)}
    passed = elapsed >= duration and scores_ok and all(
        not row["failures"] for row in summaries.values()) and all(
        summaries[name]["p95"] is not None and summaries[name]["p95"] <= limit
        for name, limit in (("start", 2), ("submit", 3), ("read", 2)))
    return {"workers": workers, "elapsed_seconds": elapsed, "rounds": rounds,
            "passed": passed, "scores_ok": scores_ok, "metrics": summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--levels", default="50,100,150,200")
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--period", type=int, default=75)
    parser.add_argument("--think", type=int, default=20)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    assert fixture["syntheticOnly"] and fixture["connect_address"] == "127.0.0.1"
    levels = [int(value) for value in args.levels.split(",")]
    assert all(1 <= n <= 200 for n in levels)
    results = {"fixture": fixture, "levels": [], "period": args.period,
               "think_seconds": args.think, "required_duration": args.duration}
    for level in levels:
        result = measure(fixture, level, args.duration, args.period, args.think)
        results["levels"].append(result)
        args.output.write_text(json.dumps(results, indent=2))
        if not result["passed"]:
            break


if __name__ == "__main__":
    main()
