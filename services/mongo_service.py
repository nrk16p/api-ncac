"""
MongoDB client กลางของ ncacdb

เดิม script แต่ละตัวสร้าง MongoClient ของตัวเอง (scripts/engineon/common.py,
routes/pipeline/pipeline_routes.py) ซึ่งพอมี route ที่ต้องใช้ Mongo ระหว่าง
request จริง ๆ การเปิด client ใหม่ทุกครั้งจะเสีย handshake ซ้ำ ๆ
ไฟล์นี้จึงถือ client ตัวเดียวไว้ให้ทั้งแอปใช้ร่วมกัน (pymongo จัดการ pool ให้เอง
และ thread-safe อยู่แล้ว)

env: MONGODB_URI (fallback MONGO_URI) — ตามที่ scripts เดิมใช้
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()


def _mongo_uri() -> str:
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("❌ MONGODB_URI / MONGO_URI not found in environment variables")
    return uri


@lru_cache(maxsize=1)
def get_mongo_client() -> MongoClient:
    """MongoClient ตัวเดียวทั้งแอป — สร้างครั้งแรกที่มีคนเรียกใช้"""
    return MongoClient(
        _mongo_uri(),
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        tz_aware=True,
    )


def get_mongo_db(db_name: str) -> Database:
    return get_mongo_client()[db_name]
