# Deploying Flo x Flipkart Operations on Streamlit Community Cloud

This guide takes the app from your laptop to a URL like
`https://flo-flipkart-ops.streamlit.app`, protected by the login page.

**Overview**

1. Set up login secrets and test locally (10 min)
2. Push the code to a **private** GitHub repository (15 min)
3. Allow Streamlit's servers into MongoDB Atlas (5 min)
4. Create the app on Streamlit Community Cloud and paste secrets (10 min)
5. Test, then switch off dry run
6. Day-to-day: updating, secrets, logs, limits

---

## Part 1 — Login + local test

The app opens on a sign-in page. Nothing else (uploads, data, MongoDB, Flipkart) loads until the user signs in.

- **Username:** `FKFlo`
- **Password:** the one you chose. It's **not** stored anywhere in the code or secrets. Only a salted PBKDF2-SHA256 hash is stored.

### 1.1 Add the login block to your local secrets
Open `.streamlit/secrets.toml` and add this block at the bottom. It is the hash of the password you chose:

```toml
[auth]
username = "FKFlo"
password_salt = "159d16f4c6323713059be30d3019bfbd"
password_hash = "92f9d11cdeffcab44bd3dd9e37adea67cdeb98ab900d61b5ea47b29447eaab36"
```

Your full `secrets.toml` should now have **three** sections: `[mongo]`, `[flipkart]` and `[auth]`.

### 1.2 Test it locally
```
streamlit run app.py
```
- Wrong password → "Incorrect username or password."
- Correct password → the app opens, with **Signed in as FKFlo** and a **Log out** button in the sidebar.

### 1.3 How the login protects the app
| Protection | Detail |
|---|---|
| Everything is gated | The login check runs before anything else; the page stops if not signed in |
| No password in code | Only a salted hash, kept in secrets |
| Guessing protection | 8 wrong attempts within 10 minutes (from anyone) pauses login for 10 minutes; each wrong attempt also waits 1 second |
| Session timeout | Signed out automatically after 12 hours |
| Log out | Clears everything in that browser session (uploaded files, results) |

Settings are at the top of `utils/auth.py`.

### 1.4 Changing the password later
```
python generate_password_hash.py
```
Enter the username and new password (hidden while typing). Copy the printed `[auth]` block into your local `secrets.toml` **and** into the Streamlit Cloud secrets (Part 6.2).

---

## Part 2 — Push the code to a private GitHub repository

Streamlit Community Cloud deploys straight from GitHub.

### 2.1 One-time setup
1. Create a GitHub account at https://github.com if you don't have one (a work email is best).
2. Install **Git** from https://git-scm.com/downloads using the default options. Restart VS Code afterwards.
3. Check it in the VS Code terminal:
   ```
   git --version
   ```
4. Tell Git who you are (once per computer):
   ```
   git config --global user.name "Tanmay Badgujar"
   git config --global user.email "data.analyst@flomattress.com"
   ```

### 2.2 Make sure secrets will NOT be uploaded
Open `.gitignore` and confirm these lines are there (they already are):
```
.streamlit/secrets.toml
.env
dispatch_logs/
*.csv
*.xlsx
```
> ⚠️ Never remove these. If `secrets.toml` reaches GitHub, your MongoDB, Flipkart and login credentials are exposed. If that ever happens, change all three immediately.

