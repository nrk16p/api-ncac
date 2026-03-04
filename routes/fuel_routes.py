from fastapi import APIRouter, UploadFile, File
import pandas as pd
import numpy as np
import json

from services.fuel_service import (
    transform_ppt,
    transform_bangchak,
    transform_caltex,
    transform_meter
)

router = APIRouter(prefix="/fuel", tags=["Fuel"])


def dataframe_to_json(df: pd.DataFrame):
    """
    Convert dataframe safely to JSON
    Handles NaN / Inf values automatically
    """
    df = df.replace([np.inf, -np.inf], None)
    df = df.where(pd.notnull(df), None)

    return json.loads(df.to_json(orient="records"))


@router.post("/ppt")
async def ppt(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_ppt(contents)
    return dataframe_to_json(df)


@router.post("/bangchak")
async def bangchak(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_bangchak(contents)
    return dataframe_to_json(df)


@router.post("/caltex")
async def caltex(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_caltex(contents)
    return dataframe_to_json(df)


@router.post("/saraburi")
async def saraburi(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return dataframe_to_json(df)


@router.post("/rayong")
async def rayong(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return dataframe_to_json(df)from fastapi import APIRouter, UploadFile, File
import pandas as pd
import numpy as np
import json

from services.fuel_service import (
    transform_ppt,
    transform_bangchak,
    transform_caltex,
    transform_meter
)

router = APIRouter(prefix="/fuel", tags=["Fuel"])


def dataframe_to_json(df: pd.DataFrame):
    """
    Convert dataframe safely to JSON
    Handles NaN / Inf values automatically
    """
    df = df.replace([np.inf, -np.inf], None)
    df = df.where(pd.notnull(df), None)

    return json.loads(df.to_json(orient="records"))


@router.post("/ppt")
async def ppt(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_ppt(contents)
    return dataframe_to_json(df)


@router.post("/bangchak")
async def bangchak(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_bangchak(contents)
    return dataframe_to_json(df)


@router.post("/caltex")
async def caltex(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_caltex(contents)
    return dataframe_to_json(df)


@router.post("/saraburi")
async def saraburi(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return dataframe_to_json(df)


@router.post("/rayong")
async def rayong(file: UploadFile = File(...)):
    contents = await file.read()
    df = transform_meter(contents)
    return dataframe_to_json(df)