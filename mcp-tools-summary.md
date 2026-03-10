## AWS Billing & Cost Management MCP Tools (Discovered)

The following tools were discovered from the AWS Billing & Cost Management MCP server (via `mcp-test.py`):

- **cost-explorer**: Retrieves AWS cost and usage data using the Cost Explorer API.  
  - Key operations: `getCostAndUsage`, `getCostAndUsageWithResources`, `getDimensionValues`, `getCostForecast`, `getUsageForecast`, `getTagsOrValues`, `getCostCategories`, `getSavingsPlansUtilization`.

- **compute-optimizer**: Retrieves recommendations from AWS Compute Optimizer.  
  - Use for performance optimization and performance-based rightsizing.  
  - Key operations: `get_ec2_instance_recommendations`, `get_auto_scaling_group_recommendations`, `get_ebs_volume_recommendations`, `get_lambda_function_recommendations`, `get_rds_recommendations`, `get_ecs_service_recommendations`.

- **cost-optimization**: Retrieves cost optimization recommendations from AWS Cost Optimization Hub.  
  - Use for idle/unused resource detection and cost savings recommendations.  
  - Key operations:
    - `list_recommendation_summaries` (requires `group_by` such as `AccountId`, `Region`, `ActionType`, `ResourceType`, `RestartNeeded`, `RollbackPossible`, `ImplementationEffort`).
    - `list_recommendations`
    - `get_recommendation` (requires `resource_id` and `resource_type`).
  - Critical parameter notes:
    - `filters` must be a JSON string.
    - `max_results` must be an integer.
    - Service is only available in `us-east-1`.

- **storage-lens**: Query S3 Storage Lens metrics data using Athena SQL.  
  - Use `{table}` placeholder and standard SQL for storage and lifecycle analysis.

- **aws-pricing**: Comprehensive AWS pricing analysis tool.  
  - Operations: `get_service_codes`, `get_service_attributes`, `get_attribute_values`, `get_pricing_from_api`.

- **bcm-pricing-calc**: AWS Billing and Cost Management Pricing Calculator API helper.  
  - Operations: `list_workload_estimates`, `get_workload_estimate`, `list_workload_estimate_usage`, `get_preferences`.

- **budgets**: Retrieves AWS budget information using the AWS Budgets API.  
  - Returns budgets, limits, actual spend, forecasted spend, and filters.

- **cost-anomaly**: Retrieves AWS cost anomalies using Cost Explorer GetAnomalies API.

- **cost-comparison**: Retrieves AWS cost comparisons between two one-month periods.  
  - Operations: `getCostAndUsageComparisons`, `getCostComparisonDrivers`.

- **free-tier-usage**: Retrieves AWS Free Tier usage information.  
  - Operation: `get_free_tier_usage`.

- **rec-details**: Get detailed cost optimization recommendation with integrated data from multiple AWS services (Cost Optimization Hub, Compute Optimizer, Cost Explorer).

- **ri-performance**: Retrieves AWS Reserved Instance (RI) coverage and utilization data.  
  - Operations: `get_reservation_coverage`, `get_reservation_utilization`.

- **sp-performance**: Retrieves AWS Savings Plans coverage and utilization data.  
  - Operations: `get_savings_plans_coverage`, `get_savings_plans_utilization`, `get_savings_plans_utilization_details`.

- **session-sql**: Execute SQL queries on the persistent session database.  
  - Use to query tables created by other tools (e.g., `cost_explorer_sql`) and perform joins across data sources.


2026-03-04 02:40:15,957 - server.tool_registry - INFO - Invalidating MCP tool cache
2026-03-04 02:40:15,957 - server.tool_registry - INFO - Registered MCP tool: cost-explorer
2026-03-04 02:40:15,957 - server.tool_registry - INFO - Registered MCP tool: compute-optimizer
2026-03-04 02:40:15,957 - server.tool_registry - INFO - Registered MCP tool: cost-optimization
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: storage-lens
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: aws-pricing
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: bcm-pricing-calc
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: budgets
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: cost-anomaly
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: cost-comparison
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: free-tier-usage
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: rec-details
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: ri-performance
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: sp-performance
2026-03-04 02:40:15,960 - server.tool_registry - INFO - Registered MCP tool: session-sql
cost-explorer: Retrieves AWS cost and usage data using the Cost Explorer API.

