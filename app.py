import hashlib
import hmac
import html
import os
import random
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from books_data import BOOKS

DB_FILE = "library_v2.db"
MAX_LOANS = 5
LOAN_DAYS = 14
PAGE_SIZE = 12

st.set_page_config(page_title="Athenaeum Library", page_icon="📚", layout="wide")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 2rem; max-width: 1200px; }
.hero { background: linear-gradient(120deg, #4f46e5 0%, #7c3aed 55%, #db2777 100%); color: #fff; padding: 28px 34px; border-radius: 20px; margin-bottom: 24px; box-shadow: 0 10px 30px rgba(79,70,229,.28); }
.hero h1 { margin: 0; font-size: 2rem; font-weight: 700; color: #fff; padding: 0; }
.hero p { margin: 6px 0 0; opacity: .92; font-size: 1.02rem; }
[data-testid="stMetric"] { background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.2); padding: 16px 18px; border-radius: 16px; }
.book-card { border: 1px solid rgba(128,128,128,.22); border-radius: 16px; overflow: hidden; background: rgba(128,128,128,.06); margin-bottom: 8px; transition: transform .15s, box-shadow .15s; }
.book-card:hover { transform: translateY(-3px); box-shadow: 0 8px 22px rgba(0,0,0,.18); }
.cover { height: 110px; display: flex; align-items: center; justify-content: space-between; padding: 0 18px; color: #fff; }
.cover .initial { font-size: 3rem; font-weight: 700; opacity: .95; }
.cover .num { font-size: .85rem; font-weight: 600; background: rgba(0,0,0,.28); padding: 4px 12px; border-radius: 999px; }
.book-body { padding: 14px 16px 16px; }
.book-title { font-weight: 600; font-size: 1.02rem; line-height: 1.3; min-height: 2.6em; }
.book-author { opacity: .7; font-size: .88rem; margin: 2px 0 10px; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .75rem; font-weight: 600; margin-right: 6px; }
.badge-ok { background: rgba(16,185,129,.18); color: #10b981; }
.badge-no { background: rgba(239,68,68,.18); color: #ef4444; }
.badge-warn { background: rgba(245,158,11,.2); color: #f59e0b; }
.badge-genre { background: rgba(99,102,241,.18); color: #818cf8; }
.loan-card { display: flex; gap: 16px; align-items: center; padding: 14px 16px; border: 1px solid rgba(128,128,128,.22); border-radius: 16px; background: rgba(128,128,128,.06); }
.mini-cover { min-width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; }
.user-chip { display: flex; gap: 12px; align-items: center; padding: 12px; border-radius: 14px; background: rgba(128,128,128,.1); margin-bottom: 14px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; background: linear-gradient(135deg, #4f46e5, #db2777); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; }
.stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 600; }
</style>
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    genre TEXT,
    year INTEGER,
    isbn TEXT,
    copies INTEGER NOT NULL DEFAULT 1,
    available INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    borrowed_on TEXT NOT NULL,
    due_on TEXT NOT NULL,
    returned_on TEXT
);
"""

HELP = """Hi! I'm the library bot. Try things like:

- **A book number** — `42` or `book #42`
- **A title, author or genre** — `dune`, `tolkien`, `sci-fi available`
- **Borrow or return** — `borrow 42`, `return 42`, `borrow dune`
- **Other** — `my loans`, `genres`, `surprise me`
"""

TEMPLATE = "title,author,genre,year,copies\nExample Book,Jane Doe,Fiction,2020,2\n"

STOP = {
    "a", "an", "the", "do", "you", "have", "has", "is", "are", "there", "any", "book",
    "books", "by", "about", "find", "search", "look", "for", "me", "show", "please", "i",
    "want", "to", "of", "in", "on", "with", "can", "get", "tell", "what", "whats", "where",
    "who", "wrote", "written", "author", "title", "number", "no", "copy", "available",
    "genre", "novel", "some", "like", "something", "it", "my", "and",
}


def run(query, params=()):
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.lastrowid


def read(query, params=()):
    with closing(sqlite3.connect(DB_FILE)) as conn:
        return pd.read_sql_query(query, conn, params=params)


def hash_pw(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, digest


def verify_pw(password, salt, digest):
    return hmac.compare_digest(hash_pw(password, salt)[1], digest)


def create_user(username, full_name, email, password, role="member"):
    username = username.strip().lower()
    if not full_name.strip():
        return "Please enter your full name."
    if not re.fullmatch(r"[a-z0-9_.-]{3,20}", username):
        return "Username must be 3–20 characters: letters, numbers, . _ -"
    if "@" not in email:
        return "Please enter a valid email."
    if len(password) < 6:
        return "Password must be at least 6 characters."
    salt, digest = hash_pw(password)
    try:
        run(
            "INSERT INTO users (username, full_name, email, password_hash, salt, role, joined) "
            "VALUES (?,?,?,?,?,?,?)",
            (username, full_name.strip(), email.strip().lower(), digest, salt, role, str(date.today())),
        )
    except sqlite3.IntegrityError:
        return "That username or email is already taken."
    return None


def authenticate(username, password):
    df = read("SELECT * FROM users WHERE username=?", (username.strip().lower(),))
    if df.empty:
        return None
    row = df.iloc[0]
    if verify_pw(password, row["salt"], row["password_hash"]):
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "name": row["full_name"],
            "role": row["role"],
        }
    return None


@st.cache_resource
def init_db():
    with closing(sqlite3.connect(DB_FILE)) as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
            rows = []
            for i, (title, author, genre, year) in enumerate(BOOKS):
                copies = 1 + (i * 7) % 4
                rows.append((title, author, genre, year, "", copies, copies))
            conn.executemany(
                "INSERT INTO books (title, author, genre, year, isbn, copies, available) "
                "VALUES (?,?,?,?,?,?,?)",
                rows,
            )
        conn.commit()
    if read("SELECT COUNT(*) AS n FROM users")["n"][0] == 0:
        create_user("admin", "Library Admin", "admin@library.local", "admin123", "admin")
    return True


def borrow_book(book_id, user_id, days=LOAN_DAYS):
    book = read("SELECT * FROM books WHERE id=?", (int(book_id),))
    if book.empty:
        return False, f"There is no book with number {book_id}."
    b = book.iloc[0]
    if b["available"] < 1:
        return False, f"“{b['title']}” has no free copies right now."
    held = read("SELECT book_id FROM loans WHERE user_id=? AND returned_on IS NULL", (user_id,))
    if len(held) >= MAX_LOANS:
        return False, f"You already have {MAX_LOANS} books out. Return one first."
    if int(book_id) in held["book_id"].tolist():
        return False, "You already have this book."
    today = date.today()
    due = today + timedelta(days=days)
    run(
        "INSERT INTO loans (book_id, user_id, borrowed_on, due_on) VALUES (?,?,?,?)",
        (int(book_id), user_id, str(today), str(due)),
    )
    run("UPDATE books SET available = available - 1 WHERE id=?", (int(book_id),))
    return True, f"Borrowed “{b['title']}”. Due back on {due:%d %b %Y}."


def return_book(loan_id, user_id):
    loan = read(
        "SELECT l.*, b.title FROM loans l JOIN books b ON b.id = l.book_id "
        "WHERE l.id=? AND l.returned_on IS NULL",
        (int(loan_id),),
    )
    if loan.empty or int(loan.iloc[0]["user_id"]) != user_id:
        return False, "Loan not found."
    row = loan.iloc[0]
    run("UPDATE loans SET returned_on=? WHERE id=?", (str(date.today()), int(loan_id)))
    run("UPDATE books SET available = available + 1 WHERE id=?", (int(row["book_id"]),))
    late = (date.today() - date.fromisoformat(row["due_on"])).days
    if late > 0:
        return True, f"Returned “{row['title']}” — {late} day(s) late."
    return True, f"Returned “{row['title']}”. Thank you!"


def active_loans(user_id):
    return read(
        """SELECT l.id, b.id AS book_no, b.title, b.author, b.genre, l.borrowed_on, l.due_on
           FROM loans l JOIN books b ON b.id = l.book_id
           WHERE l.user_id = ? AND l.returned_on IS NULL ORDER BY l.due_on""",
        (user_id,),
    )


def import_csv(file):
    df = pd.read_csv(file)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"title", "author"} - set(df.columns)
    if missing:
        return 0, f"Missing column(s): {', '.join(sorted(missing))}"
    rows = []
    for r in df.to_dict("records"):
        title = str(r.get("title", "")).strip()
        author = str(r.get("author", "")).strip()
        if not title or not author or title == "nan" or author == "nan":
            continue
        genre = r.get("genre")
        genre = str(genre).strip() if pd.notna(genre) else "Other"
        year = int(r["year"]) if pd.notna(r.get("year")) else None
        copies = max(1, int(r["copies"])) if pd.notna(r.get("copies")) else 1
        rows.append((title, author, genre, year, "", copies, copies))
    with closing(sqlite3.connect(DB_FILE)) as conn:
        conn.executemany(
            "INSERT INTO books (title, author, genre, year, isbn, copies, available) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    return len(rows), None


def cover_color(genre):
    hue = sum(ord(c) for c in (genre or "x")) * 37 % 360
    return f"linear-gradient(135deg, hsl({hue},65%,48%), hsl({(hue + 45) % 360},70%,36%))"


def book_card_html(b):
    avail = int(b.available)
    if avail > 0:
        badge = f'<span class="badge badge-ok">{avail} of {int(b.copies)} available</span>'
    else:
        badge = '<span class="badge badge-no">All borrowed</span>'
    year = int(b.year) if pd.notna(b.year) else "n/a"
    return (
        '<div class="book-card">'
        f'<div class="cover" style="background:{cover_color(b.genre)}">'
        f'<span class="initial">{html.escape(b.title[:1].upper())}</span>'
        f'<span class="num">No. {int(b.id)}</span></div>'
        '<div class="book-body">'
        f'<div class="book-title">{html.escape(b.title)}</div>'
        f'<div class="book-author">{html.escape(b.author)} · {year}</div>'
        f'<span class="badge badge-genre">{html.escape(b.genre or "")}</span>{badge}'
        "</div></div>"
    )


def loan_card_html(r):
    due = date.fromisoformat(r.due_on)
    days = (due - date.today()).days
    if days < 0:
        badge = f'<span class="badge badge-no">{-days} day(s) overdue</span>'
    elif days == 0:
        badge = '<span class="badge badge-warn">Due today</span>'
    elif days <= 3:
        badge = f'<span class="badge badge-warn">Due in {days} day(s)</span>'
    else:
        badge = f'<span class="badge badge-ok">Due {due:%d %b}</span>'
    return (
        '<div class="loan-card">'
        f'<div class="mini-cover" style="background:{cover_color(r.genre)}">{r.book_no}</div>'
        "<div>"
        f'<div class="book-title" style="min-height:0">{html.escape(r.title)}</div>'
        f'<div class="book-author" style="margin-bottom:6px">{html.escape(r.author)}</div>'
        f"{badge}</div></div>"
    )


def page_header(title, subtitle):
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def done(ok, msg):
    st.session_state.flash = (ok, msg)
    st.rerun()


def flash():
    msg = st.session_state.pop("flash", None)
    if msg:
        st.toast(msg[1], icon="✅" if msg[0] else "⚠️")


def find_books(query, limit=6, only_available=False):
    df = read("SELECT * FROM books")
    if only_available:
        df = df[df["available"] > 0]
    words = [w for w in re.findall(r"[\w'.-]+", query.lower()) if w not in STOP]
    if not words:
        return df.head(limit) if only_available else df.iloc[0:0]
    blob = (
        df["title"] + " " + df["author"] + " " + df["genre"].fillna("") + " " + df["isbn"].fillna("")
    ).str.lower()
    df = df.assign(score=sum(blob.str.contains(w, regex=False).astype(int) for w in words))
    df = df[df["score"] > 0]
    if df.empty:
        return df
    df = df[df["score"] == df["score"].max()].sort_values("title")
    return df.head(limit)


def describe(r):
    year = int(r.year) if pd.notna(r.year) else "n/a"
    status = f"✅ {int(r.available)}/{int(r.copies)} available" if r.available > 0 else "❌ all copies out"
    return f"**No. {r.id} · {r.title}** — {r.author} ({year}) · {r.genre} · {status}"


def genre_in_text(genre, text):
    if genre.lower() in text:
        return True
    return any(len(w) > 4 and w in text for w in re.split(r"\W+", genre.lower()))


def handle_action(verb, rest, user):
    borrowing = verb.startswith(("borrow", "check"))
    num = re.search(r"\d+", rest)
    if num:
        book_id = int(num.group())
    else:
        found = find_books(rest)
        if found.empty:
            return "I couldn't tell which book you mean. Give me a title or a book number."
        if len(found) > 1:
            lines = "\n".join(f"- {describe(r)}" for r in found.itertuples())
            return f"Which one? Tell me the number:\n{lines}"
        book_id = int(found.iloc[0]["id"])
    if borrowing:
        ok, msg = borrow_book(book_id, user["id"])
        return ("✅ " if ok else "⚠️ ") + msg
    loan = read(
        "SELECT id FROM loans WHERE book_id=? AND user_id=? AND returned_on IS NULL",
        (book_id, user["id"]),
    )
    if loan.empty:
        return "You don't have that book checked out."
    ok, msg = return_book(int(loan.iloc[0]["id"]), user["id"])
    return ("✅ " if ok else "⚠️ ") + msg


def bot_reply(text, user):
    t = text.lower().strip()
    if re.fullmatch(r"(hi|hello|hey|help|\?|menu)\W*", t) or "what can you do" in t:
        return HELP

    m = re.search(r"\b(borrow|check\s*out|return|give back|bring back)\b(.*)", t)
    if m:
        return handle_action(m.group(1), m.group(2), user)

    if re.search(r"\b(my loans|my books|what do i have|what have i)\b", t):
        df = active_loans(user["id"])
        if df.empty:
            return "You don't have any books checked out."
        lines = "\n".join(f"- **No. {r.book_no} · {r.title}** — due {r.due_on}" for r in df.itertuples())
        return f"Your current loans:\n{lines}"

    if re.search(r"\b(genres?|categories|sections)\b", t):
        df = read("SELECT genre, COUNT(*) AS n FROM books GROUP BY genre ORDER BY n DESC")
        lines = "\n".join(f"- {r.genre} ({r.n} titles)" for r in df.itertuples())
        return f"We have these genres:\n{lines}"

    if re.search(r"\b(surprise|random|recommend|suggest)\b", t):
        df = read("SELECT * FROM books WHERE available > 0")
        genres = [g for g in df["genre"].dropna().unique() if genre_in_text(g, t)]
        if genres:
            df = df[df["genre"].isin(genres)]
        if df.empty:
            return "Nothing matching is available right now."
        r = next(df.sample(1).itertuples())
        return f"How about this one?\n\n{describe(r)}\n\nSay `borrow {r.id}` to take it home."

    m = re.search(r"(?:#|\bno\.?\s*|\bnumber\s*|\bbook\s*)(\d+)", t) or re.fullmatch(r"\s*(\d+)\s*", t)
    if m:
        digits = m.group(1)
        if len(digits) >= 10:
            df = read("SELECT * FROM books WHERE isbn=?", (digits,))
            if df.empty:
                return f"No book with ISBN {digits} in our catalog."
            r = next(df.itertuples())
            return describe(r)
        n = int(digits)
        df = read("SELECT * FROM books WHERE id=?", (n,))
        if df.empty:
            top = int(read("SELECT MAX(id) AS m FROM books")["m"][0])
            return f"There is no book number {n}. Numbers go from 1 to {top}."
        r = next(df.itertuples())
        return f"{describe(r)}\n\nSay `borrow {r.id}` to take it home."

    only = bool(re.search(r"\bavailable\b", t))
    found = find_books(t, only_available=only)
    if found.empty:
        return "I couldn't find anything like that. Try another title, author or genre — or type `genres`."
    lines = "\n".join(f"- {describe(r)}" for r in found.itertuples())
    return f"Top matches:\n{lines}\n\nSay `borrow <number>` to take one home."


def page_auth():
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown(
            '<div class="hero" style="text-align:center"><h1>📚 Athenaeum Library</h1>'
            "<p>Sign in to browse, borrow and chat with our librarian bot.</p></div>",
            unsafe_allow_html=True,
        )
        tab_in, tab_up = st.tabs(["Sign in", "Create account"])
        with tab_in:
            with st.form("login"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Sign in", type="primary", width="stretch"):
                    user = authenticate(username, password)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("Wrong username or password.")
        with tab_up:
            with st.form("signup", clear_on_submit=False):
                full_name = st.text_input("Full name")
                new_user = st.text_input("Choose a username")
                email = st.text_input("Email")
                pw1 = st.text_input("Password", type="password", key="pw1")
                pw2 = st.text_input("Confirm password", type="password", key="pw2")
                if st.form_submit_button("Create account", type="primary", width="stretch"):
                    if pw1 != pw2:
                        st.error("Passwords don't match.")
                    else:
                        err = create_user(new_user, full_name, email, pw1)
                        if err:
                            st.error(err)
                        else:
                            st.success("Account created — you can sign in now.")


def page_dashboard(user):
    first = html.escape(user["name"].split()[0])
    page_header(f"Welcome back, {first} 👋", "Here's what's happening in the library today.")
    books = read("SELECT * FROM books")
    mine = active_loans(user["id"])
    today = str(date.today())

    c = st.columns(4)
    c[0].metric("Titles in library", f"{len(books):,}")
    c[1].metric("Copies on shelf", f"{int(books['available'].sum()):,}")
    c[2].metric("Your active loans", len(mine))
    c[3].metric("Your overdue", int((mine["due_on"] < today).sum()))

    if user["role"] == "admin":
        all_active = read("SELECT * FROM loans WHERE returned_on IS NULL")
        a = st.columns(3)
        a[0].metric("Members", int(read("SELECT COUNT(*) AS n FROM users")["n"][0]))
        a[1].metric("Loans out now", len(all_active))
        a[2].metric("Overdue (all)", int((all_active["due_on"] < today).sum()))

    st.write("")
    left, right = st.columns(2)
    with left:
        st.subheader("Top genres")
        st.bar_chart(
            books.groupby("genre").size().sort_values(ascending=False).head(10),
            horizontal=True,
        )
    with right:
        st.subheader("Most borrowed")
        top = read(
            """SELECT b.title, COUNT(*) AS times_borrowed
               FROM loans l JOIN books b ON b.id = l.book_id
               GROUP BY b.id ORDER BY times_borrowed DESC LIMIT 8"""
        )
        if top.empty:
            st.info("No loans yet — be the first to borrow a book!")
        else:
            st.bar_chart(top.set_index("title"), horizontal=True)

    st.subheader("Your next returns")
    if mine.empty:
        st.caption("You have nothing checked out.")
    else:
        for r in mine.head(3).itertuples():
            st.markdown(loan_card_html(r), unsafe_allow_html=True)
            st.write("")


def page_catalog(user):
    page_header("Catalog", "Browse, search and borrow from the collection.")
    books = read("SELECT * FROM books")
    c1, c2, c3, c4 = st.columns([3, 2, 2, 1.4])
    query = c1.text_input(
        "Search", placeholder="🔍 Title, author, genre or book number", label_visibility="collapsed"
    )
    genre = c2.selectbox(
        "Genre", ["All genres"] + sorted(books["genre"].dropna().unique().tolist()),
        label_visibility="collapsed",
    )
    sort = c3.selectbox(
        "Sort", ["Title A–Z", "Author A–Z", "Newest first", "Oldest first", "Book number"],
        label_visibility="collapsed",
    )
    only = c4.toggle("Available")

    df = books.copy()
    q = query.strip().lower()
    if q:
        blob = (df["title"] + " " + df["author"] + " " + df["genre"].fillna("")).str.lower()
        mask = blob.str.contains(q, regex=False)
        if q.isdigit():
            mask |= df["id"] == int(q)
        df = df[mask]
    if genre != "All genres":
        df = df[df["genre"] == genre]
    if only:
        df = df[df["available"] > 0]
    if sort == "Title A–Z":
        df = df.sort_values("title")
    elif sort == "Author A–Z":
        df = df.sort_values(["author", "title"])
    elif sort == "Newest first":
        df = df.sort_values("year", ascending=False)
    elif sort == "Oldest first":
        df = df.sort_values("year")
    else:
        df = df.sort_values("id")

    sig = (q, genre, sort, only)
    if st.session_state.get("cat_sig") != sig:
        st.session_state.cat_sig = sig
        st.session_state.cat_page = 0
    pages = max(1, -(-len(df) // PAGE_SIZE))
    page = min(st.session_state.get("cat_page", 0), pages - 1)
    start = page * PAGE_SIZE
    chunk = df.iloc[start:start + PAGE_SIZE]
    shown_from = start + 1 if len(df) else 0
    st.caption(f"Showing {shown_from}–{start + len(chunk)} of {len(df)} books")

    rows = list(chunk.itertuples())
    for i in range(0, len(rows), 3):
        cols = st.columns(3)
        for col, b in zip(cols, rows[i:i + 3]):
            with col:
                st.markdown(book_card_html(b), unsafe_allow_html=True)
                label = "Borrow" if b.available > 0 else "Unavailable"
                if st.button(label, key=f"borrow_{b.id}", disabled=b.available < 1, width="stretch"):
                    ok, msg = borrow_book(int(b.id), user["id"])
                    done(ok, msg)
        st.write("")

    nav = st.columns([1, 2, 1])
    if nav[0].button("← Previous", disabled=page == 0, width="stretch"):
        st.session_state.cat_page = page - 1
        st.rerun()
    nav[1].markdown(
        f"<div style='text-align:center;padding-top:8px'>Page {page + 1} of {pages}</div>",
        unsafe_allow_html=True,
    )
    if nav[2].button("Next →", disabled=page >= pages - 1, width="stretch"):
        st.session_state.cat_page = page + 1
        st.rerun()


def page_bot(user):
    page_header("Librarian Bot 🤖", "Type a book title, an author or a book number — I'll tell you where it stands.")
    if "chat" not in st.session_state:
        st.session_state.chat = [{"role": "assistant", "content": HELP}]

    pending = None
    quick = [
        ("🎲 Surprise me", "surprise me"),
        ("🔖 My loans", "my loans"),
        ("🗂️ Genres", "genres"),
        ("✅ Available now", "available"),
    ]
    for col, (label, text) in zip(st.columns(4), quick):
        if col.button(label, width="stretch"):
            pending = text

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask about a book or type a number…") or pending
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        st.session_state.chat.append({"role": "assistant", "content": bot_reply(prompt, user)})
        st.rerun()


def page_my_loans(user):
    page_header("My Loans", f"You can hold up to {MAX_LOANS} books at a time, for {LOAN_DAYS} days each.")
    mine = active_loans(user["id"])
    if mine.empty:
        st.info("You have no active loans. Head to the catalog to borrow something!")
    for r in mine.itertuples():
        left, right = st.columns([5, 1])
        left.markdown(loan_card_html(r), unsafe_allow_html=True)
        if right.button("Return", key=f"ret_{r.id}", width="stretch"):
            ok, msg = return_book(int(r.id), user["id"])
            done(ok, msg)
        st.write("")

    with st.expander("📜 Loan history"):
        hist = read(
            """SELECT b.id AS book_no, b.title, l.borrowed_on, l.due_on, l.returned_on
               FROM loans l JOIN books b ON b.id = l.book_id
               WHERE l.user_id = ? ORDER BY l.id DESC""",
            (user["id"],),
        )
        st.dataframe(hist, width="stretch", hide_index=True)


def page_profile(user):
    page_header("Profile", "Your account details and security.")
    st.markdown(f"**Name:** {user['name']}  \n**Username:** {user['username']}  \n**Role:** {user['role']}")
    st.subheader("Change password")
    with st.form("change_pw", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Update password", type="primary"):
            if not authenticate(user["username"], current):
                st.error("Current password is wrong.")
            elif len(new) < 6:
                st.error("New password must be at least 6 characters.")
            elif new != confirm:
                st.error("New passwords don't match.")
            else:
                salt, digest = hash_pw(new)
                run("UPDATE users SET password_hash=?, salt=? WHERE id=?", (digest, salt, user["id"]))
                st.success("Password updated.")


def page_add_books(user):
    page_header("Add Books", "Add a single title or import many at once from a CSV file.")
    tab_one, tab_csv = st.tabs(["Add one book", "Bulk import (CSV)"])
    with tab_one:
        with st.form("add_book", clear_on_submit=True):
            title = st.text_input("Title *")
            author = st.text_input("Author *")
            c1, c2, c3 = st.columns(3)
            genre = c1.text_input("Genre")
            year = c2.number_input("Year", 0, date.today().year, 2000)
            copies = c3.number_input("Copies", 1, 100, 1)
            isbn = st.text_input("ISBN")
            if st.form_submit_button("Add book", type="primary"):
                if not title.strip() or not author.strip():
                    st.error("Title and author are required.")
                else:
                    new_id = run(
                        "INSERT INTO books (title, author, genre, year, isbn, copies, available) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (title.strip(), author.strip(), genre.strip() or "Other", int(year),
                         isbn.strip(), int(copies), int(copies)),
                    )
                    st.success(f"Added “{title}” as book number {new_id}.")
    with tab_csv:
        st.write("Upload a CSV with the columns `title`, `author`, and optionally `genre`, `year`, `copies`.")
        st.download_button("Download template", TEMPLATE, "books_template.csv", "text/csv")
        file = st.file_uploader("CSV file", type="csv")
        if file and st.button("Import books", type="primary"):
            count, err = import_csv(file)
            if err:
                st.error(err)
            else:
                done(True, f"Imported {count} book(s).")


def page_manage(user):
    page_header("Manage", "Members, all loans and catalog clean-up.")
    tab_members, tab_loans, tab_books = st.tabs(["Members", "All loans", "Remove books"])

    with tab_members:
        users = read("SELECT id, username, full_name, email, role, joined FROM users ORDER BY id")
        st.dataframe(users, width="stretch", hide_index=True)
        others = users[users["id"] != user["id"]]
        if not others.empty:
            labels = {f"{r.full_name} (@{r.username}) — {r.role}": int(r.id) for r in others.itertuples()}
            pick = st.selectbox("Change role for", list(labels))
            role = st.selectbox("New role", ["member", "admin"])
            if st.button("Update role", type="primary"):
                run("UPDATE users SET role=? WHERE id=?", (role, labels[pick]))
                done(True, "Role updated.")

    with tab_loans:
        loans = read(
            """SELECT l.id, b.id AS book_no, b.title, u.full_name AS member,
                      l.borrowed_on, l.due_on, l.returned_on
               FROM loans l JOIN books b ON b.id = l.book_id
               JOIN users u ON u.id = l.user_id ORDER BY l.id DESC"""
        )
        if loans.empty:
            st.info("No loans yet.")
        else:
            today = str(date.today())
            loans["status"] = [
                "Returned" if pd.notna(r.returned_on) else ("Overdue" if r.due_on < today else "Active")
                for r in loans.itertuples()
            ]
            st.dataframe(loans, width="stretch", hide_index=True)

    with tab_books:
        books = read("SELECT id, title, author FROM books ORDER BY title")
        labels = {f"{r.title} — {r.author} (#{r.id})": int(r.id) for r in books.itertuples()}
        pick = st.selectbox("Book", list(labels))
        if st.button("Delete book", type="primary"):
            bid = labels[pick]
            busy = read(
                "SELECT COUNT(*) AS n FROM loans WHERE book_id=? AND returned_on IS NULL", (bid,)
            )["n"][0]
            if busy:
                done(False, "That book has active loans and can't be removed.")
            run("DELETE FROM loans WHERE book_id=?", (bid,))
            run("DELETE FROM books WHERE id=?", (bid,))
            done(True, "Book removed.")


def sidebar(user, pages):
    with st.sidebar:
        initial = html.escape(user["name"][:1].upper())
        st.markdown(
            f'<div class="user-chip"><div class="avatar">{initial}</div>'
            f'<div><b>{html.escape(user["name"])}</b><br>'
            f'<small style="opacity:.7">{html.escape(user["role"]).title()}</small></div></div>',
            unsafe_allow_html=True,
        )
        choice = st.radio("Menu", list(pages), label_visibility="collapsed")
        st.divider()
        if st.button("Log out", width="stretch"):
            st.session_state.clear()
            st.rerun()
    return choice


def main():
    init_db()
    st.markdown(CSS, unsafe_allow_html=True)
    user = st.session_state.get("user")
    if not user:
        page_auth()
        return
    pages = {
        "🏠 Dashboard": page_dashboard,
        "📖 Catalog": page_catalog,
        "🤖 Librarian Bot": page_bot,
        "🔖 My Loans": page_my_loans,
        "👤 Profile": page_profile,
    }
    if user["role"] == "admin":
        pages["➕ Add Books"] = page_add_books
        pages["🛠️ Manage"] = page_manage
    choice = sidebar(user, pages)
    flash()
    pages[choice](user)


if __name__ == "__main__":
    main()
