# StockSense — Backend Engine

StockSense is an **Adaptive, Risk-Aware Inventory Replenishment Engine**. This service provides the core FastAPI application, database schemas, configuration management, replenishment calculations, anomaly detection, forecast benchmarking, and What-If simulation.

> **Frontend Integration Guide**: For the complete REST API contract, request/response schemas, and workflow details, see [API_INTEGRATION.md](API_INTEGRATION.md).

---

## 1. System Architecture & Directory Structure

```
backend/
├── alembic.ini                   # Alembic migration configuration
├── alembic/
│   ├── env.py                   # Dynamic migration runner (SQLite batch + PostgreSQL)
│   ├── script.py.mako           # Migration template
│   └── versions/
│       └── 0001_initial_schema.py # Initial migration for the 6 core entities
├── app/
│   ├── __init__.py              # Package marker
│   ├── main.py                  # FastAPI application factory & lifespan manager
│   ├── core/
│   │   ├── config.py            # Pydantic BaseSettings & production safety validation
│   │   ├── errors.py            # Standardized error responses & exception handlers
│   │   └── logging.py           # Structured logging with sensitive data masking
│   ├── db/
│   │   ├── base.py              # Declarative Base with explicit naming conventions
│   │   └── session.py           # Dialect-aware engine builder & get_db dependency
│   ├── models/                  # SQLAlchemy 2.0 ORM models
│   │   ├── product.py           # products
│   │   ├── demand.py            # demand_records
│   │   ├── anomaly.py           # anomaly_reviews
│   │   ├── forecast.py          # forecast_runs
│   │   ├── recommendation.py    # recommendations
│   │   └── scenario.py          # scenario_runs
│   ├── schemas/                 # Pydantic request/response validation schemas
│   │   ├── common.py            # Health & error schemas
│   │   ├── product.py
│   │   ├── demand.py
│   │   ├── anomaly.py
│   │   ├── forecast.py
│   │   ├── recommendation.py
│   │   └── scenario.py
│   ├── api/
│   │   ├── health.py            # GET /health/live and GET /health/ready
│   │   └── v1/
│   │       └── router.py        # Versioned router mounted at /api/v1
│   └── services/                # Business logic services (reserved for future phases)
├── tests/                       # Pytest automated test suite (100% isolated)
│   ├── conftest.py              # Isolated in-memory & file DB fixtures
│   ├── test_app.py              # Import, startup, CORS, and OpenAPI checks
│   ├── test_config.py           # Settings validation & production safety checks
│   ├── test_database.py         # Session lifecycle, CRUD, and constraint checks
│   ├── test_errors.py           # Error formats & stack trace masking checks
│   ├── test_health.py           # Liveness & readiness probe checks
│   ├── test_isolation.py        # Zero external credential dependency checks
│   └── test_migrations.py       # Alembic upgrade, inspect, and downgrade checks
├── .env.example                 # Safe environment configuration template
├── pyproject.toml               # Build system, pytest, and ruff linting configuration
├── requirements.txt             # Production dependencies
└── requirements-dev.txt         # Development & testing dependencies
```

---

## 2. Local Setup Instructions (Windows PowerShell)

Follow these exact steps from a freshly cloned repository.

### Step 1: Open PowerShell and Navigate to the Backend Directory
```powershell
cd backend
```

### Step 2: Create a Python 3.11 Virtual Environment
```powershell
py -3.11 -m venv .venv
```

