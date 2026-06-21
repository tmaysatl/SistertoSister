"""Mongo client. Imported wherever DB access is required."""
from motor.motor_asyncio import AsyncIOMotorClient
from .settings import MONGO_URL, DB_NAME

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
