"""
seed_dummy_data.py
Fills library.db with 150 dummy books, 32 members of every kind, and a
realistic borrow history so analytics / recommendations / fines have data.

Seeds demo data into the app's SQLite database. Core tables used:
    BOOKS(id, name, price, quantity, genre)           genre = "fantasy,epic"
    MEMBERS(id, name, MEM_TYPE, membership_expiry)    0 = Normal, 1 = Pro, expiry = DD-MM-YYYY
    BORROWED_BOOKS(id, member_id, book_id, issue_date, return_date)   dates = YYYY-MM-DD

Usage:
    python seed_dummy_data.py                 # seeds ./library.db (refuses if data already exists)
    python seed_dummy_data.py --db path/to/library.db
    python seed_dummy_data.py --reset         # wipes the three tables first (a backup copy is saved)
"""
import argparse
import os
import random
import shutil
import sqlite3
from datetime import date, timedelta

random.seed(42)  # same data every run
TODAY = date.today()

# ----------------------------------------------------------------- BOOKS ---
REAL_BOOKS = [
    ("Harry Potter and the Philosopher's Stone", "fantasy,adventure", 450),
    ("The Hobbit", "fantasy,adventure", 399),
    ("A Game of Thrones", "fantasy,epic", 599),
    ("The Name of the Wind", "fantasy,epic", 549),
    ("Mistborn: The Final Empire", "fantasy,epic", 520),
    ("Dune", "scifi,epic", 499),
    ("Foundation", "scifi,classic", 420),
    ("Neuromancer", "scifi,cyberpunk", 380),
    ("The Martian", "scifi,thriller", 450),
    ("Ender's Game", "scifi,adventure", 360),
    ("1984", "dystopian,classic", 299),
    ("Brave New World", "dystopian,classic", 320),
    ("The Hunger Games", "dystopian,adventure", 350),
    ("Pride and Prejudice", "romance,classic", 250),
    ("Jane Eyre", "romance,classic", 260),
    ("The Fault in Our Stars", "romance,drama", 340),
    ("Me Before You", "romance,drama", 330),
    ("Sherlock Holmes: The Complete Novels", "mystery,classic", 499),
    ("And Then There Were None", "mystery,thriller", 310),
    ("The Girl with the Dragon Tattoo", "mystery,thriller", 420),
    ("Gone Girl", "thriller,mystery", 400),
    ("The Da Vinci Code", "thriller,mystery", 380),
    ("Dracula", "horror,classic", 280),
    ("Frankenstein", "horror,classic", 270),
    ("It", "horror,thriller", 599),
    ("The Shining", "horror,thriller", 450),
    ("Sapiens", "history,nonfiction", 520),
    ("Guns, Germs, and Steel", "history,nonfiction", 560),
    ("The Discovery of India", "history,nonfiction", 340),
    ("India After Gandhi", "history,nonfiction", 650),
    ("Atomic Habits", "selfhelp,nonfiction", 480),
    ("Deep Work", "selfhelp,productivity", 420),
    ("The 7 Habits of Highly Effective People", "selfhelp,nonfiction", 460),
    ("Think and Grow Rich", "selfhelp,business", 250),
    ("Rich Dad Poor Dad", "business,selfhelp", 300),
    ("The Psychology of Money", "business,nonfiction", 390),
    ("Zero to One", "business,nonfiction", 410),
    ("The Lean Startup", "business,nonfiction", 440),
    ("A Brief History of Time", "science,nonfiction", 380),
    ("Cosmos", "science,nonfiction", 420),
    ("The Selfish Gene", "science,nonfiction", 400),
    ("Surely You're Joking, Mr. Feynman!", "science,biography", 390),
    ("Wings of Fire", "biography,nonfiction", 275),
    ("My Experiments with Truth", "biography,classic", 230),
    ("Steve Jobs", "biography,business", 520),
    ("Long Walk to Freedom", "biography,history", 480),
    ("The Alchemist", "fiction,adventure", 299),
    ("To Kill a Mockingbird", "fiction,classic", 310),
    ("The Great Gatsby", "fiction,classic", 240),
    ("The Kite Runner", "fiction,drama", 350),
    ("Life of Pi", "fiction,adventure", 340),
    ("The God of Small Things", "fiction,drama", 360),
    ("Midnight's Children", "fiction,history", 420),
    ("The White Tiger", "fiction,drama", 330),
    ("Python Crash Course", "programming,education", 650),
    ("Clean Code", "programming,education", 720),
    ("Introduction to Algorithms", "programming,education", 950),
    ("Hands-On Machine Learning", "programming,datascience", 899),
    ("Python for Data Analysis", "programming,datascience", 780),
    ("Designing Data-Intensive Applications", "programming,education", 850),
    ("Naruto, Vol. 1", "manga,adventure", 220),
    ("One Piece, Vol. 1", "manga,adventure", 230),
]

