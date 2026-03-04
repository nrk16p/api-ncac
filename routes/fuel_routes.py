from fastapi import APIRouter, UploadFile, File
from services.fuel_service import (
    transform_ppt,
    transform_bangchak,
    transform_caltex,
    transform_meter
)

router = APIRouter(prefix="/fuel", tags=["Fuel"])


@router.post("/ppt")
async def ppt(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_ppt(contents)
    return df.to_dict(orient="records")


@router.post("/bangchak")
async def bangchak(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_bangchak(contents)
    return df.to_dict(orient="records")


@router.post("/caltex")
async def caltex(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_caltex(contents)
    return df.to_dict(orient="records")


@router.post("/saraburi")
async def saraburi(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return df.to_dict(orient="records")


@router.post("/rayong")
async def rayong(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return df.to_dict(orient="records")