### 2.3 Publish from VS Code (easiest)
1. Click the **Source Control** icon in the left bar (branch icon, or `Ctrl+Shift+G`).
2. Click **Initialize Repository**.
3. Look at the **Changes** list. You should see `app.py`, `config.py`, `utils/…`, `sections/…`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`, etc.
   **`secrets.toml` must NOT be in this list.** If it is, stop and fix `.gitignore` first.
4. Type a message in the box above the list, e.g. `Initial version`, then click **Commit**. If asked *"stage all changes?"* click **Yes**.
5. Click **Publish Branch**.
6. Sign in to GitHub when the browser opens and allow VS Code.
7. Choose **Publish to GitHub private repository**. Name it `flo-flipkart-operations`.
8. Open https://github.com/<your-username>/flo-flipkart-operations and confirm:
   - It shows a 🔒 **Private** badge.
   - `.streamlit/` contains only `config.toml` and `secrets.toml.example`.

<details>
<summary>Prefer the command line? (alternative to 2.3)</summary>

Create an empty **private** repo on github.com (no README), then in the project folder:
```
git init
git add .
git status            # check secrets.toml is NOT listed
git commit -m "Initial version"
git branch -M main
git remote add origin https://github.com/<your-username>/flo-flipkart-operations.git
git push -u origin main
```
</details>

> **Company GitHub organisation?** If Flo has one, create the repo there instead of your personal account, so the team keeps access if people change roles.

---

## Part 3 — Allow Streamlit's servers into MongoDB Atlas

Locally, Atlas lets you in because your IP is on its Network Access list. Streamlit Community Cloud runs on shared servers **without a fixed IP address**, so Atlas has to allow connections from anywhere.

1. Go to https://cloud.mongodb.com → your project → **Security → Network Access**.
2. **+ Add IP Address** → **Allow Access from Anywhere** (this fills in `0.0.0.0/0`) → **Confirm**.
3. Wait until the status shows **Active** (about 1 minute).

Because the database is now reachable from any IP, the username/password is the only lock. So:
- Use a **long, random password** for the database user.
- **Recommended:** create a dedicated **read-only** user for this app. The app only reads `Test.allorders`.
  1. Go to **Security → Database Access → + Add New Database User**.
  2. Choose Password authentication, e.g. user `flo_flipkart_app`, and click *Autogenerate Secure Password*.
  3. Under Database User Privileges pick **Built-in Role → Only read any database**, or a custom role with `read` on `Test`.
  4. Use this user in the `uri` in the Streamlit Cloud secrets.

> **Flipkart side:** if your Flipkart Seller API application only accepts calls from whitelisted IPs, Community Cloud can't be used (its IP isn't fixed). You'd see HTTP 401/403 on dispatch even with correct credentials. In that case host on a server with a static IP (an AWS/Azure VM, Render or Railway with static outbound IP). Ask if you need that guide.

---

## Part 4 — Create the app on Streamlit Community Cloud

1. Go to **https://share.streamlit.io** and click **Continue with GitHub**.
2. Authorize Streamlit. When asked about repository access, allow **private repositories**, at least `flo-flipkart-operations`. If you used a company GitHub organisation, the org may need to approve the Streamlit app (*Grant* / *Request* next to the org name).
3. Complete the short sign-up form if it's your first time.
4. Click **Create app** (top right) → choose **Deploy a public app from GitHub** / **"Yup, I have an app"** (wording varies slightly).
5. Fill in:
   | Field | Value |
   |---|---|
   | Repository | `<your-username>/flo-flipkart-operations` |
   | Branch | `main` |
   | Main file path | `app.py` |
   | App URL (optional) | e.g. `flo-flipkart-ops` → `https://flo-flipkart-ops.streamlit.app` |
6. Click **Advanced settings**:
   - **Python version:** `3.12`
   - **Secrets:** paste the **entire contents** of your local `secrets.toml`, with all three sections:
     ```toml
     [mongo]
     uri = "mongodb+srv://flo_flipkart_app:<db-password>@flobridgedb.xa97s.mongodb.net"
     database = "Test"
     collection = "allorders"

     [flipkart]
     username = "<flipkart app id>"
     password = "<flipkart app secret>"
     dry_run = true

     [auth]
     username = "FKFlo"
     password_salt = "159d16f4c6323713059be30d3019bfbd"
     password_hash = "92f9d11cdeffcab44bd3dd9e37adea67cdeb98ab900d61b5ea47b29447eaab36"
     ```
     Start with **`dry_run = true`** for the first cloud test.
   - Click **Save**.
