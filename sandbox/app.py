"""Mock ERP (Flask + SQLite) with injected failures for the operator demo.

Injected behaviours (when SANDBOX_FAULTS=1, the default):
  * The invoice form hints MM/DD/YYYY but the server only accepts YYYY-MM-DD (date-format error).
  * The first otherwise-valid invoice POST returns a one-time HTTP 500 (nothing is saved).
  * Duplicate (vendor, invoice number) pairs are rejected.
"""
import argparse
import os
import re
import sqlite3
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

BASE = Path(__file__).parent
DB = os.getenv("SANDBOX_DB", str(BASE / "erp.db"))
FAULTS = os.getenv("SANDBOX_FAULTS", "1") == "1"
FAULT_STATE = {"server_error_done": False}

VENDORS = ["Acme Corp", "Acme Corporation Ltd", "Globex Inc", "Initech LLC"]
POS = [  # po_number, vendor, order_date
    ("PO-7001", "Globex Inc", "2026-01-05"),
    ("PO-7002", "Globex Inc", "2026-02-20"),
    ("PO-7003", "Globex Inc", "2026-03-12"),
    ("PO-4410", "Acme Corp", "2026-03-01"),
    ("PO-9100", "Initech LLC", "2026-02-02"),
]

app = Flask(__name__)
app.secret_key = "sandbox-only-not-secret"


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db(reset=False):
    if reset and os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT, invoice_number TEXT,
        amount REAL, due_date TEXT, UNIQUE(vendor, invoice_number));
    CREATE TABLE IF NOT EXISTS pos(
        id INTEGER PRIMARY KEY AUTOINCREMENT, po_number TEXT UNIQUE, vendor TEXT,
        order_date TEXT, status TEXT DEFAULT 'open');
    """)
    if conn.execute("SELECT COUNT(*) FROM pos").fetchone()[0] == 0:
        conn.executemany("INSERT INTO pos(po_number, vendor, order_date) VALUES (?,?,?)", POS)
    conn.commit()
    conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


@app.route("/")
def index():
    return redirect(url_for("vendors"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "demo" and request.form.get("password") == "demo123":
            session["user"] = "demo"
            return redirect(url_for("vendors"))
        flash("Invalid username or password.", "err")
    return render_template("login.html")


@app.route("/vendors")
@login_required
def vendors():
    return render_template("vendors.html", vendors=VENDORS)


@app.route("/invoices")
@login_required
def invoices():
    rows = db().execute("SELECT * FROM invoices ORDER BY id").fetchall()
    return render_template("invoices.html", rows=rows)


@app.route("/invoices/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        vendor = form.get("vendor", "").strip()
        number = form.get("invoice_number", "").strip()
        amount = form.get("amount", "").strip()
        due = form.get("due_date", "").strip()
        error = None
        if vendor not in VENDORS or not number or not amount or not due:
            error = "All fields are required and the vendor must be in the vendor list."
        elif not re.fullmatch(r"\d+(\.\d{1,2})?", amount):
            error = "Amount must be a plain number such as 1234.50 (no currency symbol or commas)."
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            error = "Invalid due date format. Use YYYY-MM-DD."
        elif db().execute("SELECT 1 FROM invoices WHERE vendor=? AND invoice_number=?",
                          (vendor, number)).fetchone():
            error = f"Duplicate invoice: {number} already exists for {vendor}."
        if error:
            flash(error, "err")
        else:
            if FAULTS and not FAULT_STATE["server_error_done"]:
                FAULT_STATE["server_error_done"] = True
                abort(500)
            cur = db().execute(
                "INSERT INTO invoices(vendor, invoice_number, amount, due_date) VALUES (?,?,?,?)",
                (vendor, number, float(amount), due))
            db().commit()
            flash(f"Invoice {number} saved.", "ok")
            return redirect(url_for("invoice_detail", inv_id=cur.lastrowid))
    return render_template("invoice_form.html", vendors=VENDORS, form=form, faults=FAULTS)


@app.route("/invoices/<int:inv_id>")
@login_required
def invoice_detail(inv_id):
    row = db().execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        abort(404)
    return render_template("invoice_detail.html", r=row)


@app.route("/pos")
@login_required
def pos():
    rows = db().execute("SELECT * FROM pos ORDER BY id").fetchall()
    return render_template("pos.html", rows=rows)


@app.route("/pos/<int:po_id>")
@login_required
def po_detail(po_id):
    row = db().execute("SELECT * FROM pos WHERE id=?", (po_id,)).fetchone()
    if not row:
        abort(404)
    return render_template("po_detail.html", r=row)


@app.route("/pos/<int:po_id>/receive", methods=["POST"])
@login_required
def po_receive(po_id):
    row = db().execute("SELECT * FROM pos WHERE id=?", (po_id,)).fetchone()
    if not row:
        abort(404)
    if row["status"] == "received":
        flash("PO is already marked received.", "err")
    else:
        db().execute("UPDATE pos SET status='received' WHERE id=?", (po_id,))
        db().commit()
        flash(f"{row['po_number']} marked as received.", "ok")
    return redirect(url_for("po_detail", po_id=po_id))


@app.route("/api/stats")
def stats():
    c = db()
    return jsonify(
        invoices=c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        pos_received=c.execute("SELECT COUNT(*) FROM pos WHERE status='received'").fetchone()[0],
        invoice_rows=[dict(r) for r in c.execute("SELECT * FROM invoices ORDER BY id")],
        received_pos=[r["po_number"] for r in c.execute("SELECT po_number FROM pos WHERE status='received'")],
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--reset", action="store_true", help="recreate the database")
    args = ap.parse_args()
    init_db(reset=args.reset)
    app.run(host="127.0.0.1", port=args.port, debug=False)
