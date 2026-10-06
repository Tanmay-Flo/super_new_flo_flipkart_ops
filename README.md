# Flo x Flipkart Operations — Setup & Run Guide (VS Code)

> To put the app online, see **DEPLOYMENT.md**.

A Streamlit app to prepare Flipkart **Self-Ship** orders for dispatch.

**Dispatch Self-Ship Orders** section flow:

1. Upload one or many Flipkart Self-Ship CSV files (one per location)
2. Process: skip first 3 rows → keep/rename columns → strip apostrophes → dates to YYYY-MM-DD → combine all files → fetch MongoDB (`Test.allorders`) → merge → add warehouse columns
3. Review: shape before/after, duplicates on Order Item ID, unmapped orders, missing Shipping provider / Tracking Number, unknown facilities
4. Tick the Dispatch By Date(s) to dispatch → `SELF` and `SELF_NOT_DISPATCHED`
5. Confirm dispatch on Flipkart (Yes / No) → **Step 1** fetch Flipkart access token → **Step 2** mark shipments as dispatched → download results for every order item

---

## 0. Project structure

```
flo_flipkart_ops/
├── app.py                        ← entry point (run this)
├── config.py                     ← column maps, facility master, Mongo settings
├── requirements.txt              ← Python packages
├── .gitignore
├── README.md
├── .streamlit/
│   ├── config.toml               ← theme + upload size
│   └── secrets.toml.example      ← template for MongoDB credentials
├── sections/
│   ├── __init__.py
│   ├── dispatch.py               ← "Dispatch Self-Ship Orders" section (all 5 steps)
│   └── returns.py                ← "Mark Orders as Returned" placeholder
└── utils/
    ├── __init__.py
    ├── data_processing.py        ← all pandas logic (load, merge, WH, checks, split)
    ├── mongo_utils.py            ← MongoDB connection + batched fetch
    ├── flipkart_api.py           ← Flipkart token + Self-Ship dispatch API calls
    └── ui.py                     ← CSS, header, step headers, metric cards
```

---

## 1. Install the prerequisites (one time)

### 1.1 Python
1. Check whether Python is installed: open **Command Prompt** (Windows) or **Terminal** (Mac) and run
   ```
   python --version
   ```
   (on Mac use `python3 --version`). You need **3.10 or newer** (3.11 / 3.12 recommended).
2. If missing, download from https://www.python.org/downloads/.
   - **Windows:** on the first installer screen tick **"Add python.exe to PATH"**, then click *Install Now*.
3. Close and reopen the terminal, and run the version command again to confirm.

### 1.2 VS Code
1. Install from https://code.visualstudio.com/ and open it.
2. Click the **Extensions** icon in the left bar (four squares) or press `Ctrl+Shift+X` (`Cmd+Shift+X` on Mac).
3. Install **Python** (by Microsoft). It also installs **Pylance**.

---

## 2. Put the project on your computer

1. Download `flo_flipkart_ops.zip` from this chat.
2. Extract it somewhere simple, e.g. `D:\Projects\flo_flipkart_ops` (Windows) or `~/Projects/flo_flipkart_ops` (Mac).
   Make sure the folder you open contains `app.py` directly (not a nested `flo_flipkart_ops/flo_flipkart_ops`).
3. In VS Code: **File → Open Folder…** → select the `flo_flipkart_ops` folder → **Select Folder**.
   If asked *"Do you trust the authors of the files in this folder?"* → **Yes, I trust the authors**.

> **Hidden folder note:** `.streamlit` starts with a dot, so Mac Finder hides it. VS Code's Explorer shows it. If it's missing after extracting, create it manually (Step 6).

---

## 3. Open the terminal inside VS Code

