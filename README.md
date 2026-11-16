# 📚 Stacks — Library Management System

A web-based library management system built with **Flask and SQLite**. I built it as a personal project to practice backend development, database design, and access-control patterns, and to put together a clean, usable interface without a JS framework.

> **Status:** learning/portfolio project. It is not hardened for production use — see [Security notes](#-security-notes).

<!-- Add screenshots here after running the app locally:
![Home](docs/screenshots/home.png)
![Catalog](docs/screenshots/catalog.png)
![Admin dashboard](docs/screenshots/admin-dashboard.png)
![Member dashboard](docs/screenshots/member-dashboard.png)
-->

## ✨ Features

**Public**
- Landing page and searchable catalog (title, author, ISBN, genre)
- Book detail pages with availability ("available / total copies")

**Members** (verified by Member ID, e.g. `LIB10245`)
- Borrow, return, renew and reserve books
- Borrowing history, suggestions, personal dashboard
- Every action uses the Member ID stored in the **server-side session**, so one member can't view another's data by editing a URL

**Admin console** (username + hashed password)
- Live dashboard with stats and a recent-activity feed
- Add/edit/delete books with a searchable multi-select genre picker
- Register members and get a human-readable Member ID
- Desk operations: issue, return and renew on behalf of any member
- Reservations, active loans, history, revenue report, genre/trend analytics (charts)
- CSV export for books, members and borrow history

## 🧮 Business rules

- 3-day grace period before a book counts as late
- Tiered late fine: ₹20/day (days 1–4) → ₹80 flat + ₹40/day (days 5–11) → ₹360 flat + ₹80/day (day 12+)
- Pro members get 50% off fines
- Late membership renewal: flat ₹50/day past expiry, capped at 60 days

## 🛠️ Tech stack

Python 3 · Flask · SQLite3 · pandas · matplotlib · Chart.js (CDN) · plain HTML/CSS/JS (no build step, native `<dialog>` for confirmations)

## 🗂️ Project structure

```
├── app.py                # Routes: public, /member/*, /admin/*
├── models.py             # Schema, auto-migrations, business logic
├── charts.py             # Analytics chart helpers
├── seed_dummy_data.py    # Generates demo books, members and borrow history
├── requirements.txt
├── templates/            # Jinja templates (public, member/, admin/, errors/)
└── static/               # CSS design system + JS (toasts, chip picker, modals)
```

## ⚙️ Getting started

Requires Python 3.9+.

```bash
git clone https://github.com/Anshu109301/stacks-library-management.git
cd stacks-library-management

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python seed_dummy_data.py         # optional: fills the DB with demo data
python app.py
```

Open **http://127.0.0.1:5000**.

`library.db` is created automatically on first run, along with a default admin account:

| Username | Password |
|---|---|
| `admin` | `admin123` |

⚠️ These are **demo credentials**. Change the password under *Admin → Account* straight away, and never deploy with the default.

**Optional environment variables**

| Variable | Purpose |
|---|---|
| `LIBRARY_SECRET_KEY` | Flask session key. If unset, a random key is generated each start (sessions reset on restart). |
| `FLASK_DEBUG=1` | Enables Flask debug mode for local development (off by default). |

## 🔐 Security notes

This project demonstrates access-control *patterns*; it isn't a hardened system:

- Member verification uses only the Member ID (no second factor), so anyone who knows an ID can act as that member. A real deployment would need something the member alone holds (e.g. email OTP).
- There is a single shared admin account, with no per-staff roles. The activity log is the only audit trail.
- Run behind HTTPS and a production WSGI server for anything beyond local use; `python app.py` is the dev server.

## 🚀 Ideas for next steps

- [ ] Second factor for member verification
- [ ] Per-staff admin accounts with roles
- [ ] Email/SMS reminders for due and reserved books
- [ ] ISBN lookup and cover images on add
- [ ] Automated tests for the fine and renewal logic
- [ ] Postgres instead of local SQLite

## 👤 Author

**Anshu Dhaka** — B.Tech CSE (Data Science), Delhi Technological University
[GitHub](https://github.com/Anshu109301)

## 📄 License

MIT — see [LICENSE](LICENSE).
