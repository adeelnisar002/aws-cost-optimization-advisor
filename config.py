from pydantic_settings import BaseSettings
from pydantic import Field
import logging


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Groq
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama3-70b-8192", alias="GROQ_MODEL")

    # AWS / MCP (for deployment: leave AWS_PROFILE unset to use IAM role; set region as needed)
    mcp_billing_endpoint: str = Field(..., alias="MCP_BILLING_ENDPOINT")
    mcp_pricing_endpoint: str = Field(..., alias="MCP_PRICING_ENDPOINT")
    aws_profile: str = Field(default="", alias="AWS_PROFILE")  # empty = use IAM role (task/instance)
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_role_arn: str = Field(default="", alias="AWS_ROLE_ARN")  # SaaS: customer role we assume to access their account
    aws_external_id: str = Field(default="", alias="AWS_EXTERNAL_ID")  # optional: external ID when assuming customer role

    # Flask (for session; set in production)
    flask_secret_key: str = Field(default="cost-analysis-agent-dev-secret", alias="FLASK_SECRET_KEY")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("aws-cost-advisor")


