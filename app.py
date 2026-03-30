import csv
import time
import requests
import os
from flask import Flask, jsonify, render_template, send_file

app = Flask(__name__)

latency_history = []

# ---------------- LATENCY FUNCTION ----------------
def get_latency(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        start = time.time()
        response = requests.get(url, headers=headers, timeout=3)
        end = time.time()

        if response.status_code == 200:
            return round((end - start) * 1000, 2)
        else:
            return None
    except:
        return None

# ---------------- STATUS ----------------
def get_status(latency):
    if latency is None:
        return "Checking..."
    elif latency < 50:
        return "Stable 🟢"
    elif latency < 100:
        return "Moderate 🟡"
    else:
        return "High 🔴"

# ---------------- SUGGESTION ----------------
def get_suggestion(latency):
    if latency is None:
        return "Collecting data..."
    elif latency < 50:
        return "Network is stable 👍"
    elif latency < 100:
        return "Network moderate ⚠️"
    else:
        return "High congestion 🚨 Switch network"

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/data")
def data():
    global latency_history

    g = get_latency("https://example.com")
    c = get_latency("https://httpbin.org/get")

    # fallback if None
    if g is None:
        g = 0
    if c is None:
        c = 0

    avg = round((g + c) / 2, 2)

    # update graph
    latency_history.append(avg)
    if len(latency_history) > 20:
        latency_history.pop(0)

    return jsonify({
        "google": g,
        "cloudflare": c,
        "avg": avg,
        "download": None,
        "upload": None,
        "status": get_status(avg),
        "suggestion": get_suggestion(avg),
        "history": latency_history
    })

@app.route("/download")
def download():
    with open("report.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Latency(ms)"])
        for val in latency_history:
            writer.writerow([val])

    return send_file("report.csv", as_attachment=True)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))