IMPORTANT USAGE GUIDELINES:
- Use UnblendedCost metric by default (not BlendedCost) unless user specifies otherwise
- Exclude record_types 'Credit' and 'Refund' by default unless user requests inclusion
- Choose DAILY granularity for periods <3 months, MONTHLY for longer periods
- Start with high-level dimensions (SERVICE, LINKED_ACCOUNT) before detailed ones
- Always remember that the end_date is exclusive

USE THIS TOOL FOR:
- **Historical cost trends** and spending analysis (any time period)
- **Usage pattern analysis** over time
- **Cost breakdown** by service, account, region, or any dimension
- **Forecasting** future costs and usage
- **Resource-level cost analysis** (last 14 days)
- **Multi-dimensional cost analysis** with complex grouping

## OPERATIONS

1) getCostAndUsage — account-level historical cost/usage
   Required: operation="getCostAndUsage", start_date, end_date, granularity, metrics
   Optional: group_by, filter, next_token, max_pages
   Example: {"operation": "getCostAndUsage", "start_date": "2024-01-01", "end_date": "2024-02-01", "granularity": "DAILY", "metrics": ["UnblendedCost"], "group_by": "[{"Type": "DIMENSION", "Key": "SERVICE"}]"}

2. getCostAndUsageWithResources - Resource-level cost data (limited to last 14 days)
   Required: operation="getCostAndUsageWithResources", filter, granularity, start_date, end_date
   Optional: metrics, group_by
   Notes: RESOURCE_ID must be included in either filter OR group_by parameters. This operation is limited to past 14 days of data from current date. Hourly granularity is only available for EC2-Instances resource-level data. All other resource-level data is available at daily granularity.
   Example: {"operation": "getCostAndUsageWithResources", "start_date": "2025-08-07", "end_date": "2025-08-21", "granularity": "DAILY", "filter": "{"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}}", "group_by": "[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}]"}
   Returns: Cost data with resource-level granularity

3. getDimensionValues - List of available values for specified dimension
   Required: operation="getDimensionValues", dimension, start_date, end_date
   Optional: context, search_string, filter, max_results
   Example: {"operation": "getDimensionValues", "dimension": "SERVICE", "start_date": "2024-01-01", "end_date": "2024-02-01"}
   Returns: List of values for specified dimension with automatic pagination

4. getCostForecast - Future cost projections
   Required: operation="getCostForecast", metric, granularity, start_date, end_date
   Optional: filter, prediction_interval_level
   Example: {"operation": "getCostForecast", "metric": "UNBLENDED_COST", "granularity": "MONTHLY", "start_date": "2025-08-22", "end_date": "2025-11-22"}
   Notes: metric value for this operation should be in all caps
   Returns: Cost forecast for specified time period and granularity

