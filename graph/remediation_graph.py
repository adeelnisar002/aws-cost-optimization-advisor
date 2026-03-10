import logging
from typing import Any, Dict, List

from langgraph.graph import END, START, StateGraph

from agents.remediation_agent import RemediationAgent
from models.schemas import (
    RemediationGraphState,
    RemediationInputItem,
)
from server.client import billing_mcp_client
from utils.opportunity_detector import detect_opportunities
from utils.recommendation_fetcher import fetch_recommendations_for_opportunities

logger = logging.getLogger(__name__)


class RemediationGraph:
    """LangGraph-based orchestration for the Remediation agent."""

    def __init__(self) -> None:
        self.agent = RemediationAgent()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(dict)

        workflow.add_node("discover_tools_node", self.discover_tools_node)
        workflow.add_node("input_validation_node", self.input_validation_node)
        workflow.add_node("opportunity_detection_node", self.opportunity_detection_node)
        workflow.add_node("recommendation_fetch_node", self.recommendation_fetch_node)
        workflow.add_node("strategy_generator_node", self.strategy_generator_node)
        workflow.add_node("output_formatter_node", self.output_formatter_node)

        workflow.add_edge(START, "discover_tools_node")
        workflow.add_edge("discover_tools_node", "input_validation_node")
        workflow.add_edge("input_validation_node", "opportunity_detection_node")
        workflow.add_edge("opportunity_detection_node", "recommendation_fetch_node")
        workflow.add_edge("recommendation_fetch_node", "strategy_generator_node")
        workflow.add_edge("strategy_generator_node", "output_formatter_node")
        workflow.add_edge("output_formatter_node", END)

        return workflow.compile()

    async def discover_tools_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("discover_tools_node: discovering MCP tools for remediation")
        try:
            tools = await billing_mcp_client.discover_tools()
            state["discovered_tools"] = tools
            # Keep log compact but confirm relevant tools are available.
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
            logger.info("Remediation discovered tools: %s", ", ".join(tool_names))
        except Exception as exc:
            logger.error("discover_tools_node failed: %s", exc)
            state.setdefault("errors", []).append(f"discover_tools: {exc}")
            state["discovered_tools"] = []
        return state

    async def input_validation_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("input_validation_node: validating remediation input items")
        raw_items: List[Dict[str, Any]] = state.get("analysis_items", [])

        valid_items: List[RemediationInputItem] = []
        errors = state.get("errors", [])

        for idx, item in enumerate(raw_items):
            try:
                validated = RemediationInputItem.model_validate(item)
                valid_items.append(validated)
            except Exception as exc:
                msg = f"input_item[{idx}] validation failed: {exc}"
                logger.error(msg)
                errors.append(msg)

        if not valid_items:
            logger.warning("No valid remediation input items; returning empty plan")
            state["analysis_items"] = []
            state["detected_opportunities"] = []
            state["raw_recommendations"] = {}
            state["remediation_plan"] = {
                "summary": {
                    "total_estimated_savings": 0.0,
                    "risk_profile": "None",
                    "priority_score": 0.0,
                },
                "remediations": [],
            }
            state["errors"] = errors
            return state

        state["analysis_items"] = [i.model_dump() for i in valid_items]
        state["errors"] = errors
        return state

    async def opportunity_detection_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("opportunity_detection_node: detecting optimization opportunities")
        items_data = state.get("analysis_items", [])
        items = [RemediationInputItem.model_validate(i) for i in items_data]

        opportunities = detect_opportunities(items)
        state["detected_opportunities"] = [o.model_dump() for o in opportunities]
        return state

    async def recommendation_fetch_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("recommendation_fetch_node: fetching raw recommendation context")
        opp_data = state.get("detected_opportunities", [])
        opportunities = [RemediationInputItem.model_validate(o) for o in []]  # type: ignore[call-arg]
        # Re-validate as RemediationOpportunity
        from models.schemas import RemediationOpportunity  # local import to avoid cycle

        opportunities = [RemediationOpportunity.model_validate(o) for o in opp_data]

        if not opportunities:
            logger.info("No opportunities detected; skipping recommendation fetch")
            state["raw_recommendations"] = {}
            return state

        raw_recs = await fetch_recommendations_for_opportunities(opportunities)
        state["raw_recommendations"] = raw_recs
        return state

    async def strategy_generator_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("strategy_generator_node: generating remediation strategy")
        from models.schemas import RemediationOpportunity  # local import to avoid cycle

        opp_data = state.get("detected_opportunities", [])
        opportunities = [RemediationOpportunity.model_validate(o) for o in opp_data]
        raw_recs = state.get("raw_recommendations", {})

        plan = await self.agent.generate_plan(opportunities, raw_recs)
        state["remediation_plan"] = plan
        return state

    async def output_formatter_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("output_formatter_node: finalizing remediation output")
        plan = state.get("remediation_plan") or {}

        # Ensure summary fields are present and numeric
        summary = plan.get("summary") or {}
        total_savings = float(summary.get("total_estimated_savings", 0.0) or 0.0)
        risk_profile = summary.get("risk_profile", "Unknown") or "Unknown"
        priority_score = float(summary.get("priority_score", total_savings) or 0.0)

        plan["summary"] = {
            "total_estimated_savings": total_savings,
            "risk_profile": risk_profile,
            "priority_score": priority_score,
        }
        plan.setdefault("remediations", [])

        state["remediation_plan"] = plan
        return state

    async def invoke(self, analysis_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Helper to run the full remediation graph for a set of input items."""
        initial_state = RemediationGraphState(
            analysis_items=[
                RemediationInputItem.model_validate(item) for item in analysis_items
            ],
            discovered_tools=[],
            detected_opportunities=[],
            raw_recommendations={},
            remediation_plan=None,
            errors=[],
        ).model_dump()
        final_state = await self.graph.ainvoke(initial_state)
        # Return only the structured remediation output by default
        return final_state.get("remediation_plan", final_state)



