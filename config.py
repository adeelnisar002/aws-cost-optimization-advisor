from pydantic_settings import BaseSettings
from pydantic import Field
import logging


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Groq
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama3-70b-8192", alias="GROQ_MODEL")

    # AWS / MCP
    mcp_billing_endpoint: str = Field(..., alias="MCP_BILLING_ENDPOINT")
    mcp_pricing_endpoint: str = Field(..., alias="MCP_PRICING_ENDPOINT")
    aws_profile: str = Field(default="default", alias="AWS_PROFILE")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")

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


