import json
from typing import List, Literal, Union

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """
    Application settings with environment-specific validation.
    Enforces strict production safety rules (e.g., requiring PostgreSQL
    and secure secret keys when ENVIRONMENT == 'production').
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    ENVIRONMENT: Literal["development", "testing", "production"] = "development"
    PROJECT_NAME: str = "StockSense"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    # Database Configuration
    DATABASE_URL: str = "sqlite:///./stocksense.db"

    # Security
    SECRET_KEY: SecretStr = SecretStr("dev_insecure_secret_key_change_in_production_min32chars")

    # CORS
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://stocksense-self.vercel.app",
    ]

    # Logging
    LOG_LEVEL: str = "INFO"

    # CSV Import Limits & Parameters
    MAX_UPLOAD_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB
    MAX_IMPORT_ROWS: int = 5000
    DEFAULT_PREVIEW_LIMIT: int = 50
    DEFAULT_SERVICE_LEVEL: float = 0.95
    DEFAULT_LEAD_TIME_DAYS: int = 1
    DEFAULT_LEAD_TIME_STDDEV: float = 0.0
    DEFAULT_PACK_SIZE: int = 1
    DEFAULT_MOQ: int = 1

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    origins = json.loads(v)
                    return [str(o).strip().rstrip("/") for o in origins if str(o).strip()]
                except json.JSONDecodeError:
                    pass
            return [origin.strip().rstrip("/") for origin in v.split(",") if origin.strip()]
        if isinstance(v, list):
            return [str(o).strip().rstrip("/") for o in v if str(o).strip()]
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper_v = v.upper().strip()
        if upper_v not in valid_levels:
            raise ValueError(f"Invalid LOG_LEVEL '{v}'. Must be one of {valid_levels}")
        return upper_v

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "Settings":
        """
        Ensure strict production configuration standards.
        SQLite is prohibited in production.
        Production secret keys must be robust and not use default dev placeholders.
        """
        if self.ENVIRONMENT == "production":
            # 1. Database validation
            url_str = self.DATABASE_URL.lower()
            if url_str.startswith("sqlite"):
                raise ValueError(
                    "Production configuration error: SQLite is not allowed in production. "
                    "A PostgreSQL DATABASE_URL is required."
                )
            if not (
                url_str.startswith("postgresql://")
                or url_str.startswith("postgresql+psycopg2://")
                or url_str.startswith("postgresql+psycopg://")
            ):
                raise ValueError(
                    "Production configuration error: DATABASE_URL must be a valid PostgreSQL connection string."
                )

            # 2. Secret key validation
            secret_val = self.SECRET_KEY.get_secret_value()
            if (
                not secret_val
                or "dev" in secret_val.lower()
                or "insecure" in secret_val.lower()
                or len(secret_val) < 32
            ):
                raise ValueError(
                    "Production configuration error: SECRET_KEY must be a cryptographically secure string "
                    "with a minimum length of 32 characters and cannot use default dev placeholders."
                )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.lower().startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def masked_database_url(self) -> str:
        """
        Return the database URL with passwords masked, safe for logging.
        Never exposes credentials.
        """
        try:
            url = make_url(self.DATABASE_URL)
            return url.render_as_string(hide_password=True)
        except Exception:
            # Fallback if URL parsing fails
            return "[DATABASE_URL_UNPARSEABLE]"


# Global settings instance initialized from environment
settings = Settings()
