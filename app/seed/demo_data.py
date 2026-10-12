import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models.demand import DemandRecord
from app.models.product import Product

# Clearly labelled deterministic synthetic demo dataset
DEMO_PRODUCTS = [
    {
        "sku": "SKU-001",
        "name": "Notebook (Demo Synthetic)",
        "category": "Stationery",
        "unit_of_measure": "piece",
        "current_stock": 80,
        "committed_qty": 5,
        "safety_stock": 18,
        "reorder_point": 65,
        "lead_time_days": 4,
        "lead_time_stddev_days": 1.2,
        "unit_cost": Decimal("25.00"),
        "ordering_cost": Decimal("100.00"),
        "holding_cost_rate": Decimal("0.1600"),
        "target_service_level": 0.95,
        "minimum_order_qty": 10,
        "pack_size": 5,
        "is_active": True,
    },
    {
        "sku": "SKU-002",
        "name": "Water Bottle (Demo Synthetic)",
        "category": "Accessories",
        "unit_of_measure": "piece",
        "current_stock": 24,
        "committed_qty": 0,
        "safety_stock": 12,
        "reorder_point": 45,
        "lead_time_days": 7,
        "lead_time_stddev_days": 2.0,
        "unit_cost": Decimal("90.00"),
        "ordering_cost": Decimal("100.00"),
        "holding_cost_rate": Decimal("0.1333"),
        "target_service_level": 0.95,
        "minimum_order_qty": 6,
        "pack_size": 2,
        "is_active": True,
    },
    {
        "sku": "SKU-DRY-MILK-1L",
        "name": "Amul Taaza Homogenised Toned Milk (1L Tetra Pak)",
        "category": "Dairy & Fresh",
        "unit_of_measure": "1L Tetra Pak",
        "current_stock": 145,
        "committed_qty": 18,
        "safety_stock": 42,
        "reorder_point": 198,
        "lead_time_days": 2,
        "lead_time_stddev_days": 0.6,
        "unit_cost": Decimal("1.40"),
        "ordering_cost": Decimal("15.00"),
        "holding_cost_rate": Decimal("0.3200"),
        "target_service_level": 0.98,
        "minimum_order_qty": 24,
        "pack_size": 12,
        "is_active": True,
    },
    {
        "sku": "SKU-STP-ATTA-5K",
        "name": "Aashirvaad Superior MP Shudh Chakki Atta (5kg Bag)",
        "category": "Staples & Grains",
        "unit_of_measure": "5kg Bag",
        "current_stock": 210,
        "committed_qty": 10,
        "safety_stock": 58,
        "reorder_point": 238,
        "lead_time_days": 4,
        "lead_time_stddev_days": 1.1,
        "unit_cost": Decimal("4.80"),
        "ordering_cost": Decimal("35.00"),
        "holding_cost_rate": Decimal("0.2200"),
        "target_service_level": 0.96,
        "minimum_order_qty": 10,
        "pack_size": 5,
        "is_active": True,
    },
    {
        "sku": "SKU-OIL-SUN-1L",
        "name": "Fortune Sunlite Refined Sunflower Oil (1L Pouch)",
        "category": "Staples & Grains",
        "unit_of_measure": "1L Pouch",
        "current_stock": 195,
        "committed_qty": 14,
        "safety_stock": 75,
        "reorder_point": 385,
        "lead_time_days": 5,
        "lead_time_stddev_days": 1.4,
        "unit_cost": Decimal("2.10"),
        "ordering_cost": Decimal("25.00"),
        "holding_cost_rate": Decimal("0.2000"),
        "target_service_level": 0.95,
        "minimum_order_qty": 20,
        "pack_size": 10,
        "is_active": True,
    },
    {
        "sku": "SKU-SNK-MAGG-12P",
        "name": "Maggi 2-Minute Masala Instant Noodles (12-Pack)",
        "category": "Instant & Snacks",
        "unit_of_measure": "12-Pack Carton",
        "current_stock": 380,
        "committed_qty": 22,
        "safety_stock": 88,
        "reorder_point": 373,
        "lead_time_days": 3,
        "lead_time_stddev_days": 0.8,
        "unit_cost": Decimal("2.40"),
        "ordering_cost": Decimal("20.00"),
        "holding_cost_rate": Decimal("0.1800"),
        "target_service_level": 0.99,
        "minimum_order_qty": 16,
        "pack_size": 8,
        "is_active": True,
    },
    {
        "sku": "SKU-BEV-TEA-1K",
        "name": "Tata Tea Premium Desh Ki Chai (1kg Poly Pack)",
        "category": "Beverages & Tea",
        "unit_of_measure": "1kg Poly Pack",
        "current_stock": 160,
        "committed_qty": 7,
        "safety_stock": 34,
        "reorder_point": 162,
        "lead_time_days": 4,
        "lead_time_stddev_days": 0.9,
        "unit_cost": Decimal("5.20"),
        "ordering_cost": Decimal("22.00"),
        "holding_cost_rate": Decimal("0.1600"),
        "target_service_level": 0.95,
        "minimum_order_qty": 12,
        "pack_size": 6,
        "is_active": True,
    },
    {
        "sku": "SKU-HSH-SURF-2L",
        "name": "Surf Excel Matic Front Load Liquid Detergent (2L Pouch)",
        "category": "Household & Cleaning",
        "unit_of_measure": "2L Pouch",
        "current_stock": 72,
        "committed_qty": 5,
        "safety_stock": 32,
        "reorder_point": 143,
        "lead_time_days": 6,
        "lead_time_stddev_days": 1.3,
        "unit_cost": Decimal("11.20"),
        "ordering_cost": Decimal("40.00"),
        "holding_cost_rate": Decimal("0.2400"),
        "target_service_level": 0.94,
        "minimum_order_qty": 8,
        "pack_size": 4,
        "is_active": True,
    },
    {
        "sku": "SKU-PER-DETT-750",
        "name": "Dettol Original Liquid Handwash Refill Pouch (750ml)",
        "category": "Personal Care",
        "unit_of_measure": "750ml Refill",
        "current_stock": 115,
        "committed_qty": 8,
        "safety_stock": 25,
        "reorder_point": 109,
        "lead_time_days": 3,
        "lead_time_stddev_days": 0.7,
        "unit_cost": Decimal("2.20"),
        "ordering_cost": Decimal("18.00"),
        "holding_cost_rate": Decimal("0.1800"),
        "target_service_level": 0.96,
        "minimum_order_qty": 12,
        "pack_size": 6,
        "is_active": True,
    },
]