5. getUsageForecast - Future usage projections
   Required: operation="getUsageForecast", metric, granularity, start_date, end_date, filter
   Optional: prediction_interval_level
   Example 1: {"operation": "getUsageForecast", "metric": "USAGE_QUANTITY", "granularity": "MONTHLY", "start_date": "2025-08-22", "end_date": "2025-11-22", "filter": "{"Dimensions": {"Key": "USAGE_TYPE_GROUP", "Values": ["EC2-Instance"]}}"}
   Example 2: {"operation": "getUsageForecast", "metric": "USAGE_QUANTITY", "granularity": "MONTHLY", "start_date": "2025-08-22", "end_date": "2025-11-22", "filter": "{"And": [{"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}}, {"Dimensions": {"Key": "USAGE_TYPE", "Values": ["BoxUsage:p4de.24xlarge"]}}]}"}
   Example 3: {"operation": "getUsageForecast", "metric": "USAGE_QUANTITY", "granularity": "MONTHLY", "start_date": "2025-08-22", "end_date": "2025-11-22", "filter": "{"Dimensions": {"Key": "USAGE_TYPE", "Values": ["BoxUsage:p4de.24xlarge", "Reservation:p4de.24xlarge", "UnusedBox:p4de.24xlarge"]}}", "group_by": "[{"Type": "DIMENSION", "Key": "REGION"}]"}
   Notes: Valid values for metric is: USAGE_QUANTITY, NORMALIZED_USAGE_AMOUNT. Valid values for granularity is: DAILY, MONTHLY. Filter is REQUIRED and must specify USAGE_TYPE or USAGE_TYPE_GROUP to define what usage units to forecast.
   Returns: Usage forecast for specified time period and granularity

6. getTagsOrValues - Available cost allocation tags or values
   Required: operation="getTagsOrValues"
   Optional: start_date, end_date, search_string, next_token, max_pages
   Example 1: {"operation": "getTagsOrValues"}
   Example 2: {"operation": "getTagsOrValues", "tag_key": "Environment"}
   Returns: List of available cost allocation tags with automatic pagination. If tag values for a particular key are needed, pass the tag key as a parameter.

8. getCostCategories - Available cost categories
   Required: operation="getCostCategories", start_date, end_date
   Optional: search_string, next_token, max_pages
   Example: {"operation": "getCostCategories", "start_date": "2024-01-01", "end_date": "2024-08-01"}
   Returns: List of available cost categories with automatic pagination

9. getSavingsPlansUtilization - Savings Plans utilization data
   Required: operation="getSavingsPlansUtilization", start_date, end_date
   Optional: granularity, filter
   Example: {"operation": "getSavingsPlansUtilization", "granularity": "MONTHLY"}
   Notes: This operation supports only DAILY and MONTHLY granularity
   Returns: Savings Plans utilization for the specified time period

DIMENSION REFERENCE:
- AZ: The Availability Zone (e.g., us-east-1a)
- DATABASE_ENGINE: The Amazon RDS database (e.g., Aurora, MySQL)
- DEPLOYMENT_OPTION: RDS deployment scope (SingleAZ, MultiAZ)
- INSTANCE_TYPE: The EC2 instance type (e.g., m4.xlarge)
- INSTANCE_TYPE_FAMILY: Family of instances (e.g., Compute Optimized, Memory Optimized)
- LINKED_ACCOUNT: AWS member accounts
- OPERATING_SYSTEM: OS type (e.g., Windows, Linux)
- PLATFORM: EC2 operating system
- PURCHASE_TYPE: Reservation type (e.g., On-Demand, Reserved)
- REGION: AWS Region
- SERVICE: AWS service (e.g., Amazon DynamoDB)
- TAG: Cost allocation tag
- TENANCY: EC2 tenancy (shared, dedicated)
- USAGE_TYPE: Usage type (e.g., DataTransfer-In-Bytes)
- RECORD_TYPE: Charge types (e.g., RI fees, usage costs)
compute-optimizer: Retrieves recommendations from AWS Compute Optimizer.

IMPORTANT USAGE GUIDELINES:
- Focus on recommendations with the highest estimated savings first
- Include all relevant details when presenting specific recommendations

USE THIS TOOL FOR:
- **Performance optimization** (CPU, memory, network utilization analysis)
- **Performance-based rightsizing** (not cost-based)

DO NOT USE FOR: Cost optimization or idle detection (use cost-optimization-hub)

This tool supports the following operations:
1. get_ec2_instance_recommendations: Get recommendations for EC2 instances
2. get_auto_scaling_group_recommendations: Get recommendations for Auto Scaling groups
3. get_ebs_volume_recommendations: Get recommendations for EBS volumes
4. get_lambda_function_recommendations: Get recommendations for Lambda functions
5. get_rds_recommendations: Get recommendations for RDS instances
6. get_ecs_service_recommendations: Get recommendations for ECS services

