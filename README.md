# AWS Cost Analysis Agent (LangGraph + AWS Billing MCP)

This project is an **AWS cost analysis agent** built with **LangGraph** and an **AWS Billing MCP server**. It orchestrates multiple AWS Cost Explorer and forecasting calls, then produces a natural‑language analysis of your AWS spend.

The core orchestration logic lives in `graph/manager_graph.py` as a LangGraph workflow.

---

## High‑Level Overview

- **Goal**: Given a user question about AWS costs (e.g. “Explain my AWS spend and forecast next month”), the agent:
  - Discovers available AWS billing tools via MCP
  - Fetches production cost data (monthly totals, MTD, same‑period‑last‑month, top services)
  - Fetches a cost forecast for the next full month
  - Runs an LLM‑based manager agent over that data to produce a human‑readable report

---

## ManagerGraph Workflow (LangGraph)

The `ManagerGraph` is defined in `graph/manager_graph.py`. It is a `StateGraph(dict)` with the following nodes and edges:

```mermaid
flowchart LR
    START --> discover_tools_node
    discover_tools_node --> fetch_cost_node
    fetch_cost_node --> fetch_forecast_node
    fetch_forecast_node --> analyze_node
    analyze_node --> output_node
    output_node --> END
```

### Node‑by‑Node Behavior

- **discover_tools_node**
  - Checks AWS credentials via the billing MCP client
  - Connects to the MCP server and discovers available AWS billing tools
  - Writes `aws_credentials` and `discovered_tools` into the graph state
  - Appends any errors to `errors`

- **fetch_cost_node**
  - Computes several date windows:
    - Previous full billing month
    - Current month‑to‑date (MTD)
    - Same‑period‑last‑month (SPLM) aligned to current MTD days
  - Uses MCP to call `get_cost_and_usage` multiple times:
    - Monthly total (ungrouped, `NetUnblendedCost`)
    - Service breakdown (grouped by `SERVICE`)
    - MTD daily costs
    - SPLM daily costs
  - Processes responses with `CostDataProcessor` to derive:
    - `total_monthly_spend`
    - `gross_spend` and `credits_refunds`
    - `top_services`
    - `month_to_date_spend` and `same_period_last_month_spend`
    - `month_to_date_change_pct`
  - Stores everything in `state["cost_data"]`

- **fetch_forecast_node**
  - Computes the **next full month** window (start and end‑exclusive)
  - Uses MCP to call `get_cost_forecast` with `DAILY` granularity
  - Uses `CostDataProcessor.extract_forecast_total` to get a compact number
  - Stores in `state["forecast_data"]`:
    - `forecast_next_month`
    - `forecast_month`
    - `forecast_period`

- **analyze_node**
  - Reads:
    - `user_query` (defaults to `"Analyze my AWS costs."` if missing)
    - `cost_data`
    - `forecast_data`
  - Calls `ManagerAgent.analyze(user_query, cost_data, forecast_data)`
  - Stores the LLM’s response in `state["analysis_result"]`

- **output_node**
  - Final pass‑through node; currently just logs and returns the accumulated state

---

## How the Graph Is Invoked

The helper method `ManagerGraph.invoke` constructs the initial state and runs the compiled LangGraph:

```python
from graph.manager_graph import ManagerGraph

graph = ManagerGraph()
final_state = await graph.invoke("Explain my AWS costs and forecast for next month.")

print(final_state["analysis_result"])
```

Internally, `invoke`:

1. Creates a `GraphState` Pydantic model with:
   - `user_query`
   - `discovered_tools` (empty list)
   - `cost_data`, `forecast_data`, `analysis_result` (all `None`)
   - `errors` (empty list)
2. Converts it to a dict via `.model_dump()`
3. Calls `self.graph.ainvoke(initial_state)` which runs:
   `START → discover_tools_node → fetch_cost_node → fetch_forecast_node → analyze_node → output_node → END`

---

## Running the Project

### 1. Install Dependencies

Make sure you have Python 3.10+ and `pip` installed, then:

```bash
pip install -r requirements.txt
```

Or, if you are using `uv` (recommended for reproducibility):

```bash
uv sync
```

### 2. Configure AWS Billing MCP

The MCP server configuration is in `server/aws_mcp.json`. Ensure that:

- Your AWS credentials and region are correctly configured in the environment used by the MCP server
- Any required IAM permissions for Cost Explorer, Budgets, and Forecast APIs are granted

### 3. Run the App

Depending on how this repo is wired, you can typically start the agent via:

```bash
python -m app
```

or:

```bash
python main.py
```

(Check `app.py` and `main.py` for the entrypoint you prefer.)

Once running, the app will:

1. Accept a user query
2. Execute the LangGraph workflow described above
3. Return an analysis that combines historical costs and a near‑term forecast

---

## Key Files

- `graph/manager_graph.py` – LangGraph orchestration of the manager workflow
- `agents/manager_agent.py` – LLM‑based agent that interprets cost and forecast data
- `utils/cost_processor.py` – Helpers to aggregate and normalize Cost Explorer / forecast responses
- `server/client.py` – MCP client responsible for talking to the AWS Billing MCP server
- `models/schemas.py` – Pydantic models including `GraphState`

---

## Extending the Workflow

You can customize the LangGraph workflow in `ManagerGraph` by:

- Adding additional nodes (e.g., anomaly detection, savings‑plans analysis)
- Branching based on state (e.g., if forecast is above a threshold, route to a mitigation planner node)
- Enriching `cost_data` and `forecast_data` with more granular dimensions (accounts, regions, services)

Because the workflow is a `StateGraph(dict)`, new nodes just need to read and write from the shared state dict in a consistent way.


