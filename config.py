"""
config.py
---------
Central place for every constant used by the app:
  * App metadata
  * Flipkart Self-Ship CSV column mapping
  * MongoDB field names
  * Facility (warehouse) master data

If anything changes in the Flipkart export format or a new warehouse is
added, this is the ONLY file you should need to edit.
"""

# ==========================================================================
# APP METADATA
# ==========================================================================
APP_NAME = "Flo x Flipkart Operations"
APP_ICON = "📦"

SECTION_DISPATCH = "Dispatch Self-Ship Orders"
SECTION_RETURNS = "Self-Ship Returns"

# ==========================================================================
# FLIPKART SELF-SHIP CSV SETTINGS
# ==========================================================================
# First 3 rows of every Flipkart Self-Ship export are garbage rows.
# Row 4 is the header row.
CSV_SKIP_ROWS = 3

# Column in the raw CSV  ->  column name we want in the app
SELF_COLUMN_MAP = {
    "Ordered On": "Order Date",
    "Order Id": "Order ID",
    "PRIMARY ORDER ITEM ID": "Primary Order Item ID",
    "ORDER ITEM ID": "Order Item ID",
    "SKU Code": "Flipkart SKU",
    "Quantity": "Quantity",
    "Invoice No.": "Invoice Number",
    "Invoice Date (mm/dd/yy)": "Invoice Date",
    "Dispatch By Date": "Dispatch By Date",
    "Tentative Delivery Date (mm/dd/yy)": "Tentative Delivery Date",
}

# Raw columns that MUST be present in every uploaded file
REQUIRED_RAW_COLUMNS = list(SELF_COLUMN_MAP.keys())

# Columns (after rename) that must be converted to YYYY-MM-DD
DATE_COLUMNS = [
    "Order Date",
    "Invoice Date",
    "Dispatch By Date",
    "Tentative Delivery Date",
]

# Columns (after rename) from which the leading apostrophe (') is removed
APOSTROPHE_COLUMNS = [
    "Primary Order Item ID",
    "Order Item ID",
]

# Extra column added by the app so you always know which file a row came from
SOURCE_FILE_COLUMN = "Source File"

# ==========================================================================
# MONGODB SETTINGS
# (the connection URI itself lives in .streamlit/secrets.toml - NEVER here)
# ==========================================================================
MONGO_DEFAULT_DB = "Test"
MONGO_DEFAULT_COLLECTION = "allorders"

MONGO_MATCH_FIELD = "Sale Order Item Code"

MONGO_PROJECTION = {
    "_id": 0,
    "Sale Order Item Code": 1,
    "Sale Order Code": 1,
    "Item SKU Code": 1,
    "Facility": 1,
    "Shipping provider": 1,
    "Tracking Number": 1,
}

# Columns that get merged into SELF from MongoDB
MONGO_MERGE_COLUMNS = [
    "Sale Order Code",
    "Item SKU Code",
    "Facility",
    "Shipping provider",
    "Tracking Number",
]

# How many Order Item IDs to send to MongoDB per query.
# Keeps each query small and fast even with thousands of orders.
MONGO_BATCH_SIZE = 500

# Seconds to wait for MongoDB before giving up
MONGO_TIMEOUT_MS = 15000

