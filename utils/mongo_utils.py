"""
utils/mongo_utils.py
--------------------
Everything that talks to MongoDB.

The connection URI is read from .streamlit/secrets.toml:

    [mongo]
    uri = "mongodb+srv://USERNAME:PASSWORD@flobridgedb.xa97s.mongodb.net"
    database = "Test"
    collection = "allorders"

Credentials are NEVER hard-coded in the code.
"""

from __future__ import annotations

import re
from typing import Callable

import streamlit as st
from bson.regex import Regex
from pymongo import MongoClient
from pymongo.collection import Collection

import config


class MongoConfigError(RuntimeError):
    """Raised when secrets.toml is missing or incomplete."""


def _mongo_settings() -> tuple[str, str, str]:
    try:
        mongo_secrets = st.secrets["mongo"]
    except Exception as exc:  # noqa: BLE001 - no secrets file / no [mongo] section
        raise MongoConfigError(
            "MongoDB settings not found. Create .streamlit/secrets.toml with a "
            "[mongo] section (see README.md, Step 6)."
        ) from exc

    uri = mongo_secrets.get("uri", "")
    if not uri or "USERNAME" in uri or "PASSWORD" in uri:
        raise MongoConfigError(
            "The MongoDB 'uri' in .streamlit/secrets.toml is empty or still has the "
            "USERNAME/PASSWORD placeholders. Put your real connection string there."
        )
    database = mongo_secrets.get("database", config.MONGO_DEFAULT_DB)
    collection = mongo_secrets.get("collection", config.MONGO_DEFAULT_COLLECTION)
    return uri, database, collection


@st.cache_resource(show_spinner=False)
def _get_client(uri: str) -> MongoClient:
    """One MongoClient for the whole app (cached across reruns and users)."""
    return MongoClient(
        uri,
        serverSelectionTimeoutMS=config.MONGO_TIMEOUT_MS,
        connectTimeoutMS=config.MONGO_TIMEOUT_MS,
        appname="flo-flipkart-operations",
    )


def get_collection() -> Collection:
    uri, database, collection = _mongo_settings()
    client = _get_client(uri)
    return client[database][collection]


def test_connection() -> tuple[bool, str]:
    """Ping MongoDB. Returns (ok, message) - used by the sidebar status check."""
    try:
        uri, database, collection = _mongo_settings()
        _get_client(uri).admin.command("ping")
        return True, f"Connected · {database}.{collection}"
    except MongoConfigError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not connect: {exc}"


def fetch_orders_by_item_ids(
    order_item_ids: list[str],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """
    Fetch MongoDB docs whose 'Sale Order Item Code' is either exactly the ID
    or the ID followed by '-something'. Same logic as your notebook:

        {"Sale Order Item Code": {"$regex": f"^{order_id}(-|$)"}}

    Done in batches of config.MONGO_BATCH_SIZE using $in with anchored regexes,
    which is much faster than one giant $or and can use an index on
    'Sale Order Item Code' if one exists.
    """
    if not order_item_ids:
        return []

    collection = get_collection()
    results: list[dict] = []
    total = len(order_item_ids)
    batch_size = config.MONGO_BATCH_SIZE

    for start in range(0, total, batch_size):
        batch = order_item_ids[start:start + batch_size]
        patterns = [Regex(f"^{re.escape(str(oid))}(-|$)") for oid in batch]
        query = {config.MONGO_MATCH_FIELD: {"$in": patterns}}
        results.extend(collection.find(query, config.MONGO_PROJECTION))

        if progress_callback:
            progress_callback(min(start + batch_size, total), total)

    return results
