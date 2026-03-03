import asyncio
import logging
from threading import Thread
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

from graph.manager_graph import ManagerGraph


logger = logging.getLogger(__name__)

app = Flask(__name__)
graph = ManagerGraph()

DEFAULT_QUERY = "What are my top 3 AWS services by spend and forecast for next month?"


def run_async(coro_factory):
    """
    Run async code from Flask sync routes.
    """
    try:
        return asyncio.run(coro_factory())
    except RuntimeError:
        result: Dict[str, Any] = {}
        error: Dict[str, Exception] = {}

        def _runner() -> None:
            try:
                result["value"] = asyncio.run(coro_factory())
            except Exception as exc:
                error["value"] = exc

        thread = Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()

        if "value" in error:
            raise error["value"]
        return result.get("value")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _currency_2dp(value: Any) -> float:
    number = round(_safe_float(value), 2)
    if abs(number) < 0.005:
        return 0.0
    return number


def _percentage_2dp(value: Any) -> float:
    number = round(_safe_float(value), 2)
    if abs(number) < 0.005:
        return 0.0
    return number


def _currency_display(value: Any) -> str:
    number = _safe_float(value)
    if number == 0:
        return "$0.00"
    if abs(number) < 0.01:
        return "-< $0.01" if number < 0 else "< $0.01"
    if number < 0:
        return f"-${abs(number):.2f}"
    return f"${number:.2f}"


def normalize_output(state: Dict[str, Any]) -> Dict[str, Any]:
    analysis = state.get("analysis_result")
    if isinstance(analysis, dict) and "error" not in analysis:
        cost_data = state.get("cost_data", {})
        forecast_data = state.get("forecast_data", {})
        if not isinstance(cost_data, dict):
            cost_data = {}
        if not isinstance(forecast_data, dict):
            forecast_data = {}

        raw_cost_data = analysis.get("raw_cost_data", {})
        if not isinstance(raw_cost_data, dict):
            raw_cost_data = {}

        net_spend = _safe_float(analysis.get("total_monthly_spend", 0.0))
        gross_spend = _safe_float(
            raw_cost_data.get("gross_spend", analysis.get("gross_spend", 0.0))
        )
        credits_refunds = _safe_float(
            raw_cost_data.get("credits_refunds", analysis.get("credits_refunds", 0.0))
        )

        # Fallback derivation in case upstream values are missing.
        if gross_spend == 0.0 and credits_refunds > 0.0:
            gross_spend = net_spend + credits_refunds
        elif credits_refunds == 0.0 and gross_spend > net_spend:
            credits_refunds = gross_spend - net_spend

        top_services = analysis.get("top_services", [])
        if not isinstance(top_services, list):
            top_services = []
        normalized_top_services = []
        for svc in top_services:
            if not isinstance(svc, dict):
                continue
            raw_spend = _safe_float(svc.get("spend", 0.0))
            normalized_top_services.append(
                {
                    "service": svc.get("service", "Unknown"),
                    "spend": _currency_2dp(raw_spend),
                    "display_spend": _currency_display(raw_spend),
                }
            )

        month_to_date_spend = _currency_2dp(cost_data.get("month_to_date_spend", 0.0))
        same_period_last_month_spend = _currency_2dp(
            cost_data.get("same_period_last_month_spend", 0.0)
        )
        month_to_date_change_pct = _percentage_2dp(
            cost_data.get("month_to_date_change_pct", 0.0)
        )

        return {
            "ok": True,
            "total_monthly_spend": _currency_2dp(net_spend),
            "gross_spend": _currency_2dp(gross_spend),
            "credits_refunds": _currency_2dp(abs(credits_refunds)),
            "top_services": normalized_top_services,
            "forecast_next_month": _currency_2dp(analysis.get("forecast_next_month", 0.0)),
            "summary": analysis.get("summary", ""),
            "errors": state.get("errors", []),
            "billing_month": cost_data.get("billing_month", ""),
            "billing_period": cost_data.get("billing_period", ""),
            "forecast_month": forecast_data.get("forecast_month", ""),
            "forecast_period": forecast_data.get("forecast_period", ""),
            "month_to_date_spend": month_to_date_spend,
            "month_to_date_period": cost_data.get("month_to_date_period", ""),
            "same_period_last_month_spend": same_period_last_month_spend,
            "same_period_last_month_period": cost_data.get(
                "same_period_last_month_period", ""
            ),
            "month_to_date_change_pct": month_to_date_change_pct,
            "display_gross_spend": _currency_display(gross_spend),
            "display_credits_refunds": _currency_display(-abs(credits_refunds)),
            "display_total_monthly_spend": _currency_display(net_spend),
            "display_forecast_next_month": _currency_display(
                analysis.get("forecast_next_month", 0.0)
            ),
            "display_month_to_date_spend": _currency_display(
                cost_data.get("month_to_date_spend", 0.0)
            ),
            "display_same_period_last_month_spend": _currency_display(
                cost_data.get("same_period_last_month_spend", 0.0)
            ),
        }

    error_msg = (
        analysis.get("error")
        if isinstance(analysis, dict)
        else "Unknown analysis error"
    )
    return {
        "ok": False,
        "error": error_msg,
        "summary": analysis.get("summary", "") if isinstance(analysis, dict) else "",
        "errors": state.get("errors", []),
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        query=DEFAULT_QUERY,
        result=None,
    )


@app.post("/analyze")
def analyze():
    user_query = (request.form.get("query") or "").strip() or DEFAULT_QUERY

    try:
        state = run_async(lambda: graph.invoke(user_query))
        result = normalize_output(state)
    except Exception as exc:
        logger.exception("Analysis failed")
        result = {
            "ok": False,
            "error": str(exc),
            "summary": "The request failed before analysis could complete.",
            "errors": [],
        }

    if request.is_json:
        return jsonify(result)
    return render_template(
        "index.html",
        query=user_query,
        result=result,
    )


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    user_query = (payload.get("query") or "").strip() or DEFAULT_QUERY

    try:
        state = run_async(lambda: graph.invoke(user_query))
        result = normalize_output(state)
        return jsonify(result), 200 if result.get("ok") else 500
    except Exception as exc:
        logger.exception("API analysis failed")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "summary": "The request failed before analysis could complete.",
                    "errors": [],
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

