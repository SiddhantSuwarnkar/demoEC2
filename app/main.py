from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_router as v1_router
from app.core.config import Settings, settings
from app.core.errors import register_error_handlers
from app.core.logging import logger, setup_logging
from app.db.session import check_database_connection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifecycle management.
    Performs startup validation and resource cleanup on shutdown.
    """
    # 1. Initialize logging
    setup_logging(settings.LOG_LEVEL)
    logger.info(
        "Starting %s v%s in %s environment",
        settings.PROJECT_NAME,
        settings.VERSION,
        settings.ENVIRONMENT,
    )
    logger.info("Configured database: %s", settings.masked_database_url)

    # 2. Startup validation
    if settings.is_production:
        settings.validate_production_requirements()
        db_ok = check_database_connection()
        if not db_ok:
            logger.critical("Fatal: Database is unreachable in production environment!")
            raise RuntimeError("Database connection could not be established on startup.")
    else:
        # Development check (non-fatal warning if DB not yet migrated)
        db_ok = check_database_connection()
        if db_ok:
            logger.info("Database connectivity verified.")
        else:
            logger.warning(
                "Database connectivity check failed or tables pending migration. "
                "Run 'alembic upgrade head' to apply schema."
            )

    yield

    logger.info("Shutting down %s", settings.PROJECT_NAME)


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """
    FastAPI application factory.
    Allows injecting custom settings for tests.
    """
    active_settings = custom_settings or settings

    # OpenAPI docs are enabled in development/testing, disabled in production
    is_prod = active_settings.is_production
    docs_url = None if is_prod else "/docs"
    redoc_url = None if is_prod else "/redoc"
    openapi_url = None if is_prod else "/openapi.json"

    app = FastAPI(
        title=active_settings.PROJECT_NAME,
        version=active_settings.VERSION,
        description="Adaptive, Risk-Aware Inventory Replenishment Engine",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # CORS configuration
    if active_settings.CORS_ORIGINS:
        has_wildcard = "*" in active_settings.CORS_ORIGINS
        cors_origins = (
            list(active_settings.CORS_ORIGINS)
            if isinstance(active_settings.CORS_ORIGINS, list)
            else [active_settings.CORS_ORIGINS]
        )
        if "https://stocksense-self.vercel.app" not in cors_origins and not has_wildcard:
            cors_origins.append("https://stocksense-self.vercel.app")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_origin_regex=r"^(https:\/\/.*\.vercel\.app|https:\/\/.*\.ngrok-free\.(dev|app)|http:\/\/localhost:\d+|http:\/\/127\.0\.0\.1:\d+)$",
            allow_credentials=not has_wildcard,
            allow_private_network=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Register standardized error handlers
    register_error_handlers(app)

    # Mount routers
    app.include_router(health_router)
    app.include_router(v1_router, prefix=active_settings.API_V1_PREFIX)

    @app.get("/", tags=["Root"], summary="Root Index")
    def root_index():
        return {
            "service": active_settings.PROJECT_NAME,
            "version": active_settings.VERSION,
            "environment": active_settings.ENVIRONMENT,
            "docs_url": docs_url,
            "health_live": "/health/live",
            "health_ready": "/health/ready",
            "api_v1_status": f"{active_settings.API_V1_PREFIX}/status",
            "api_v1_products": f"{active_settings.API_V1_PREFIX}/products",
        }

    return app


# Default ASGI application instance
app = create_app()
