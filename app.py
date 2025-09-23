import requests
import pandas as pd
from collections import Counter
from flask import Flask, render_template, jsonify, send_file
import logging, sqlite3, csv, os
from datetime import datetime

# ==============================
# CONFIG
# ==============================
URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
PAGE_SIZE = 20
DB_FILE = "results.db"

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
        result = str(df.iloc[0]["BigSmall"])
        number = int(df.iloc[0]["Number"])
        color = str(df.iloc[0]["Color"])
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
            if self.loss_streak >= 2:
                self.current_prediction = "Big" if base == "Small" else "Small"
                self.strategy = f"Switched (losses={self.loss_streak})"
            else:
                self.current_prediction = base
                self.strategy = "Follow-Trend"

            hot = self.analyze(df)
            twoNums = ", ".join(map(str, hot[:2])) if hot else str(number)

            save_prediction(
                latest_issue,
                number,
                result,
                color,
                f"{self.current_prediction} ({twoNums})",
                self.strategy,
                outcome
            )

        hot = self.analyze(df)
        return latest_issue[-5:], number, result, color, outcome, hot

predictor = WinGoPredictor()

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

    return jsonify({
        "issue": str(issue),
        "number": int(number),
        "result": str(result),
        "color": str(color),
        "prediction": str(predictor.current_prediction),
        "outcome": str(outcome),
        "wins": int(predictor.total_wins),
        "losses": int(predictor.total_losses),
        "total": int(predictor.total_predictions),
        "accuracy": round(float(acc), 1),
        "hot": hot
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

# ✅ CSV Export Route
@app.route("/history/export")
def history_export():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT * FROM predictions ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    filename = "prediction_history.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([d[0] for d in cur.description])  # headers
        writer.writerows(rows)

    return send_file(filename, mimetype="text/csv", as_attachment=True)

# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