def generate_demand_series(base_qty: float, days: int = 45, start_date: date = date(2026, 8, 1)) -> List[dict]:
    """Generates deterministic demand series with variance, weekday seasonality, a spike, and a stockout."""
    records = []
    for d in range(days):
        current_date = start_date + timedelta(days=d)
        weekday = current_date.weekday()
        # Weekend bump (Saturday/Sunday)
        weekend_mult = 1.25 if weekday in (5, 6) else 1.0
        # Deterministic wave
        wave = math.sin(d * 0.45) * 0.15
        qty = round(base_qty * weekend_mult * (1.0 + wave), 1)

        stockout = False
        promo = False
        notes = None

        if d == 14:
            # Promotional surge
            qty = round(base_qty * 3.2, 1)
            promo = True
            notes = "Mid-month quick-commerce promotion surge"
        elif d == 28:
            # Stockout day
            qty = 0.0
            stockout = True
            notes = "Supplier stockout / out of shelf stock"

        records.append({
            "date": current_date,
            "quantity": max(0.0, qty),
            "stockout_flag": stockout,
            "promotion_flag": promo,
            "notes": notes,
        })
    return records


DEMO_DEMAND_RECORDS: Dict[str, List[dict]] = {
    "SKU-001": generate_demand_series(14.0),
    "SKU-002": generate_demand_series(6.5),
    "SKU-DRY-MILK-1L": generate_demand_series(78.0),
    "SKU-STP-ATTA-5K": generate_demand_series(45.0),
    "SKU-OIL-SUN-1L": generate_demand_series(62.0),
    "SKU-SNK-MAGG-12P": generate_demand_series(95.0),
    "SKU-BEV-TEA-1K": generate_demand_series(32.0),
    "SKU-HSH-SURF-2L": generate_demand_series(18.5),
    "SKU-PER-DETT-750": generate_demand_series(28.0),
}


def seed_demo_data(db: Session) -> Dict[str, int]:
    """
    Idempotently seeds deterministic demo products and demand records.
    Never overwrites existing products.
    """
    seeded_products = 0
    seeded_demand = 0

    for prod_data in DEMO_PRODUCTS:
        existing = db.query(Product).filter_by(sku=prod_data["sku"]).first()
        if not existing:
            product = Product(**prod_data)
            db.add(product)
            db.flush()  # Populates product.id
            seeded_products += 1

            # Seed demand records for this product
            demand_items = DEMO_DEMAND_RECORDS.get(product.sku, [])
            for item in demand_items:
                already = db.query(DemandRecord).filter_by(product_id=product.id, date=item["date"]).first()
                if not already:
                    record = DemandRecord(
                        product_id=product.id,
                        date=item["date"],
                        quantity=item["quantity"],
                        stockout_flag=item["stockout_flag"],
                        promotion_flag=item["promotion_flag"],
                        notes=item["notes"],
                    )
                    db.add(record)
                    seeded_demand += 1
        else:
            # Seed missing demand records for existing product
            demand_items = DEMO_DEMAND_RECORDS.get(existing.sku, [])
            for item in demand_items:
                already = db.query(DemandRecord).filter_by(product_id=existing.id, date=item["date"]).first()
                if not already:
                    record = DemandRecord(
                        product_id=existing.id,
                        date=item["date"],
                        quantity=item["quantity"],
                        stockout_flag=item["stockout_flag"],
                        promotion_flag=item["promotion_flag"],
                        notes=item["notes"],
                    )
                    db.add(record)
                    seeded_demand += 1

    db.commit()
    logger.info("Seeded %d demo products and %d demand records.", seeded_products, seeded_demand)
    return {"products": seeded_products, "demand_records": seeded_demand}