Each operation can be filtered by AWS account IDs, regions, finding types, and more.

Common finding types include:
- UNDERPROVISIONED: The resource doesn't have enough capacity
- OVERPROVISIONED: The resource has excess capacity and could be downsized
- OPTIMIZED: The resource is already optimized
- NOT_OPTIMIZED: The resource can be optimized but specific finding type isn't available
cost-optimization: Retrieves cost optimization recommendations from AWS Cost Optimization Hub.

IMPORTANT USAGE GUIDELINES:
- Focus on recommendations with the highest estimated savings first
- Include all relevant details when presenting specific recommendations

USE THIS TOOL FOR:
- **Idle/unused resource detection** (EC2, RDS, EBS, Lambda, etc.)
- **Cost savings recommendations** (rightsizing, stopping, deleting resources)
- **Reserved Instance and Savings Plans purchase recommendations**
- **Cross-service cost optimization analysis**
- **Monthly cost reduction opportunities**

DO NOT USE FOR: Performance optimization (use compute-optimizer)

Supported Operations:
1. list_recommendation_summaries: High-level overview of savings opportunities grouped by a dimension
2. list_recommendations: Detailed list of specific recommendations
3. get_recommendation: Get detailed information about a specific recommendation

IMPORTANT: 'list_recommendation_summaries' operation REQUIRES a 'group_by' parameter.
Valid 'group_by' values: AccountId, Region, ActionType, ResourceType, RestartNeeded, RollbackPossible, ImplementationEffort

CRITICAL PARAMETER REQUIREMENTS:
- 'filters' parameter must be passed as JSON string format
- 'max_results' must be integer (not string)
- 'get_recommendation' requires both 'resource_id' AND 'resource_type' parameters
- Service only available in us-east-1 region

Available Filter Parameters (pass as JSON string):
- resourceTypes: ['Ec2Instance', 'LambdaFunction', 'EbsVolume', 'EcsService', 'Ec2AutoScalingGroup', 'Ec2InstanceSavingsPlans', 'ComputeSavingsPlans', 'SageMakerSavingsPlans', 'Ec2ReservedInstances', 'RdsReservedInstances', 'OpenSearchReservedInstances', 'RedshiftReservedInstances', 'ElastiCacheReservedInstances', 'RdsDbInstanceStorage', 'RdsDbInstance', 'DynamoDbReservedCapacity', 'MemoryDbReservedInstances']
- actionTypes: ['Rightsize', 'Stop', 'Upgrade', 'PurchaseSavingsPlans', 'PurchaseReservedInstances', 'MigrateToGraviton', 'Delete', 'ScaleIn']
- implementationEfforts: ['VeryLow', 'Low', 'Medium', 'High', 'VeryHigh']
- regions: AWS region codes (e.g., ["us-east-1", "us-west-2"])
- accountIds: List of AWS account IDs
- restartNeeded: boolean
- rollbackPossible: boolean

Cost Optimization Hub provides recommendations across multiple AWS services, including:
- EC2 instances (right-sizing, Graviton migration)
- EBS volumes (unused volumes, IOPS optimization)
- RDS instances (right-sizing, engine optimization)
- Lambda functions (memory size optimization)
- SP/RI
- And more

Each recommendation includes:
- The resource ARN and ID
- The estimated monthly savings
- The current state of the resource
- The recommended state of the resource

storage-lens: Query S3 Storage Lens metrics data using Athena SQL.

IMPORTANT USAGE GUIDELINES:
- Before using this tool, provide a 1-3 sentence explanation starting with "EXPLANATION:"
- Use standard SQL syntax for Athena queries
- Use {table} as a placeholder for the Storage Lens metrics table name
- Perform aggregations (GROUP BY) when analyzing data across multiple dimensions

This tool allows you to analyze S3 Storage Lens metrics data using SQL queries.
Storage Lens provides metrics about your S3 storage, including:

