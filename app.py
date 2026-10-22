from flask import Flask, render_template, request, jsonify
import os
from weinstein_engine import analyze_ticker, scan_market, billion_companies, FinvizError

app = Flask(__name__)

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "data_sources": ["Nasdaq Stock Screener", "Yahoo Chart API", "Stooq"]})

@app.get("/api/analyze")
def analyze():
    ticker = (request.args.get("ticker") or "").upper().strip()
    if not ticker:
        return jsonify({"ok":False,"error":"اكتب رمز السهم."}), 400
    try:
        cap = float(request.args.get("min_cap_b", 50))
        return jsonify({"ok":True,"data":analyze_ticker(ticker, cap)})
    except FinvizError as e:
        return jsonify({"ok":False,"error":str(e)}), 502
    except Exception as e:
        return jsonify({"ok":False,"error":f"تعذر التحليل: {str(e)[:240]}"}), 500


@app.get("/api/billion-companies")
def billion_directory():
    try:
        min_cap_b = float(request.args.get("min_cap_b", 10))
        return jsonify({"ok": True, "data": billion_companies(min_cap_b)})
    except FinvizError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"تعذر تحميل قائمة الشركات: {str(e)[:240]}"}), 500

@app.get("/api/scan")
def scan():
    try:
        cap = float(request.args.get("min_cap_b", 50))
        score = float(request.args.get("min_score", 70))
        n = int(request.args.get("max_names", 40))
        return jsonify({"ok":True,"data":scan_market(cap, score, n)})
    except FinvizError as e:
        return jsonify({"ok":False,"error":str(e)}), 502
    except Exception as e:
        return jsonify({"ok":False,"error":f"تعذر المسح: {str(e)[:240]}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
