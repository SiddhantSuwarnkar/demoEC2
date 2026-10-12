# StockSense Backend — Frontend API Integration Guide

This guide provides the complete, audited REST API contract, workflows, configuration, and integration instructions for the independently developed frontend communicating with the **StockSense Backend**.

All endpoint paths, HTTP methods, parameter names, and schemas in this document have been audited directly against the FastAPI application and Pydantic models.

---

## 1. Quickstart & Local Backend Setup

### Prerequisites
- Python 3.11 (`python --version` must report 3.11.x)
- PostgreSQL 15+ (local instance or Docker container on port 5434 or 5432)
- Windows PowerShell / Linux Bash

### Step-by-Step Local Startup (PowerShell)
```powershell
# 1. Navigate to backend directory
cd backend

# 2. Activate virtual environment
.\.venv\Scripts\Activate.ps1

# 3. Configure local environment
Copy-Item .env.example .env
# Ensure DATABASE_URL in .env points to your PostgreSQL database:
# DATABASE_URL="postgresql+psycopg2://stocksense_app:stocksense_dev_password@localhost:5434/stocksense"

# 4. Run database migrations to apply latest schema
.\.venv\Scripts\alembic.exe upgrade head

# 5. Start the ASGI development server
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

- **Base URL**: `http://localhost:8000` (or `http://127.0.0.1:8000`)
- **API Version Prefix**: `/api/v1`
- **Interactive OpenAPI Documentation**: `http://localhost:8000/docs` (Swagger UI)
- **Alternative Documentation**: `http://localhost:8000/redoc` (ReDoc)
- **OpenAPI Schema (JSON)**: `http://localhost:8000/openapi.json`

*(Note: OpenAPI documentation endpoints `/docs`, `/redoc`, and `/openapi.json` are automatically disabled when `ENVIRONMENT=production` for security).*

---

## 2. CORS Configuration

The backend uses FastAPI's `CORSMiddleware`. Allowed origins are configured via the `CORS_ORIGINS` environment variable.

### Local Development Default
```ini
CORS_ORIGINS='["http://localhost:3000", "http://127.0.0.1:3000"]'
```

### Deployed Frontend Origins
In staging or production, supply a JSON array or comma-separated list of origins in `.env` or container environment variables:
```ini
CORS_ORIGINS='["https://stocksense.example.com", "https://app.stocksense.com"]'
```
or:
```ini
CORS_ORIGINS=https://stocksense.example.com,https://app.stocksense.com
```

> **Security Rule**: The backend strictly adheres to CORS standards. If wildcard `*` is specified, credentials (`allow_credentials`) are automatically disabled to prevent browser security exceptions. When explicit origins are supplied, `allow_credentials` is enabled.

---

## 3. Standard Response & Error Format

All API responses return structured JSON. Successful responses match the Pydantic schemas documented below.

When an error occurs (e.g. 400 Bad Request, 404 Not Found, 422 Unprocessable Content, or 500 Internal Server Error), the backend guarantees the following structured error envelope:

```json
{
  "error": {
    "code": "PRODUCT_NOT_FOUND",
    "message": "Product with ID 999 was not found.",
    "details": null
  }
}
```

