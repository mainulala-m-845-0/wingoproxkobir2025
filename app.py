import requests
import pandas as pd
from collections import Counter
from flask import Flask, render_template, jsonify, request
import logging, json

# ==============================
# CONFIG
# ==============================
URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
PAGE_SIZE = 20

app = Flask(__name__)

# Enable logging to console
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wingo")

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
        """Fetch live WinGo game data from API"""
        try:
            params = {"pageNo": 1, "pageSize": PAGE_SIZE}
            headers = {"User-Agent": "Mozilla/5.0 (WinGoPredict/1.0)"}
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
        """Find Hot & Cold numbers by frequency"""
        freq = Counter(df["Number"])
        hot = [int(num) for num, _ in freq.most_common(3)]
        cold = [int(num) for num, _ in freq.most_common()[-3:]]
        return hot, cold

    def follow_trend(self, df):
        """Follow-trend logic for prediction"""
        last = df["BigSmall"].values[:10]
        big_count = list(last).count("Big")
        small_count = list(last).count("Small")
        if big_count >= 7: return "Small"
        if small_count >= 7: return "Big"
        if all(x == "Big" for x in last[:3]): return "Big"
        if all(x == "Small" for x in last[:3]): return "Small"
        return "Big" if big_count > small_count else "Small"

    def evaluate(self, df):
        """Evaluate last draw and update prediction"""
        if df.empty:
            return "-----", "-", "-", "-", "No Data"

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

        return latest_issue[-5:], number, result, color, outcome

predictor = WinGoPredictor()

# ==============================
# Routes
# ==============================
@app.before_request
def log_request_info():
    logger.info("➡️ Request: %s %s", request.method, request.path)

@app.after_request
def log_response_info(response):
    try:
        logger.info("⬅️ Response status: %s", response.status)
    except Exception:
        pass
    return response

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def data():
    df = predictor.fetch_data()
    if df.empty:
        return jsonify({"error": "No data fetched from API"}), 500

    hot, cold = predictor.analyze(df)
    issue, number, result, color, outcome = predictor.evaluate(df)
    acc = (predictor.total_wins / predictor.total_predictions * 100) if predictor.total_predictions > 0 else 0

    # Ensure JSON-serializable types
    hot = [int(x) for x in hot]
    cold = [int(x) for x in cold]
    last10 = df.head(10).to_dict(orient="records")
    # Normalize rows
    for row in last10:
        row["Issue"] = str(row["Issue"])
        row["Number"] = int(row["Number"])
        row["BigSmall"] = str(row["BigSmall"])
        row["Color"] = str(row["Color"])

    payload = {
        "issue": str(issue),
        "number": int(number) if isinstance(number, (int, float)) else str(number),
        "result": str(result),
        "color": str(color),
        "prediction": str(predictor.current_prediction),
        "strategy": str(predictor.strategy),
        "outcome": str(outcome),
        "wins": int(predictor.total_wins),
        "losses": int(predictor.total_losses),
        "total": int(predictor.total_predictions),
        "accuracy": round(float(acc), 1),
        "hot": hot,
        "cold": cold,
        "last10": last10
    }

    logger.info("✅ Payload ready: %s", json.dumps(payload, indent=2))
    return jsonify(payload)

# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
