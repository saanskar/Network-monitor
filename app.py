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
_speed_cache = {
    "download": None, 
    "upload": None, 
    "speed_ts": None, 
    "is_running": False
}

# Use a Session for connection pooling (drastically improves latency accuracy)
ping_session = requests.Session()
ping_session.headers.update({"User-Agent": "NetMonitor-Agent/2.1"})

SERVERS = {
    "google":     "https://www.google.com",
    "cloudflare": "https://www.cloudflare.com",
}

# ─── Latency ──────────────────────────────────────────────────────────────────
def get_latency(url):
    try:
        start = time.time()
        # Ping the full URL (e.g., "https://www.google.com")
        r = ping_session.get(url, timeout=3)
        elapsed = round((time.time() - start) * 1000, 2)
        return elapsed if r.status_code == 200 else None
    except requests.RequestException:
        return None

# ─── Speed Test (Bulletproof HTTP Approach) ───────────────────────────────────
def _run_speed_test():
    with _lock:
        if _speed_cache.get("is_running"):
            return
        _speed_cache["is_running"] = True

    dl, ul = None, None
    ts = datetime.now().strftime("%H:%M:%S")

    try:
        # 1. HTTP Download Test (5MB)
        start = time.time()
        # Strict 10-second timeout so it NEVER hangs forever
        r = requests.get("https://speed.cloudflare.com/__down?bytes=5000000", timeout=10)
        elapsed = time.time() - start
        if elapsed > 0.1:
            dl = round((5000000 * 8) / (elapsed * 1_000_000), 2)

        # 2. HTTP Upload Test (1MB)
        start = time.time()
        requests.post("https://speed.cloudflare.com/__up", data=os.urandom(1024 * 1024), timeout=10)
        elapsed = time.time() - start
        if elapsed > 0.1:
            ul = round((1024 * 1024 * 8) / (elapsed * 1_000_000), 2)

    except Exception as e:
        print(f"HTTP Speedtest Error: {e}")
        dl, ul = 0.0, 0.0  # Force values to zero so the UI shows it failed instead of hanging

    finally:
        # The 'finally' block ensures that even if the code crashes,
        # it will ALWAYS release the lock and tell the frontend the test is done.
        with _lock:
            _speed_cache["download"] = dl if dl is not None else 0.0
            _speed_cache["upload"]   = ul if ul is not None else 0.0
            _speed_cache["speed_ts"] = ts
            _speed_cache["is_running"] = False

def _speed_loop():
    while True:
        _run_speed_test()
        time.sleep(30)

threading.Thread(target=_speed_loop, daemon=True).start()

# ─── Status helpers ───────────────────────────────────────────────────────────
def get_status(avg, packet_loss, is_speed_testing):
    if packet_loss > 80:
        return "Offline"
    if is_speed_testing:
        return "Testing Bandwidth..."
    if avg is None:
        return "Unreachable"
    if avg < 50 and packet_loss < 5:
        return "Stable"
    if avg < 150:
        return "Moderate"
    return "Congested"

def get_suggestion(avg, packet_loss, is_speed_testing):
    if is_speed_testing:
        return "Latency may spike while bandwidth is measured."
    if packet_loss > 50:
        return "High packet loss — network is very unstable."
    if avg is None:
         return "No connection detected. Check your network."
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
    ts = datetime.now().strftime("%H:%M:%S")

    valid = [x for x in [g, c] if x is not None]
    avg = round(sum(valid) / len(valid), 2) if valid else None

    # Fast lock to update and copy history
    with _lock:
        latency_history.append({
            "timestamp": ts,
            "google": g,
            "cloudflare": c,
            "avg": avg
        })
        if len(latency_history) > 30:
            latency_history.pop(0)
            
        history_copy = list(latency_history)
        speed_data = dict(_speed_cache)

    # Calculate Rolling Packet Loss (Outside of lock)
    total_pings = len(history_copy) * 2
    failed_pings = sum(1 for entry in history_copy for val in [entry["google"], entry["cloudflare"]] if val is None)
    rolling_packet_loss = round((failed_pings / total_pings) * 100) if total_pings > 0 else 0

    # Calculate Jitter (Outside of lock)
    avgs = [h["avg"] for h in history_copy if h["avg"] is not None]
    if len(avgs) >= 2:
        diffs = [abs(avgs[i] - avgs[i - 1]) for i in range(1, len(avgs))]
        jitter = round(sum(diffs) / len(diffs), 2)
    else:
        jitter = 0.0

    return jsonify({
        "google": g,
        "cloudflare": c,
        "avg": avg,
        "jitter": jitter,
        "packet_loss": rolling_packet_loss,
        "download": speed_data["download"],
        "upload": speed_data["upload"],
        "speed_ts": speed_data["speed_ts"],
        "is_speed_testing": speed_data["is_running"],
        "status": get_status(avg, rolling_packet_loss, speed_data["is_running"]),
        "suggestion": get_suggestion(avg, rolling_packet_loss, speed_data["is_running"]),
        "history": history_copy,
        "timestamp": ts,
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
            entry["google"] if entry["google"] else "Timeout",
            entry["cloudflare"] if entry["cloudflare"] else "Timeout",
            entry["avg"] if entry["avg"] else "Timeout",
            dl if i == 0 else "",
            ul if i == 0 else "",
        ])

    filename = f"network_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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