For validation errors (HTTP 422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {
        "field": "body.demand_surge_pct",
        "issue": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```

Internal server exceptions mask database credentials, SQL statements, and internal stack traces from the API response while logging full diagnostic details to backend logs.

---

## 4. Complete Endpoint Reference

### A. Health & Service Status

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Root service metadata and navigation links |
| `GET` | `/health/live` | Liveness probe (returns HTTP 200 `{ "status": "alive" }`) |
| `GET` | `/health/ready` | Readiness probe (verifies PostgreSQL connection; HTTP 200 or 503) |
| `GET` | `/api/v1/status` | Current API version, active phase capabilities, and status |
| `GET` | `/api/v1/health` | Alias readiness probe under API v1 prefix |

#### `GET /health/ready` Response Example:
```json
{
  "status": "ready",
  "database": "connected",
  "database_type": "postgresql",
  "version": "0.1.0"
}
```

---

### B. Product & Demand Catalog

| Method | Endpoint | Query Parameters | Description |
|---|---|---|---|
| `GET` | `/api/v1/products` | None | Returns complete array of products (`List[ProductRead]`) |
| `GET` | `/api/v1/products/{product_id}` | None | Product detail including lead time, costs, and stock |
| `GET` | `/api/v1/products/{product_id}/demand` | None | Chronological daily demand records (`List[DemandRecordRead]`) |

#### `GET /api/v1/products/{product_id}` Response Example:
```json
{
  "id": 1,
  "sku": "SKU-BEV-001",
  "product_name": "Premium Sparkling Water 500ml",
  "category": "Beverages",
  "current_stock": 145,
  "unit_cost": 1.20,
  "unit_price": 2.50,
  "holding_cost_per_unit_per_year": 0.35,
  "ordering_cost_per_order": 25.00,
  "lead_time_days": 5,
  "lead_time_variance_days": 1.0,
  "min_order_quantity": 24,
  "pack_size": 12,
  "is_active": true
}
```

---

### C. CSV Imports (Two-Phase Preview & Confirmation)

The import workflow is strictly **two-phase**: upload & preview first to obtain an `import_id`, then execute a transactional confirm using that `import_id`. This prevents silent partial imports or dirty writes.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/imports/preview` | Upload and preview CSV (`file_type` query: `products`, `demand`, or `suppliers`) |
| `POST` | `/api/v1/imports/products/preview` | Upload and preview products CSV |
| `POST` | `/api/v1/imports/demand/preview` | Upload and preview daily demand CSV |
| `POST` | `/api/v1/imports/suppliers/preview` | Upload and preview supplier settings CSV |
| `POST` | `/api/v1/imports/{import_id}/confirm` | Transactionally persist records from validated import job |
| `GET` | `/api/v1/imports/{import_id}` | Retrieve durable status, counts, and diagnostic summary of import job |

#### 1. Preview CSV Upload
`POST /api/v1/imports/preview?file_type=demand`
- **Form-data**: `file` (multipart/form-data, `.csv` format, max 5 MB, max 5,000 rows)
- **Response**: `ImportPreviewResponse`
```json
{
  "import_id": "imp_6b2d13e0",
  "filename": "daily_demand.csv",
  "file_type": "demand",
  "total_rows": 120,
  "valid_rows": 120,
  "error_rows": 0,
  "warning_rows": 0,
  "can_confirm": true,
  "preview_rows": [
    {
      "row_number": 1,
      "raw_data": {"sku": "SKU-BEV-001", "date": "2026-01-01", "quantity": "25"},
      "errors": [],
      "warnings": []
    }
  ],
  "row_errors": []
}
```

#### 2. Confirm CSV Import
`POST /api/v1/imports/imp_6b2d13e0/confirm`
- **Path Parameter**: `import_id`
- **Request Body**: None
- **Response**: `ImportConfirmResponse`
```json
{
  "import_id": "imp_6b2d13e0",
  "file_type": "demand",
  "status": "success",
  "persisted_count": 120,
  "summary": "Import imp_6b2d13e0 confirmed: 120 demand records successfully written.",
  "diagnostics": {
    "total_rows": 120,
    "valid_rows": 120,
    "error_rows": 0
  }
}
```

---

### D. Replenishment Recommendations

StockSense calculates explainable, deterministic inventory replenishment recommendations incorporating Safety Stock, Reorder Point (ROP), Economic Order Quantity (EOQ), Minimum Order Quantity (MOQ), and Pack-Size constraints.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/recommendations/calculate` | Calculate and persist a new recommendation snapshot |
| `GET` | `/api/v1/products/{product_id}/recommendations/latest` | Retrieve latest recommendation snapshot for a product |
| `GET` | `/api/v1/products/{product_id}/recommendations` | Retrieve recommendation history for a product (`limit`, `offset`) |
| `GET` | `/api/v1/recommendations` | Overview list across products (`latest_only`, `urgency`, `status`, `reorder_only`, `limit`, `offset`) |
| `GET` | `/api/v1/recommendations/{recommendation_id}` | Retrieve a specific recommendation snapshot by primary key ID |
| `PATCH` | `/api/v1/recommendations/{recommendation_id}/status` | Update review status (`pending`, `approved`, `rejected`) |

#### 1. Calculate Recommendation Request:
`POST /api/v1/products/1/recommendations/calculate`
```json
{
  "target_service_level": 0.95,
  "lead_time_days": 5,
  "lead_time_stddev_days": 1.0
}
```
*(All fields in `RecommendationCalculateRequest` are optional overrides; if omitted or `{}` is passed, catalog and supplier default parameters are used).*

#### 2. Calculate Recommendation Response (`RecommendationRead`):
```json
{
  "id": 14,
  "product_id": 1,
  "recommended_order_quantity": 48,
  "current_stock": 145,
  "safety_stock": 28,
  "reorder_point": 151,
  "inventory_position": 145.0,
  "urgency": "high",
  "explanation": "Current inventory position (145) is at or below reorder point (151). Recommended order quantity of 48 units brings position above reorder point with MOQ and pack size rounding applied.",
  "assumptions": {
    "service_level": 0.95,
    "z_score": 1.644854,
    "lead_time_days": 5.0,
    "lead_time_stddev": 1.0,
    "mean_daily_demand": 24.5,
    "daily_demand_stddev": 6.2,
    "eoq": 42.8,
    "moq": 24,
    "pack_size": 12,
    "in_transit_inventory_included": false,
    "in_transit_limitation_note": "In-transit purchase orders are currently assumed unavailable and not added to inventory position."
  },
  "status": "pending",
  "formula_version": "v1.0",
  "calculated_at": "2026-10-11T12:00:00Z",
  "input_snapshot": {
    "current_stock": 145,
    "lead_time_days": 5,
    "lead_time_stddev": 1.0,
    "target_service_level": 0.95
  },
  "mean_daily_demand": 24.5,
  "daily_demand_stddev": 6.2,
  "average_lead_time_days": 5.0,
  "lead_time_stddev_days": 1.0
}
```

#### 3. Update Recommendation Status:
`PATCH /api/v1/recommendations/14/status`
```json
{
  "status": "approved"
}
```
*(Allowed status values in `RecommendationStatusUpdateRequest`: `"pending"`, `"approved"`, `"rejected"`).*

---

### E. Demand Anomaly Review

Deterministic anomaly detection based on rolling Median/MAD and IQR statistics, paired with human-in-the-loop review audit tracking. Raw source demand records remain strictly immutable.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/anomalies/detect` | Execute deterministic anomaly detector on product demand history |
| `GET` | `/api/v1/products/{product_id}/anomalies` | List anomaly candidates for product (`review_status`, `classification`, `skip`, `limit`) |
| `GET` | `/api/v1/products/{product_id}/anomalies/summary` | Summary counts for a single product |
| `GET` | `/api/v1/anomalies` | Global anomaly list across all products (`product_id`, `sku`, `review_status`, `skip`, `limit`) |
| `GET` | `/api/v1/anomalies/summary` | Global aggregate counts across catalog (`total`, `pending`, `reviewed`, `dismissed`) |
| `GET` | `/api/v1/anomalies/{anomaly_id}` | Retrieve complete anomaly candidate detail and detector evidence |
| `PATCH` | `/api/v1/anomalies/{anomaly_id}/review` | Submit user-confirmed classification and review notes |
| `POST` | `/api/v1/anomalies/{anomaly_id}/reviews` | Spec alias for submitting review decision |
| `POST` | `/api/v1/anomalies/{anomaly_id}/dismiss` | Dismiss anomaly candidate as false positive or normal variance |

#### 1. Human Review Submission:
`PATCH /api/v1/anomalies/42/review`
```json
{
  "classification": "promotion",
  "review_status": "reviewed",
  "reviewer": "RetailOpsLead",
  "reviewer_notes": "Verified promotional spike during regional summer festival.",
  "user_treatment": "clamp",
  "adjusted_quantity": 40.0
}
```
*(Valid `classification` values: `"promotion"`, `"data_error"`, `"unknown"`. Valid `review_status` values: `"pending"`, `"reviewed"`, `"dismissed"`).*

#### 2. Anomaly Summary Response (`AnomalySummaryResponse`):
```json
{
  "product_id": null,
  "sku": null,
  "total_anomalies": 18,
  "pending_count": 5,
  "reviewed_count": 11,
  "dismissed_count": 2,
  "by_classification": {
    "promotion": 8,
    "data_error": 3,
    "unknown": 7
  },
  "by_type": {
    "spike": 12,
    "drop": 6
  }
}
```

---

### F. Forecast Benchmark & Model Tournament

StockSense provides a **chronological holdout benchmarking engine** comparing a Moving Average baseline against Prophet.
> **Key Distinctions**:
> 1. This evaluates model accuracy over an **unseen historical holdout window** (e.g. 20% most recent historical observations).
> 2. It compares MAE and WAPE on identical holdout dates.
> 3. It is a benchmark for model selection, NOT an unconstrained future projection. Missing dates are never silently filled with zeroes.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/forecast-benchmark` | Run holdout evaluation & model tournament |
| `GET` | `/api/v1/forecast-runs` | List benchmark runs across products (query params: `product_id`, `page`, `limit`) |
| `GET` | `/api/v1/products/{product_id}/forecast-runs` | List benchmark runs for a product (query params: `page`, `limit`) |
| `GET` | `/api/v1/forecast-runs/{run_id}` | Retrieve benchmark run summary |
| `GET` | `/api/v1/forecast-runs/{run_id}/metrics` | Detailed per-day evaluation points & error metrics |

#### 1. Trigger Benchmark:
`POST /api/v1/products/1/forecast-benchmark`
```json
{
  "test_size": 0.20,
  "window_size": 7,
  "prophet_growth": "linear",
  "prophet_seasonality_mode": "additive"
}
```

#### 2. Benchmark Summary Response (`ForecastRunRead`):
```json
{
  "id": 5,
  "product_id": 1,
  "sku": "SKU-BEV-001",
  "product_name": "Premium Sparkling Water 500ml",
  "status": "completed",
  "baseline_method": "moving_average",
  "candidate_method": "prophet",
  "mae_baseline": 3.42,
  "wape_baseline": 0.124,
  "mae_candidate": 2.15,
  "wape_candidate": 0.078,
  "training_start_date": "2026-01-01",
  "training_end_date": "2026-08-15",
  "holdout_start_date": "2026-08-16",
  "holdout_end_date": "2026-09-10",
  "comparison": {
    "winning_model": "prophet",
    "mae_difference": -1.27,
    "wape_difference": -0.046,
    "relative_improvement_pct": 37.13,
    "comparison_valid": true,
    "interpretation": "Prophet outperformed Moving Average with a 37.13% reduction in MAE across 18 holdout observations."
  },
  "assumptions_and_limitations": [
    "Chronological holdout benchmark evaluates past accuracy on unseen historical window; this is not a future demand forecast.",
    "Observed recorded demand dates only. Missing calendar dates are not imputed as zero."
  ],
  "created_at": "2026-10-11T12:05:00Z"
}
```

---

### G. What-If Replenishment Simulator

A discrete-event daily replenishment simulator that stress-tests inventory availability against demand surges and supplier delivery delays without mutating physical inventory or creating real purchase orders.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/scenarios` | Execute and save a stressed scenario simulation |
| `GET` | `/api/v1/scenarios` | List saved scenarios across products (query params: `product_id`, `page`, `limit`) |
| `GET` | `/api/v1/products/{product_id}/scenarios` | List saved scenarios for a specific product (`page`, `limit`) |
| `GET` | `/api/v1/scenarios/{scenario_id}` | Retrieve complete daily timeline and replenishment events |
| `POST` | `/api/v1/products/{product_id}/scenarios/compare` | Compare baseline vs multiple stress scenarios |

#### 1. Run Scenario Simulation:
`POST /api/v1/products/1/scenarios`
```json
{
  "scenario_name": "Peak Season + Port Delay",
  "demand_surge_pct": 25.0,
  "supplier_delay_days": 3,
  "horizon_days": 30,
  "forecast_run_id": null
}
```

#### 2. Multi-Scenario Comparison:
`POST /api/v1/products/1/scenarios/compare`
```json
{
  "horizon_days": 30,
  "scenarios": [
    {
      "scenario_name": "Mild Surge",
      "demand_surge_pct": 10.0,
      "supplier_delay_days": 0
    },
    {
      "scenario_name": "Severe Delay & Surge",
      "demand_surge_pct": 20.0,
      "supplier_delay_days": 4
    }
  ]
}
```

#### 3. Comparison Response (`ScenarioComparisonResponse`):
```json
{
  "product_id": 1,
  "sku": "SKU-BEV-001",
  "product_name": "Premium Sparkling Water 500ml",
  "simulation_start_date": "2026-09-12",
  "horizon_days": 30,
  "baseline": {
    "scenario_name": "Baseline (0% surge, 0d delay)",
    "demand_surge_pct": 0.0,
    "supplier_delay_days": 0,
    "ending_inventory": 45,
    "total_unmet_demand": 0,
    "unmet_demand_difference_vs_baseline": 0,
    "stockout_days_count": 0,
    "stockout_days_difference_vs_baseline": 0,
    "fulfillment_rate_pct": 100.0,
    "total_units_ordered": 240,
    "total_inventory_cost": 312.50,
    "cost_difference_vs_baseline": 0.0,
    "first_stockout_date": null
  },
  "comparisons": [
    {
      "scenario_name": "Mild Surge",
      "demand_surge_pct": 10.0,
      "supplier_delay_days": 0,
      "ending_inventory": 21,
      "total_unmet_demand": 0,
      "unmet_demand_difference_vs_baseline": 0,
      "stockout_days_count": 0,
      "stockout_days_difference_vs_baseline": 0,
      "fulfillment_rate_pct": 100.0,
      "total_units_ordered": 264,
      "total_inventory_cost": 338.20,
      "cost_difference_vs_baseline": 25.70,
      "first_stockout_date": null
    },
    {
      "scenario_name": "Severe Delay & Surge",
      "demand_surge_pct": 20.0,
      "supplier_delay_days": 4,
      "ending_inventory": 0,
      "total_unmet_demand": 38,
      "unmet_demand_difference_vs_baseline": 38,
      "stockout_days_count": 3,
      "stockout_days_difference_vs_baseline": 3,
      "fulfillment_rate_pct": 86.8,
      "total_units_ordered": 264,
      "total_inventory_cost": 318.40,
      "cost_difference_vs_baseline": 5.90,
      "first_stockout_date": "2026-09-24"
    }
  ],
  "insights": [
    "'Mild Surge' maintained 100% stock availability equivalent to baseline.",
    "'Severe Delay & Surge' (+20.0% surge, +4d delay) results in 38 additional unmet units and 3 additional stockout days vs. baseline."
  ]
}
```

---

## 5. Deployment Readiness & Environment Variables

| Variable | Environment | Default / Example | Required | Notes |
|---|---|---|---|---|
| `ENVIRONMENT` | All | `production` | Yes | Valid values: `development`, `testing`, `production` |
| `DATABASE_URL` | Production | `postgresql://user:pass@host:5432/stocksense` | **Yes** | **SQLite is rejected in production.** Must be valid PostgreSQL. |
| `SECRET_KEY` | Production | High-entropy string (>= 32 chars) | **Yes** | Cannot contain `'dev'` or `'insecure'`. |
| `CORS_ORIGINS` | All | `["http://localhost:3000"]` | Yes | List of allowed frontend web origins. |
| `LOG_LEVEL` | All | `INFO` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

### Production ASGI Command
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 6. Architectural Limitations & Production Scope Boundary

1. **Authentication & Multi-Tenant Authorization**:
   - StockSense backend currently operates as a **single-organization / internal enterprise microservice**.
   - User identity, JWT sessions, and multi-tenant row-level access control are not implemented in the current backend core.
   - For public-facing cloud deployments, the backend **must be hosted behind an API Gateway or Reverse Proxy** (such as Kong, Traefik, AWS API Gateway, or Cloudflare Access) that enforces token authentication and rate limiting.
2. **Forecast Benchmarking Scope**:
   - Phase 5 evaluates model accuracy (Moving Average vs Prophet) on **chronological historical holdout observations**.
   - It is designed to identify which model better tracks demand patterns on known history. It does **not** generate unconstrained future forecasts beyond available test boundaries.
3. **In-Transit Inventory Support**:
   - In accordance with the current data schema, `inventory_position` reflects `current_stock`.
   - In-transit purchase orders are currently not tracked in the database and are explicitly marked as unavailable in calculation explanations.
