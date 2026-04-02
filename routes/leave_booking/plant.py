from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models.master.plant import PlantMaster

router = APIRouter(prefix="/plants")

# ======================================================
# 🚀 CREATE
# ======================================================
@router.post("/")
def create_plant(payload: dict, db: Session = Depends(get_db)):
    """
    {
      "plant_code": "RC1",
      "plant_name": "รัชดา1",
      "fleet": "Asia"
    }
    """

    exists = db.query(PlantMaster).filter(
        PlantMaster.plant_code == payload["plant_code"]
    ).first()

    if exists:
        raise HTTPException(400, "plant_code already exists")

    plant = PlantMaster(**payload)
    db.add(plant)
    db.commit()

    return {"status": "success", "data": payload}


# ======================================================
# 📄 LIST (by fleet)
# ======================================================
@router.get("/")
def list_plants(
    fleet: str = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(PlantMaster)

    if fleet:
        query = query.filter(PlantMaster.fleet == fleet)

    data = query.all()

    return {"status": "success", "data": data}


# ======================================================
# 📄 GET ONE
# ======================================================
@router.get("/{plant_code}")
def get_plant(plant_code: str, db: Session = Depends(get_db)):
    plant = db.query(PlantMaster).filter(
        PlantMaster.plant_code == plant_code
    ).first()

    if not plant:
        raise HTTPException(404, "plant not found")

    return {"status": "success", "data": plant}


# ======================================================
# 🔄 UPDATE
# ======================================================
@router.put("/{plant_code}")
def update_plant(plant_code: str, payload: dict, db: Session = Depends(get_db)):
    plant = db.query(PlantMaster).filter(
        PlantMaster.plant_code == plant_code
    ).first()

    if not plant:
        raise HTTPException(404, "plant not found")

    for key, value in payload.items():
        setattr(plant, key, value)

    db.commit()

    return {"status": "success", "data": payload}


# ======================================================
# ❌ DELETE
# ======================================================
@router.delete("/{plant_code}")
def delete_plant(plant_code: str, db: Session = Depends(get_db)):
    plant = db.query(PlantMaster).filter(
        PlantMaster.plant_code == plant_code
    ).first()

    if not plant:
        raise HTTPException(404, "plant not found")

    db.delete(plant)
    db.commit()

    return {"status": "success", "message": "deleted"}