- Storage metrics: Total bytes, object counts by storage class
- Cost optimization metrics: Transition opportunities, incomplete multipart uploads
- Data protection metrics: Replication, versioning, encryption status
- Activity metrics: Upload, download, and request metrics

STORAGE LENS EXPORT SCHEMA:
The Storage Lens export data has the following standard columns:
- version_number: The version of the S3 Storage Lens metrics being used
- configuration_id: The configuration_id of your S3 Storage Lens configuration
- report_date: The date that the metrics were tracked
- aws_account_number: Your AWS account number
- aws_region: The AWS Region for which the metrics are being tracked
- storage_class: The storage class (STANDARD, STANDARD_IA, GLACIER, etc.)
- record_type: The type of artifact being reported (ACCOUNT, BUCKET, PREFIX, STORAGE_LENS_GROUP_BUCKET, STORAGE_LENS_GROUP_ACCOUNT)
- record_value: The value of the record_type artifact (account ID, bucket name, prefix, or Storage Lens group ARN)
- bucket_name: The name of the bucket (when record_type is BUCKET or PREFIX)
- metric_name: The name of the metric (e.g., 'StorageBytes', 'ObjectCount', 'EncryptedStorageBytes')
- metric_value: The numeric value of the metric

IMPORTANT: Metrics are stored in rows, not columns. Each row represents one metric value for a specific combination of dimensions.

Environment variables:
- STORAGE_LENS_MANIFEST_LOCATION: S3 URI to manifest file or folder (required)
- STORAGE_LENS_OUTPUT_LOCATION: S3 location for Athena query results (optional)

Example queries:
1. Top 10 buckets by storage size:
   SELECT
       bucket_name,
       SUM(CAST(metric_value AS BIGINT)) as total_size
   FROM {table}
   WHERE metric_name = 'StorageBytes'
   GROUP BY bucket_name
   ORDER BY total_size DESC
   LIMIT 10

2. Storage distribution by storage class:
   SELECT
       storage_class,
       SUM(CAST(metric_value AS BIGINT)) as total_size
   FROM {table}
   WHERE metric_name = 'StorageBytes'
   GROUP BY storage_class
   ORDER BY total_size DESC

3. Buckets with incomplete multipart uploads:
   SELECT
       bucket_name,
       SUM(CAST(metric_value AS BIGINT)) as incomplete_bytes
   FROM {table}
   WHERE metric_name = 'IncompleteMultipartUploadStorageBytes'
     AND CAST(metric_value AS BIGINT) > 0
   GROUP BY bucket_name
   ORDER BY incomplete_bytes DESC

4. Storage Distribution by Region and Storage Class:
   SELECT
       aws_region,
       storage_class,
       SUM(CAST(metric_value AS BIGINT)) as total_bytes
   FROM {table}
   WHERE metric_name = 'StorageBytes'
   GROUP BY aws_region, storage_class
   ORDER BY total_bytes DESC

5. Object Lifecycle Management Opportunities:
   SELECT
       aws_region,
       storage_class,
       SUM(CASE WHEN metric_name = 'NonCurrentVersionStorageBytes' THEN CAST(metric_value AS BIGINT) ELSE 0 END) as noncurrent_bytes,
       SUM(CASE WHEN metric_name = 'StorageBytes' THEN CAST(metric_value AS BIGINT) ELSE 0 END) as total_bytes    
   FROM {table}
   WHERE metric_name IN ('NonCurrentVersionStorageBytes', 'StorageBytes')
   GROUP BY aws_region, storage_class
   HAVING SUM(CASE WHEN metric_name = 'NonCurrentVersionStorageBytes' THEN CAST(metric_value AS BIGINT) ELSE 0 END) > 0
   ORDER BY noncurrent_bytes DESC

