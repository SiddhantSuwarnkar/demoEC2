import os

from app.core.config import Settings


def test_isolation_no_production_credentials_required():
    """Verify testing can execute without any external/production database credentials."""
    test_cfg = Settings(
        ENVIRONMENT="testing",
        DATABASE_URL="sqlite:///:memory:",
        SECRET_KEY="isolated_test_secret_key_minimum_length_32_characters",
    )
    assert test_cfg.is_sqlite is True
    assert test_cfg.ENVIRONMENT == "testing"


def test_test_environment_cannot_reach_production_accidentally():
    """Verify test environment settings are isolated from production defaults."""
    # Ensure tests do not read production environment variables
    env_backup = os.environ.get("ENVIRONMENT")
    try:
        os.environ["ENVIRONMENT"] = "testing"
        cfg = Settings(DATABASE_URL="sqlite:///:memory:")
        assert cfg.ENVIRONMENT == "testing"
        assert cfg.is_production is False
    finally:
        if env_backup is not None:
            os.environ["ENVIRONMENT"] = env_backup
        else:
            os.environ.pop("ENVIRONMENT", None)
