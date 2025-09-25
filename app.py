import requests
import pandas as pd
from collections import Counter
from flask import Flask, render_template, jsonify, send_file, send_from_directory
import logging, sqlite3, csv, os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

# ==============================
# CONFIG
# ==============================
URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
PAGE_SIZE = 20
DB_FILE = "results.db"
ARCHIVE_FOLDER = "archives"

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wingo")

# ==============================
# INIT DB
# ==============================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT,
        number INTEGER,
        bigsmall TEXT,
        color TEXT,
        prediction TEXT,
        strategy TEXT,
        outcome TEXT,
        timestamp TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

def save_prediction(issue, number, bigsmall, color, prediction, strategy, outcome):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO predictions(issue, number, bigsmall, color, prediction, strategy, outcome, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (issue, number, bigsmall, color, prediction, strategy, outcome, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

# ==============================
# Archive CSV
# ==============================
def export_history_csv(filename):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT * FROM predictions ORDER BY id ASC")
    rows = cur.fetchall()
    headers = [d[0] for d in cur.description]
    conn.close()

    if not os.path.exists(ARCHIVE_FOLDER):
        os.makedirs(ARCHIVE_FOLDER)

    filepath = os.path.join(ARCHIVE_FOLDER, filename)
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    logger.info(f"📁 History exported to {filepath}")

# ==============================
# Predictor Logic
# ==============================
class WinGoPredictor:
    def __init__(self):
        self.loss_streak = 0
        self.total_predictions = 0
        self.total_wins = 0
        self.total_losses = 0
        self.last_issue = None
        self.current_prediction = None
        self.strategy = "Follow-Trend"

    def fetch_data(self):
        try:
            params = {"pageNo": 1, "pageSize": PAGE_SIZE}
            headers = {"User-Agent": "Mozilla/5.0 WingPredictor/1.0"}
            resp = requests.get(URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if "data" not in data or "list" not in data["data"]:
                logger.warning("⚠️ Bad API Response: %s", data)
                return pd.DataFrame([])
            return pd.DataFrame([{
                "Issue": str(d["issueNumber"]),
                "Number": int(d["number"]),
                "Color": str(d["color"]),
                "BigSmall": "Big" if int(d["number"]) >= 5 else "Small"
            } for d in data["data"]["list"]])
        except Exception as e:
            logger.error("❌ Error fetching API: %s", e)
            return pd.DataFrame([])

    def analyze(self, df):
        freq = Counter(df["Number"])
        hot = [int(num) for num, _ in freq.most_common(3)]
        return hot

    def follow_trend(self, df):
        last = df["BigSmall"].values[:10]
        big_count = list(last).count("Big")
        small_count = list(last).count("Small")
        if big_count >= 7: return "Small"
        if small_count >= 7: return "Big"
        if all(x == "Big" for x in last[:3]): return "Big"
        if all(x == "Small" for x in last[:3]): return "Small"
        return "Big" if big_count > small_count else "Small"

    def evaluate(self, df):
        if df.empty:
            return "-----", "-", "-", "-", "No Data", []

        latest_issue = str(df.iloc[0]["Issue"])
        result = str(df.iloc[0]["BigSmall"])    # Actual
        number = int(df.iloc[0]["Number"])      # Actual number
        color = str(df.iloc[0]["Color"])        # Actual color
        outcome = ""

        if self.last_issue != latest_issue:
            if self.current_prediction is not None:
                self.total_predictions += 1
                if self.current_prediction == result:
                    self.total_wins += 1
                    self.loss_streak = 0
                    outcome = "WIN ✅"
                else:
                    self.total_losses += 1
                    self.loss_streak += 1
                    outcome = "LOSS ❌"
            else:
                outcome = "First Run"

            self.last_issue = latest_issue
            base = self.follow_trend(df)
            if self.loss_streak >= 3:
                self.current_prediction = "Big" if base == "Small" else "Small"
                self.strategy = f"Switched (losses={self.loss_streak})"
            else:
                self.current_prediction = base
                self.strategy = "Follow-Trend"

            hot = self.analyze(df)
            twoNums = ", ".join(map(str, hot[:2])) if hot else "?"

            # ✅ Save prediction separately, not mixing with actual result
            prediction_display = f"{self.current_prediction} ({twoNums})"

            save_prediction(
                latest_issue,
                number,             # Actual
                result,             # Actual Big/Small
                color,              # Actual color
                prediction_display, # Our prediction
                self.strategy,
                outcome
            )

        hot = self.analyze(df)
        return latest_issue[-5:], number, result, color, outcome, hot

predictor = WinGoPredictor()

# ==============================
# Daily Reset Job
# ==============================
def reset_daily():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    export_history_csv(f"history_{today}.csv")

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM predictions")
    conn.commit()
    conn.close()

    predictor.loss_streak = 0
    predictor.total_predictions = 0
    predictor.total_wins = 0
    predictor.total_losses = 0
    predictor.last_issue = None
    predictor.current_prediction = None
    predictor.strategy = "Follow-Trend"

    logger.info("🔄 Daily reset complete (with archive saved)")

scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily, 'cron', hour=0, minute=0)
scheduler.start()

# ==============================
# Routes
# ==============================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def data():
    df = predictor.fetch_data()
    if df.empty:
        return jsonify({"error": "No data fetched from API"}), 500

    issue, number, result, color, outcome, hot = predictor.evaluate(df)
    acc = (predictor.total_wins / predictor.total_predictions * 100) if predictor.total_predictions > 0 else 0

    try:
        next_issue = str(int(predictor.last_issue) + 1)
    except:
        next_issue = "N/A"

    last10 = []
    for _, row in df.head(10).iterrows():
        last10.append({
            "Issue": str(row["Issue"]),
            "Number": int(row["Number"]),
            "BigSmall": str(row["BigSmall"]),
            "Color": str(row["Color"]),
            "Outcome": "WIN" if predictor.current_prediction == row["BigSmall"] else "LOSS"
        })

    return jsonify({
        "issue": issue,
        "next_issue": next_issue,
        "number": number,
        "result": result,
        "color": color,
        "prediction": predictor.current_prediction,
        "strategy": predictor.strategy,
        "outcome": outcome,
        "wins": predictor.total_wins,
        "losses": predictor.total_losses,
        "total": predictor.total_predictions,
        "accuracy": round(float(acc), 1),
        "hot": hot,
        "last10": last10
    })

@app.route("/history/view")
def history_view():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM predictions ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM predictions WHERE outcome LIKE 'WIN%'")
    wins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM predictions WHERE outcome LIKE 'LOSS%'")
    losses = cur.fetchone()[0]

    total = wins + losses
    accuracy = round((wins / total) * 100, 1) if total > 0 else 0
    conn.close()

    return render_template("history.html", rows=rows, wins=wins, losses=losses, accuracy=accuracy)

@app.route("/history/export")
def history_export():
    filename = "prediction_history.csv"
    export_history_csv(filename)
    return send_file(os.path.join(ARCHIVE_FOLDER, filename), mimetype="text/csv", as_attachment=True)

@app.route("/archives")
def list_archives():
    if not os.path.exists(ARCHIVE_FOLDER):
        return "<h3>No archives found yet.</h3>"
    files = os.listdir(ARCHIVE_FOLDER)
    links = "<h2>📂 Archived CSVs</h2><ul>"
    for f in sorted(files):
        links += f'<li><a href="/archives/{f}">{f}</a></li>'
    links += "</ul><a href='/history/view'>⬅️ Back</a>"
    return links

@app.route("/archives/<path:filename>")
def download_archive(filename):
    return send_from_directory(ARCHIVE_FOLDER, filename, as_attachment=True)

# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
