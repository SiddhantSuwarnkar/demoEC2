import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_valid_development_config():
    """Verify development settings with SQLite defaults."""
    cfg = Settings(
        ENVIRONMENT="development",
        DATABASE_URL="sqlite:///./stocksense.db",
    )
    assert cfg.ENVIRONMENT == "development"
    assert cfg.is_sqlite is True
    assert cfg.is_production is False


def test_invalid_log_level_detected():
    """Verify invalid log level raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(LOG_LEVEL="SUPER_VERBOSE_UNKNOWN")
    assert "Invalid LOG_LEVEL" in str(exc_info.value)


def test_cors_origins_parsing():
    """Verify CORS origins string is parsed into list of origins."""
    cfg = Settings(CORS_ORIGINS='["http://localhost:3000", "http://example.com"]')
    assert cfg.CORS_ORIGINS == ["http://localhost:3000", "http://example.com"]

    cfg_csv = Settings(CORS_ORIGINS="http://localhost:3000, http://frontend.org")
    assert cfg_csv.CORS_ORIGINS == ["http://localhost:3000", "http://frontend.org"]


def test_production_rejects_sqlite():
    """Verify production environment strictly rejects SQLite databases."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="sqlite:///./stocksense.db",
            SECRET_KEY="a_very_secure_production_key_32_characters_long",
        )
    assert "SQLite is not allowed in production" in str(exc_info.value)


def test_production_rejects_invalid_database_url():
    """Verify production requires a valid PostgreSQL URL."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="mysql://user:pass@localhost/db",
            SECRET_KEY="a_very_secure_production_key_32_characters_long",
        )
    assert "DATABASE_URL must be a valid PostgreSQL connection string" in str(exc_info.value)


def test_production_rejects_insecure_secret_key():
    """Verify production requires a secure, non-default secret key of sufficient length."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql://user:pass@localhost:5432/stocksense",
            SECRET_KEY="short",
        )
    assert "SECRET_KEY must be a cryptographically secure string" in str(exc_info.value)

    # Rejection of dev placeholder keyword
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql://user:pass@localhost:5432/stocksense",
            SECRET_KEY="dev_insecure_key_that_is_32_chars_long_padded",
        )
    assert "cannot use default dev placeholders" in str(exc_info.value)


def test_production_valid_postgres_config():
    """Verify valid PostgreSQL production configuration is accepted."""
    cfg = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql://prod_user:prod_pass@pg.example.com:5432/stocksense_prod",
        SECRET_KEY="real_production_secret_key_minimum_32_characters_long!",
    )
    assert cfg.is_production is True
    assert cfg.is_sqlite is False


def test_database_url_and_secret_masking():
    """Verify credentials and passwords are never displayed in string or masked representations."""
    cfg = Settings(
        DATABASE_URL="postgresql://stocksense_user:super_secret_db_pass@db.example.com:5432/stocksense",
        SECRET_KEY="my_secret_token_value_that_should_be_hidden",
    )
    # 1. Masked database URL hides password
    masked_url = cfg.masked_database_url
    assert "super_secret_db_pass" not in masked_url
    assert "***" in masked_url or "stocksense_user" in masked_url

    # 2. SecretStr hides raw value on str()
    assert "my_secret_token_value" not in str(cfg.SECRET_KEY)
    assert "**********" in str(cfg.SECRET_KEY)


def test_cors_middleware_wildcard_vs_explicit_credentials():
    """Verify that wildcard CORS origins do not enable credentials (security compliance)."""
    from starlette.middleware.cors import CORSMiddleware

    from app.main import create_app

    # Wildcard origin -> allow_credentials must be False
    cfg_wildcard = Settings(CORS_ORIGINS=["*"])
    app_wildcard = create_app(cfg_wildcard)
    cors_mw_wildcard = next(
        (m for m in app_wildcard.user_middleware if m.cls == CORSMiddleware), None
    )
    assert cors_mw_wildcard is not None
    assert cors_mw_wildcard.kwargs["allow_credentials"] is False

    # Explicit origin -> allow_credentials is True
    cfg_explicit = Settings(CORS_ORIGINS=["https://frontend.example.com"])
    app_explicit = create_app(cfg_explicit)
    cors_mw_explicit = next(
        (m for m in app_explicit.user_middleware if m.cls == CORSMiddleware), None
    )
    assert cors_mw_explicit is not None
    assert cors_mw_explicit.kwargs["allow_credentials"] is True