6. Lifecycle Rule Analysis:
   SELECT
       bucket_name,
       SUM(CASE WHEN metric_name = 'TotalLifecycleRuleCount' THEN CAST(metric_value AS INTEGER) ELSE 0 END) as lifecycle_rule_count,
       SUM(CASE WHEN metric_name = 'StorageBytes' THEN CAST(metric_value AS BIGINT) ELSE 0 END) as total_bytes    
   FROM {table}
   WHERE metric_name IN ('TotalLifecycleRuleCount', 'StorageBytes')
   GROUP BY bucket_name
   ORDER BY lifecycle_rule_count ASC, total_bytes DESC
aws-pricing: Comprehensive AWS pricing analysis tool that provides access to AWS service pricing information and cost analysis capabilities.

This tool supports four main operations:
1. get_service_codes: Get a comprehensive list of AWS service codes from the AWS Price List API
2. get_service_attributes: Get filterable attributes for a specific AWS service's pricing
3. get_attribute_values: Get all valid values for a specific attribute of an AWS service
4. get_pricing_from_api: Get detailed pricing information from AWS Price List API with optional filters

USE THE OPERATIONS IN THIS ORDER:
1. get_service_codes: Entry point - discover available AWS services and their unique service codes. Note that service codes may not match your expectations, so it's best to get service codes first.
2. get_service_attributes: Second step - understand which dimensions affect pricing for a chosen service
3. get_attribute_values: Third step - get possible values you can use in pricing filters
4. get_pricing_from_api: Final step - retrieve actual pricing data based on service and filters
**If you deviate from this order of operations, you will struggle to form the correct filters, and you will not get results from the API**

IMPORTANT GUIDELINES:
- When retrieving foundation model pricing, always use the latest models for comparison
- For database compatibility with services, only include confirmed supported databases
- Providing less information is better than giving incorrect information
- Price list APIs can return large data volumes. Use narrower filters to retrieve less data when possible
- Service codes often differ from AWS console names (e.g., 'AmazonES' for OpenSearch)

ARGS:
      ctx: The MCP context object
      operation: The pricing operation to perform ('get_service_codes', 'get_service_attributes', 'get_attribute_values', 'get_pricing_from_api')
      service_code: AWS service code (e.g., 'AmazonEC2', 'AmazonS3', 'AmazonES'). Required for get_service_attributes, get_attribute_values, and get_pricing_from_api operations.
      attribute_name: Attribute name (e.g., 'instanceType', 'location', 'storageClass'). Required for get_attribute_values operation.
      region: AWS region (e.g., 'us-east-1', 'us-west-2', 'eu-west-1'). Required for get_pricing_from_api operation.
      filters: Optional filters for pricing queries. Format: {'instanceType': 't3.medium', 'location': 'US East (N. Virginia)'}

RETURNS:
        Dict containing the pricing information

SUPPORTED AWS PRICING API REGIONS:
- Classic partition: us-east-1, eu-central-1, ap-southeast-1
- China partition: cn-northwest-1
The tool automatically maps your region to the nearest pricing endpoint.
bcm-pricing-calc: Allows working with workload estimates using the AWS Billing and Cost Management Pricing Calculator API.

IMPORTANT USAGE GUIDELINES:
- Always first check the rate preference setting for the authorized principal by calling the get_preferences operation.
- DO NOT state assumptions about Free Tier API

USE THIS TOOL FOR:
- Listing available **workload estimates** for the logged in account.
- **Filter list of available workload estimates** using name, status, created date, or expiration date.
- Get **details of a workload estimate**.
- Get the list of **services, usage type, operation, and usage amount** modeled within a workload estimate.       
- Get **rate preferences** set for Pricing Calculator. These rate preferences denote what rate preferences can be used by each account type in your organization.

## OPERATIONS

1) list_workload_estimates - list of available workload estimates
   Required: operation="list_workload_estimates"
   Optional: created_after, created_before, expires_after, expires_before, status_filter, name_filter, name_match_option, next_token, max_results
   Returns: List of all workload estimates for the account.

2) get_workload_estimate - get details of a workload estimate
   Required: operation="get_workload_estimate", identifier
   Returns: Details of a specific workload estimate.

