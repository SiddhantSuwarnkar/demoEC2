from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.demand import DemandRecord
from app.models.product import Product
from app.schemas.demand import DemandRecordRead
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.seed.demo_data import seed_demo_data

router = APIRouter(prefix="/products", tags=["Products"])


@router.get(
    "",
    response_model=List[ProductRead],
    summary="List Products",
    description="Retrieve all configured products and inventory profiles.",
)
def list_products(db: Session = Depends(get_db)) -> List[ProductRead]:
    products = db.query(Product).order_by(Product.id.asc()).all()
    return products


@router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product",
    description="Create a new product profile.",
)
def create_product(product_in: ProductCreate, db: Session = Depends(get_db)) -> ProductRead:
    existing = db.query(Product).filter(Product.sku == product_in.sku).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Product with SKU '{product_in.sku}' already exists.",
        )
    product_data = product_in.model_dump()
    product = Product(**product_data)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.post(
    "/seed-demo-data",
    summary="Seed Demo Data",
    description="Seeds default retail demonstration SKUs and historical demand observations.",
)
def seed_demo(db: Session = Depends(get_db)) -> Dict[str, int]:
    return seed_demo_data(db)


@router.delete(
    "",
    summary="Clear All Products",
    description="Deletes all products and cascades deletion to all demand records and analytics.",
)
def clear_all_products(db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services.import_service import ImportService
    result = ImportService.reset_catalog_data(db=db)
    return {"status": "success", "deleted_products": result["deleted_products_count"]}


@router.get(
    "/{product_id}",
    response_model=ProductRead,
    summary="Get Product Details",
    description="Retrieve specific product details and parameters by product ID.",
)
def get_product(product_id: int, db: Session = Depends(get_db)) -> ProductRead:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )
    return product


@router.get(
    "/{product_id}/demand",
    response_model=List[DemandRecordRead],
    summary="Get Product Daily Demand",
    description="Retrieve historical daily demand records for a specific product.",
)
def get_product_demand(product_id: int, db: Session = Depends(get_db)) -> List[DemandRecordRead]:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )
    records = (
        db.query(DemandRecord)
        .filter(DemandRecord.product_id == product_id)
        .order_by(DemandRecord.date.asc())
        .all()
    )
    return records


@router.patch(
    "/{product_id}",
    response_model=ProductRead,
    summary="Update Product",
    description="Update product parameters and stock levels.",
)
def update_product(
    product_id: int,
    product_in: ProductUpdate,
    db: Session = Depends(get_db),
) -> ProductRead:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )
    update_data = product_in.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if hasattr(product, field):
            setattr(product, field, val)
    db.commit()
    db.refresh(product)
    return product