Menu **Terminal → New Terminal** (or `` Ctrl+` ``). A terminal opens at the bottom, already inside the project folder. Confirm with:

```
dir        (Windows)
ls         (Mac)
```

You should see `app.py`, `config.py`, `requirements.txt`, etc.

---

## 4. Create a virtual environment (keeps packages isolated)

### Windows (PowerShell — the VS Code default)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If you get *"running scripts is disabled on this system"*, run this once, then activate again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
(If your terminal is Command Prompt instead: `.venv\Scripts\activate.bat`.)

### Mac / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
```

When it works, the prompt starts with **`(.venv)`**. Every time you open a new terminal to work on this app, activate it again.

### Tell VS Code to use this environment
`Ctrl+Shift+P` (`Cmd+Shift+P`) → type **Python: Select Interpreter** → pick the one showing **`.venv`**.
This removes the yellow "import could not be resolved" squiggles.

---

## 5. Install the packages

With `(.venv)` active:

```
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This installs Streamlit, pandas, pymongo (with `dnspython` for `mongodb+srv://` URLs), openpyxl (Excel downloads), requests (Flipkart API calls) and python-dotenv (optional `.env` support). Verify:

> **Updating from the previous version?** Run `pip install -r requirements.txt` again — `requests` and `python-dotenv` are new.

```
streamlit --version
```

---

## 6. Add your MongoDB credentials (secrets)

The connection string is **not** in the code — it's read from `.streamlit/secrets.toml`.

1. In VS Code's Explorer, open the `.streamlit` folder.
2. Right-click `secrets.toml.example` → **Copy**, then right-click the `.streamlit` folder → **Paste**, and rename the copy to exactly **`secrets.toml`**.
   (Or in the terminal: Windows `copy .streamlit\secrets.toml.example .streamlit\secrets.toml`, Mac `cp .streamlit/secrets.toml.example .streamlit/secrets.toml`.)
3. Open `secrets.toml` and replace `USERNAME` and `PASSWORD` with the real values:
   ```toml
   [mongo]
   uri = "mongodb+srv://realuser:realpass@flobridgedb.xa97s.mongodb.net"
   database = "Test"
   collection = "allorders"

   [flipkart]
   username = "your_flipkart_app_id"
   password = "your_flipkart_app_secret"
   dry_run = false
   ```
   - `username` / `password` are your Flipkart Seller API application's **App ID** and **App Secret** (from the API access section of Flipkart Seller Hub). They're used as Basic Auth for the token call.
   - **`dry_run = true`** builds the exact API requests and shows them in the app, but sends **nothing** to Flipkart. Use it for the first test run, then set it to `false`.
   - **Prefer environment variables?** Leave out the `[flipkart]` section and set `FLIPKART_USERNAME`, `FLIPKART_PASSWORD` (and optionally `FLIPKART_DRY_RUN=true`), or put them in a `.env` file in the project folder:
     ```
     FLIPKART_USERNAME=your_flipkart_app_id
     FLIPKART_PASSWORD=your_flipkart_app_secret
     ```
     Values in `secrets.toml` take priority over environment variables.
   Also add the **login** block (see `DEPLOYMENT.md` → Part 1 for the ready-made values, or generate your own with `python generate_password_hash.py`):
   ```toml
   [auth]
   username = "FKFlo"
   password_salt = "..."
   password_hash = "..."
   ```
4. Save (`Ctrl+S`).

**Important:**
- If the password contains special characters (`@ : / ? # [ ] %`), URL-encode them, e.g. `@` → `%40`, `#` → `%23`, `%` → `%25`.
- `secrets.toml` is in `.gitignore` — never commit or share it.
- **MongoDB Atlas IP access:** your current internet IP must be allowed. Atlas → your project → **Security → Network Access** → **Add IP Address** → *Add Current IP Address*. Office and home IPs differ; if the connection works in one place and not the other, this is why.

---

## 7. Run the app

With `(.venv)` active and the terminal in the project folder:

```
streamlit run app.py
```

- The browser opens automatically at **http://localhost:8501**. If it doesn't, copy that URL from the terminal.
- The first time, Streamlit may ask for an email in the terminal — just press **Enter**.
- In the sidebar, click **🔌 Test MongoDB connection**. You should see a green *Connected · Test.allorders*.

**To stop the app:** click in the terminal and press `Ctrl+C`.

**Editing code while running:** `runOnSave = true` is set, so saving a file reloads the app. If a change in a `utils/` or `sections/` file doesn't show up, press `R` in the browser or stop (`Ctrl+C`) and run again.

---

## 8. Using the app (Dispatch Self-Ship Orders)

1. **Step 1 – Upload.** Drag all Self-Ship CSVs (one per location) into the uploader, or click *Browse files* and multi-select with `Ctrl`/`Cmd`. Each file must have 3 junk rows and the header on row 4 — exactly as Flipkart exports it.
2. **Step 2 – Process.** Click **⚙️ Process files**. A status box shows live progress: reading files, the MongoDB lookup (in batches of 500 IDs), merge, and warehouse columns.
   - Files missing required columns are skipped with the reason shown.
   - The exact same file uploaded twice is ignored automatically.
3. **Step 3 – Review.**
   - **Shape before merge / after merge** (rows × columns) and whether the row count changed.
   - **Duplicates** on `Order Item ID` (count + the rows, in the *Duplicates* tab).
   - **Orders not mapped** (no MongoDB match) and **Missing Shipping / Tracking**.
   - **Unknown facility** — MongoDB returned a Facility that isn't in the warehouse master in `config.py`.
   - Download the full merged data (CSV) or a multi-sheet quality report (Excel).
4. **Step 4 – Dispatch By Date.** Tick the dates to dispatch (or use *Select all*, *Clear all*, *Select today & overdue*). Live counts show what goes to `SELF` and `SELF_NOT_DISPATCHED`, each with a download button. `SELF_NOT_DISPATCHED` has a **Not Dispatched Reason** column.
   - **⚙️ Dispatch options:**
     - *Exclude orders with missing Tracking Number / Shipping provider / warehouse* — **ON by default**. Such orders are moved to `SELF_NOT_DISPATCHED` even if their date is selected, since Flipkart can't dispatch them. Turn it off to keep them strictly by date.
     - *Remove duplicate Order Item IDs (keep first)* — OFF by default.
5. **Step 5 – Confirm & dispatch.**
   - **Pre-check:** before you click anything, the app lists every selected item that has an empty or invalid field the API needs. Those items **won't be sent**.
   - **🔍 Preview the API request:** shows the exact JSON body for the first 3 shipments.
   - Click **✅ Yes, dispatch**:
     1. **Step 1 – Access token:** `GET https://seller.api.flipkart.net/oauth-service/oauth/token?grant_type=client_credentials&scope=Seller_Api` with Basic Auth. If this fails, nothing is dispatched and the error is shown.
     2. **Step 2 – Dispatch:** `POST https://api.flipkart.net/sellers/v3/shipments/selfShip/dispatch` with `Authorization: Bearer <token>`, in batches of 20 shipments, with a progress bar.
   - **❌ No** cancels; nothing is sent.
   - **Results:** cards for Dispatched / Failed / Not sent / Held back, tabs for each group, the raw API log, and downloads:
     - **All order items (CSV / Excel)** — every uploaded order item with these new columns: `Dispatched on Flipkart` (Yes/No), `Dispatch Status`, `Not Dispatched Reason`, `Delivery Partner Sent`, `Flipkart Response`, `Dispatch Attempted At (UTC)`.
     - **API log (JSON)** — every request body sent and every response received.
   - A copy of each run is also saved automatically in the `dispatch_logs/` folder in the project.
   - **🔁 Retry failed** appears if Flipkart rejected some items; it resends only those.

#### How each field in the request is filled

| API field | Source |
|---|---|
| `shipmentId` | `Sale Order Code` |
| `tentativeDeliveryDate` | `Tentative Delivery Date` → `YYYY-MM-DDT12:30:00.000Z` (time set in `config.py`) |
| `dispatchDate` | Time you clicked Yes, UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ` |
| `deliveryPartner`, `deliveryPartnerCode` | `DELHIVERY` if `Shipping provider` contains "delhivery" (any case), else `Other` |
| `trackingId` | `Tracking Number` |
| `locationId`, `dispatchLocation.locationId` | `Location ID` |
| `invoice.invoiceDate` | `Invoice Date` (`YYYY-MM-DD`) |
| `invoice.items[].orderItemId` / `quantity` | `Order Item ID` / `1` |
| `dispatchLocation.address.*` | `WH Address Line 1`, `WH Address Line 2`, `WH City`, `WH State`, `WH Pincode` |

#### Dispatch Status values

| Status | Meaning |
|---|---|
| Dispatched | Flipkart accepted it |
| Failed | Flipkart rejected it, or the API errored — see `Flipkart Response` |
| Not Sent – Missing Data | A required field was empty or a date was invalid — reason lists which |
| Not Sent – Conflicting Data | Items sharing a `Sale Order Code` had different tracking/location/dates |
| Not Sent – Duplicate Row | Same Order Item ID appeared twice; it was sent once via the first row |
| Skipped – Already Dispatched | Already dispatched earlier in this session; not sent again |
| Held Back – Incomplete Data | Date was selected but data was incomplete (Step 4 option) |
| Not Selected for Dispatch | Its Dispatch By Date wasn't ticked |
| Dry Run – Not Sent | `dry_run = true` |

**Safety:**
- The same selection can't be dispatched twice; the Yes button is replaced by the results.
- Order items already dispatched in this session are never re-sent, even if you change the selection.
- Network errors, HTTP 429 and 5xx responses are retried up to 3 times.
- If a batch is rejected with a 4xx, the shipments are resent one by one so a single bad shipment doesn't block the others.

**🧹 Start over** clears the uploaded files and all results.

---

## 9. What changed vs. your Jupyter code (and why)

| Area | Notebook | App | Why |
|---|---|---|---|
| Reading CSVs | one file | any number, concatenated | multi-location upload |
| Column types | default | everything read as text first, Quantity → integer | stops long IDs turning into `4.25E+17` |
| Encodings | UTF-8 only | tries UTF-8, then cp1252/latin-1 | Excel-saved files sometimes aren't UTF-8 |
| Dates | `to_datetime` | `to_datetime(format="mixed")` + count of unreadable values | handles mixed formats; warns instead of silently blanking |
| Mongo query | one giant `$or` | same anchored regex as the notebook, sent as `$in` in batches of 500 | much faster with thousands of IDs; same matches |
| Facility | `astype(str).upper()` | same, but missing stays blank | avoids a fake `"NAN"` facility |
| Extra column | – | `Source File` | tells you which location file a row came from |
| Credentials | in code | `.streamlit/secrets.toml` | security |

All other logic (column list and names, apostrophe removal, splitting Order Item ID on `-`, keeping the first Mongo doc per base ID, left merge, WH mapping, `Int64` pincode, `reset_index` on both split dataframes) is the same as your notebook.

---

## 10. Common changes

- **New warehouse:** add an entry to `FACILITY_DATA` in `config.py` (key in UPPER-CASE, exactly as Facility appears in MongoDB).
- **Flipkart renamed a column:** update `SELF_COLUMN_MAP` in `config.py`.
- **Different DB/collection:** change `database` / `collection` in `secrets.toml`.
- **Send the real Quantity instead of 1:** set `USE_QUANTITY_FROM_FILE = True` in `config.py`.
- **Change the time used in `tentativeDeliveryDate`:** `TENTATIVE_DELIVERY_TIME_UTC` in `config.py`.
- **Batch size / retries / timeout:** `FLIPKART_DISPATCH_BATCH_SIZE`, `FLIPKART_MAX_RETRIES`, `FLIPKART_TIMEOUT_SECONDS` in `config.py`.
- **Which fields are required before sending:** `FLIPKART_REQUIRED_COLUMNS` in `config.py`.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| `'streamlit' is not recognized` | The venv isn't active. Activate it (Step 4), or run `python -m streamlit run app.py`. |
| `ModuleNotFoundError: No module named 'config'` / `'utils'` | Run the command from the folder that contains `app.py`. Check with `dir` / `ls`. |
| "MongoDB settings not found" | `.streamlit/secrets.toml` missing or misnamed (e.g. `secrets.toml.txt`). In Windows Explorer turn on *View → File name extensions* to check. |
| `ServerSelectionTimeoutError` / connection timeout | Add your IP in Atlas Network Access (Step 6); check VPN/office firewall; check the URI. |
| `Authentication failed` | Wrong username/password, or special characters not URL-encoded. |
| `The DNS query name does not exist` / `dnspython` error | `pip install "pymongo[srv]"` (included in requirements). |
| File skipped: "Missing required column(s)" | The file isn't a Self-Ship export, or the header isn't on row 4. Open it in a text editor to check. |
| All orders show "not mapped" | Order Item IDs aren't in `Test.allorders` yet, or `Sale Order Item Code` uses a different format. |
| "Flipkart API credentials not found" | Add the `[flipkart]` section to `secrets.toml` (Step 6) and restart the app. |
| "Step 1 failed – could not get a Flipkart access token" (HTTP 401) | Wrong App ID / App Secret, or the app isn't approved for Seller API access. |
| Everything shows `Failed` with HTTP 401/403 | Token not accepted for the dispatch API — check the app's API permissions in Seller Hub. |
| `Failed` with a message from Flipkart | Read `Flipkart Response` — usually a wrong shipmentId, tracking ID, location ID or a date in the past. |
| Port 8501 already in use | `streamlit run app.py --server.port 8502` |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |

---

## 12. Daily routine (quick version)

```
# open VS Code → File → Open Folder → flo_flipkart_ops → Terminal → New Terminal
.\.venv\Scripts\Activate.ps1      # Windows   (Mac: source .venv/bin/activate)
streamlit run app.py
```