# ==========================================================================
# FACILITY (WAREHOUSE) MASTER DATA
# Keys MUST be UPPER-CASE (Facility values are upper-cased before lookup)
# ==========================================================================
FACILITY_DATA = {
    "FLO SAKINAKA": {
        "Location ID": "LOC69de59427d9a468db11d1efdbc49d733",
        "WH Address Line 1": "Excom House 7",
        "WH Address Line 2": "Bandi Bazaar",
        "WH City": "Mumbai",
        "WH State": "Maharashtra",
        "WH Pincode": 400072,
    },
    "FLO VASAI": {
        "Location ID": "LOCc5520ff6ef58430ca8e851be8b421730",
        "WH Address Line 1": "No 04 Gala no 1, Siddharth industrial Estate, Siddharth industrial Estate,",
        "WH Address Line 2": "Near Agarwal Naka, Vasai Virar, Palghar",
        "WH City": "Vasai",
        "WH State": "Maharashtra",
        "WH Pincode": 401208,
    },
    "FLO MEDCHAL": {
        "Location ID": "LOC514b424f17ff46dd95611f8986546e81",
        "WH Address Line 1": "Survey No. 615/A, 624/2/3, Medchal Mandal, Opp Sneha Farms Pvt Ltd Pudur,",
        "WH Address Line 2": "Pudur, Hyderabad, Medchal Malkajgiri,",
        "WH City": "Rangareddy",
        "WH State": "Telangana",
        "WH Pincode": 501401,
    },
    "FLO TALEGAON": {
        "Location ID": "LOC080ba8d835124a4da68a4c18888e081e",
        "WH Address Line 1": "Flo Sleep Solutions Pvt. Ltd. GAT No 566 and 657/1,",
        "WH Address Line 2": "Next to Hyndai Glovis, Tal Mavel, Badhalwadi, Talegaon Dabhade",
        "WH City": "Badhalawadi",
        "WH State": "Maharashtra",
        "WH Pincode": 410507,
    },
    "FLO TALEGAON WH": {
        "Location ID": "LOC080ba8d835124a4da68a4c18888e081e",
        "WH Address Line 1": "Flo Sleep Solutions Pvt. Ltd. GAT No 566 and 657/1,",
        "WH Address Line 2": "Next to Hyndai Glovis, Tal Mavel, Badhalwadi, Talegaon Dabhade",
        "WH City": "Badhalawadi",
        "WH State": "Maharashtra",
        "WH Pincode": 410507,
    },
    "FLO BANGALORE": {
        "Location ID": "LOC3e4868efcfe741d78354951c5f2ec700",
        "WH Address Line 1": "Property No. 360/84/6, Survey No. 84/6 and 84/7,Budhihal Village, Kasaba Hobli, Nelamangala",
        "WH Address Line 2": "Kachanahalli",
        "WH City": "Kachanahalli",
        "WH State": "Karnataka",
        "WH Pincode": 562123,
    },
    "FLO GURGAON 2": {
        "Location ID": "LOCda86e0e44c0646a9801fd82c697f92ac",
        "WH Address Line 1": "Killa No. 18/2(2-18),Village- Gurhi, Gurugram Circle- 2, Sohna, Bilaspur Rd, Taoru, Haryana- 122105",
        "WH Address Line 2": "Nuh",
        "WH City": "Nuh",
        "WH State": "Haryana",
        "WH Pincode": 122105,
    },
}

WH_COLUMNS = [
    "Location ID",
    "WH Address Line 1",
    "WH Address Line 2",
    "WH City",
    "WH State",
    "WH Pincode",
]

# Label used in the Dispatch By Date checkbox list for rows with no date
NO_DATE_LABEL = "No Dispatch By Date"

# Reason column added to SELF_NOT_DISPATCHED
NOT_DISPATCHED_REASON_COLUMN = "Not Dispatched Reason"

# ==========================================================================
# FLIPKART SELLER API (credentials live in .streamlit/secrets.toml)
# ==========================================================================
FLIPKART_TOKEN_URL = "https://seller.api.flipkart.net/oauth-service/oauth/token"
FLIPKART_DISPATCH_URL = "https://api.flipkart.net/sellers/v3/shipments/selfShip/dispatch"

FLIPKART_TIMEOUT_SECONDS = 60
FLIPKART_MAX_RETRIES = 3            # retries on network errors / HTTP 429 / 5xx
FLIPKART_DISPATCH_BATCH_SIZE = 20   # shipments per POST request

# The Tentative Delivery Date column only has a date - this time (UTC) is
# appended to build "2026-10-06T12:30:00.000Z"
TENTATIVE_DELIVERY_TIME_UTC = "12:30:00.000"

# False -> every item is sent with "quantity": 1 (as specified)
# True  -> uses the Quantity column from the Self-Ship file
USE_QUANTITY_FROM_FILE = False

# Every one of these must be filled for a row to be sent to Flipkart
FLIPKART_REQUIRED_COLUMNS = [
    "Sale Order Code",          # shipmentId
    "Order Item ID",            # invoice.items[].orderItemId
    "Tentative Delivery Date",  # tentativeDeliveryDate
    "Invoice Date",             # invoice.invoiceDate
    "Shipping provider",        # deliveryPartner / deliveryPartnerCode
    "Tracking Number",          # trackingId
    "Location ID",              # locationId + dispatchLocation.locationId
    "WH Address Line 1",        # dispatchLocation.address.address1
    "WH Address Line 2",        # dispatchLocation.address.address2
    "WH City",                  # dispatchLocation.address.city
    "WH State",                 # dispatchLocation.address.state
    "WH Pincode",               # dispatchLocation.address.pincode
]

