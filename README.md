# FileNest — File Sharing Platform

A local-first file management project built with FastAPI, PostgreSQL, HTML/CSS/JavaScript, and Razorpay Checkout (test mode).

## Features

- Signup/signin/signout with signed session cookies and salted password hashes.
- Private per-account file listing, upload, download, search, and deletion.
- File metadata stored in PostgreSQL; file bytes stored under `uploads/`.
- Pricing page and one-time plan checkout through Razorpay.
- Server-side payment signature verification before marking an order paid.
- Razorpay webhook endpoint for payment events.
- FastAPI interactive API docs at `/docs`.

## Requirements

- Windows 10/11, VS Code, Python 3.11+ recommended, PostgreSQL 16/17/18.
- Internet is required to push to GitHub, install missing Python packages, and load Razorpay Checkout / reach Razorpay. Once dependencies are installed, core local file management can run without internet. A real payment gateway cannot process payments fully offline.
- No Docker required.

## 1. Create the PostgreSQL database

Start the PostgreSQL Windows service, then in PowerShell (adjust the path/version if needed):

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1
```

At the `postgres=#` prompt:

```sql
CREATE DATABASE fileshare_db;
\q
```

If the database already exists, do not create it again.

## 2. Configure the project

Open this folder in VS Code. In the VS Code terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL`. Generate a secret with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output into `SESSION_SECRET`. Do not commit `.env`.

## 3. Configure Razorpay test mode

Create or sign in to a Razorpay account and use **Test Mode** credentials. Put the Key ID and Key Secret in `.env`. Do not expose the Key Secret in frontend JavaScript or commit it to GitHub.

Without keys, the app still runs; checkout displays a setup message and cannot create a payment order. The gateway checkout script requires internet access.

For webhook testing, configure your Razorpay dashboard webhook URL to your publicly reachable development endpoint, e.g. a temporary HTTPS tunnel to `/api/payments/webhook`, and set the same webhook secret in `.env`. Do not expose a local development server permanently to the public internet.

## 4. Run the app

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000. Keep the terminal open.

## 5. Test checklist

1. Sign up with a username, email, and password (10+ characters).
2. Upload a small file, download it, search for it, then delete it.
3. Open Pricing and choose a plan.
4. With Razorpay test keys configured and internet available, complete a test checkout using test credentials documented by Razorpay.
5. Check `payments` records in PostgreSQL.
6. Sign out, sign in again, and verify files remain associated with your account.

## 6. Push the complete project to GitHub

Create an empty repository on GitHub (do not add a README if you already have this README). Then from the project folder:

```powershell
git init
git add .
git status
git commit -m "Build FileNest file sharing platform"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the remote URL with your repository URL. If Git prompts you to authenticate, follow GitHub's browser sign-in or use Git Credential Manager / a personal access token as GitHub instructs.

Before committing, run `git status` and make sure `.env` and private uploaded files are not listed. `.gitignore` excludes them.

## Database tables

Tables are created on app startup:
- `users`: accounts and password hashes.
- `files`: metadata and ownership.
- `payments`: plan, amount, currency, Razorpay order/payment IDs, and status.

## Security and deployment notes

This is a learning/demo project, not a production-ready public file-hosting service. Before public deployment add CSRF protection, HTTPS-only secure cookies, rate limiting, email verification/password reset, stronger file-content validation and malware scanning, quotas enforced server-side, database migrations, backup/retention policies, and production session storage. Configure gateway webhooks using the gateway dashboard and verify their signatures. Never mark an order paid based only on a browser callback.