3) list_workload_estimate_usage - list of modeled usage lines within a workload estimate
   Required: operation="get_workload_estimate", identifier
   Optional: usage_account_id_filter, service_code_filter, usage_type_filter, operation_filter, location_filter, usage_group_filter, next_token, max_results
   Returns: List of usage associated with a workload estimate.

4) get_preferences - get the rate preferences available to an account
   Required: operation="get_preferences"
   Returns: Retrieves the current preferences for AWS Billing and Cost Management Pricing Calculator.

budgets: Retrieves AWS budget information using the AWS Budgets API.

This tool uses the DescribeBudgets API to retrieve all budgets for an account.

The API returns information about:
- Budget names, types, and time periods
- Budget limits (amount and unit)
- Current actual spend
- Forecasted spend
- Cost filters applied to budgets

With this information, you can determine which budgets have been exceeded or are projected to exceed their limits.

The tool automatically retrieves the AWS account ID of the calling identity or uses the provided account_id.      
cost-anomaly: Retrieves AWS cost anomalies using the Cost Explorer GetAnomalies API.

This tool allows you to retrieve cost anomalies detected on your AWS account during a specified time period.      
Anomalies are available for up to 90 days.

You can filter anomalies by:
- Date range (required)
- Monitor ARN (optional)
- Feedback status (optional)
- Total impact (optional)

Feedback status options:
- YES: Anomalies marked as accurate
- NO: Anomalies marked as inaccurate
- PLANNED_ACTIVITY: Anomalies marked as planned activities
cost-comparison: Retrieves AWS cost comparisons between two one-month periods.

Do not use this tool except for comparing the costs of one month to the costs of another month. This tool should not be used for week-over-week or quarter-over-quarter (e.g., comparing Q2 vs. Q1) analysis.

USE THIS TOOL ONLY FOR:
- **Month-to-month cost variance analysis** (e.g., January vs February)
- **Root cause analysis** of cost changes between specific months
- **Detailed cost driver identification** (what exactly caused the cost change)
- **Service-level impact analysis** for month-over-month changes
- **Executive reporting** on monthly cost variances

STRICT LIMITATIONS:
- ONLY compares exactly one month to another month
- Both periods must start on 1st day of month, end on 1st day of next month
- Cannot compare weeks, quarters, or custom periods
- DO NOT USE for general cost analysis or flexible time periods

This tool supports two main operations:
1. getCostAndUsageComparisons: Compare costs between two time periods with flexible grouping and filtering        
2. getCostComparisonDrivers: Identify key factors driving cost changes between two time periods

Both operations require:
- BaselineTimePeriod: Earlier time period for comparison (must be exactly one month)
- ComparisonTimePeriod: Later time period for comparison (must be exactly one month)
- MetricForComparison: The cost metric to compare (e.g., BlendedCost, UnblendedCost)

Supported metrics for comparison include:
- AmortizedCost: Costs with upfront and recurring reservation fees spread across the period
- BlendedCost: Average cost of all usage throughout the billing period
- NetAmortizedCost: Amortized cost after discounts
- NetUnblendedCost: Unblended cost after discounts
- NormalizedUsageAmount: Normalized usage amount
- UnblendedCost: Actual costs incurred during the specified period
- UsageQuantity: Usage amounts in their respective units

You can group results by dimensions such as:
- SERVICE: AWS service (e.g., Amazon EC2, Amazon S3)
- LINKED_ACCOUNT: Member accounts in an organization
- REGION: AWS Region
- USAGE_TYPE: Type of usage (e.g., BoxUsage:t2.micro)
- INSTANCE_TYPE: EC2 instance type (e.g., t2.micro, m5.large)
- PLATFORM: Operating system (e.g., Windows, Linux)
- TENANCY: Instance tenancy (e.g., shared, dedicated)
- RECORD_TYPE: Record type (e.g., Usage, Credit, Tax)
- LEGAL_ENTITY_NAME: AWS seller of record

