import asyncio
import logging
from threading import Thread
from typing import Any, Dict, List

from flask import Flask, g, jsonify, render_template, request, session

from config import settings
from graph.manager_graph import ManagerGraph
from graph.remediation_graph import RemediationGraph


logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = settings.flask_secret_key
graph = ManagerGraph()
remediation_graph = RemediationGraph()


@app.before_request
def set_aws_config_from_session():
    """Set request-scoped AWS region/role from session so MCP client uses them (SaaS: user's role = access their account)."""
    g.aws_region = session.get("aws_region") or settings.aws_region
    g.aws_role_arn = session.get("aws_role_arn") or getattr(settings, "aws_role_arn", "") or ""
    g.aws_profile = session.get("aws_profile") or settings.aws_profile or ""
    g.aws_external_id = session.get("aws_external_id") or getattr(settings, "aws_external_id", "") or ""


def _require_aws_connection():
    """Return None if user has connected (role ARN or local profile); else return error dict for JSON/HTML response."""
    role_arn = (getattr(g, "aws_role_arn", None) or "").strip()
    profile = (getattr(g, "aws_profile", None) or "").strip()
    if role_arn or profile:
        return None
    return {
        "ok": False,
        "error": "Connect your AWS account",
        "summary": "Enter your IAM Role ARN and region above, then click Save AWS settings. We use that role to access your account's cost data. If you use a local profile instead, set AWS_PROFILE in the server environment.",
    }

DEFAULT_QUERY = "What are my top 5 AWS services by spend and forecast for next month?"
ANALYSIS_TYPES = {
    "ec2",
    "s3",
    "rds",
    "lambda",
    "ebs",
    "networking",
    "full_account",
}


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


def _normalize_analysis_type(value: Any) -> str:
    normalized = str(value or "full_account").strip().lower()
    if normalized not in ANALYSIS_TYPES:
        return "full_account"
    return normalized


def build_remediation_items_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive remediation input items from the latest cost analysis state.

    For now, we use the top services cost breakdown as the primary signal.
    """
    cost_data = state.get("cost_data") or {}
    if not isinstance(cost_data, dict):
        cost_data = {}

    top_services = cost_data.get("top_services") or []
    if not isinstance(top_services, list):
        top_services = []

    region = getattr(g, "aws_region", None) or settings.aws_region
    items: List[Dict[str, Any]] = []

    for svc in top_services:
        if not isinstance(svc, dict):
            continue
        items.append(
            {
                "service_name": svc.get("service", "Unknown"),
                "monthly_cost": _safe_float(svc.get("spend", 0.0)),
                "usage_type": "Unknown",
                "region": region,
                "anomaly_flag": False,
            }
        )

    return items


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
            "analysis_type": cost_data.get("analysis_type", "full_account"),
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


@app.get("/api/settings")
def api_get_settings():
    """Return current AWS settings (region, role ARN, external ID) from session or config."""
    return jsonify({
        "aws_region": session.get("aws_region") or settings.aws_region,
        "aws_role_arn": session.get("aws_role_arn") or getattr(settings, "aws_role_arn", "") or "",
        "aws_external_id": session.get("aws_external_id") or getattr(settings, "aws_external_id", "") or "",
    })


@app.post("/api/settings")
def api_save_settings():
    """Save AWS region, IAM role ARN, and optional external ID to session (SaaS: user connects their account)."""
    payload = request.get_json(silent=True) or {}
    region = (payload.get("aws_region") or "").strip() or None
    role_arn = (payload.get("aws_role_arn") or "").strip() or None
    external_id = (payload.get("aws_external_id") or "").strip() or None
    if region:
        session["aws_region"] = region
    else:
        session.pop("aws_region", None)
    if role_arn is not None:
        session["aws_role_arn"] = role_arn if role_arn else ""
    else:
        session.pop("aws_role_arn", None)
    if external_id is not None:
        session["aws_external_id"] = external_id if external_id else ""
    else:
        session.pop("aws_external_id", None)
    return jsonify({
        "ok": True,
        "aws_region": session.get("aws_region") or settings.aws_region,
        "aws_role_arn": session.get("aws_role_arn") or "",
        "aws_external_id": session.get("aws_external_id") or "",
    })


def _template_aws_context():
    return {
        "aws_region": session.get("aws_region") or settings.aws_region,
        "aws_role_arn": session.get("aws_role_arn") or getattr(settings, "aws_role_arn", "") or "",
        "aws_external_id": session.get("aws_external_id") or getattr(settings, "aws_external_id", "") or "",
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        query=DEFAULT_QUERY,
        analysis_type="full_account",
        result=None,
        **_template_aws_context(),
    )


@app.post("/analyze")
def analyze():
    err = _require_aws_connection()
    if err:
        if request.is_json:
            return jsonify(err), 400
        return render_template(
            "index.html",
            query=request.form.get("query") or DEFAULT_QUERY,
            analysis_type=request.form.get("analysis_type") or "full_account",
            result=err,
            **_template_aws_context(),
        ), 400
    user_query = (request.form.get("query") or "").strip() or DEFAULT_QUERY
    analysis_type = _normalize_analysis_type(request.form.get("analysis_type"))

    try:
        state = run_async(lambda: graph.invoke(user_query, analysis_type=analysis_type))
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
        analysis_type=analysis_type,
        result=result,
        **_template_aws_context(),
    )


@app.post("/api/analyze")
def api_analyze():
    err = _require_aws_connection()
    if err:
        return jsonify(err), 400
    payload = request.get_json(silent=True) or {}
    user_query = (payload.get("query") or "").strip() or DEFAULT_QUERY
    analysis_type = _normalize_analysis_type(payload.get("analysis_type"))

    try:
        state = run_async(lambda: graph.invoke(user_query, analysis_type=analysis_type))
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


@app.post("/api/recommend")
def api_recommend():
    """Generate remediation recommendations from cost analysis.

    If the client provides explicit `items`, they are used directly.
    Otherwise, this endpoint will run a fresh ManagerGraph analysis using
    the provided or default query, and derive remediation items from the
    resulting top services breakdown.
    """
    err = _require_aws_connection()
    if err:
        return jsonify(err), 400
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    user_query = (payload.get("query") or "").strip() or DEFAULT_QUERY
    analysis_type = _normalize_analysis_type(payload.get("analysis_type"))

    try:
        if not items:
            # Run a fresh cost analysis to derive remediation items.
            state = run_async(lambda: graph.invoke(user_query, analysis_type=analysis_type))
            items = build_remediation_items_from_state(state)

        plan = run_async(lambda: remediation_graph.invoke(items))
        return jsonify(plan), 200
    except Exception as exc:
        logger.exception("API recommendations failed")
        return (
            jsonify(
                {
                    "error": str(exc),
                    "summary": "The request failed before recommendations could be generated.",
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

