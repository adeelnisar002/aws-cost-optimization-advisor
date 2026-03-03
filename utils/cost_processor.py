"""Cost data processing and aggregation utilities.

This module handles:
- Extracting database/table references from MCP responses
- Querying session-sql for cost data
- Aggregating cost data by service
- Extracting forecast totals
- Creating compact structured summaries
"""
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CostDataProcessor:
    """Processes raw MCP cost data into compact structured summaries."""

    @staticmethod
    def _parse_mcp_result_text(mcp_response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse the MCP response which may have data in result[0].text as JSON string.
        
        Returns:
            Parsed data dict, or None if parsing fails
        """
        if not isinstance(mcp_response, dict):
            return None
        
        # Check if result is a list with text content
        if "result" in mcp_response:
            result = mcp_response["result"]
            if isinstance(result, list) and len(result) > 0:
                first_item = result[0]
                if isinstance(first_item, dict) and "text" in first_item:
                    text_content = first_item["text"]
                    try:
                        # Parse the JSON string
                        parsed = json.loads(text_content)
                        return parsed
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Failed to parse result text as JSON: %s", e)
        
        # If no result array, return the response itself
        return mcp_response

    @staticmethod
    def extract_session_info(mcp_response: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Extract session database and table name from MCP cost-explorer response.
        
        The MCP response may have structure:
        - result[0].text: JSON string containing the actual data
        - data.session_db: Path to session database
        - data.table_name: Name of the table
        
        Returns:
            Dict with 'database_name' and 'table_name', or None if not found
        """
        if not isinstance(mcp_response, dict):
            return None
        
        # First, try to parse result[0].text if it exists
        parsed_data = CostDataProcessor._parse_mcp_result_text(mcp_response)
        if parsed_data:
            # Check data.session_db and data.table_name
            data = parsed_data.get("data", {})
            if isinstance(data, dict):
                session_db = data.get("session_db")
                table_name = data.get("table_name")
                if session_db and table_name:
                    return {
                        "database_name": session_db,
                        "table_name": table_name,
                    }
        
        # Check for direct keys
        if "database_name" in mcp_response and "table_name" in mcp_response:
            return {
                "database_name": mcp_response["database_name"],
                "table_name": mcp_response["table_name"],
            }
        
        # Check nested in common response structures
        if "session" in mcp_response:
            session = mcp_response["session"]
            if isinstance(session, dict):
                db = session.get("database_name")
                table = session.get("table_name")
                if db and table:
                    return {"database_name": db, "table_name": table}
        
        # Check for metadata or info keys
        for key in ["metadata", "info", "result"]:
            if key in mcp_response and isinstance(mcp_response[key], dict):
                result_data = mcp_response[key]
                
                # Check direct keys in result
                db = result_data.get("database_name")
                table = result_data.get("table_name")
                if db and table:
                    return {"database_name": db, "table_name": table}
                
                # Check nested session info
                if "session" in result_data and isinstance(result_data["session"], dict):
                    session = result_data["session"]
                    db = session.get("database_name")
                    table = session.get("table_name")
                    if db and table:
                        return {"database_name": db, "table_name": table}
        
        logger.warning("Could not extract session info from MCP response. Keys: %s", list(mcp_response.keys()))
        return None

    @staticmethod
    def aggregate_cost_data(results_by_time: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Backwards-compatible wrapper around service aggregation."""
        return CostDataProcessor.aggregate_grouped_service_cost(
            results_by_time=results_by_time,
            metric_key="NetUnblendedCost",
        )

    @staticmethod
    def _extract_amount_from_metric_map(metrics: Dict[str, Any], metric_key: str) -> float:
        """Extract numeric amount from CE metric structures.

        Supports both:
        - {"NetUnblendedCost": {"Amount": "12.34"}}
        - {"Metrics": {"NetUnblendedCost": {"Amount": "12.34"}}}
        """
        candidates: List[Any] = []
        if not isinstance(metrics, dict):
            return 0.0

        candidates.append(metrics.get(metric_key))
        nested_metrics = metrics.get("Metrics")
        if isinstance(nested_metrics, dict):
            candidates.append(nested_metrics.get(metric_key))

        for candidate in candidates:
            if isinstance(candidate, dict):
                amount = candidate.get("Amount")
                if amount is None:
                    continue
                try:
                    return float(amount)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def extract_results_by_time(mcp_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract ResultsByTime array from MCP response formats."""
        if not isinstance(mcp_response, dict):
            return []

        parsed_data = CostDataProcessor._parse_mcp_result_text(mcp_response)
        results_by_time: List[Dict[str, Any]] = []

        if isinstance(parsed_data, dict):
            data = parsed_data.get("data", {})
            if isinstance(data, dict):
                # Preferred location
                direct_results = data.get("ResultsByTime")
                if isinstance(direct_results, list):
                    return [r for r in direct_results if isinstance(r, dict)]
                if isinstance(direct_results, str):
                    try:
                        parsed = json.loads(direct_results)
                        if isinstance(parsed, list):
                            return [r for r in parsed if isinstance(r, dict)]
                    except json.JSONDecodeError:
                        pass

                # Common MCP preview format (key/value rows)
                preview = data.get("preview", [])
                if isinstance(preview, list):
                    for item in preview:
                        if not isinstance(item, dict):
                            continue
                        if item.get("key") != "ResultsByTime":
                            continue
                        value = item.get("value")
                        if isinstance(value, list):
                            results_by_time.extend(
                                [r for r in value if isinstance(r, dict)]
                            )
                        elif isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                                if isinstance(parsed, list):
                                    results_by_time.extend(
                                        [r for r in parsed if isinstance(r, dict)]
                                    )
                            except json.JSONDecodeError:
                                logger.warning("Failed parsing ResultsByTime preview JSON")

        # Top-level fallback
        if not results_by_time and isinstance(mcp_response.get("ResultsByTime"), list):
            results_by_time = [
                r for r in mcp_response["ResultsByTime"] if isinstance(r, dict)
            ]

        return results_by_time

    @staticmethod
    def extract_total_from_results(
        results_by_time: List[Dict[str, Any]],
        metric_key: str,
    ) -> float:
        """Sum Total.<metric_key>.Amount across time periods."""
        total = 0.0
        for period in results_by_time:
            if not isinstance(period, dict):
                continue
            total_metrics = period.get("Total", {})
            if not isinstance(total_metrics, dict):
                continue
            total += CostDataProcessor._extract_amount_from_metric_map(
                total_metrics, metric_key
            )
        return total

    @staticmethod
    def aggregate_grouped_service_cost(
        results_by_time: List[Dict[str, Any]],
        metric_key: str = "NetUnblendedCost",
    ) -> Dict[str, Any]:
        """Aggregate grouped CE response by service only.

        Important: this does not derive account total from grouped rows.
        """
        service_spend: Dict[str, float] = {}

        if not results_by_time:
            logger.warning("No ResultsByTime data provided for aggregation")
            return {
                "total_monthly_spend": 0.0,
                "gross_spend": 0.0,
                "credits_refunds": 0.0,
                "service_spend": {},
            }

        for time_period in results_by_time:
            if not isinstance(time_period, dict):
                continue

            groups = time_period.get("Groups", [])
            if not groups:
                continue

            for group in groups:
                if not isinstance(group, dict):
                    continue

                keys = group.get("Keys", [])
                service_name = keys[0] if keys and len(keys) > 0 else "Unknown"

                metrics = group.get("Metrics", {})
                amount = CostDataProcessor._extract_amount_from_metric_map(
                    metrics if isinstance(metrics, dict) else {},
                    metric_key,
                )
                service_spend[service_name] = service_spend.get(service_name, 0.0) + amount

        gross_spend = sum(v for v in service_spend.values() if v > 0)
        credits_refunds = abs(sum(v for v in service_spend.values() if v < 0))
        net_total = gross_spend - credits_refunds

        logger.info(
            (
                "Grouped aggregation complete: gross_spend=%.6f, "
                "credits_refunds=%.6f, grouped_net=%.6f, service_count=%d"
            ),
            gross_spend, credits_refunds, net_total, len(service_spend)
        )

        return {
            "total_monthly_spend": net_total,
            "gross_spend": gross_spend,
            "credits_refunds": credits_refunds,
            "service_spend": service_spend,
        }

    @staticmethod
    def get_top_services(service_spend: Dict[str, float], top_n: int = 3) -> List[Dict[str, float]]:
        """Get top N services by spend, sorted descending.
        
        Args:
            service_spend: Dict mapping service names to spend amounts
            top_n: Number of top services to return
            
        Returns:
            List of dicts with 'service' and 'spend' keys, sorted by spend descending
        """
        # Sort by spend descending
        sorted_services = sorted(
            service_spend.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Take top N and convert to list of dicts
        top_services = [
            {"service": service, "spend": float(spend)}
            for service, spend in sorted_services[:top_n]
        ]
        
        return top_services

    @staticmethod
    def extract_forecast_total(forecast_response: Dict[str, Any]) -> float:
        """Extract total forecasted amount from getCostForecast response.
        
        The forecast response may have structure:
        - result[0].text: JSON string containing the actual data
        - data.Total.Amount: Total forecast amount
        - data.ForecastResultsByTime: List of forecast periods
        
        Returns:
            Total forecasted amount as float, or 0.0 if not found
        """
        if not isinstance(forecast_response, dict):
            logger.warning("Forecast response is not a dict")
            return 0.0
        
        # First, try to parse result[0].text if it exists (MCP text response format)
        parsed_data = CostDataProcessor._parse_mcp_result_text(forecast_response)
        if parsed_data:
            data = parsed_data.get("data", {})
            if isinstance(data, dict):
                # Try Total.Amount first (most direct)
                total = data.get("Total", {})
                if isinstance(total, dict):
                    amount_str = total.get("Amount")
                    if amount_str:
                        try:
                            return float(amount_str)
                        except (ValueError, TypeError) as e:
                            logger.warning("Failed to parse Total.Amount '%s': %s", amount_str, e)
                
                # Fallback to summing ForecastResultsByTime
                forecast_results = data.get("ForecastResultsByTime", [])
                if forecast_results:
                    total_forecast = 0.0
                    for period in forecast_results:
                        if isinstance(period, dict):
                            # Try MeanValue (common in forecast responses)
                            amount_str = period.get("MeanValue") or period.get("Amount", "0")
                            try:
                                amount = float(amount_str)
                                total_forecast += amount
                            except (ValueError, TypeError) as e:
                                logger.warning("Failed to parse forecast amount '%s': %s", amount_str, e)
                    if total_forecast > 0:
                        return total_forecast
        
        # Check for ForecastResultsByTime at top level
        forecast_results = forecast_response.get("ForecastResultsByTime", [])
        if not forecast_results:
            # Try alternative keys
            for key in ["results", "forecast", "data", "result"]:
                if key in forecast_response:
                    candidate = forecast_response[key]
                    if isinstance(candidate, list):
                        forecast_results = candidate
                        break
                    elif isinstance(candidate, dict) and "ForecastResultsByTime" in candidate:
                        forecast_results = candidate["ForecastResultsByTime"]
                        break
        
        if not forecast_results:
            logger.warning("Could not find ForecastResultsByTime in forecast response. Keys: %s", list(forecast_response.keys()))
            return 0.0
        
        # Sum all forecast periods
        total_forecast = 0.0
        for period in forecast_results:
            if not isinstance(period, dict):
                continue
                
            # Try Total.Amount (most common)
            total = period.get("Total", {})
            if isinstance(total, dict):
                # Try Amount first, then MeanValue
                amount_str = total.get("Amount") or total.get("MeanValue", "0")
            else:
                # Try direct Amount or MeanValue keys
                amount_str = period.get("Amount") or period.get("MeanValue", "0")
            
            # If still no amount, try Metrics structure
            if amount_str == "0" or not amount_str:
                metrics = period.get("Metrics", {})
                if isinstance(metrics, dict):
                    forecast_metric = metrics.get("MeanValue") or metrics.get("Amount", "0")
                    amount_str = forecast_metric
            
            try:
                amount = float(amount_str)
                total_forecast += amount
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse forecast amount '%s': %s", amount_str, e)
        
        return total_forecast

    @staticmethod
    def create_cost_summary(
        total_monthly_spend: float,
        top_services: List[Dict[str, float]],
        forecast_next_month: float,
    ) -> Dict[str, Any]:
        """Create a compact structured summary for the LLM.
        
        Args:
            total_monthly_spend: Total monthly spend
            top_services: List of top services with spend
            forecast_next_month: Forecasted spend for next month
            
        Returns:
            Compact structured summary dict
        """
        return {
            "total_monthly_spend": float(total_monthly_spend),
            "top_services": top_services,
            "forecast_next_month": float(forecast_next_month),
        }