# Fields that must be identical for all items grouped under one shipmentId
FLIPKART_SHIPMENT_LEVEL_COLUMNS = [
    "Tentative Delivery Date",
    "Invoice Date",
    "Shipping provider",
    "Tracking Number",
    "Location ID",
]

# Result columns added to the final download
RESULT_STATUS_COLUMN = "Dispatch Status"
RESULT_DISPATCHED_COLUMN = "Dispatched on Flipkart"
RESULT_RESPONSE_COLUMN = "Flipkart Response"
RESULT_PARTNER_COLUMN = "Delivery Partner Sent"
RESULT_TIME_COLUMN = "Dispatch Attempted At (UTC)"

# Folder where every dispatch run is saved automatically (audit trail)
DISPATCH_LOG_DIR = "dispatch_logs"

# Sent as deliveryPartnerCode for every partner except DELHIVERY (Ekart and Other)
FLIPKART_OTHER_PARTNER_VALUE = "self_ship_vendor"

# A shipment counts as dispatched ONLY when Flipkart's response has
# "processingStatus" equal to this (compared in lower case). HTTP 200 alone is not enough.
FLIPKART_SUCCESS_STATUS = "success"

# ==========================================================================
# SELF-SHIP RETURNS (Flipkart Returns API v2)
# ==========================================================================
FLIPKART_RETURNS_URL = "https://api.flipkart.net/sellers/v2/returns"

# The API's "nextUrl" is relative (e.g. "/returns/search/ewog..."), so it is
# joined to this base to build the next page's URL.
FLIPKART_RETURNS_NEXT_BASE = "https://api.flipkart.net/sellers/v2"

# Both sources are fetched; the 'type' field in the response holds the same value
RETURN_SOURCES = {
    "customer_return": "Customer Return",
    "courier_return": "Courier Return",
}

# Returns created AFTER this date/time are fetched (sent as createdAfter, no offset)
RETURNS_CREATED_AFTER = "2026-09-01T00:00:00"

# Timezone used for dates shown in the returns tables
RETURNS_TIMEZONE = "Asia/Kolkata"

# Safety stop so a pagination bug can never loop forever (25 returns per page)
RETURNS_MAX_PAGES = 2000

# MongoDB field that says whether Flipkart (channel) does the shipping
CHANNEL_SHIPPING_FIELD = "Channel Shipping"

RETURNS_MONGO_PROJECTION = {**MONGO_PROJECTION, CHANNEL_SHIPPING_FIELD: 1}
RETURNS_MONGO_MERGE_COLUMNS = MONGO_MERGE_COLUMNS + [CHANNEL_SHIPPING_FIELD]

# Column added by the app that classifies each return
RETURN_CATEGORY_COLUMN = "Return Category"
RETURN_FACILITY_COLUMN = "Return Location (Facility)"

# ==========================================================================
# GOOGLE SHEET SYNC (returns)
# Credentials: [gcp_service_account] in secrets (see README / secrets example)
# ==========================================================================
GSHEET_SPREADSHEET_ID = "1dAD-lxlMEKcWuSlg8ONxrtkmrqFI5LTUAsDuojhGGVE"
GSHEET_URL = f"https://docs.google.com/spreadsheets/d/{GSHEET_SPREADSHEET_ID}/edit"

# Result group -> tab name in the "Flipkart Returns" spreadsheet
GSHEET_TABS = {
    "self_ship": "Self-Ship Returns",
    "needs_check": "Needs Check",
    "flipkart_managed": "Flipkart Managed",
}

# Rows are matched on this column: existing returnIds are updated, new ones appended
GSHEET_KEY_COLUMN = "returnId"

# Extra column written on every synced row (IST time of the sync)
GSHEET_SYNC_COLUMN = "Last Synced (IST)"

# Sync automatically after every "Fetch returns"
GSHEET_AUTO_SYNC = True

# A return belongs to exactly one tab. If its category changed since the last
# sync (e.g. "Needs Check" -> "Self-Ship Returns"), remove it from the old tab.
GSHEET_MOVE_BETWEEN_TABS = True
