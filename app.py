
import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

DB_FILE = "library.db"

st.set_page_config(page_title="Library", page_icon="📚", layout="wide")


def run(query, params=()):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.lastrowid


def read(query, params=()):
    with sqlite3.connect(DB_FILE) as conn:
        return pd.read_sql_query(query, conn, params=params)


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                joined TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id),
                member_id INTEGER NOT NULL REFERENCES members(id),
                borrowed_on TEXT NOT NULL,
                due_on TEXT NOT NULL,
                returned_on TEXT
            );
            """
        )
        if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO books (title, author, genre, year, isbn, copies, available) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    ("1984", "George Orwell", "Dystopian", 1949, "9780451524935", 3, 3),
                    ("The Hobbit", "J.R.R. Tolkien", "Fantasy", 1937, "9780547928227", 2, 2),
                    ("Dune", "Frank Herbert", "Sci-Fi", 1965, "9780441172719", 2, 2),
                    ("Pride and Prejudice", "Jane Austen", "Romance", 1813, "9780141439518", 1, 1),
                    ("Sapiens", "Yuval Noah Harari", "History", 2011, "9780062316097", 2, 2),
                ],
            )
            conn.execute(
                "INSERT INTO members (name, email, joined) VALUES (?,?,?)",
                ("Demo Member", "demo@example.com", str(date.today())),
            )
            conn.commit()


def page_dashboard():
    st.title("📚 Library Dashboard")
    books = read("SELECT * FROM books")
    active = read("SELECT * FROM loans WHERE returned_on IS NULL")
    overdue = active[active["due_on"] < str(date.today())]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Titles", len(books))
    c2.metric("Total copies", int(books["copies"].sum()) if not books.empty else 0)
    c3.metric("Active loans", len(active))
    c4.metric("Overdue", len(overdue), delta_color="inverse")

    left, right = st.columns(2)
    with left:
        st.subheader("Books by genre")
        if not books.empty:
            st.bar_chart(books.groupby("genre")["copies"].sum())
    with right:
        st.subheader("Most borrowed")
        top = read(
            """SELECT b.title, COUNT(*) AS times_borrowed
               FROM loans l JOIN books b ON b.id = l.book_id
               GROUP BY b.id ORDER BY times_borrowed DESC LIMIT 5"""
        )
        if top.empty:
            st.info("No loans yet.")
        else:
            st.bar_chart(top.set_index("title"))

    st.subheader("⚠️ Overdue loans")
    od = read(
        """SELECT l.id AS loan, b.title, m.name AS member, l.due_on
           FROM loans l JOIN books b ON b.id = l.book_id
           JOIN members m ON m.id = l.member_id
           WHERE l.returned_on IS NULL AND l.due_on < ?
           ORDER BY l.due_on""",
        (str(date.today()),),
    )
    if od.empty:
        st.success("Nothing overdue.")
    else:
        st.dataframe(od, width="stretch", hide_index=True)


def page_catalog():
    st.title("📖 Catalog")
    books = read("SELECT * FROM books")
    col1, col2, col3 = st.columns([3, 2, 1])
    query = col1.text_input("Search title, author or ISBN")
    genres = ["All"] + sorted(books["genre"].dropna().unique().tolist())
    genre = col2.selectbox("Genre", genres)
    only_avail = col3.checkbox("Available only")

    df = books.copy()
    if query:
        q = query.lower()
        mask = (
            df["title"].str.lower().str.contains(q, na=False)
            | df["author"].str.lower().str.contains(q, na=False)
            | df["isbn"].astype(str).str.contains(q, na=False)
        )
        df = df[mask]
    if genre != "All":
        df = df[df["genre"] == genre]
    if only_avail:
        df = df[df["available"] > 0]

    st.caption(f"{len(df)} book(s) found")
    st.dataframe(df, width="stretch", hide_index=True)

    with st.expander("🗑️ Remove a book"):
        if books.empty:
            st.write("No books.")
        else:
            labels = {f"{r.title} — {r.author} (#{r.id})": r.id for r in books.itertuples()}
            choice = st.selectbox("Book", list(labels))
            if st.button("Delete book", type="primary"):
                bid = labels[choice]
                in_use = read(
                    "SELECT COUNT(*) AS n FROM loans WHERE book_id=? AND returned_on IS NULL",
                    (bid,),
                )["n"][0]
                if in_use:
                    st.error("This book has active loans and can't be removed.")
                else:
                    run("DELETE FROM books WHERE id=?", (bid,))
                    st.success("Book removed.")
                    st.rerun()


def page_add_book():
    st.title("➕ Add a Book")
    with st.form("add_book", clear_on_submit=True):
        title = st.text_input("Title *")
        author = st.text_input("Author *")
        c1, c2, c3 = st.columns(3)
        genre = c1.text_input("Genre")
        year = c2.number_input("Year", 0, date.today().year, 2000)
        copies = c3.number_input("Copies", 1, 100, 1)
        isbn = st.text_input("ISBN")
        if st.form_submit_button("Add book"):
            if not title.strip() or not author.strip():
                st.error("Title and author are required.")
            else:
                run(
                    "INSERT INTO books (title, author, genre, year, isbn, copies, available) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (title.strip(), author.strip(), genre.strip(), int(year), isbn.strip(),
                     int(copies), int(copies)),
                )
                st.success(f"Added “{title}”.")


def page_members():
    st.title("👥 Members")
    st.dataframe(read("SELECT * FROM members"), width="stretch", hide_index=True)

    st.subheader("Register a new member")
    with st.form("add_member", clear_on_submit=True):
        name = st.text_input("Full name *")
        email = st.text_input("Email *")
        if st.form_submit_button("Register"):
            if not name.strip() or "@" not in email:
                st.error("Enter a name and a valid email.")
            else:
                try:
                    run(
                        "INSERT INTO members (name, email, joined) VALUES (?,?,?)",
                        (name.strip(), email.strip(), str(date.today())),
                    )
                    st.success("Member registered.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("A member with that email already exists.")


def page_loans():
    st.title("🔄 Borrow & Return")
    tab_borrow, tab_return, tab_history = st.tabs(["Borrow", "Return", "History"])

    with tab_borrow:
        books = read("SELECT * FROM books WHERE available > 0")
        members = read("SELECT * FROM members")
        if books.empty or members.empty:
            st.info("You need at least one available book and one member.")
        else:
            b_map = {f"{r.title} — {r.author} ({r.available} left)": r.id for r in books.itertuples()}
            m_map = {f"{r.name} ({r.email})": r.id for r in members.itertuples()}
            b = st.selectbox("Book", list(b_map))
            m = st.selectbox("Member", list(m_map))
            days = st.slider("Loan period (days)", 1, 60, 14)
            if st.button("Borrow", type="primary"):
                today = date.today()
                run(
                    "INSERT INTO loans (book_id, member_id, borrowed_on, due_on) VALUES (?,?,?,?)",
                    (b_map[b], m_map[m], str(today), str(today + timedelta(days=days))),
                )
                run("UPDATE books SET available = available - 1 WHERE id=?", (b_map[b],))
                st.success(f"Loan created. Due on {today + timedelta(days=days)}.")
                st.rerun()

    with tab_return:
        active = read(
            """SELECT l.id, b.id AS book_id, b.title, m.name, l.due_on
               FROM loans l JOIN books b ON b.id = l.book_id
               JOIN members m ON m.id = l.member_id
               WHERE l.returned_on IS NULL ORDER BY l.due_on"""
        )
        if active.empty:
            st.info("No active loans.")
        else:
            l_map = {
                f"{r.title} — {r.name} (due {r.due_on})": (r.id, r.book_id, r.due_on)
                for r in active.itertuples()
            }
            choice = st.selectbox("Active loan", list(l_map))
            if st.button("Mark as returned", type="primary"):
                loan_id, book_id, due = l_map[choice]
                run("UPDATE loans SET returned_on=? WHERE id=?", (str(date.today()), loan_id))
                run("UPDATE books SET available = available + 1 WHERE id=?", (book_id,))
                late = (date.today() - date.fromisoformat(due)).days
                if late > 0:
                    st.warning(f"Returned {late} day(s) late.")
                else:
                    st.success("Returned on time.")
                st.rerun()

    with tab_history:
        hist = read(
            """SELECT l.id, b.title, m.name AS member, l.borrowed_on, l.due_on, l.returned_on
               FROM loans l JOIN books b ON b.id = l.book_id
               JOIN members m ON m.id = l.member_id
               ORDER BY l.id DESC"""
        )
        st.dataframe(hist, width="stretch", hide_index=True)


def main():
    init_db()
    pages = {
        "📊 Dashboard": page_dashboard,
        "📖 Catalog": page_catalog,
        "➕ Add Book": page_add_book,
        "👥 Members": page_members,
        "🔄 Borrow & Return": page_loans,
    }
    st.sidebar.title("📚 Library")
    choice = st.sidebar.radio("Navigate", list(pages))
    st.sidebar.caption(f"Today: {date.today():%d %b %Y}")
    pages[choice]()


if __name__ == "__main__":
    main()