PATTERNS = {
    "fantasy": ("The {a} {n} of {p}", ["Crimson", "Silver", "Hollow", "Ember", "Forgotten", "Shattered", "Ivory", "Obsidian"],
                ["Crown", "Blade", "Oracle", "Wyrm", "Throne", "Grimoire", "Sorcerer", "Citadel"],
                ["Eldoria", "Valmora", "the North", "Thornwick", "Arkenfall", "the Deep", "Mistral", "Stormhaven"], "fantasy,epic", (300, 550)),
    "scifi": ("{a} {n} {p}", ["Quantum", "Stellar", "Orbital", "Neon", "Silent", "Binary", "Lunar", "Entropy"],
              ["Protocol", "Horizon", "Drift", "Paradox", "Colony", "Signal", "Archive", "Gate"],
              ["Zero", "Nine", "Prime", "Ascendant", "Rising", "Beyond", "Omega", "Echo"], "scifi,adventure", (320, 520)),
    "mystery": ("The {a} {n} {p}", ["Vanishing", "Midnight", "Hidden", "Velvet", "Locked", "Broken", "Quiet", "Paper"],
                ["Letter", "Alibi", "Witness", "Cipher", "Portrait", "Key", "Garden", "Inheritance"],
                ["Mystery", "Case", "Affair", "Murders", "Files", "Secret", "Riddle", "Deception"], "mystery,thriller", (280, 450)),
    "romance": ("{a} {n} {p}", ["Letters", "Summers", "Promises", "Seasons", "Whispers", "Moments", "Echoes", "Stolen"],
                ["of", "in", "under", "beyond", "before", "after", "across", "within"],
                ["Paris", "the Rain", "Goa", "Autumn", "Monsoon", "a Heartbeat", "Midnight", "the Stars"], "romance,drama", (240, 400)),
    "history": ("{a} {n} of {p}", ["A Short History", "The Rise", "The Fall", "Untold Stories", "The Making", "Voices", "Roots", "Chronicles"],
                ["", "", "", "", "", "", "", ""],
                ["the Mughals", "Ancient Rome", "the Raj", "Modern India", "the Silk Road", "the Maurya Empire", "the Cold War", "Empires"],
                "history,nonfiction", (350, 650)),
    "selfhelp": ("{a} {n} {p}", ["The Power of", "Mastering", "Unlock", "The Art of", "Rewire", "Build", "Think", "Master"],
                 ["Focus", "Habits", "Discipline", "Confidence", "Mindset", "Clarity", "Resilience", "Growth"],
                 ["Daily", "Now", "for Life", "Every Day", "in 30 Days", "Step by Step", "Today", "That Lasts"], "selfhelp,nonfiction", (250, 450)),
    "programming": ("{a} {n} {p}", ["Practical", "Modern", "Effective", "Essential", "Advanced", "Pragmatic", "Beginner's", "Applied"],
                    ["Python", "SQL", "Data Structures", "Machine Learning", "Statistics", "Algorithms", "Java", "Deep Learning"],
                    ["Handbook", "Cookbook", "in Action", "Explained", "Guide", "Patterns", "Fundamentals", "Projects"],
                    "programming,education", (450, 900)),
    "horror": ("The {a} {n} {p}", ["Whispering", "Cursed", "Haunted", "Pale", "Dreadful", "Moonless", "Rotting", "Sunken"],
               ["House", "Doll", "Asylum", "Mirror", "Forest", "Hotel", "Manor", "Chapel"],
               ["", "of Shadows", "at Dusk", "Below", "of Bones", "in the Walls", "Awakens", "Returns"], "horror,thriller", (260, 450)),
}


