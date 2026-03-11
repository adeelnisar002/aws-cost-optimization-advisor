import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings
from .tool_registry import Tool, tool_registry, categorize_tool_name

logger = logging.getLogger(__name__)


def _get_aws_config() -> Tuple[str, str, str, str]:
    """Resolve (region, role_arn, profile, external_id) from request context (Flask g) or settings."""
    region = settings.aws_region
    role_arn = getattr(settings, "aws_role_arn", None) or ""
    profile = (settings.aws_profile or "").strip()
    external_id = getattr(settings, "aws_external_id", None) or ""
    try:
        from flask import g
        if hasattr(g, "aws_region") and g.aws_region:
            region = g.aws_region
        if hasattr(g, "aws_role_arn"):
            role_arn = g.aws_role_arn or ""
        if hasattr(g, "aws_profile"):
            profile = (g.aws_profile or "").strip()
        if hasattr(g, "aws_external_id"):
            external_id = g.aws_external_id or ""
    except Exception:
        pass
    return (region, role_arn, profile, external_id)


def _get_assumed_role_credentials(
    region: str, role_arn: str, profile: str, external_id: str = ""
) -> Optional[Dict[str, str]]:
    """If role_arn is set, assume the customer role (SaaS) and return env vars for the MCP subprocess."""
    if not (role_arn and role_arn.strip()):
        return None
    try:
        import boto3
        from botocore.exceptions import ClientError
        # Use app credentials (no profile in SaaS) or local dev profile (e.g. cost-agent that assumes backend role)
        session = boto3.Session(profile_name=profile if profile else None)
        sts = session.client("sts", region_name=region or None)
        params = {
            "RoleArn": role_arn.strip(),
            "RoleSessionName": "cost-analysis-mcp",
        }
        if external_id and external_id.strip():
            params["ExternalId"] = external_id.strip()
        resp = sts.assume_role(**params)
        creds = resp.get("Credentials") or {}
        return {
            "AWS_ACCESS_KEY_ID": creds.get("AccessKeyId", ""),
            "AWS_SECRET_ACCESS_KEY": creds.get("SecretAccessKey", ""),
            "AWS_SESSION_TOKEN": creds.get("SessionToken", ""),
        }
    except ClientError as exc:
        err_code = exc.response.get("Error", {}).get("Code", "")
        err_msg = str(exc)
        if err_code == "AccessDenied":
            if "AssumeRole" in err_msg and "CostAgentBackendRole" in err_msg:
                logger.warning(
                    "AssumeRole failed (backend role not trusted by your credentials). "
                    "If using profile that assumes CostAgentBackendRole, add your local IAM principal to that role's trust policy. See README 'Local development with assume-role profile'."
                )
            else:
                logger.warning("Failed to assume role %s: %s", role_arn, exc)
        else:
            logger.warning("Failed to assume role %s: %s", role_arn, exc)
        return None
    except Exception as exc:
        logger.warning("Failed to assume role %s: %s", role_arn, exc)
        return None


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
        self._last_config_key: Optional[Tuple[str, str, str]] = None  # (region, role_arn, external_id) for invalidation

    def _build_servers_config(self) -> Dict[str, Any]:
        """Build MultiServerMCPClient config from aws_mcp.json and runtime AWS config (region, IAM role)."""
        config_path = Path(__file__).with_name("aws_mcp.json")
        logger.debug("Loading MCP server config from %s", config_path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        server_cfg = data["mcpServers"][self.server_key]
        logger.debug("Loaded config for server %s: command=%s args=%s", self.server_key, server_cfg.get("command"), server_cfg.get("args"))

        base_env: Dict[str, str] = dict(server_cfg.get("env", {}))
        region, role_arn, profile, external_id = _get_aws_config()

        # Runtime env: region always; profile only when set (else use IAM role).
        # If we inject assumed-role temp creds, ensure AWS_PROFILE is removed
        # because botocore prioritizes explicit profile over env credentials.
        base_env["AWS_REGION"] = region or "us-east-1"
        if profile:
            base_env["AWS_PROFILE"] = profile
        # When profile is empty we omit AWS_PROFILE so the SDK uses default chain (app's task/instance role)

        assumed = _get_assumed_role_credentials(region, role_arn, profile, external_id)
        if assumed:
            base_env.pop("AWS_PROFILE", None)
            base_env.update(assumed)

        servers = {
            self.server_key: {
                "command": server_cfg["command"],
                "args": server_cfg.get("args", []),
                "env": base_env,
                "transport": "stdio",
            }
        }
        self._last_config_key = (region or "", role_arn or "", external_id or "")
        return servers

    async def connect(self) -> None:
        region, role_arn, _, external_id = _get_aws_config()
        current_key = (region or "", role_arn or "", external_id or "")
        if self._mcp is not None and self._last_config_key == current_key:
            return
        if self._mcp is not None and self._last_config_key != current_key:
            logger.info("AWS config changed (region/role); reconnecting MCP client")
            self._mcp = None
            self._tools_by_name = {}

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
            # Some AWS Billing MCP operations return a JSON string in result[0].text.
            # When upstream returns an error payload, surface it explicitly in logs.
            try:
                result_items = result.get("result")
                if isinstance(result_items, list) and result_items:
                    first_item = result_items[0]
                    if isinstance(first_item, dict):
                        text = first_item.get("text")
                        if isinstance(text, str) and text.strip():
                            parsed_text = json.loads(text)
                            if isinstance(parsed_text, dict) and parsed_text.get("error_type"):
                                logger.error(
                                    (
                                        "MCP tool %s returned error payload: "
                                        "status=%s http_status=%s error_type=%s message=%s request_id=%s"
                                    ),
                                    tool_name,
                                    parsed_text.get("status"),
                                    parsed_text.get("http_status"),
                                    parsed_text.get("error_type"),
                                    parsed_text.get("message"),
                                    parsed_text.get("request_id"),
                                )
            except Exception:
                # Avoid failing tool calls due to best-effort diagnostics.
                pass
            return result
        return {"result": result}

    async def get_cost_and_usage(
        self,
        time_period: Dict[str, Any],
        granularity: str = "MONTHLY",
        metric: str = "NetUnblendedCost",
        group_by: Optional[str] = "SERVICE",
        filter_expr: Optional[Dict[str, Any]] = None,
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
            if filter_expr:
                arguments["filter"] = json.dumps(filter_expr)

            return await self.call_tool(
                "cost-explorer",
                arguments,
            )
        except Exception as exc:
            logger.error("get_cost_and_usage failed: %s", exc)
            return {"error": str(exc)}

    async def get_cost_forecast(
        self,
        time_period: Dict[str, Any],
        granularity: str = "MONTHLY",
        filter_expr: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            arguments: Dict[str, Any] = {
                "operation": "getCostForecast",
                "metric": "UNBLENDED_COST",
                "granularity": granularity,
                "start_date": start_date,
                "end_date": end_date,
            }
            if filter_expr:
                arguments["filter"] = json.dumps(filter_expr)
            return await self.call_tool("cost-explorer", arguments)
        except Exception as exc:
            logger.error("get_cost_forecast failed: %s", exc)
            return {"error": str(exc)}

    async def list_recommendation_summaries(
        self,
        group_by: str = "ResourceType",
        filters: Optional[Dict[str, Any]] = None,
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Use the Billing MCP Cost Optimization tool to list recommendation summaries.

        This wraps the `cost-optimization` MCP tool with the
        `list_recommendation_summaries` operation.
        """
        try:
            args: Dict[str, Any] = {
                "operation": "list_recommendation_summaries",
                "group_by": group_by,
            }
            if filters:
                # Cost Optimization expects filters as JSON-encoded dict.
                args["filters"] = json.dumps(filters)
            if max_results is not None:
                args["max_results"] = max_results

            logger.info(
                (
                    "Requesting cost-optimization list_recommendation_summaries "
                    "group_by=%s filters=%s max_results=%s"
                ),
                group_by,
                filters,
                max_results,
            )
            return await self.call_tool("cost-optimization", args)
        except Exception as exc:
            logger.error("list_recommendation_summaries failed: %s", exc)
            return {"error": str(exc)}

    async def list_recommendations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Use the Billing MCP Cost Optimization tool to list detailed recommendations.

        This wraps the `cost-optimization` MCP tool with the
        `list_recommendations` operation.
        """
        try:
            args: Dict[str, Any] = {"operation": "list_recommendations"}
            if filters:
                args["filters"] = json.dumps(filters)
            if max_results is not None:
                args["max_results"] = max_results

            logger.info(
                "Requesting cost-optimization list_recommendations with filters=%s max_results=%s",
                filters,
                max_results,
            )
            return await self.call_tool("cost-optimization", args)
        except Exception as exc:
            logger.error("list_recommendations failed: %s", exc)
            return {"error": str(exc)}

    async def get_compute_optimizer_recommendations(
        self,
        operation: str,
        filters: Optional[Dict[str, Any]] = None,
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get recommendations from the compute-optimizer MCP tool."""
        try:
            args: Dict[str, Any] = {"operation": operation}
            if filters:
                args["filters"] = json.dumps(filters)
            if max_results is not None:
                args["max_results"] = max_results

            logger.info(
                "Requesting compute-optimizer %s with filters=%s max_results=%s",
                operation,
                filters,
                max_results,
            )
            return await self.call_tool("compute-optimizer", args)
        except Exception as exc:
            logger.error("get_compute_optimizer_recommendations failed: %s", exc)
            return {"error": str(exc)}

    async def get_recommendation_details(
        self,
        recommendation_id: str,
    ) -> Dict[str, Any]:
        """Get detailed recommendation context from the rec-details MCP tool."""
        try:
            logger.info(
                "Requesting rec-details for recommendation_id=%s",
                recommendation_id,
            )
            return await self.call_tool(
                "rec-details",
                {"recommendation_id": recommendation_id},
            )
        except Exception as exc:
            logger.error("get_recommendation_details failed: %s", exc)
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
        """Validate that AWS credentials used by the MCP server are usable.

        Uses the same config as the MCP client: region/role from request or settings;
        when profile is set uses it, else uses IAM role (task/instance); when role_arn
        is set assumes that role. Returns validity and, when valid, account/ARN.
        """
        try:
            import boto3
        except ImportError:
            logger.warning("boto3 not installed; skipping AWS credential validation")
            return {
                "valid": None,
                "error": "boto3 not installed",
            }

        region, role_arn, profile, external_id = _get_aws_config()
        region = region or "us-east-1"

        try:
            session = boto3.Session(profile_name=profile if profile else None)
            sts = session.client("sts", region_name=region)
            if role_arn and role_arn.strip():
                params = {
                    "RoleArn": role_arn.strip(),
                    "RoleSessionName": "cost-analysis-cred-check",
                }
                if external_id and external_id.strip():
                    params["ExternalId"] = external_id.strip()
                resp = sts.assume_role(**params)
                creds = resp.get("Credentials") or {}
                sts_assumed = session.client(
                    "sts",
                    region_name=region,
                    aws_access_key_id=creds.get("AccessKeyId"),
                    aws_secret_access_key=creds.get("SecretAccessKey"),
                    aws_session_token=creds.get("SessionToken"),
                )
                identity = sts_assumed.get_caller_identity()
            else:
                identity = sts.get_caller_identity()
            result: Dict[str, Any] = {
                "valid": True,
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
                "user_id": identity.get("UserId"),
                "profile": profile or "(IAM role)",
                "region": region,
                "role_arn": role_arn if role_arn else None,
            }
            logger.info(
                "AWS credentials check succeeded account=%s arn=%s",
                result["account"],
                result["arn"],
            )
            return result
        except Exception as exc:
            err_msg = str(exc)
            hint = None
            if "AccessDenied" in err_msg and "AssumeRole" in err_msg and "CostAgentBackendRole" in err_msg:
                hint = (
                    "Your profile may assume CostAgentBackendRole; that role must trust your local IAM identity. "
                    "See README: Local development with assume-role profile."
                )
            logger.error(
                "AWS credential check failed region=%s role_arn=%s: %s",
                region,
                role_arn or "(none)",
                exc,
            )
            if hint:
                logger.info("Hint: %s", hint)
            result = {
                "valid": False,
                "error": err_msg,
                "profile": profile or "(IAM role)",
                "region": region,
                "role_arn": role_arn if role_arn else None,
            }
            if hint:
                result["hint"] = hint
            return result


# Single Billing MCP client (Pricing MCP can be added later if needed).
billing_mcp_client = MCPClient("awslabs.billing-cost-management-mcp-server")
pricing_mcp_client = billing_mcp_client


