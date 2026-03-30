import csv
import io
import os
import time
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify, render_template, send_file

app = Flask(__name__)

# ─── Thread-safe shared state ─────────────────────────────────────────────────
_lock = threading.Lock()

latency_history = []

_speed_cache = {"download": None, "upload": None, "speed_ts": None}

# ─── Ping targets ─────────────────────────────────────────────────────────────
SERVERS = {
    "google":     "https://www.google.com",
    "cloudflare": "https://www.cloudflare.com",
}

# ─── Latency ──────────────────────────────────────────────────────────────────
def get_latency(url):
    try:
        start = time.time()
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        elapsed = round((time.time() - start) * 1000, 2)
        return elapsed if r.status_code == 200 else None
    except Exception:
        return None


# ─── Speed Test (Cloudflare speed-test endpoints) ────────────────────────────
def measure_download():
    """Download 10 MB from Cloudflare, return Mbps."""
    url = "https://speed.cloudflare.com/__down?bytes=10000000"
    try:
        start = time.time()
        r = requests.get(url, timeout=30, stream=True)
        total_bytes = 0
        for chunk in r.iter_content(chunk_size=65536):
            total_bytes += len(chunk)
        elapsed = time.time() - start
        if elapsed < 0.1 or total_bytes == 0:
            return None
        return round((total_bytes * 8) / (elapsed * 1_000_000), 2)
    except Exception:
        return None


def measure_upload():
    """Upload 4 MB to Cloudflare, return Mbps."""
    url  = "https://speed.cloudflare.com/__up"
    data = os.urandom(4 * 1024 * 1024)
    try:
        start = time.time()
        requests.post(url, data=data, timeout=30)
        elapsed = time.time() - start
        if elapsed < 0.1:
            return None
        return round((len(data) * 8) / (elapsed * 1_000_000), 2)
    except Exception:
        return None


def _run_speed_test():
    dl = measure_download()
    ul = measure_upload()
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _speed_cache["download"] = dl
        _speed_cache["upload"]   = ul
        _speed_cache["speed_ts"] = ts


def _speed_loop():
    while True:
        _run_speed_test()
        time.sleep(20)


threading.Thread(target=_speed_loop, daemon=True).start()


# ─── Status helpers ───────────────────────────────────────────────────────────
def get_status(avg, packet_loss):
    if packet_loss == 100 or avg is None:
        return "Offline"
    if avg < 50 and packet_loss == 0:
        return "Stable"
    if avg < 150:
        return "Moderate"
    return "Congested"


def get_suggestion(avg, packet_loss):
    if packet_loss == 100 or avg is None:
        return "No connection detected. Check your network."
    if packet_loss > 50:
        return "High packet loss — network is very unstable."
    if avg < 50 and packet_loss == 0:
        return "Network is running optimally."
    if avg < 150:
        return "Slight congestion — performance may vary."
    return "High congestion detected — consider switching networks."


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/data")
def data():
    g = get_latency(SERVERS["google"])
    c = get_latency(SERVERS["cloudflare"])

    failed      = sum(1 for x in [g, c] if x is None)
    packet_loss = round((failed / 2) * 100)

    valid = [x for x in [g, c] if x is not None]
    avg   = round(sum(valid) / len(valid), 2) if valid else None

    ts = datetime.now().strftime("%H:%M:%S")

    with _lock:
        latency_history.append({
            "timestamp":  ts,
            "google":     g if g is not None else 0,
            "cloudflare": c if c is not None else 0,
            "avg":        avg if avg is not None else 0,
        })
        if len(latency_history) > 30:
            latency_history.pop(0)

        avgs = [h["avg"] for h in latency_history if h["avg"] > 0]
        if len(avgs) >= 2:
            diffs  = [abs(avgs[i] - avgs[i - 1]) for i in range(1, len(avgs))]
            jitter = round(sum(diffs) / len(diffs), 2)
        else:
            jitter = 0.0

        history_copy = list(latency_history)
        dl           = _speed_cache["download"]
        ul           = _speed_cache["upload"]
        speed_ts     = _speed_cache["speed_ts"]

    return jsonify({
        "google":      g,
        "cloudflare":  c,
        "avg":         avg,
        "jitter":      jitter,
        "packet_loss": packet_loss,
        "download":    dl,
        "upload":      ul,
        "speed_ts":    speed_ts,
        "status":      get_status(avg, packet_loss),
        "suggestion":  get_suggestion(avg, packet_loss),
        "history":     history_copy,
        "timestamp":   ts,
    })


@app.route("/download")
def download_report():
    with _lock:
        history_copy = list(latency_history)
        dl = _speed_cache["download"]
        ul = _speed_cache["upload"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp", "Google (ms)", "Cloudflare (ms)",
        "Average (ms)", "Download (Mbps)", "Upload (Mbps)"
    ])
    for i, entry in enumerate(history_copy):
        writer.writerow([
            entry["timestamp"],
            entry["google"],
            entry["cloudflare"],
            entry["avg"],
            dl if i == 0 else "",
            ul if i == 0 else "",
        ])

    filename  = f"network_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    bytes_out = io.BytesIO(output.getvalue().encode("utf-8"))
    bytes_out.seek(0)

    return send_file(
        bytes_out,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))