def build_books(total=150):
    books, seen = [], set()
    for title, genre, price in REAL_BOOKS:
        books.append((title, genre, price))
        seen.add(title.lower())
    keys = list(PATTERNS)
    i = 0
    while len(books) < total:
        k = keys[i % len(keys)]
        i += 1
        pat, A, N, P, genre, (lo, hi) = PATTERNS[k]
        title = " ".join(pat.format(a=random.choice(A), n=random.choice(N), p=random.choice(P)).split())
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        price = random.randrange(lo, hi + 1, 10) - 1  # e.g. 349, 429
        books.append((title, genre, price))
    return books[:total]


# --------------------------------------------------------------- MEMBERS ---
NAMES = [
    "Aarav Sharma", "Diya Verma", "Rohan Mehta", "Ananya Singh", "Kabir Malhotra", "Ishita Gupta",
    "Vihaan Khanna", "Saanvi Reddy", "Arjun Nair", "Meera Iyer", "Aditya Joshi", "Kavya Bansal",
    "Rahul Chauhan", "Priya Kapoor", "Siddharth Rao", "Nisha Agarwal", "Yash Tiwari", "Tanvi Desai",
    "Harsh Pandey", "Sneha Menon", "Manav Saxena", "Riya Bhatia", "Dev Choudhary", "Pooja Yadav",
    "Karan Thakur", "Simran Kaur", "Nikhil Jain", "Aditi Mishra", "Varun Dutta", "Neha Sinha",
    "Ayaan Qureshi", "Zoya Khan",
]


def fmt_expiry(d):          # MEMBERS.membership_expiry -> DD-MM-YYYY
    return d.strftime("%d-%m-%Y")


def fmt_db(d):              # BORROWED_BOOKS dates -> YYYY-MM-DD
    return d.strftime("%Y-%m-%d")


# (mem_type, expiry offset in days from today, borrow plan)
# borrow plan: n_current books, how overdue the oldest current book is (days past the 3-day due date, 0 = on time)
def member_plan():
    plan = []
    for _ in range(10):  plan.append(("Normal active", 0, random.randint(30, 300), random.choice([0, 1, 1, 2]), 0))
    for _ in range(8):   plan.append(("Pro active", 1, random.randint(30, 300), random.choice([0, 1, 2, 3]), 0))
    for _ in range(3):   plan.append(("Normal expired", 0, -random.randint(10, 90), 0, 0))
    for _ in range(3):   plan.append(("Pro expired", 1, -random.randint(10, 90), 0, 0))
    plan.append(("Normal expiring soon", 0, 2, 1, 0))
    plan.append(("Normal expiring soon", 0, 6, 0, 0))
    plan.append(("Pro expiring soon", 1, 4, 1, 0))
    plan.append(("Normal new", 0, 30, 0, 0))
    plan.append(("Pro new", 1, 30, 1, 0))
    plan.append(("Pro at book limit", 1, 120, 5, 0))
    plan.append(("Normal at book limit", 0, 120, 2, 0))
    plan.append(("Pro long overdue", 1, 90, 1, 12))   # 12+ days late -> top fine tier
    assert len(plan) == 32, len(plan)
    return plan


