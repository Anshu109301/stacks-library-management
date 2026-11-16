"""
models.py
=========
Data access + business logic layer for the Library Management System.

This is a faithful, extended port of the original CLI's logic
(library_management.py) into functions the Flask routes can call.
Every fine/plan/renewal rule from the original CLI is preserved exactly;
everything added since (search, sorting, reservations, exports, member
codes, admin accounts, activity log, richer book records) is layered
on top without changing that core math.
"""

import csv
import io
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

DB_NAME = "library.db"

GRACE_PERIOD_DAYS = 3
NORMAL_PLAN = {"key": 0, "label": "Normal", "fee": 1000, "book_limit": 2}
PRO_PLAN = {"key": 1, "label": "Pro", "fee": 1500, "book_limit": 5}

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"  # change this immediately after first login

GENRES = [
    "Fiction", "Science Fiction", "Fantasy", "Mystery", "Thriller", "Romance",
    "Historical Fiction", "Horror", "Adventure", "Biography", "Autobiography",
    "History", "Philosophy", "Psychology", "Self-Help", "Business", "Economics",
    "Finance", "Technology", "Computer Science", "Programming", "Data Science",
    "Artificial Intelligence", "Mathematics", "Physics", "Chemistry", "Biology",
    "Engineering", "Education", "Politics", "Sociology", "Religion & Spirituality",
    "Poetry", "Drama", "Essays", "Travel", "Health & Wellness",
    "Children's Literature", "Young Adult", "Graphic Novel", "Comics", "Crime",
    "Non-Fiction", "Memoir", "Reference", "Academic", "Other",
]


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS BOOKS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        author TEXT,
        price INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        genre TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS MEMBERS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT,
        MEM_TYPE INTEGER NOT NULL,
        membership_expiry TEXT NOT NULL,
        joined_date TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS BORROWED_BOOKS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        issue_date TEXT NOT NULL,
        return_date TEXT,
        fine_paid INTEGER DEFAULT 0,
        FOREIGN KEY (member_id) REFERENCES MEMBERS(id),
        FOREIGN KEY (book_id) REFERENCES BOOKS(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS RESERVATIONS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        reserved_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        FOREIGN KEY (member_id) REFERENCES MEMBERS(id),
        FOREIGN KEY (book_id) REFERENCES BOOKS(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ADMINS (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_date TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ACTIVITY_LOG (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        member_id INTEGER,
        message TEXT NOT NULL
    )
    """)

    conn.commit()

    # Additive migrations — safe to run against an older library.db
    # (the original CLI's schema, or an earlier version of this app).
    _add_column_if_missing(cur, "BOOKS", "author", "TEXT")
    _add_column_if_missing(cur, "BOOKS", "isbn", "TEXT")
    _add_column_if_missing(cur, "BOOKS", "publisher", "TEXT")
    _add_column_if_missing(cur, "BOOKS", "pub_year", "INTEGER")
    _add_column_if_missing(cur, "MEMBERS", "email", "TEXT")
    _add_column_if_missing(cur, "MEMBERS", "phone", "TEXT")
    _add_column_if_missing(cur, "MEMBERS", "joined_date", "TEXT")
    _add_column_if_missing(cur, "MEMBERS", "member_code", "TEXT")
    _add_column_if_missing(cur, "BORROWED_BOOKS", "fine_paid", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "BORROWED_BOOKS", "condition", "TEXT DEFAULT 'good'")
    _add_column_if_missing(cur, "BORROWED_BOOKS", "condition_charge", "INTEGER DEFAULT 0")
    conn.commit()

    # Backfill member_code for any member registered before this column
    # existed, so every member always has one, no exceptions.
    for row in cur.execute("SELECT id FROM MEMBERS WHERE member_code IS NULL OR member_code = ''").fetchall():
        cur.execute("UPDATE MEMBERS SET member_code=? WHERE id=?", (_member_code_for(row[0]), row[0]))
    conn.commit()

    # Seed exactly one default admin account, only if none exist yet —
    # never overwrites credentials someone has already changed.
    admin_count = cur.execute("SELECT COUNT(*) FROM ADMINS").fetchone()[0]
    if admin_count == 0:
        cur.execute(
            "INSERT INTO ADMINS (username, password_hash, created_date) VALUES (?, ?, ?)",
            (DEFAULT_ADMIN_USERNAME, generate_password_hash(DEFAULT_ADMIN_PASSWORD), today().strftime("%d-%m-%Y")),
        )
        conn.commit()

    conn.close()


def _add_column_if_missing(cur, table, column, coltype):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _member_code_for(member_id: int) -> str:
    return f"LIB{10000 + member_id}"


# ----------------------------------------------------------------------
# SHARED HELPERS
# ----------------------------------------------------------------------
def format_currency(amount: int) -> str:
    return f"\u20b9{amount:,}"


def normalize_genres(raw) -> str:
    """Accepts either a comma-separated string or a list of genre names
    (the multi-select chip picker posts a list)."""
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = (raw or "").split(",")
    seen = []
    for g in parts:
        g = g.strip()
        if g and g not in seen:
            seen.append(g)
    return ", ".join(seen)


def calculate_fine(late_days: int, is_pro_member: bool) -> int:
    if late_days <= 4:
        fine = 20 * late_days
    elif late_days <= 11:
        fine = 20 * 4 + 40 * (late_days - 4)
    else:
        fine = 20 * 4 + 40 * 7 + 80 * (late_days - 11)
    if is_pro_member:
        fine //= 2
    return fine


def plan_details(mem_type: int) -> dict:
    return PRO_PLAN if mem_type == 1 else NORMAL_PLAN


def parse_expiry(expiry_str: str) -> datetime:
    return datetime.strptime(expiry_str, "%d-%m-%Y")


def today() -> datetime:
    return datetime.today()


def greeting_for_now() -> str:
    hour = today().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


# ----------------------------------------------------------------------
# ACTIVITY LOG
# ----------------------------------------------------------------------
def log_activity(member_id, message: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO ACTIVITY_LOG (ts, member_id, message) VALUES (?, ?, ?)",
        (today().strftime("%Y-%m-%d %H:%M"), member_id, message),
    )
    conn.commit()
    conn.close()


def recent_activity(limit: int = 10):
    conn = get_db()
    rows = conn.execute(
        """SELECT A.ts, A.message, M.name AS member_name, M.member_code
           FROM ACTIVITY_LOG A LEFT JOIN MEMBERS M ON A.member_id = M.id
           ORDER BY A.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------------
# ADMIN ACCOUNTS
# ----------------------------------------------------------------------
def verify_admin(username: str, password: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM ADMINS WHERE username=?", (username.strip(),)).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return row
    return None


def change_admin_password(admin_id: int, new_password: str):
    conn = get_db()
    conn.execute(
        "UPDATE ADMINS SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), admin_id),
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# MEMBERSHIP
# ----------------------------------------------------------------------
def find_members_by_name(query: str):
    """Name-based lookup — a *search helper only*, never an auth path.

    Returns masked Member IDs (e.g. 'LIB10•••') plus a little non-sensitive
    context (plan, joined date) so someone can tell their own record apart
    from another member sharing their name. It deliberately never returns
    the full Member ID, email, or phone — whoever uses this still has to
    type their real Member ID into the verify form to actually log in.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT name, member_code, MEM_TYPE, joined_date FROM MEMBERS WHERE name LIKE ? ORDER BY name",
        (f"%{query.strip()}%",),
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        code = r["member_code"]
        masked = code[:5] + "•" * max(len(code) - 5, 0)
        results.append({
            "name": r["name"],
            "masked_code": masked,
            "plan": plan_details(r["MEM_TYPE"])["label"],
            "joined_date": r["joined_date"],
        })
    return results


def create_member(name: str, email: str, phone: str, mem_type: int) -> dict:
    plan = plan_details(mem_type)
    expiry = (today() + timedelta(days=30)).strftime("%d-%m-%Y")
    joined = today().strftime("%d-%m-%Y")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO MEMBERS (name, email, phone, MEM_TYPE, membership_expiry, joined_date, member_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), (email or "").strip(), (phone or "").strip(), mem_type, expiry, joined, ""),
    )
    member_id = cur.lastrowid
    code = _member_code_for(member_id)
    cur.execute("UPDATE MEMBERS SET member_code=? WHERE id=?", (code, member_id))
    conn.commit()
    conn.close()

    log_activity(member_id, f"{name.strip()} joined as a {plan['label']} member")
    return {"id": member_id, "member_code": code, "plan": plan, "expiry": expiry}


def get_member(member_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM MEMBERS WHERE id=?", (member_id,)).fetchone()
    conn.close()
    return row


def get_member_by_code(code: str):
    if not code:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM MEMBERS WHERE UPPER(member_code)=?", (code.strip().upper(),)
    ).fetchone()
    conn.close()
    return row


def member_current_books_count(member_id: int) -> int:
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE member_id=? AND return_date IS NULL",
        (member_id,),
    ).fetchone()[0]
    conn.close()
    return n


def renew_preview(member_id: int, new_type: int):
    """Compute a renewal quote without writing anything. Returns dict or
    an {'error': ...} dict."""
    member = get_member(member_id)
    if not member:
        return {"error": "Member not found."}

    expiry_date = parse_expiry(member["membership_expiry"])
    now = today()
    new_plan = plan_details(new_type)

    current_books = member_current_books_count(member_id)
    if current_books > new_plan["book_limit"]:
        return {
            "error": (
                f"You currently hold {current_books} book(s); the "
                f"{new_plan['label']} plan only allows {new_plan['book_limit']}. "
                "Return extra books first, or choose a plan with a higher limit."
            )
        }

    late_fine = 0
    late_days = 0
    if expiry_date < now:
        late_days = (now - expiry_date).days
        late_fine = late_days * 50

    total = new_plan["fee"] + late_fine
    max_allowed = now + timedelta(days=60)
    new_expiry = (expiry_date if expiry_date > now else now) + timedelta(days=30)
    if new_expiry > max_allowed:
        new_expiry = max_allowed

    return {
        "member": member,
        "new_plan": new_plan,
        "late_days": late_days,
        "late_fine": late_fine,
        "total": total,
        "new_expiry": new_expiry.strftime("%d-%m-%Y"),
    }


def renew_commit(member_id: int, new_type: int, new_expiry_str: str):
    conn = get_db()
    member = conn.execute("SELECT name FROM MEMBERS WHERE id=?", (member_id,)).fetchone()
    conn.execute(
        "UPDATE MEMBERS SET MEM_TYPE=?, membership_expiry=? WHERE id=?",
        (new_type, new_expiry_str, member_id),
    )
    conn.commit()
    conn.close()
    if member:
        log_activity(member_id, f"{member['name']} renewed membership, now valid until {new_expiry_str}")


def member_profile(member_id: int):
    member = get_member(member_id)
    if not member:
        return None

    plan = plan_details(member["MEM_TYPE"])
    is_pro = member["MEM_TYPE"] == 1
    expiry_date = parse_expiry(member["membership_expiry"])
    now = today()
    is_expired = expiry_date < now
    days_to_expiry = (expiry_date - now).days

    conn = get_db()
    rows = conn.execute(
        """
        SELECT B.id, B.name, B.author, B.genre, BB.issue_date, BB.id AS loan_id
        FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
        WHERE BB.member_id=? AND BB.return_date IS NULL
        ORDER BY BB.issue_date ASC
        """,
        (member_id,),
    ).fetchall()
    conn.close()

    books = []
    due_soon_count = 0
    for r in rows:
        issue_date = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        days_held = (now - issue_date).days
        overdue = days_held > GRACE_PERIOD_DAYS
        fine = calculate_fine(days_held - GRACE_PERIOD_DAYS, is_pro) if overdue else 0
        due_in = GRACE_PERIOD_DAYS - days_held
        due_date = issue_date + timedelta(days=GRACE_PERIOD_DAYS)
        if not overdue and due_in <= 1:
            due_soon_count += 1
        books.append({
            "id": r["id"], "loan_id": r["loan_id"], "name": r["name"], "author": r["author"],
            "genre": r["genre"], "issue_date": issue_date.strftime("%d-%m-%Y"),
            "days_held": days_held, "overdue": overdue, "fine": fine,
            "due_in": due_in, "due_date": due_date.strftime("%d %b %Y"),
        })

    return {
        "member": member, "plan": plan, "is_expired": is_expired,
        "days_to_expiry": days_to_expiry, "books": books,
        "book_limit": plan["book_limit"], "due_soon_count": due_soon_count,
    }


def member_borrow_history(member_id: int, limit: int = 100):
    conn = get_db()
    rows = conn.execute(
        """SELECT B.name AS book_name, B.author, BB.issue_date, BB.return_date
           FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
           WHERE BB.member_id=?
           ORDER BY BB.issue_date DESC LIMIT ?""",
        (member_id, limit),
    ).fetchall()
    conn.close()
    return rows


def all_members(search: str = ""):
    conn = get_db()
    q = "SELECT * FROM MEMBERS"
    params = ()
    if search:
        q += " WHERE name LIKE ? OR email LIKE ? OR UPPER(member_code) = ? OR CAST(id AS TEXT) = ?"
        s = search.strip()
        params = (f"%{s}%", f"%{s}%", s.upper(), s)
    q += " ORDER BY id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    out = []
    now = today()
    for r in rows:
        expiry_date = parse_expiry(r["membership_expiry"])
        out.append({
            "id": r["id"], "name": r["name"], "email": r["email"],
            "member_code": r["member_code"] or _member_code_for(r["id"]),
            "plan": plan_details(r["MEM_TYPE"])["label"],
            "expiry": r["membership_expiry"],
            "expired": expiry_date < now,
            "joined": r["joined_date"] or "-",
        })
    return out


# ----------------------------------------------------------------------
# BOOKS
# ----------------------------------------------------------------------
def add_book(name, author, price, qty, genre, isbn="", publisher="", pub_year=None) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO BOOKS (name, author, price, quantity, genre, isbn, publisher, pub_year)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name.strip(), (author or "").strip(), price, qty, normalize_genres(genre),
         (isbn or "").strip(), (publisher or "").strip(), pub_year),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def update_book(book_id, name, author, price, qty, genre, isbn="", publisher="", pub_year=None):
    conn = get_db()
    conn.execute(
        """UPDATE BOOKS SET name=?, author=?, price=?, quantity=?, genre=?, isbn=?, publisher=?, pub_year=?
           WHERE id=?""",
        (name.strip(), (author or "").strip(), price, qty, normalize_genres(genre),
         (isbn or "").strip(), (publisher or "").strip(), pub_year, book_id),
    )
    conn.commit()
    conn.close()


def get_book(book_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM BOOKS WHERE id=?", (book_id,)).fetchone()
    conn.close()
    return row


def book_availability(book_id: int):
    conn = get_db()
    book = conn.execute("SELECT quantity FROM BOOKS WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        return None
    borrowed = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE book_id=? AND return_date IS NULL", (book_id,)
    ).fetchone()[0]
    conn.close()
    return {"available": book["quantity"], "total": book["quantity"] + borrowed, "borrowed": borrowed}


def delete_book(book_id: int):
    conn = get_db()
    on_loan = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE book_id=? AND return_date IS NULL",
        (book_id,),
    ).fetchone()[0]
    if on_loan > 0:
        conn.close()
        return {"error": f"{on_loan} copy/copies are still on loan — can't delete yet."}

    active_res = conn.execute(
        "SELECT COUNT(*) FROM RESERVATIONS WHERE book_id=? AND status='active'",
        (book_id,),
    ).fetchone()[0]
    if active_res > 0:
        conn.close()
        return {"error": f"{active_res} member(s) have an active reservation on this title — cancel those first."}

    conn.execute("DELETE FROM BOOKS WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def all_books(search: str = "", genre_filter: str = "", sort: str = "id", in_stock_only: bool = False):
    conn = get_db()
    q = "SELECT * FROM BOOKS WHERE 1=1"
    params = []
    if search:
        q += " AND (name LIKE ? OR author LIKE ? OR isbn LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if genre_filter:
        q += " AND genre LIKE ?"
        params.append(f"%{genre_filter}%")
    if in_stock_only:
        q += " AND quantity > 0"

    sort_map = {
        "id": "id ASC", "name": "name ASC", "qty": "quantity DESC",
        "price": "price DESC", "genre": "genre ASC",
    }
    q += " ORDER BY " + sort_map.get(sort, "id ASC")

    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def all_genres():
    conn = get_db()
    rows = conn.execute("SELECT genre FROM BOOKS").fetchall()
    conn.close()
    genres = set()
    for r in rows:
        for g in (r["genre"] or "").split(","):
            g = g.strip()
            if g:
                genres.add(g)
    return sorted(genres)


def preview_available_books(limit: int = 12, search: str = ""):
    conn = get_db()
    q = "SELECT id, name, author, genre, quantity FROM BOOKS WHERE quantity > 0"
    params = []
    if search:
        q += " AND (name LIKE ? OR author LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY id LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------------
# ISSUE / RETURN
# ----------------------------------------------------------------------
def issue_book(member_id: int, book_id: int):
    member = get_member(member_id)
    if not member:
        return {"error": "Invalid Member ID."}

    if parse_expiry(member["membership_expiry"]) < today():
        return {"error": "Membership expired. Please renew before issuing a book."}

    plan = plan_details(member["MEM_TYPE"])
    count = member_current_books_count(member_id)
    if count >= plan["book_limit"]:
        return {"error": f"{member['name']}, you've already reached your limit of {plan['book_limit']} book(s)."}

    book = get_book(book_id)
    if not book:
        return {"error": "Invalid Book ID."}
    if book["quantity"] <= 0:
        return {"error": f"'{book['name']}' is currently out of stock. You can reserve it instead.",
                "out_of_stock": True}

    conn = get_db()
    conn.execute("UPDATE BOOKS SET quantity=quantity-1 WHERE id=?", (book_id,))
    conn.execute(
        "INSERT INTO BORROWED_BOOKS (member_id, book_id, issue_date) VALUES (?, ?, DATE('now'))",
        (member_id, book_id),
    )
    conn.commit()
    conn.close()

    due_date = today() + timedelta(days=GRACE_PERIOD_DAYS)
    log_activity(member_id, f"{member['name']} borrowed \u2018{book['name']}\u2019")

    return {
        "ok": True, "book_name": book["name"], "member_name": member["name"],
        "member_code": member["member_code"] or _member_code_for(member_id),
        "grace_days": GRACE_PERIOD_DAYS, "due_date": due_date.strftime("%d %B %Y"),
    }


CONDITION_CHOICES = {
    "good":   {"label": "Good condition",          "rate": 0.0,  "flat": 0,   "removes_from_circulation": False},
    "slight": {"label": "Slightly worn / torn",     "rate": 0.10, "flat": 0,   "removes_from_circulation": False},
    "heavy":  {"label": "Heavily worn / damaged",   "rate": 1.0,  "flat": 100, "removes_from_circulation": True},
    "lost":   {"label": "Lost",                     "rate": 1.0,  "flat": 100, "removes_from_circulation": True},
}


def condition_charge_for(book_price: int, condition: str) -> int:
    rule = CONDITION_CHOICES.get(condition, CONDITION_CHOICES["good"])
    return round(book_price * rule["rate"]) + rule["flat"]


def return_book(loan_id: int, condition: str = "good", expected_member_id: int = None):
    if condition not in CONDITION_CHOICES:
        condition = "good"

    conn = get_db()
    record = conn.execute(
        "SELECT id, member_id, book_id, issue_date FROM BORROWED_BOOKS WHERE id=? AND return_date IS NULL",
        (loan_id,),
    ).fetchone()
    if not record:
        conn.close()
        return {"error": "No active borrow record found for that book."}
    if expected_member_id is not None and record["member_id"] != expected_member_id:
        conn.close()
        return {"error": "That loan doesn't belong to the verified member."}

    member_id = record["member_id"]
    book_id = record["book_id"]
    issue_date = datetime.strptime(record["issue_date"], "%Y-%m-%d")
    days = (today() - issue_date).days

    member = conn.execute("SELECT name, MEM_TYPE, member_code FROM MEMBERS WHERE id=?", (member_id,)).fetchone()
    is_pro = member["MEM_TYPE"] == 1

    late_fine = 0
    late = 0
    if days > GRACE_PERIOD_DAYS:
        late = days - GRACE_PERIOD_DAYS
        late_fine = calculate_fine(late, is_pro_member=is_pro)

    book = conn.execute("SELECT name, price FROM BOOKS WHERE id=?", (book_id,)).fetchone()
    condition_charge = condition_charge_for(book["price"], condition) if book else 0
    removes_copy = CONDITION_CHOICES[condition]["removes_from_circulation"]
    total_charge = late_fine + condition_charge

    # WHERE id=? targets exactly this one loan — critical when a member
    # holds more than one active loan of the same title, so a single
    # return never accidentally closes out their other copies too.
    conn.execute(
        "UPDATE BORROWED_BOOKS SET return_date=DATE('now'), condition=?, condition_charge=? WHERE id=?",
        (condition, condition_charge, loan_id),
    )
    # A book that's lost or too damaged to keep in circulation doesn't
    # go back on the shelf — everything else (good, or a minor scuff
    # with just a 10% charge) does.
    if not removes_copy:
        conn.execute("UPDATE BOOKS SET quantity=quantity+1 WHERE id=?", (book_id,))
    conn.commit()

    # Surface any active reservation on this title so the desk can act on it.
    next_reservation = conn.execute(
        """SELECT R.id, M.name, M.id as member_id, M.member_code FROM RESERVATIONS R
           JOIN MEMBERS M ON R.member_id = M.id
           WHERE R.book_id=? AND R.status='active' ORDER BY R.reserved_date ASC LIMIT 1""",
        (book_id,),
    ).fetchone()
    conn.close()

    if book and member:
        note = ""
        if condition == "lost":
            note = " (reported lost)"
        elif condition == "heavy":
            note = " (returned damaged)"
        log_activity(member_id, f"{member['name']} returned \u2018{book['name']}\u2019{note}")

    result = {
        "ok": True, "late_days": late, "fine": late_fine,
        "condition": condition, "condition_label": CONDITION_CHOICES[condition]["label"],
        "condition_charge": condition_charge, "total_charge": total_charge,
        "removed_from_circulation": removes_copy,
        "member_name": member["name"] if member else "", "member_code": member["member_code"] if member else "",
        "book_name": book["name"] if book else "",
    }
    if next_reservation:
        result["reservation_alert"] = {
            "member_name": next_reservation["name"], "member_id": next_reservation["member_id"],
            "member_code": next_reservation["member_code"],
        }
    return result


# ----------------------------------------------------------------------
# RESERVATIONS
# ----------------------------------------------------------------------
def reserve_book(member_id: int, book_id: int):
    member = get_member(member_id)
    if not member:
        return {"error": "Invalid Member ID."}
    book = get_book(book_id)
    if not book:
        return {"error": "Invalid Book ID."}
    if book["quantity"] > 0:
        return {"error": f"'{book['name']}' is available right now — issue it instead of reserving."}

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM RESERVATIONS WHERE member_id=? AND book_id=? AND status='active'",
        (member_id, book_id),
    ).fetchone()
    if existing:
        conn.close()
        return {"error": "You already have an active reservation for this title."}

    conn.execute(
        "INSERT INTO RESERVATIONS (member_id, book_id, reserved_date, status) VALUES (?, ?, DATE('now'), 'active')",
        (member_id, book_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "book_name": book["name"]}


def active_reservations():
    conn = get_db()
    rows = conn.execute(
        """SELECT R.id, M.name AS member_name, M.id AS member_id, M.member_code, B.name AS book_name,
                  B.id AS book_id, B.quantity, R.reserved_date
           FROM RESERVATIONS R
           JOIN MEMBERS M ON R.member_id = M.id
           JOIN BOOKS B ON R.book_id = B.id
           WHERE R.status='active'
           ORDER BY R.reserved_date ASC"""
    ).fetchall()
    conn.close()
    return rows


def cancel_reservation(reservation_id: int):
    conn = get_db()
    conn.execute("UPDATE RESERVATIONS SET status='cancelled' WHERE id=?", (reservation_id,))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# RECOMMENDATIONS
# ----------------------------------------------------------------------
def suggest_books(member_id: int, limit_per_group: int = 8):
    """Recommend books with a sensible fallback hierarchy, each group carrying
    a human-readable reason so the UI can explain *why* a book is shown:

        1. Genre match  -> "Recommended because you like <genre>"
        2. Trending     -> "Trending this week" (most borrowed, last 8 weeks)
        3. New arrivals -> "New in the library" (most recently added)
        4. Popular      -> "Popular with other members" (all-time, used only
           when the tiers above produced nothing at all — e.g. a brand new
           member with no history on a catalog with no recent activity)

    A book never appears in more than one group, and a member never sees a
    book they already currently hold or have already borrowed before.
    """
    conn = get_db()

    hist_rows = conn.execute(
        """SELECT B.genre FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
           WHERE BB.member_id=?""",
        (member_id,),
    ).fetchall()
    genres = set()
    for r in hist_rows:
        for g in (r["genre"] or "").split(","):
            if g.strip():
                genres.add(g.strip().lower())

    borrowed_ids = {
        r["book_id"] for r in conn.execute(
            "SELECT book_id FROM BORROWED_BOOKS WHERE member_id=?", (member_id,)
        ).fetchall()
    }

    catalog = conn.execute("SELECT * FROM BOOKS WHERE quantity > 0 ORDER BY id").fetchall()

    groups = []
    shown_ids = set()

    # Tier 1 — genre match from the member's own borrow history
    if genres:
        matches = []
        for b in catalog:
            if b["id"] in borrowed_ids or b["id"] in shown_ids:
                continue
            book_genres = [g.strip().lower() for g in (b["genre"] or "").split(",")]
            if any(g in genres for g in book_genres):
                matches.append(b)
        if matches:
            label_genres = ", ".join(sorted(genres)[:3])
            groups.append({
                "reason": f"Recommended because you like {label_genres}",
                "books": matches[:limit_per_group],
            })
            shown_ids |= {b["id"] for b in matches[:limit_per_group]}

    # Tier 2 — trending in the last 8 weeks
    cutoff = (today() - timedelta(weeks=8)).strftime("%Y-%m-%d")
    trend_rows = conn.execute(
        """SELECT B.*, COUNT(*) AS borrow_count
           FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
           WHERE BB.issue_date >= ? AND B.quantity > 0
           GROUP BY BB.book_id ORDER BY borrow_count DESC""",
        (cutoff,),
    ).fetchall()
    trending = [b for b in trend_rows if b["id"] not in borrowed_ids and b["id"] not in shown_ids]
    if trending:
        picked = trending[:limit_per_group]
        groups.append({"reason": "Trending this week", "books": picked})
        shown_ids |= {b["id"] for b in picked}

    # Tier 3 — newest arrivals (highest id = most recently added)
    newest = [
        b for b in sorted(catalog, key=lambda b: b["id"], reverse=True)
        if b["id"] not in borrowed_ids and b["id"] not in shown_ids
    ]
    if newest:
        picked = newest[:limit_per_group]
        groups.append({"reason": "New in the library", "books": picked})
        shown_ids |= {b["id"] for b in picked}

    # Tier 4 — last-resort fallback: all-time popularity, only if every tier
    # above came up completely empty (e.g. nothing in stock matched anything)
    if not groups:
        popular_rows = conn.execute(
            """SELECT B.*, COUNT(*) AS borrow_count
               FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
               WHERE B.quantity > 0
               GROUP BY BB.book_id ORDER BY borrow_count DESC LIMIT ?""",
            (limit_per_group,),
        ).fetchall()
        popular = [b for b in popular_rows if b["id"] not in borrowed_ids]
        if popular:
            groups.append({"reason": "Popular with other members", "books": popular})

    conn.close()
    return {"history": bool(genres), "genres": sorted(genres), "groups": groups}


# ----------------------------------------------------------------------
# ANALYTICS / REPORTS
# ----------------------------------------------------------------------
def _genre_distribution(rows):
    count = {}
    for r in rows:
        for g in (r[0] or "").split(","):
            g = g.strip()
            if g:
                count[g] = count.get(g, 0) + 1
    return count


def catalog_genre_distribution():
    conn = get_db()
    rows = conn.execute("SELECT genre FROM BOOKS").fetchall()
    conn.close()
    return _genre_distribution([(r["genre"],) for r in rows])


def borrowed_genre_distribution():
    conn = get_db()
    rows = conn.execute(
        """SELECT B.genre FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
           WHERE BB.return_date IS NULL"""
    ).fetchall()
    conn.close()
    return _genre_distribution([(r["genre"],) for r in rows])


def most_read_genre():
    conn = get_db()
    rows = conn.execute(
        """SELECT B.genre FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id"""
    ).fetchall()
    conn.close()
    count = _genre_distribution([(r["genre"],) for r in rows])
    if not count:
        return None
    top = max(count, key=count.get)
    return {"genre": top, "count": count[top]}


def borrow_trend_last_8_weeks():
    """Books issued per ISO week, for the last 8 weeks — powers a trend chart."""
    conn = get_db()
    rows = conn.execute("SELECT issue_date FROM BORROWED_BOOKS").fetchall()
    conn.close()

    buckets = {}
    now = today()
    for i in range(7, -1, -1):
        wk_start = now - timedelta(days=now.weekday(), weeks=i)
        key = wk_start.strftime("%d %b")
        buckets[key] = 0

    week_starts = list(buckets.keys())
    for r in rows:
        d = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        wk_start = (d - timedelta(days=d.weekday())).strftime("%d %b")
        if wk_start in buckets:
            buckets[wk_start] += 1

    return {"labels": week_starts, "values": [buckets[k] for k in week_starts]}


def revenue_trend_last_8_weeks():
    """Late fines + condition charges collected per week, for the last 8 weeks."""
    conn = get_db()
    rows = conn.execute(
        """SELECT BB.issue_date, BB.return_date, BB.condition_charge, M.MEM_TYPE
           FROM BORROWED_BOOKS BB JOIN MEMBERS M ON BB.member_id = M.id
           WHERE BB.return_date IS NOT NULL"""
    ).fetchall()
    conn.close()

    buckets = {}
    now = today()
    for i in range(7, -1, -1):
        wk_start = now - timedelta(days=now.weekday(), weeks=i)
        buckets[wk_start.strftime("%d %b")] = 0
    week_starts = list(buckets.keys())

    for r in rows:
        d1 = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        d2 = datetime.strptime(r["return_date"], "%Y-%m-%d")
        days = (d2 - d1).days
        amount = (r["condition_charge"] or 0)
        if days > GRACE_PERIOD_DAYS:
            amount += calculate_fine(days - GRACE_PERIOD_DAYS, is_pro_member=(r["MEM_TYPE"] == 1))
        wk_start = (d2 - timedelta(days=d2.weekday())).strftime("%d %b")
        if wk_start in buckets:
            buckets[wk_start] += amount

    return {"labels": week_starts, "values": [buckets[k] for k in week_starts]}


def plan_distribution():
    conn = get_db()
    normal = conn.execute("SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=0").fetchone()[0]
    pro = conn.execute("SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=1").fetchone()[0]
    conn.close()
    return {"labels": ["Normal", "Pro"], "values": [normal, pro]}


def condition_breakdown():
    conn = get_db()
    good = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE return_date IS NOT NULL AND (condition IS NULL OR condition='good')"
    ).fetchone()[0]
    slight = conn.execute("SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='slight'").fetchone()[0]
    heavy = conn.execute("SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='heavy'").fetchone()[0]
    lost = conn.execute("SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='lost'").fetchone()[0]
    conn.close()
    return {"labels": ["Good", "Worn", "Damaged", "Lost"], "values": [good, slight, heavy, lost]}


def top_borrowed_books(limit: int = 5):
    conn = get_db()
    rows = conn.execute(
        """SELECT B.name, COUNT(*) AS times_borrowed
           FROM BORROWED_BOOKS BB JOIN BOOKS B ON BB.book_id = B.id
           GROUP BY BB.book_id ORDER BY times_borrowed DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def top_borrowers(limit: int = 5):
    conn = get_db()
    rows = conn.execute(
        """SELECT M.name, M.member_code, COUNT(*) AS books_borrowed
           FROM BORROWED_BOOKS BB JOIN MEMBERS M ON BB.member_id = M.id
           GROUP BY BB.member_id ORDER BY books_borrowed DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def memberships_expiring_soon(days: int = 7):
    conn = get_db()
    rows = conn.execute("SELECT id, name, member_code, membership_expiry FROM MEMBERS").fetchall()
    conn.close()
    now = today()
    horizon = now + timedelta(days=days)
    out = []
    for r in rows:
        exp = parse_expiry(r["membership_expiry"])
        if now <= exp <= horizon:
            out.append({
                "id": r["id"], "name": r["name"], "member_code": r["member_code"],
                "expiry": r["membership_expiry"], "days_left": (exp - now).days,
            })
    return sorted(out, key=lambda x: x["days_left"])


def average_days_late():
    conn = get_db()
    rows = conn.execute(
        "SELECT issue_date, return_date FROM BORROWED_BOOKS WHERE return_date IS NOT NULL"
    ).fetchall()
    conn.close()
    late_days = []
    for r in rows:
        d1 = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        d2 = datetime.strptime(r["return_date"], "%Y-%m-%d")
        days = (d2 - d1).days
        if days > GRACE_PERIOD_DAYS:
            late_days.append(days - GRACE_PERIOD_DAYS)
    if not late_days:
        return 0
    return round(sum(late_days) / len(late_days), 1)


def member_growth_last_8_weeks():
    conn = get_db()
    rows = conn.execute("SELECT joined_date FROM MEMBERS WHERE joined_date IS NOT NULL").fetchall()
    conn.close()
    buckets = {}
    now = today()
    for i in range(7, -1, -1):
        wk_start = now - timedelta(days=now.weekday(), weeks=i)
        buckets[wk_start.strftime("%d %b")] = 0
    week_starts = list(buckets.keys())
    for r in rows:
        try:
            d = datetime.strptime(r["joined_date"], "%d-%m-%Y")
        except (ValueError, TypeError):
            continue
        wk_start = (d - timedelta(days=d.weekday())).strftime("%d %b")
        if wk_start in buckets:
            buckets[wk_start] += 1
    return {"labels": week_starts, "values": [buckets[k] for k in week_starts]}


def revenue_report():
    conn = get_db()
    normal_count = conn.execute("SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=0").fetchone()[0]
    pro_count = conn.execute("SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=1").fetchone()[0]

    normal_rev = normal_count * NORMAL_PLAN["fee"]
    pro_rev = pro_count * PRO_PLAN["fee"]

    rows = conn.execute(
        """SELECT BB.issue_date, BB.return_date, M.MEM_TYPE
           FROM BORROWED_BOOKS BB JOIN MEMBERS M ON BB.member_id = M.id
           WHERE BB.return_date IS NOT NULL"""
    ).fetchall()

    damage_total = conn.execute(
        "SELECT COALESCE(SUM(condition_charge),0) FROM BORROWED_BOOKS WHERE return_date IS NOT NULL"
    ).fetchone()[0]
    lost_count = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='lost'"
    ).fetchone()[0]
    damaged_count = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='heavy'"
    ).fetchone()[0]
    worn_count = conn.execute(
        "SELECT COUNT(*) FROM BORROWED_BOOKS WHERE condition='slight'"
    ).fetchone()[0]
    conn.close()

    fine_total = 0
    for r in rows:
        d1 = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        d2 = datetime.strptime(r["return_date"], "%Y-%m-%d")
        days = (d2 - d1).days
        if days > GRACE_PERIOD_DAYS:
            fine_total += calculate_fine(days - GRACE_PERIOD_DAYS, is_pro_member=(r["MEM_TYPE"] == 1))

    return {
        "normal_count": normal_count, "pro_count": pro_count,
        "normal_rev": normal_rev, "pro_rev": pro_rev, "fine_total": fine_total,
        "damage_total": damage_total, "lost_count": lost_count,
        "damaged_count": damaged_count, "worn_count": worn_count,
        "total": normal_rev + pro_rev + fine_total + damage_total,
    }


def borrow_history(limit: int = 200):
    conn = get_db()
    rows = conn.execute(
        """SELECT M.name AS member_name, M.member_code, B.name AS book_name, BB.issue_date, BB.return_date,
                  BB.condition, BB.condition_charge
           FROM BORROWED_BOOKS BB
           JOIN MEMBERS M ON BB.member_id = M.id
           JOIN BOOKS B ON BB.book_id = B.id
           ORDER BY BB.issue_date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def currently_borrowed():
    conn = get_db()
    rows = conn.execute(
        """SELECT M.id AS member_id, M.name AS member_name, M.member_code, B.name AS book_name,
                  B.id as book_id, BB.issue_date, M.MEM_TYPE
           FROM BORROWED_BOOKS BB
           JOIN MEMBERS M ON BB.member_id = M.id
           JOIN BOOKS B ON BB.book_id = B.id
           WHERE BB.return_date IS NULL
           ORDER BY BB.issue_date ASC"""
    ).fetchall()
    conn.close()

    now = today()
    out = []
    for r in rows:
        issue_date = datetime.strptime(r["issue_date"], "%Y-%m-%d")
        days_held = (now - issue_date).days
        overdue = days_held > GRACE_PERIOD_DAYS
        fine = calculate_fine(days_held - GRACE_PERIOD_DAYS, r["MEM_TYPE"] == 1) if overdue else 0
        out.append({
            "member_id": r["member_id"], "member_name": r["member_name"], "member_code": r["member_code"],
            "book_name": r["book_name"], "book_id": r["book_id"],
            "issue_date": issue_date.strftime("%d-%m-%Y"), "days_held": days_held,
            "overdue": overdue, "fine": fine,
        })
    return out


def dashboard_stats():
    conn = get_db()
    total_books = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM BOOKS").fetchone()[0]
    titles = conn.execute("SELECT COUNT(*) FROM BOOKS").fetchone()[0]
    total_members = conn.execute("SELECT COUNT(*) FROM MEMBERS").fetchone()[0]
    active_loans = conn.execute("SELECT COUNT(*) FROM BORROWED_BOOKS WHERE return_date IS NULL").fetchone()[0]
    active_reservations_count = conn.execute("SELECT COUNT(*) FROM RESERVATIONS WHERE status='active'").fetchone()[0]

    now = today()
    expired_count = 0
    for r in conn.execute("SELECT membership_expiry FROM MEMBERS").fetchall():
        if parse_expiry(r["membership_expiry"]) < now:
            expired_count += 1
    conn.close()

    borrowed = currently_borrowed()
    overdue = [b for b in borrowed if b["overdue"]]
    rev = revenue_report()
    total_genres = len(all_genres())
    expiring_soon = memberships_expiring_soon(7)

    return {
        "total_books": total_books, "titles": titles, "total_members": total_members,
        "active_loans": active_loans, "overdue_count": len(overdue), "overdue": overdue[:8],
        "reservations_count": active_reservations_count, "revenue_total": rev["total"],
        "available_books": total_books, "borrowed_books": active_loans,
        "active_members": total_members - expired_count, "expired_members": expired_count,
        "total_genres": total_genres, "expiring_soon": expiring_soon, "expiring_soon_count": len(expiring_soon),
    }


# ----------------------------------------------------------------------
# CSV EXPORTS
# ----------------------------------------------------------------------
def export_csv(kind: str) -> io.StringIO:
    buf = io.StringIO()
    writer = csv.writer(buf)

    if kind == "books":
        writer.writerow(["ID", "Name", "Author", "ISBN", "Genre", "Price", "Available", "Total"])
        for b in all_books():
            avail = book_availability(b["id"])
            writer.writerow([b["id"], b["name"], b["author"] or "", b["isbn"] or "", b["genre"] or "",
                              b["price"], avail["available"], avail["total"]])
    elif kind == "members":
        writer.writerow(["Member ID", "Name", "Email", "Phone", "Plan", "Expiry", "Joined"])
        for m in all_members():
            writer.writerow([m["member_code"], m["name"], m["email"] or "", "", m["plan"], m["expiry"], m["joined"]])
    elif kind == "history":
        writer.writerow(["Member", "Member ID", "Book", "Issue Date", "Return Date", "Condition", "Condition Charge"])
        for h in borrow_history(limit=100000):
            writer.writerow([h["member_name"], h["member_code"], h["book_name"], h["issue_date"],
                              h["return_date"] or "Still out", h["condition"] or "", h["condition_charge"] or 0])

    buf.seek(0)
    return buf