Note:
- Time periods must start and end on the first day of a month, with a duration of exactly one month
- The getCostComparisonDrivers operation automatically includes SERVICE and USAGE_TYPE dimensions
- Data is available for the last 13 months, or up to 38 months if multi-year data is enabled
free-tier-usage: Retrieves AWS Free Tier usage information using the Free Tier Usage API.

This tool provides insights into your AWS Free Tier usage across services:

1. get_free_tier_usage: Shows your current Free Tier usage across AWS services
   - Helps identify where you are approaching Free Tier limits
   - Shows actual usage against Free Tier allocations
   - Supports filtering by service, region, or usage type
   - Possible Dimensions values are: 'SERVICE'|'OPERATION'|'USAGE_TYPE'|'REGION'|'FREE_TIER_TYPE'|'DESCRIPTION'|'USAGE_PERCENTAGE'
   - Possible MatchOptions are: 'EQUALS'|'STARTS_WITH'|'ENDS_WITH'|'CONTAINS'|'GREATER_THAN_OR_EQUAL'

rec-details: Get detailed cost optimization recommendation with integrated data from multiple AWS services.       

This tool combines data from:
- Cost Optimization Hub (base recommendation)
- AWS Compute Optimizer (detailed metrics for compute resources)
- Cost Explorer (Savings Plans/RI purchase recommendations)

It provides comprehensive analysis with utilization metrics, savings calculations, and implementation guidance.   

RESPONSE FORMATTING INSTRUCTIONS:
The tool may return both raw recommendation data and a formatting template.
When presenting the recommendation:
1. If a template is provided, use it to organize your response
2. If no template is provided, structure your response in a clear, logical manner
3. Always include key information like resource details, savings amounts, and implementation steps
4. Ensure all numeric values (costs, savings, metrics) are included
5. Add natural language explanations to make the information more accessible
ri-performance: Retrieves AWS Reserved Instance (RI) coverage and utilization data using the Cost Explorer API.   

This tool provides insights into your Reserved Instance (RI) and Savings Plans usage patterns through two main operations:

1. get_reservation_coverage: Shows how much of your eligible usage is covered by RIs
   - Helps identify opportunities to purchase additional RIs
   - Supports grouping by dimensions like REGION, INSTANCE_TYPE, etc.
   - Can filter by specific services, regions, or instance types

2. get_reservation_utilization: Shows how effectively you're using your purchased RIs
   - Reveals underutilized or idle reserved capacity
   - Can be grouped by SUBSCRIPTION_ID to see utilization per RI
   - Helps identify RIs that could be modified or sold in the marketplace

Supported dimensions for grouping reservation coverage:
- AZ: Availability Zone
- INSTANCE_TYPE: Instance type (e.g., m4.xlarge)
- LINKED_ACCOUNT: Member accounts in organization
- PLATFORM: Operating system
- REGION: AWS Region
- SERVICE: AWS service (EC2, RDS, etc.)
- TENANCY: Instance tenancy (default, dedicated)

Reservation utilization can only be grouped by SUBSCRIPTION_ID.
sp-performance: Tool that retrieves AWS Savings Plans coverage and utilization data using the Cost Explorer API.  

This tool provides insights into your Savings Plans usage patterns through three main operations:

1. get_savings_plans_coverage: Shows how much of your eligible usage is covered by Savings Plans
2. get_savings_plans_utilization: Shows overall utilization metrics for your Savings Plans
3. get_savings_plans_utilization_details: Shows detailed per-Savings Plan utilization
session-sql: Execute SQL queries on the persistent session database.

This tool queries tables created by other tools (like cost_explorer_sql) within the current session.
All tools share the same database, allowing cross-tool data analysis and joins.

Use this tool to:
- Query tables created by cost_explorer_sql and other tools
- Join data from multiple AWS APIs
- Perform complex analysis across different data sources

Common queries:
- SELECT name FROM sqlite_master WHERE type='table' -- List all tables
- SELECT * FROM [table_name] LIMIT 10 -- Preview table data