# ------------------------------------------------------------------ MAIN ---
SCHEMA = """
CREATE TABLE IF NOT EXISTS BOOKS (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, quantity INTEGER, genre TEXT);
CREATE TABLE IF NOT EXISTS MEMBERS (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, MEM_TYPE INTEGER, membership_expiry TEXT);
CREATE TABLE IF NOT EXISTS BORROWED_BOOKS (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member_id INTEGER, book_id INTEGER, issue_date TEXT, return_date TEXT,
    FOREIGN KEY(member_id) REFERENCES MEMBERS(id), FOREIGN KEY(book_id) REFERENCES BOOKS(id));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="library.db")
    ap.add_argument("--reset", action="store_true", help="wipe BOOKS, MEMBERS, BORROWED_BOOKS first (backup saved)")
    args = ap.parse_args()

    if args.reset and os.path.exists(args.db):
        backup = args.db + ".bak"
        shutil.copy(args.db, backup)
        print(f"Backup saved: {backup}")

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    if args.reset:
        for t in ("BORROWED_BOOKS", "MEMBERS", "BOOKS"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("DELETE FROM sqlite_sequence WHERE name IN ('BORROWED_BOOKS','MEMBERS','BOOKS')")
    elif cur.execute("SELECT COUNT(*) FROM BOOKS").fetchone()[0] or cur.execute("SELECT COUNT(*) FROM MEMBERS").fetchone()[0]:
        print("Database already has books/members. Re-run with --reset to replace them (a backup is saved).")
        return

    # --- books: total copies 2-6, stock reduced later for books currently out
    books = build_books(150)
    total_copies = {}
    book_rows = []
    for i, (name, genre, price) in enumerate(books, start=1):
        copies = random.randint(2, 6)
        total_copies[i] = copies
        book_rows.append((i, name, price, copies, genre))
    cur.executemany("INSERT INTO BOOKS(id,name,price,quantity,genre) VALUES (?,?,?,?,?)", book_rows)
    stock = dict(total_copies)
    genre_of = {i: g.split(",") for i, (_, g, _) in enumerate(books, start=1)}
    all_genres = sorted({g for gs in genre_of.values() for g in gs})

    def pick_book(fav, exclude, need_stock):
        pool = [b for b in genre_of if b not in exclude and (stock[b] > 0 or not need_stock)]
        liked = [b for b in pool if fav & set(genre_of[b])]
        return random.choice(liked if liked and random.random() < 0.7 else pool)

    # --- members + borrow history
    plan = member_plan()
    names = NAMES[:]
    random.shuffle(names)
    borrow_rows = []
    for mid, ((kind, mtype, exp_offset, n_current, overdue_days), name) in enumerate(zip(plan, names), start=1):
        expiry = TODAY + timedelta(days=exp_offset)
        cur.execute("INSERT INTO MEMBERS(id,name,MEM_TYPE,membership_expiry) VALUES (?,?,?,?)",
                    (mid, name, mtype, fmt_expiry(expiry)))
        fav = set(random.sample(all_genres, 2))
        used = set()

        # past, returned borrows (skipped for brand-new members)
        n_past = 0 if "new" in kind else random.randint(3, 10)
        for _ in range(n_past):
            b = pick_book(fav, set(), need_stock=False)
            issue = TODAY - timedelta(days=random.randint(8, 150))
            returned = issue + timedelta(days=random.choice([1, 2, 2, 3, 3, 3, 4, 5, 7, 9]))
            if returned >= TODAY:
                returned = TODAY - timedelta(days=1)
            borrow_rows.append((mid, b, fmt_db(issue), fmt_db(returned)))

        # currently borrowed books (return_date NULL), respecting plan limits (Normal 2 / Pro 5)
        limit = 5 if mtype == 1 else 2
        for j in range(min(n_current, limit)):
            b = pick_book(fav, used, need_stock=True)
            used.add(b)
            stock[b] -= 1
            if overdue_days and j == 0:
                days_ago = 3 + overdue_days                      # due date is 3 days after issue
            elif kind.endswith("at book limit"):
                days_ago = random.randint(0, 3)
            else:
                days_ago = random.choice([0, 1, 1, 2, 3, 5, 8])  # mix of on-time and a few overdue
            borrow_rows.append((mid, b, fmt_db(TODAY - timedelta(days=days_ago)), None))

    cur.executemany("INSERT INTO BORROWED_BOOKS(member_id,book_id,issue_date,return_date) VALUES (?,?,?,?)", borrow_rows)
    cur.executemany("UPDATE BOOKS SET quantity=? WHERE id=?", [(q, b) for b, q in stock.items()])
    con.commit()

    # --- summary
    n_out = cur.execute("SELECT COUNT(*) FROM BORROWED_BOOKS WHERE return_date IS NULL").fetchone()[0]
    print(f"Books: {cur.execute('SELECT COUNT(*) FROM BOOKS').fetchone()[0]}  |  "
          f"Members: {cur.execute('SELECT COUNT(*) FROM MEMBERS').fetchone()[0]} "
          f"(Normal {cur.execute('SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=0').fetchone()[0]}, "
          f"Pro {cur.execute('SELECT COUNT(*) FROM MEMBERS WHERE MEM_TYPE=1').fetchone()[0]})  |  "
          f"Borrow records: {len(borrow_rows)} ({n_out} currently out)")
    con.close()


if __name__ == "__main__":
    main()