7. Click **Deploy**. Installing packages takes 2–5 minutes the first time. You can watch progress in the log panel.
8. When it's done, the login page opens at your app URL.

---

## Part 5 — Test on the cloud, then go live

1. Sign in as `FKFlo`.
2. Sidebar → **🔌 Test MongoDB connection** → should be green.
   If red, re-check Part 3 and the `uri`.
3. Upload a real Self-Ship file → **Process files** → check mapping looks the same as locally.
4. Select one Dispatch By Date → **Yes, dispatch**. With `dry_run = true` you'll see the requests built but nothing sent. Open the **API log** tab and check one request body.
5. Go live:
   1. Open the app's ⋮ menu → **Settings** → **Secrets** and change `dry_run = true` to `dry_run = false`.
   2. Click **Save**. The app restarts within about a minute.
6. Dispatch **one** real order first. Confirm it shows as dispatched in Flipkart Seller Hub, check the `Flipkart Response` column, then do the full batch.

---

## Part 6 — Day-to-day

### 6.1 Updating the code
Edit in VS Code → **Source Control** → write a message → **Commit** → **Sync Changes** (or `git push`).
Streamlit Cloud picks up the push and redeploys automatically in about a minute.

### 6.2 Changing secrets (passwords, dry run, tokens)
Go to https://share.streamlit.io, find the app, then ⋮ → **Settings** → **Secrets**. Edit and **Save**. The app restarts automatically.

### 6.3 Logs and restarting
- Open the app and click **Manage app** (bottom-right) to see the live server log. Errors and tracebacks appear here.
- To restart: ⋮ → **Reboot app**.

### 6.4 Sharing with the team
Share the app URL plus the `FKFlo` login.
- **Extra layer, optional:** in the app's **Settings → Sharing**, Community Cloud can restrict viewing to specific email addresses. People then sign in with Google/GitHub before they reach your login page. Use it if your workspace shows this option; the built-in login protects the app either way.

### 6.5 Limits to know about
| Limit | What it means for you |
|---|---|
| **App sleeps when unused** | After a period without visitors the app goes to sleep. The first visitor sees a "wake up" button, and it takes ~30–60 s to start. |
| **No permanent disk** | The `dispatch_logs/` folder is wiped whenever the app restarts or redeploys. **Always download the results CSV/Excel after each dispatch**; that's your record. |
| **Limited memory/CPU** | It's a free, shared machine. Thousands of orders per run is fine; very large files may be slow. |
| **Shared session state** | Each browser tab has its own session. Two people dispatching at the same time each see only their own run. The "already dispatched" protection is per session, but Flipkart will still reject truly duplicate dispatches. |
| **Data location** | Uploaded files and order data are processed on Streamlit's (Snowflake's) servers. Check this is acceptable under Flo's data policy. |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Deploy fails with `ModuleNotFoundError` | The package is missing from `requirements.txt`. Add it, commit, push. |
| "Login is not configured" | `[auth]` block missing or misspelled in the Cloud secrets (Part 4, step 6). |
| Correct password rejected | `password_salt`/`password_hash` pasted with a typo or extra space. Re-paste from Part 1.1, or regenerate (Part 1.4). |
| "Too many failed sign-in attempts" | Wait 10 minutes, or reboot the app (⋮ → Reboot) to clear it. |
| MongoDB timeout on Cloud but works locally | Atlas Network Access doesn't include `0.0.0.0/0` (Part 3). |
| `Authentication failed` (Mongo) | Wrong user/password in `uri`, or special characters not URL-encoded (`@` → `%40`). |
| Flipkart token HTTP 401 on Cloud only | Flipkart app may be IP-restricted. See the note at the end of Part 3. |
| Can't see the private repo when creating the app | Re-authorize Streamlit's GitHub access to include private repos / the org (Part 4, step 2). |
| App shows old code | Check the push reached GitHub; then ⋮ → **Reboot app**. |
