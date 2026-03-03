import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings
from .tool_registry import Tool, tool_registry, categorize_tool_name

logger = logging.getLogger(__name__)


class MCPClient:
    """Async MCP client using MCP stdio transport (per official AWS MCP docs).

    It spawns the AWS Billing & Cost Management MCP server as a child process
    using the configuration in ``server/aws_mcp.json``:
    https://awslabs.github.io/mcp/servers/billing-cost-management-mcp-server/
    """

    def __init__(self, server_key: str) -> None:
        # server_key is the key under "mcpServers" in aws_mcp.json,
        # e.g. "awslabs.billing-cost-management-mcp-server"
        self.server_key = server_key
        self._mcp: Optional[MultiServerMCPClient] = None
        self._tools_by_name: Dict[str, Any] = {}

    def _build_servers_config(self) -> Dict[str, Any]:
        """Build MultiServerMCPClient config from aws_mcp.json."""
        config_path = Path(__file__).with_name("aws_mcp.json")
        logger.debug("Loading MCP server config from %s", config_path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        server_cfg = data["mcpServers"][self.server_key]
        logger.debug("Loaded config for server %s: command=%s args=%s", self.server_key, server_cfg.get("command"), server_cfg.get("args"))

        servers = {
            self.server_key: {
                "command": server_cfg["command"],
                "args": server_cfg.get("args", []),
                "env": server_cfg.get("env", {}),
                "transport": "stdio",
            }
        }
        return servers

    async def connect(self) -> None:
        if self._mcp is not None:
            return

        servers = self._build_servers_config()
        # Note: MultiServerMCPClient currently expects servers as positional arg
        self._mcp = MultiServerMCPClient(servers)
        logger.info("Initialized MCP stdio client for server %s", self.server_key)

    async def close(self) -> None:
        if self._mcp is None:
            return
        self._mcp = None
        self._tools_by_name = {}
        logger.info("Closed MCP client for server %s", self.server_key)

    async def discover_tools(self) -> List[Dict[str, Any]]:
        """Discover tools via MCP and cache them in ToolRegistry."""
        if tool_registry.cache_valid() and self._tools_by_name:
            logger.debug("Using cached MCP tools for server %s", self.server_key)
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "category": t.category,
                }
                for t in tool_registry.list_tools()
            ]

        await self.connect()
        assert self._mcp is not None

        # langchain-mcp-adapters exposes tools as LangChain Tools (async)
        tools = await self._mcp.get_tools()
        logger.info("Discovered %d MCP tools for server %s", len(tools), self.server_key)

        self._tools_by_name = {t.name: t for t in tools}

        tool_registry.invalidate_cache()
        for t in tools:
            schema: Dict[str, Any] = {}
            args_schema = getattr(t, "args_schema", None)
            # Some MCP adapters expose args_schema as a plain dict, others as a Pydantic model
            if isinstance(args_schema, dict):
                schema = args_schema
            elif args_schema is not None:
                try:
                    schema = args_schema.model_json_schema()  # pydantic v2
                except Exception:
                    schema = {}
            tool_registry.add_tool(
                Tool(
                    name=t.name,
                    description=getattr(t, "description", "") or "",
                    input_schema=schema,
                    category=categorize_tool_name(t.name),
                )
            )

        tool_registry.mark_cache_updated()
        # Simple serialisable view for graph state
        result: List[Dict[str, Any]] = []
        for t in tools:
            args_schema = getattr(t, "args_schema", None)
            if isinstance(args_schema, dict):
                schema = args_schema
            elif args_schema is not None:
                try:
                    schema = args_schema.model_json_schema()
                except Exception:
                    schema = {}
            else:
                schema = {}

            result.append(
                {
                    "name": t.name,
                    "description": getattr(t, "description", "") or "",
                    "input_schema": schema,
                }
            )
        return result

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke a named MCP tool via the LangChain tool wrapper."""
        if tool_name not in self._tools_by_name:
            await self.discover_tools()

        tool = self._tools_by_name.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown MCP tool: {tool_name}")

        logger.info("Calling MCP tool %s with args %s", tool_name, arguments)
        result = await tool.ainvoke(arguments)
        # Ensure JSON-serializable dict on return
        if isinstance(result, dict):
            logger.debug("Tool %s returned keys: %s", tool_name, list(result.keys()))
            return result
        return {"result": result}

    async def get_cost_and_usage(
        self,
        time_period: Dict[str, Any],
        granularity: str = "MONTHLY",
        metric: str = "NetUnblendedCost",
        group_by: Optional[str] = "SERVICE",
    ) -> Dict[str, Any]:
        """Use the Billing MCP Cost Explorer tool for a cost breakdown by service.

        The Billing MCP server's `cost-explorer` tool expects the following inputs
        for this operation (per tool schema):
          - operation: "getCostAndUsage"
          - start_date: YYYY-MM-DD
          - end_date: YYYY-MM-DD
          - granularity: DAILY or MONTHLY
          - metrics: e.g. "NetUnblendedCost"
          - group_by: optional dimension such as "SERVICE"
        """
        try:
            start_date = time_period.get("start")
            end_date = time_period.get("end")

            logger.info(
                (
                    "Requesting cost-explorer getCostAndUsage for start=%s end=%s "
                    "granularity=%s metric=%s group_by=%s"
                ),
                start_date,
                end_date,
                granularity,
                metric,
                group_by,
            )
            arguments: Dict[str, Any] = {
                "operation": "getCostAndUsage",
                "start_date": start_date,
                "end_date": end_date,
                "granularity": granularity,
                # The AWS Billing MCP server expects `metrics` as JSON array string.
                "metrics": json.dumps([metric]),
            }
            if group_by:
                arguments["group_by"] = json.dumps(
                    [{"Type": "DIMENSION", "Key": group_by}]
                )

            return await self.call_tool(
                "cost-explorer",
                arguments,
            )
        except Exception as exc:
            logger.error("get_cost_and_usage failed: %s", exc)
            return {"error": str(exc)}

    async def get_cost_forecast(self, time_period: Dict[str, Any], granularity: str = "MONTHLY") -> Dict[str, Any]:
        """Use the Billing MCP Cost Explorer tool for a cost forecast.

        Tool schema expects:
          - operation: "getCostForecast"
          - metric: e.g. "UNBLENDED_COST"
          - granularity: DAILY or MONTHLY
          - start_date / end_date: YYYY-MM-DD
        """
        try:
            start_date = time_period.get("start")
            end_date = time_period.get("end")

            logger.info(
                "Requesting cost-explorer getCostForecast for start=%s end=%s granularity=%s",
                start_date,
                end_date,
                granularity,
            )
            return await self.call_tool(
                "cost-explorer",
                {
                    "operation": "getCostForecast",
                    "metric": "UNBLENDED_COST",
                    "granularity": granularity,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
        except Exception as exc:
            logger.error("get_cost_forecast failed: %s", exc)
            return {"error": str(exc)}

    async def query_session_sql(
        self,
        query: str,
    ) -> Dict[str, Any]:
        """Query the session database using session-sql MCP tool.
        
        Args:
            query: SQL query to execute (must include table name in query)
            
        Returns:
            Query results as dict
        """
        try:
            logger.info("Querying session-sql with query: %s", query[:200] if len(query) > 200 else query)
            
            # session-sql tool only accepts query parameter
            args: Dict[str, Any] = {"query": query}
            
            return await self.call_tool("session-sql", args)
        except Exception as exc:
            logger.error("session-sql query failed: %s", exc)
            return {"error": str(exc)}

    async def check_aws_credentials(self) -> Dict[str, Any]:
        """Validate that AWS credentials/profile used by the MCP server are usable.

        This uses STS GetCallerIdentity with the *same* profile/region that the
        Billing MCP server is configured with in aws_mcp.json. It returns a small
        JSON structure summarizing validity and, when valid, the account/ARN.
        """
        try:
            import boto3
        except ImportError:
            logger.warning("boto3 not installed; skipping AWS credential validation")
            return {
                "valid": None,
                "error": "boto3 not installed",
            }

        # Read the MCP server env so we match exactly what the child process uses.
        try:
            config_path = Path(__file__).with_name("aws_mcp.json")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            server_cfg = data["mcpServers"][self.server_key]
            env_cfg: Dict[str, Any] = server_cfg.get("env", {})
        except Exception as exc:
            logger.error("Failed to load aws_mcp.json for credential check: %s", exc)
            env_cfg = {}

        profile = env_cfg.get("AWS_PROFILE") or settings.aws_profile
        region = env_cfg.get("AWS_REGION") or settings.aws_region

        try:
            session = boto3.Session(profile_name=profile if profile else None)
            sts = session.client("sts", region_name=region if region else None)
            identity = sts.get_caller_identity()
            result: Dict[str, Any] = {
                "valid": True,
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
                "user_id": identity.get("UserId"),
                "profile": profile,
                "region": region,
            }
            logger.info(
                "AWS credentials check succeeded for profile=%s account=%s arn=%s",
                profile,
                result["account"],
                result["arn"],
            )
            return result
        except Exception as exc:
            logger.error(
                "AWS credential check failed for profile %s in region %s: %s",
                profile,
                region,
                exc,
            )
            return {
                "valid": False,
                "error": str(exc),
                "profile": profile,
                "region": region,
            }


# Single Billing MCP client (Pricing MCP can be added later if needed).
billing_mcp_client = MCPClient("awslabs.billing-cost-management-mcp-server")
pricing_mcp_client = billing_mcp_client


