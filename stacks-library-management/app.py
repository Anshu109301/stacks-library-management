"""
Library Management System — Flask Edition
===========================================
Public catalog browsing, a member-verification area (no password —
members identify themselves with their Member ID, e.g. LIB10245),
and a separate, real admin login (username + password) for staff.

Run with:
    python app.py
Then open http://127.0.0.1:5000

Default admin login (CHANGE THIS after first login):
    username: admin
    password: admin123
"""

import io
import os
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_file, abort,
)

import models as m
import charts

app = Flask(__name__)
# LIBRARY_SECRET_KEY should be set in any real deployment so sessions survive
# a restart. Without it, we generate a brand-new random key every time the
# process starts — deliberately, so no old session cookie (e.g. from a
# previous test run) can ever remain valid across a restart. This also means
# every admin/member session is invalidated the moment you restart the app,
# which is a clean way to verify there's no lingering "already logged in"
# state left over from earlier testing.
app.secret_key = os.environ.get("LIBRARY_SECRET_KEY") or os.urandom(32).hex()

# Runs at import time (not just under `python app.py`) so the tables
# exist whether Flask's own dev server starts app.py directly, or a
# production WSGI server imports `app` from this module.
m.init_db()


# ----------------------------------------------------------------------
# AUTH GUARDS
# ----------------------------------------------------------------------
def member_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("member_verified"):
            flash("Please verify your Member ID first.", "error")
            return redirect(url_for("member_verify", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.after_request
def apply_no_cache_headers(response):
    """Prevent the browser (and its back/forward cache) from ever serving a
    stored copy of a page instead of hitting the server. Without this, a
    user can log out, press Back, and the browser may restore the previous
    authenticated page straight from cache/bfcache without a new request —
    so member_required/admin_required never gets a chance to re-check the
    (now-cleared) session. Applied to every response since this whole app
    is dynamic and session-driven; there's no static/public page whose
    staleness would be safe to risk."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.context_processor
def inject_globals():
    return {
        "is_admin": session.get("is_admin", False),
        "member_verified": session.get("member_verified", False),
        "session_member_name": session.get("member_name"),
        "session_member_code": session.get("member_code"),
        "greeting": m.greeting_for_now(),
    }


# ----------------------------------------------------------------------
# PUBLIC — home + open catalog browsing
# ----------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/catalog")
def catalog():
    search = request.args.get("q", "")
    genre = request.args.get("genre", "")
    sort = request.args.get("sort", "id")
    books = m.all_books(search=search, genre_filter=genre, sort=sort)
    books_with_avail = [(b, m.book_availability(b["id"])) for b in books]
    return render_template(
        "catalog.html", books=books_with_avail, search=search, genre=genre,
        sort=sort, genres=m.all_genres(),
    )


@app.route("/catalog/<int:book_id>")
def book_detail(book_id):
    book = m.get_book(book_id)
    if not book:
        abort(404)
    avail = m.book_availability(book_id)
    return render_template("book_detail.html", book=book, avail=avail)


# ----------------------------------------------------------------------
# MEMBER AREA — verification instead of a password
# ----------------------------------------------------------------------
@app.route("/member/verify", methods=["GET", "POST"])
def member_verify():
    name_results = None
    name_query = ""
    if request.method == "POST":
        if "lookup_name" in request.form:
            # Name search is a lookup helper ONLY — it never authenticates.
            # It returns masked codes so the member can recognize their own
            # record, then they still have to type their real Member ID.
            name_query = request.form.get("lookup_name", "").strip()
            if name_query:
                name_results = m.find_members_by_name(name_query)
                if not name_results:
                    flash("No members found with that name. Check the spelling or ask library staff.", "error")
            else:
                flash("Enter a name to search for.", "error")
        else:
            code = request.form.get("member_id", "").strip()
            if not code:
                flash("Enter your Member ID.", "error")
                return redirect(url_for("member_verify"))
            member = m.get_member_by_code(code)
            if not member:
                flash("We couldn't find that Member ID. Double-check it and try again.", "error")
                return redirect(url_for("member_verify"))
            session.clear()
            session["member_verified"] = True
            session["member_id"] = member["id"]
            session["member_code"] = member["member_code"]
            session["member_name"] = member["name"]
            return redirect(url_for("member_dashboard"))
    return render_template("member/verify.html", name_results=name_results, name_query=name_query)


@app.route("/member/logout")
def member_logout():
    session.clear()
    flash("Signed out. See you again soon.", "success")
    return redirect(url_for("home"))


@app.route("/member/dashboard")
@member_required
def member_dashboard():
    profile = m.member_profile(session["member_id"])
    if not profile:
        return redirect(url_for("member_logout"))
    return render_template("member/dashboard.html", profile=profile)


@app.route("/member/history")
@member_required
def member_history():
    history = m.member_borrow_history(session["member_id"])
    return render_template("member/history.html", history=history)


@app.route("/member/borrow", methods=["GET", "POST"])
@member_required
def member_borrow():
    success = None
    if request.method == "POST":
        book_id = request.form.get("book_id", type=int)
        result = m.issue_book(session["member_id"], book_id)
        if result.get("error"):
            flash(result["error"], "error")
            if result.get("out_of_stock"):
                return redirect(url_for("member_reserve", book_id=book_id))
        else:
            success = result
    search = request.args.get("q", "")
    available = m.preview_available_books(limit=24, search=search)
    return render_template("member/borrow.html", success=success, available=available, search=search)


@app.route("/member/return", methods=["GET", "POST"])
@member_required
def member_return():
    success = None
    if request.method == "POST":
        loan_id = request.form.get("loan_id", type=int)
        condition = request.form.get("condition", "good")
        result = m.return_book(loan_id, condition, expected_member_id=session["member_id"])
        if result.get("error"):
            flash(result["error"], "error")
        else:
            success = result
    profile = m.member_profile(session["member_id"])
    return render_template("member/return.html", success=success, profile=profile, conditions=m.CONDITION_CHOICES)


@app.route("/member/renew", methods=["GET", "POST"])
@member_required
def member_renew():
    quote = None
    if request.method == "POST":
        step = request.form.get("step")
        new_type = 1 if request.form.get("plan") == "pro" else 0
        if step == "quote":
            quote = m.renew_preview(session["member_id"], new_type)
        elif step == "confirm":
            new_expiry = request.form.get("new_expiry")
            m.renew_commit(session["member_id"], new_type, new_expiry)
            flash("Membership renewed successfully!", "success")
            return redirect(url_for("member_dashboard"))
    member = m.get_member(session["member_id"])
    return render_template("member/renew.html", quote=quote, member=member)


@app.route("/member/suggestions")
@member_required
def member_suggestions():
    data = m.suggest_books(session["member_id"])
    return render_template("member/suggestions.html", data=data)


@app.route("/member/reserve", methods=["GET", "POST"])
@member_required
def member_reserve():
    result = None
    if request.method == "POST":
        book_id = request.form.get("book_id", type=int)
        result = m.reserve_book(session["member_id"], book_id)
        if result.get("ok"):
            flash(f"Reserved '{result['book_name']}'. We'll flag it when it's returned.", "success")
    preselect = request.args.get("book_id", type=int)
    out_of_stock = [b for b in m.all_books() if b["quantity"] == 0]
    return render_template("member/reserve.html", result=result, out_of_stock=out_of_stock, preselect=preselect)


# ----------------------------------------------------------------------
# ADMIN — real login, separate from member verification
# ----------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        admin = m.verify_admin(username, password)
        if admin:
            session.clear()
            session["is_admin"] = True
            session["admin_username"] = admin["username"]
            flash("Welcome back.", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Access denied — check your admin credentials.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Signed out of admin.", "success")
    return redirect(url_for("home"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin/dashboard.html", stats=m.dashboard_stats(), activity=m.recent_activity(8))


# ---- Books ----
@app.route("/admin/books")
@admin_required
def admin_books():
    search = request.args.get("q", "")
    genre = request.args.get("genre", "")
    sort = request.args.get("sort", "id")
    books = m.all_books(search=search, genre_filter=genre, sort=sort)
    books_with_avail = [(b, m.book_availability(b["id"])) for b in books]
    return render_template(
        "admin/books.html", books=books_with_avail, search=search, genre=genre,
        sort=sort, genres=m.all_genres(),
    )


@app.route("/admin/books/add", methods=["GET", "POST"])
@admin_required
def admin_add_book():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        author = request.form.get("author", "").strip()
        price = request.form.get("price", type=int)
        qty = request.form.get("quantity", type=int)
        genres = request.form.getlist("genres")
        isbn = request.form.get("isbn", "")
        publisher = request.form.get("publisher", "")
        pub_year = request.form.get("pub_year", type=int)
        if not name or price is None or qty is None:
            flash("Title, price and quantity are required.", "error")
            return redirect(url_for("admin_add_book"))
        if price < 0 or qty < 0:
            flash("Price and quantity can't be negative.", "error")
            return redirect(url_for("admin_add_book"))
        book_id = m.add_book(name, author, price, qty, genres, isbn, publisher, pub_year)
        flash(f"'{name}' added to the catalog.", "success")
        return redirect(url_for("admin_books"))
    return render_template("admin/add_book.html", genres=m.GENRES)


@app.route("/admin/books/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_book(book_id):
    book = m.get_book(book_id)
    if not book:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        author = request.form.get("author", "").strip()
        price = request.form.get("price", type=int)
        qty = request.form.get("quantity", type=int)
        genres = request.form.getlist("genres")
        isbn = request.form.get("isbn", "")
        publisher = request.form.get("publisher", "")
        pub_year = request.form.get("pub_year", type=int)
        if not name or price is None or qty is None:
            flash("Title, price and quantity are required.", "error")
            return redirect(url_for("admin_edit_book", book_id=book_id))
        if price < 0 or qty < 0:
            flash("Price and quantity can't be negative.", "error")
            return redirect(url_for("admin_edit_book", book_id=book_id))
        m.update_book(book_id, name, author, price, qty, genres, isbn, publisher, pub_year)
        flash(f"'{name}' updated.", "success")
        return redirect(url_for("admin_books"))
    current_genres = [g.strip() for g in (book["genre"] or "").split(",") if g.strip()]
    return render_template("admin/edit_book.html", book=book, genres=m.GENRES, current_genres=current_genres)


@app.route("/admin/books/<int:book_id>/delete", methods=["POST"])
@admin_required
def admin_delete_book(book_id):
    book = m.get_book(book_id)
    result = m.delete_book(book_id)
    if result.get("error"):
        flash(result["error"], "error")
    else:
        flash(f"'{book['name'] if book else 'Book'}' deleted.", "success")
    return redirect(url_for("admin_books"))


# ---- Members ----
@app.route("/admin/members")
@admin_required
def admin_members():
    search = request.args.get("q", "")
    return render_template("admin/members.html", members=m.all_members(search), search=search)


@app.route("/admin/members/add", methods=["GET", "POST"])
@admin_required
def admin_add_member():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        plan_choice = request.form.get("plan")
        if not name:
            flash("Name can't be empty.", "error")
            return redirect(url_for("admin_add_member"))
        mem_type = 1 if plan_choice == "pro" else 0
        result = m.create_member(name, email, phone, mem_type)
        return render_template(
            "admin/member_added.html", name=name, email=email, phone=phone,
            member_code=result["member_code"], plan=result["plan"], expiry=result["expiry"],
            member_id=result["id"],
        )
    return render_template("admin/add_member.html", normal=m.NORMAL_PLAN, pro=m.PRO_PLAN)


@app.route("/admin/members/<int:member_id>")
@admin_required
def admin_member_detail(member_id):
    profile = m.member_profile(member_id)
    if not profile:
        abort(404)
    history = m.member_borrow_history(member_id, limit=20)
    return render_template("admin/member_detail.html", profile=profile, history=history)


@app.route("/admin/members/<int:member_id>/suggestions")
@admin_required
def admin_member_suggestions(member_id):
    member = m.get_member(member_id)
    if not member:
        abort(404)
    data = m.suggest_books(member_id)
    return render_template("admin/member_suggestions.html", data=data, member=member)


# ---- Desk operations (admin acting on behalf of any member) ----
@app.route("/admin/desk/borrow", methods=["GET", "POST"])
@admin_required
def admin_desk_borrow():
    result = None
    member = None
    if request.method == "POST":
        code = request.form.get("member_code", "").strip()
        book_id = request.form.get("book_id", type=int)
        member = m.get_member_by_code(code)
        if not member:
            result = {"error": "No member found with that Member ID."}
        elif book_id:
            result = m.issue_book(member["id"], book_id)
    available = m.preview_available_books(limit=24)
    return render_template("admin/desk_borrow.html", result=result, available=available, member=member)


@app.route("/admin/desk/return", methods=["GET", "POST"])
@admin_required
def admin_desk_return():
    result = None
    if request.method == "POST":
        loan_id = request.form.get("loan_id", type=int)
        condition = request.form.get("condition", "good")
        if loan_id:
            result = m.return_book(loan_id, condition)
        else:
            result = {"error": "No book selected to return."}
    lookup_code = request.values.get("member_code", "")
    lookup_member = m.get_member_by_code(lookup_code) if lookup_code else None
    profile = m.member_profile(lookup_member["id"]) if lookup_member else None
    return render_template(
        "admin/desk_return.html", result=result, profile=profile,
        lookup_code=lookup_code, conditions=m.CONDITION_CHOICES,
    )


@app.route("/admin/desk/renew", methods=["GET", "POST"])
@admin_required
def admin_desk_renew():
    quote = None
    member_code = request.values.get("member_code", "")

    if request.method == "POST":
        step = request.form.get("step")
        member_code = request.form.get("member_code", "")
        new_type = 1 if request.form.get("plan") == "pro" else 0
        member = m.get_member_by_code(member_code)

        if not member:
            flash("No member found with that Member ID.", "error")
        elif step == "quote":
            quote = m.renew_preview(member["id"], new_type)
        elif step == "confirm":
            new_expiry = request.form.get("new_expiry")
            m.renew_commit(member["id"], new_type, new_expiry)
            flash("Membership renewed successfully!", "success")
            return redirect(url_for("admin_member_detail", member_id=member["id"]))

    member = m.get_member_by_code(member_code) if member_code else None
    return render_template("admin/desk_renew.html", quote=quote, member=member, member_code=member_code)


@app.route("/admin/reservations")
@admin_required
def admin_reservations():
    return render_template("admin/reservations.html", reservations=m.active_reservations())


@app.route("/admin/reservations/<int:res_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_reservation(res_id):
    m.cancel_reservation(res_id)
    flash("Reservation cancelled.", "success")
    return redirect(url_for("admin_reservations"))


@app.route("/admin/borrowed")
@admin_required
def admin_borrowed():
    return render_template("admin/borrowed.html", loans=m.currently_borrowed())


@app.route("/admin/history")
@admin_required
def admin_history():
    return render_template("admin/history.html", history=m.borrow_history())


@app.route("/admin/revenue")
@admin_required
def admin_revenue():
    return render_template("admin/revenue.html", report=m.revenue_report())


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    catalog_genres = m.catalog_genre_distribution()
    borrowed_genres = m.borrowed_genre_distribution()
    plan_dist = m.plan_distribution()
    condition_dist = m.condition_breakdown()
    trend = m.borrow_trend_last_8_weeks()
    revenue_trend = m.revenue_trend_last_8_weeks()
    member_growth = m.member_growth_last_8_weeks()

    return render_template(
        "admin/analytics.html",
        catalog_genres=catalog_genres,
        borrowed_genres=borrowed_genres,
        top_genre=m.most_read_genre(),
        trend=trend,
        revenue_trend=revenue_trend,
        plan_dist=plan_dist,
        condition_dist=condition_dist,
        top_books=m.top_borrowed_books(5),
        top_borrowers=m.top_borrowers(5),
        avg_days_late=m.average_days_late(),
        member_growth=member_growth,
        total_genres=len(m.all_genres()),
        chart_catalog_genres=charts.pie_chart(catalog_genres),
        chart_borrowed_genres=charts.pie_chart(borrowed_genres),
        chart_plan=charts.pie_chart(dict(zip(plan_dist["labels"], plan_dist["values"]))),
        chart_condition=charts.pie_chart(dict(zip(condition_dist["labels"], condition_dist["values"]))),
        chart_trend=charts.trend_chart(trend["labels"], trend["values"], "Books issued", "#0071e3"),
        chart_revenue_trend=charts.trend_chart(
            revenue_trend["labels"], revenue_trend["values"], "Rs collected", "#b9862e"
        ),
        chart_growth=charts.trend_chart(
            member_growth["labels"], member_growth["values"], "New members", "#1d8a4c", kind="bar"
        ),
    )


@app.route("/admin/export/<kind>")
@admin_required
def admin_export(kind):
    if kind not in {"books", "members", "history"}:
        abort(404)
    buf = m.export_csv(kind)
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(
        mem, mimetype="text/csv", as_attachment=True,
        download_name=f"library_{kind}.csv",
    )


@app.route("/admin/account", methods=["GET", "POST"])
@admin_required
def admin_account():
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif new_password != confirm:
            flash("Passwords don't match.", "error")
        else:
            admin = m.verify_admin(session["admin_username"], request.form.get("current_password", ""))
            if not admin:
                flash("Current password is incorrect.", "error")
            else:
                m.change_admin_password(admin["id"], new_password)
                flash("Password updated.", "success")
        return redirect(url_for("admin_account"))
    return render_template("admin/account.html")


# ----------------------------------------------------------------------
# ERROR PAGES
# ----------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("errors/500.html"), 500


if __name__ == "__main__":
    # Set FLASK_DEBUG=1 for local development; off by default.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