### Step 3: Activate the Virtual Environment (CRITICAL)
```powershell
.\.venv\Scripts\Activate.ps1
```
*(If PowerShell blocks script execution, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

> **IMPORTANT**: Always activate `.venv` before running commands, or explicitly prefix commands with `.\.venv\Scripts\` (e.g. `.\.venv\Scripts\alembic upgrade head`). Running bare `alembic` without activating the virtual environment invokes the system's global Python 3.13 interpreter instead of the project's Python 3.11 virtual environment.

### Step 4: Install Dependencies
```powershell
pip install -r requirements-dev.txt
```

### Step 5: Configure Environment Variables
Copy the example template to create your local `.env`:
```powershell
Copy-Item .env.example .env
```

To connect to the local Docker PostgreSQL container on port `5434`:
```ini
ENVIRONMENT=development
DATABASE_URL="postgresql+psycopg2://stocksense_app:stocksense_dev_password@localhost:5434/stocksense"
SECRET_KEY="dev_insecure_secret_key_change_in_production_min32chars"
```

---

## 3. Database Migration & Demo Data Seeding

### Apply Schema Migrations
```powershell
# Using activated venv:
alembic upgrade head

# Or explicitly via .venv executable:
.\.venv\Scripts\alembic upgrade head
```

### Seed Deterministic Demo Dataset
Seed the database with the required synthetic products and daily demand records:
```powershell
python -c "from app.db.session import SessionLocal; from app.seed.demo_data import seed_demo_data; db = SessionLocal(); seed_demo_data(db); db.close()"
```

---

## 4. Running the Backend Server

Start the development server with hot reload:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Once running:
- **Liveness Probe**: `http://127.0.0.1:8000/health/live`
- **Readiness Probe**: `http://127.0.0.1:8000/health/ready`
- **API Status**: `http://127.0.0.1:8000/api/v1/status`
- **List Products**: `http://127.0.0.1:8000/api/v1/products`
- **Product Details**: `http://127.0.0.1:8000/api/v1/products/1`
- **Product Demand**: `http://127.0.0.1:8000/api/v1/products/1/demand`
- **Interactive OpenAPI Docs**: `http://127.0.0.1:8000/docs`
- **Alternative ReDoc**: `http://127.0.0.1:8000/redoc`

---

## 5. Running the Automated Test Suite

Run all automated unit, integration, and migration tests:

```powershell
pytest -v
```

Run code formatting and linting:

```powershell
ruff check app tests
ruff format app tests
```

---

## 6. Core Database Entities (Schema Reference)

| Table | Logical Responsibility | Key Constraints & Indexes |
|---|---|---|
| `products` | Product master, on-hand stock, lead-time profile, costs, supplier constraints. | Unique SKU, non-negative stock/cost checks, positive lead-time check, service-level range check. |
| `demand_records` | Historical daily sales observations per product, preserving stockout and promotion context. | Unique `(product_id, date)`, non-negative quantity, cascade delete on product. |
| `anomaly_reviews` | Stores flagged anomaly observations, classifications, reviewer notes, and adjusted values. | Foreign key to `demand_records`, check constraint on classification (`'promotion'`, `'data_error'`, `'unknown'`). |
| `forecast_runs` | Records baseline vs. candidate forecast evaluation runs and comparative metrics (MAE, WAPE). | Foreign key to `products`, cascade delete. |
| `recommendations` | Explainable replenishment recommendations (ROP, safety stock, order qty, assumptions). | Foreign key to `products`, non-negative order qty check, urgency constraint, status constraint. |
| `scenario_runs` | What-if simulation parameters, demand surges, supplier delays, and stress impact summaries. | Foreign key to `products`, non-negative surge and delay checks. |

---

## 7. Phase 3 — Explainable Replenishment Engine

StockSense includes a deterministic, explainable replenishment calculation engine that produces point-in-time recommendation snapshots with full mathematical auditability.

> **Advisory Disclaimer**: Replenishment recommendations generated by StockSense are advisory decision-support outputs based on documented statistical approximations. They are not guaranteed optimal under non-stationary or correlated demand, and the system does not automatically generate or place purchase orders with suppliers.

### 7.1 Mathematical Formulas & Statistical Conventions

1. **Demand Statistics**:
   - **Average daily demand** ($\bar{d}$): $\bar{d} = \frac{1}{N} \sum_{i=1}^N d_i$ computed strictly over recorded observations.
   - **Daily demand standard deviation** ($\sigma_d$): Sample standard deviation with Bessel's correction ($N - 1$ degrees of freedom) for $N \ge 2$:
     $$\sigma_d = \sqrt{\frac{1}{N - 1} \sum_{i=1}^N (d_i - \bar{d})^2}$$
   - **Missing Dates Policy**: Missing calendar dates are **never** silently filled with fabricated zero demand. Date gaps are detected and disclosed in data quality warnings.
   - **Stockout-Censored Demand**: Observations with `stockout_flag = True` are retained and flagged. An explicit data quality warning is returned because sales during stockout periods understate true unconstrained customer demand.

2. **Cycle Service Level & Z-Score**:
   - **Cycle Service Level (CSL)**: Target probability of zero stockouts during a supplier replenishment lead-time cycle (defaults to 0.95). CSL is strictly distinguished from Fill Rate (volumetric fraction of demand fulfilled).
   - **Z-Factor**: One-sided standard normal quantile $Z = \Phi^{-1}(\text{CSL})$ derived via standard inverse cumulative distribution function. For $\text{CSL} = 0.95$, $Z \approx 1.644854$.

3. **Safety Stock**:
   - **Variable Lead Time**:
     $$\text{Safety Stock} = Z \times \sqrt{L \times \sigma_d^2 + \bar{d}^2 \times \sigma_L^2}$$
     Where $L$ is average lead time in days, $\sigma_d$ is daily demand standard deviation, $\bar{d}$ is average daily demand, and $\sigma_L$ is lead-time standard deviation.
   - **Constant Lead Time** ($\sigma_L = 0$):
     $$\text{Safety Stock} = Z \times \sigma_d \times \sqrt{L}$$
   - **Rounding Policy**: Integer safety stock is rounded using ceiling (`math.ceil`) to ensure conservative cycle service level protection for discrete physical units.

4. **Reorder Point (ROP)**:
   - $$\text{Expected Lead-Time Demand} = \bar{d} \times L$$
   - $$\text{ROP} = \lceil \text{Expected Lead-Time Demand} + \text{Safety Stock} \rceil$$

5. **Inventory Position**:
   - $$\text{Inventory Position} = \text{on\_hand\_qty} - \text{committed\_qty}$$
   - **Disclosed Limitation**: Incoming open purchase orders are not currently tracked in the database schema (assumed 0).

6. **Economic Order Quantity (EOQ)**:
   - $$\text{EOQ} = \sqrt{\frac{2 \times D \times S}{H}}$$
     Where $D = \bar{d} \times 365$ (annual demand), $S$ is ordering cost per order, and $H$ is annual holding cost per unit (`holding_cost_per_unit_year`).
   - Calculated **only** when $D > 0$, $S > 0$, and $H > 0$. If any cost is missing, EOQ is marked unavailable with an explicit reason, and baseline order quantity falls back to net deficit to reach ROP ($\max(1, \text{ROP} - \text{inventory\_position})$).

7. **Order Quantity & Supplier Constraints**:
   - If $\text{inventory\_position} > \text{ROP}$: Reorder not triggered (`healthy`); proposed order quantity is **0**.
   - If $\text{inventory\_position} \le \text{ROP}$: Reorder triggered (`reorder_now`). Proposed baseline quantity is $\max(\lceil\text{EOQ}\rceil, \text{deficit})$.
   - **MOQ Adjustment**: If $\text{baseline\_qty} < \text{minimum\_order\_qty}$, adjusted up to MOQ.
   - **Pack-Size Rounding**: If $\text{pack\_size} > 1$, rounded up to nearest integer multiple of pack size.

### 7.2 API Endpoints & Usage

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/recommendations/calculate` | Calculates and persists a new recommendation snapshot. |
| `GET` | `/api/v1/products/{product_id}/recommendations/latest` | Retrieves the latest recommendation snapshot for a product. |
| `GET` | `/api/v1/products/{product_id}/recommendations` | Retrieves recommendation history for a product. |
| `GET` | `/api/v1/recommendations` | Overview list across products with `latest_only`, `urgency`, `status`, `reorder_only`, and pagination filters. |
| `GET` | `/api/v1/recommendations/{id}` | Retrieves a recommendation by primary key ID. |
| `PATCH` | `/api/v1/recommendations/{id}/status` | Updates workflow status (`pending`, `approved`, `rejected`). |

#### Example Request: Calculate Recommendation
```http
POST /api/v1/products/1/recommendations/calculate HTTP/1.1
Content-Type: application/json

{
  "target_service_level": 0.95
}
```

#### Example Response (200 OK)
```json
{
  "id": 1,
  "product_id": 1,
  "formula_version": "v1.0",
  "calculated_at": "2026-10-11T14:55:00Z",
  "current_stock": 80,
  "inventory_position": "75.000",
  "mean_daily_demand": "14.285714",
  "daily_demand_stddev": "15.776716",
  "average_lead_time_days": "4.000",
  "lead_time_stddev_days": "1.200",
  "target_service_level": "0.9500",
  "z_factor": "1.644854",
  "expected_lead_time_demand": "57.142857",
  "safety_stock": 60,
  "reorder_point": 118,
  "annual_demand": "5214.286",
  "raw_eoq": "510.598",
  "recommended_order_quantity": 515,
  "urgency": "medium",
  "status": "pending",
  "explanation": "Reorder triggered: Inventory position (75) is at or below the reorder point (118). Proposed baseline quantity is 511 units based on Economic Order Quantity (raw EOQ = 510.6, integer = 511) and net deficit to ROP (43). Quantity rounded up from 511 to 515 units to match supplier pack size of 5 (103 packs).",
  "assumptions": { ... },
  "input_snapshot": { ... },
  "created_at": "2026-10-11T14:55:00Z"
}
```

### 7.3 Reasons a Recommendation May Be Unavailable

- **Missing Product**: HTTP 404 if `product_id` does not exist.
- **Missing Supplier Settings**: HTTP 422 if product has no `lead_time_days` configured.
- **Invalid Parameters**: HTTP 422 if `lead_time_days <= 0` or `target_service_level` is not in $(0, 1)$.
- **Insufficient Demand History**: HTTP 422 if 0 or 1 demand records exist (sample variance requires $N \ge 2$).
- **Missing Cost Inputs**: EOQ is flagged unavailable in snapshot (`eoq_available: false`); replenishment safely falls back to net deficit to reach ROP without failing the entire calculation.

---

## 8. Phase 4 — Demand Anomaly Review and Classification

StockSense includes a deterministic, explainable anomaly detection and review system that flags statistical outliers in historical demand series and guides human triage.

> **Advisory & Triage Disclaimer**: Detected anomalies are statistical candidates flagged for human review. They are **not** proof of fraud, data corruption, system malfunction, or definitive causal relationships. Human confirmation is required before any operational classification is considered final.

### 8.1 Detection Methodology & Statistical Mechanics

1. **Robust Median / MAD Estimator**:
   - Standard sample mean and standard deviation are severely distorted by the very outliers they seek to detect. StockSense employs the **Median Absolute Deviation (MAD)** about the median ($M$):
     $$M = \text{median}(Y)$$
     $$\text{MAD} = \text{median}(|Y - M|)$$
   - The normal-consistent standard deviation estimator is:
     $$\hat{\sigma}_{\text{MAD}} = 1.4826022 \times \text{MAD}$$
   - The modified Z-score distance for observation $y_i$ is $z_i = \frac{|y_i - M|}{\hat{\sigma}_{\text{MAD}}}$.
   - Default outlier threshold: $k = 3.0$ (modified Z-score cutoff).
     $$\text{Upper Threshold} = M + k \times \hat{\sigma}_{\text{MAD}}$$
     $$\text{Lower Threshold} = \max(0.0, M - k \times \hat{\sigma}_{\text{MAD}})$$

2. **Zero MAD & Intermittent Demand Handling**:
   - **Constant Demand**: When all observations are identical ($\min(Y) = \max(Y)$), variance is recognized as 0. Bounds collapse to $[M, M]$, and 0 false positives are flagged.
   - **Sparse / Skewed Demand**: When $>50\%$ of observations match the median (common in intermittent or slow-moving items where median demand is 0), MAD is 0 despite non-zero variations. In this scenario, the detector falls back to the **Mean Absolute Deviation (MeanAD)**:
     $$\text{MeanAD} = \frac{1}{N} \sum_{i=1}^N |y_i - M|$$
     $$\hat{\sigma} = 1.253314 \times \text{MeanAD}$$
     This prevents division-by-zero errors while maintaining robust statistical thresholds.

3. **Missing Calendar Dates**:
   - Missing calendar days are **never** converted into synthetic zero-demand observations. Statistical baselines are evaluated strictly across the recorded demand observations.

4. **Insufficient Demand History**:
   - If a product has fewer than `min_history_records` (default: 5 observations), statistical anomaly detection is rejected with HTTP 400 Bad Request to prevent spurious outlier claims on unrepresentative sample sizes.

### 8.2 Anomaly Types & Classification Policy

| Anomaly Type | Condition | Suggested Classification | Explanation / Context |
|---|---|---|---|
| `promotion_spike` | $y_i > \text{Upper Threshold}$ with `promotion_flag = True` | `promotion` | Demand surge coincides with an active promotional event. |
| `high_demand_spike` | $y_i > \text{Upper Threshold}$ with `promotion_flag = False` | `unknown` | Unusually high demand spike with no promotional record. |
| `low_demand_drop` | $y_i < \text{Lower Threshold}$ with `stockout_flag = False` | `unknown` | Demand dropped significantly below historical baseline. |
| `stockout_affected` | $y_i < \text{Lower Threshold}$ or $y_i = 0$ with `stockout_flag = True` | `unknown` | Demand may be censored due to product stockout / unavailability. |
| `potential_data_error` | $y_i < 0$ or non-finite / implausible value | `data_error` | Direct data-quality violation establishing potential entry error. |
| `unknown_anomaly` | Unusual pattern without clear attribution | `unknown` | Observation flagged for human review. |

#### Human-Confirmed Classifications
A human reviewer confirms one of three final classifications:
- `promotion`: Verified promotion, seasonal campaign, or planned marketing lift.
- `data_error`: Verified data-entry error, barcode glitch, or invalid record.
- `unknown`: Legitimate random surge or unverified variance.

### 8.3 Idempotency & Data Preservation Guarantees

- **Source Data Immutability**: Historical demand records in `demand_records` (`quantity`, `date`, `promotion_flag`, `stockout_flag`) are **never mutated, overwritten, or deleted** by the anomaly service.
- **Separate Decision Storage**: All review classifications, notes, treatments (`keep`, `exclude`, `clamp`), and optional adjusted quantities are stored strictly in `anomaly_reviews`.
- **Rerun Idempotency**: Running detection multiple times on a product does not duplicate candidates. Existing candidates are preserved via the database unique constraint on `demand_record_id`.
- **Preservation of User Decisions**: If a candidate has already been reviewed (`review_status = 'reviewed'` or `'dismissed'`), subsequent detection runs refresh only the statistical detector scores and bounds without overwriting the human reviewer's classification, review status, or notes.
- **Replenishment Engine Protection**: The replenishment calculation engine reads immutable source demand observations. Classifying an observation as `data_error` does not silently distort replenishment calculations.

### 8.4 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/anomalies/detect` | Runs statistical anomaly detector and persists candidates idempotently. |
| `GET` | `/api/v1/products/{product_id}/anomalies` | Lists anomaly candidates for a product with pagination and filters. |
| `GET` | `/api/v1/products/{product_id}/anomalies/summary` | Returns counts by status (`pending`, `reviewed`, `dismissed`) and classification. |
| `GET` | `/api/v1/anomalies` | Global list of anomaly reviews across all products with filtering. |
| `GET` | `/api/v1/anomalies/summary` | Global anomaly summary counts across the entire catalog. |
| `GET` | `/api/v1/anomalies/{anomaly_id}` | Retrieves full review details, baseline statistics, and detector evidence. |
| `PATCH` | `/api/v1/anomalies/{anomaly_id}/review` | Confirms classification, adds review notes, treatment, or adjusted quantity. |
| `POST` | `/api/v1/anomalies/{anomaly_id}/reviews` | Spec-compliant alias for submitting review decisions. |
| `POST` | `/api/v1/anomalies/{anomaly_id}/dismiss` | Dismisses a candidate as a false positive or acceptable variance. |

#### Example: Run Detection
```http
POST /api/v1/products/1/anomalies/detect HTTP/1.1
Content-Type: application/json

{
  "threshold_multiplier": 3.0,
  "min_history_records": 5
}
```

#### Example Response (200 OK)
```json
{
  "product_id": 1,
  "sku": "SKU-001",
  "total_records_analyzed": 30,
  "detector_version": "median_mad_v1.0",
  "detected_anomalies_count": 1,
  "new_candidates_created": 1,
  "existing_candidates_preserved": 0,
  "candidates": [
    {
      "id": 1,
      "demand_record_id": 15,
      "product_id": 1,
      "sku": "SKU-001",
      "observation_date": "2026-01-15",
      "original_quantity": 120.0,
      "adjusted_quantity": null,
      "detector_version": "median_mad_v1.0",
      "anomaly_type": "promotion_spike",
      "anomaly_score": 5.42,
      "detector_explanation": "Observed demand of 120.0 significantly exceeds upper expected threshold (52.3, score=5.42) and coincided with an active promotion flag.",
      "suggested_classification": "promotion",
      "user_confirmed_classification": null,
      "review_status": "pending",
      "reviewer_notes": null,
      "stockout_flag": false,
      "promotion_flag": true,
      "reviewed_at": "2026-10-11T15:00:00Z",
      "created_at": "2026-10-11T15:00:00Z"
    }
  ],
  "notes": []
}
```

#### Example: Confirm Review
```http
PATCH /api/v1/anomalies/1/review HTTP/1.1
Content-Type: application/json

{
  "classification": "promotion",
  "review_status": "reviewed",
  "reviewer_notes": "Confirmed holiday marketing campaign spike.",
  "reviewer": "InventoryAnalyst1",
  "user_treatment": "keep"
}
```

---

## 9. Phase 5: Forecast Benchmark & Performance Evaluation

StockSense provides an explainable, reproducible benchmark comparing a **Moving Average baseline** against a **Prophet candidate model** on an identical chronological holdout period.

### 9.1 Benchmark Workflow & Principles

1. **Strict Chronological Holdout Split**: Earlier observations form the training set; later observations form the holdout evaluation set. Random shuffling is strictly disallowed.
2. **Leakage Prevention**: All model fitting, baseline averages, and preprocessing statistics consume only the training series. Holdout actuals are strictly withheld until computing evaluation errors.
3. **Fixed-Origin Multi-Step Forecasting**: Both models produce forecasts for the entire holdout horizon without consuming actual holdout demand.
4. **Data Integrity & Non-Imputation**: Evaluates recorded transaction dates directly. Missing calendar dates are never imputed as zero; explicit zero demand is preserved.
5. **Raw Demand Evaluation**: Original demand records are immutable. Phase 4 reviewed anomalies are logged in diagnostic metadata for traceability but are not silently altered or removed.
6. **Zero-Denominator Safety**: When total actual holdout demand equals zero ($\sum y_i = 0$), WAPE is returned as `null` with a clear explanation (`wape_unavailable_reason`) rather than dividing by zero.
7. **Negative Prediction Audit**: Any negative demand forecasts from Prophet are clipped to `0.0` while preserving `raw_predicted` for full auditability.
8. **Graceful Degraded Performance**: If Prophet dependency is missing or Stan fitting fails, the run records a structured failure state while preserving valid Moving Average baseline metrics.
9. **Snapshot Immutability**: Benchmark results, per-date prediction points, and diagnostics are permanently stored in `forecast_runs`. Subsequent CSV imports never alter historical run snapshots.

> **Disclaimer**: Historical holdout performance measures accuracy on the selected test window and dataset. It is an empirical benchmark, not a guarantee of future forecast accuracy.

### 9.2 Formulas

- **Mean Absolute Error (MAE)**:
  $$\text{MAE} = \frac{1}{H} \sum_{i=1}^H |y_i - \hat{y}_i|$$
- **Weighted Absolute Percentage Error (WAPE)**:
  $$\text{WAPE} = \frac{\sum_{i=1}^H |y_i - \hat{y}_i|}{\sum_{i=1}^H |y_i|} \times 100\%$$
  *(Returns `null` if $\sum |y_i| = 0$)*

### 9.3 Forecasting API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{id}/forecast-benchmark` | Run chronological benchmark for product. |
| `GET` | `/api/v1/forecast-runs/{run_id}` | Retrieve persisted benchmark run snapshot. |
| `GET` | `/api/v1/products/{id}/forecast-runs` | List historical benchmark runs with pagination. |
| `GET` | `/api/v1/forecast-runs/{run_id}/metrics` | Retrieve detailed prediction curves & metrics. |

#### Example: Execute Benchmark
```http
POST /api/v1/products/1/forecast-benchmark HTTP/1.1
Content-Type: application/json

{
  "test_size": 0.2,
  "window_size": 7,
  "prophet_growth": "linear",
  "prophet_seasonality_mode": "additive"
}
```

---

## 10. Phase 6: What-If Replenishment Simulator

StockSense provides a deterministic, decision-support What-If replenishment simulation engine. It allows inventory managers to stress-test their inventory posture under configurable demand surges and supplier delivery delays over a specified horizon.

> **Critical Safety Guarantee**: The simulator is purely a decision-support and scenario-planning tool. It **never** creates real purchase orders, mutates actual on-hand stock, changes historical demand records, or alters existing replenishment recommendations.

### 10.1 Supported Scenario Types

1. **Baseline Scenario**: Evaluates stock trajectory under current replenishment policy (0% surge, 0 days additional supplier delay).
2. **Demand Surge Scenario**: Evaluates impact of percentage increase in customer demand (e.g., +10%, +25%, +50%, up to 500%).
3. **Supplier Delay Scenario**: Evaluates impact of extended delivery lead times in calendar days (e.g., +2, +4, +7, up to 90 days).
4. **Combined Scenario**: Evaluates simultaneous demand spikes and delivery delays (e.g., +25% surge with +4 days supplier delay).
5. **Scenario Comparison**: Evaluates baseline alongside one or more stress scenarios side-by-side, calculating delta unmet demand, delta stockout days, and delta inventory costs.

### 10.2 Inventory Flow Assumptions & Event Ordering

Each simulated day $t \in [1, H]$ executes in strict chronological sequence:
1. **Starting Stock**: Physical on-hand stock carried forward from day $t-1$ (Day 1 starts with the product's actual `current_stock`).
2. **Replenishment Arrivals**: Pending purchase orders scheduled to arrive on day $t$ arrive before customer demand and become immediately available.
3. **Demand Fulfillment**:
   - Surged demand is calculated: $d_t = \max(0, \lceil d_{\text{base}, t} \cdot (1 + \text{surge}\%) \rceil)$.
   - Fulfilled units = $\min(\text{available}, d_t)$.
   - Ending stock = $\text{available} - \text{fulfilled} \ge 0$. Physical stock **never** drops below zero.
   - Unmet demand = $\max(0, d_t - \text{available})$. Unmet units are recorded as lost sales and not backordered or rolled over.
4. **End-of-Day Reorder Decision**:
   - Inventory position evaluated: $\text{IP}_t = \text{EndingStock}_t + \text{InTransit}_t - \text{Committed}_t$.
   - If $\text{IP}_t \le \text{ROP}_{\text{stressed}}$, an order is placed for arrival on day $t + L_{\text{effective}}$, where $L_{\text{effective}} = L_{\text{base}} + \text{delay\_days}$.
   - Order quantity satisfies: $\max(\lceil\text{EOQ}\rceil, \text{Deficit})$, then constrained by MOQ and rounded up to pack-size multiples.

### 10.3 Demand Source Selection & Limitations

- **Primary Source**: Uses point predictions from a completed `ForecastRun` snapshot for the product when available or explicitly specified (`forecast_run_id`).
- **Fallback Source**: If no forecast run exists, falls back to historical mean daily demand without imputing missing dates as zero.
- **Traceability**: API responses explicitly disclose `demand_source_info` (source type, snapshot ID, method, mean demand, standard deviation, and data warnings).

### 10.4 Key Operational Distinctions

| Concept | Distinction |
|---|---|
| **Forecast Predictions vs. Simulated Demand** | Forecasts are point estimates of natural customer demand; simulated demand scales the baseline by the user's stress percentage. |
| **Simulated Replenishment vs. Purchase Orders** | Simulated orders are virtual mathematical events inside the simulation timeline; no real orders or supplier commitments are created. |
| **On-Hand Inventory vs. Inventory Position** | On-hand is physical stock on the warehouse shelf ($\ge 0$); inventory position includes incoming in-transit orders minus committed reservations. |
| **Stockout Days vs. Unmet Demand Units** | Stockout days count calendar days where demand exceeded stock; unmet demand counts the exact lost unit volume. |
| **Configured Costs vs. Unavailable Costs** | Costs are calculated strictly from configured economic fields (`unit_cost`, `holding_cost_rate`, `ordering_cost`, `stockout_cost`). Missing cost fields are flagged with explicit `unavailable_reasons` without inventing financial penalties. |

> **Disclaimer**: The simulator evaluates operating outcomes under specified assumptions. It does not optimize or guarantee future supply-chain outcomes.

### 10.5 Simulator API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/products/{product_id}/scenarios` | Create & execute a What-If scenario run; persists snapshot in `scenario_runs`. |
| `GET` | `/api/v1/scenarios/{scenario_id}` | Retrieve persisted scenario snapshot with full daily timeline and events. |
| `GET` | `/api/v1/products/{product_id}/scenarios` | Paginated listing of historical scenario runs for a product. |
| `POST` | `/api/v1/products/{product_id}/scenarios/compare` | Multi-scenario comparative evaluation against baseline. |

#### Example: Create & Execute Scenario
```http
POST /api/v1/products/1/scenarios HTTP/1.1
Content-Type: application/json

{
  "scenario_name": "Q4 Holiday Surge & Delay",
  "demand_surge_pct": 25.0,
  "supplier_delay_days": 4,
  "horizon_days": 30
}
```

#### Example: Multi-Scenario Comparison
```http
POST /api/v1/products/1/scenarios/compare HTTP/1.1
Content-Type: application/json

{
  "horizon_days": 30,
  "scenarios": [
    {
      "scenario_name": "Mild Surge",
      "demand_surge_pct": 10.0,
      "supplier_delay_days": 0
    },
    {
      "scenario_name": "Severe Supply Chain Delay",
      "demand_surge_pct": 0.0,
      "supplier_delay_days": 7
    },
    {
      "scenario_name": "Combined Crisis",
      "demand_surge_pct": 30.0,
      "supplier_delay_days": 5
    }
  ]
}
```

---

## 11. Running Tests & Migrations

```powershell
# Run all 154 automated unit and integration tests across all phases (100% pass)
.\.venv\Scripts\pytest.exe -v

# Run Ruff linter and formatter check
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\ruff.exe format --check app tests

# Run Alembic migrations
.\.venv\Scripts\alembic.exe upgrade head
```


"# demoEC2" 
