import requests
import pandas as pd
from collections import Counter
from flask import Flask, render_template, jsonify

# ==============================
# CONFIG
# ==============================
URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
PAGE_SIZE = 20

app = Flask(__name__)

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
        params = {"pageNo": 1, "pageSize": PAGE_SIZE}
        resp = requests.get(URL, params=params).json()
        return pd.DataFrame([{
            "Issue": d['issueNumber'],
            "Number": int(d['number']),
            "Color": d['color'],
            "BigSmall": "Big" if int(d['number']) >= 5 else "Small"
        } for d in resp['data']['list']])

    def analyze(self, df):
        freq = Counter(df["Number"])
        hot = [num for num, _ in freq.most_common(3)]
        cold = [num for num, _ in freq.most_common()[-3:]]
        return hot, cold

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
        latest_issue = df.iloc[0]["Issue"]
        result = df.iloc[0]["BigSmall"]
        number = df.iloc[0]["Number"]
        color = df.iloc[0]["Color"]
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
                # Switch after 2 losses
                self.current_prediction = "Big" if base == "Small" else "Small"
                self.strategy = f"Switched (losses={self.loss_streak})"
            else:
                self.current_prediction = base
                self.strategy = "Follow-Trend"

        return latest_issue[-5:], number, result, color, outcome

predictor = WinGoPredictor()

# ==============================
# Flask Routes
# ==============================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def data():
    df = predictor.fetch_data()
    hot, cold = predictor.analyze(df)
    issue, number, result, color, outcome = predictor.evaluate(df)

    acc = (predictor.total_wins / predictor.total_predictions * 100) if predictor.total_predictions > 0 else 0

    return jsonify({
        "issue": issue,
        "number": number,
        "result": result,
        "color": color,
        "prediction": predictor.current_prediction,
        "strategy": predictor.strategy,
        "outcome": outcome,
        "wins": predictor.total_wins,
        "losses": predictor.total_losses,
        "total": predictor.total_predictions,
        "accuracy": round(acc, 1),
        "hot": hot,
        "cold": cold,
        "last10": df.head(10).to_dict(orient="records")
    })

# ==============================
if __name__ == "__main__":
    app.run(debug=True, port=5000)
