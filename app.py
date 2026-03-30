import csv
import time
from flask import Flask, jsonify, render_template, send_file
from ping3 import ping
import speedtest

app = Flask(__name__)

latency_history = []

last_speed_time = 0
cached_download = None
cached_upload = None


def get_latency(server):
    try:
        latency = ping(server, timeout=1)
        if latency is None:
            return None
        return round(latency * 1000, 2)
    except:
        return None


def get_speed():
    global last_speed_time, cached_download, cached_upload

    if time.time() - last_speed_time > 25:
        try:
            st = speedtest.Speedtest()
            cached_download = round(st.download()/1_000_000, 2)
            cached_upload = round(st.upload()/1_000_000, 2)
            last_speed_time = time.time()
        except:
            pass

    return cached_download, cached_upload


def get_status(latency):
    if latency is None:
        return "Error ❌"
    elif latency < 50:
        return "Stable 🟢"
    elif latency < 100:
        return "Moderate 🟡"
    else:
        return "High 🔴"


def get_suggestion(latency):
    if latency is None:
        return "No data available"
    elif latency < 50:
        return "Network is stable 👍"
    elif latency < 100:
        return "Network is moderate ⚠️"
    else:
        return "High congestion 🚨 Switch network"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/data")
def data():
    global latency_history

    g = get_latency("8.8.8.8")
    c = get_latency("1.1.1.1")

    avg = None
    if g is not None and c is not None:
        avg = round((g + c) / 2, 2)

    if avg is not None:
        latency_history.append(avg)
        if len(latency_history) > 20:
            latency_history.pop(0)

    download, upload = get_speed()

    return jsonify({
        "google": g,
        "cloudflare": c,
        "avg": avg,
        "download": download,
        "upload": upload,
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

    if __name__ == "__main__":
        app.run(host="0.0.0.0", port=10000)