import os
import sqlite3
import datetime
from tkinter import (
    Tk, Frame, Label, Entry, Button, StringVar, messagebox,
    ttk, Canvas, N, S, E, W, END
)

from PIL import Image, ImageTk
import barcode
from barcode.writer import ImageWriter


# =====================================================================
# CONFIG
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")
BARCODE_DIR = os.path.join(BASE_DIR, "barcodes")
DUPLICATE_WINDOW_MINUTES = 5

DEPARTMENTS = ["CT", "FBT", "BSED", "BEED", "BSFI", "BSBA", "EMPLOYEE"]
YEAR_LEVELS = ["1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year", "N/A"]

os.makedirs(BARCODE_DIR, exist_ok=True)


# =====================================================================
# DATABASE LAYER — DAGDAG: contact_number, year_level, student_number
# =====================================================================
class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                department TEXT NOT NULL,
                contact_number TEXT,
                year_level TEXT NOT NULL,
                student_number TEXT NOT NULL UNIQUE,
                registered_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                time_in TEXT,
                time_out TEXT,
                scan_date TEXT NOT NULL
            )
        """)
        self.conn.commit()

    # -- users ----------------------------------------------------
    def register_user(self, full_name, department, contact_number, year_level, student_number):
        student_number = student_number.strip()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO users 
               (full_name, department, contact_number, year_level, student_number, registered_at) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (full_name, department, contact_number, year_level, student_number, now)
        )
        self.conn.commit()
        return student_number

    def find_user_by_barcode(self, student_number):
        cur = self.conn.execute(
            """SELECT id, full_name, department, contact_number, year_level, student_number 
               FROM users WHERE student_number = ?""", (student_number,)
        )
        return cur.fetchone()

    def get_active_session(self, user_id, today):
        cur = self.conn.execute(
            "SELECT id FROM attendance WHERE user_id = ? AND scan_date = ? AND time_out IS NULL",
            (user_id, today)
        )
        return cur.fetchone()

    def time_in(self, user_id, today):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO attendance (user_id, time_in, scan_date) VALUES (?, ?, ?)",
            (user_id, now, today)
        )
        self.conn.commit()

    def time_out(self, session_id):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE attendance SET time_out = ? WHERE id = ?", (now, session_id)
        )
        self.conn.commit()

    def all_users(self):
        cur = self.conn.execute(
            "SELECT id, full_name, department, contact_number, year_level, student_number FROM users ORDER BY id DESC"
        )
        return cur.fetchall()

    # -- attendance -------------------------------------------------
    def recent_attendance(self, limit=15):
        cur = self.conn.execute("""
            SELECT users.full_name, users.department, users.year_level, 
                   attendance.time_in, attendance.time_out, attendance.scan_date
            FROM attendance
            JOIN users ON users.id = attendance.user_id
            ORDER BY attendance.id DESC
            LIMIT ?
        """, (limit,))
        return cur.fetchall()

    # -- statistics ---------------------------------------------------
    def total_users(self):
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def total_attendance(self):
        return self.conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]

    def attendance_today(self):
        today = datetime.date.today().isoformat()
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE scan_date = ?", (today,)
        )
        return cur.fetchone()[0]

    def attendance_per_user(self):
        cur = self.conn.execute("""
            SELECT users.full_name, users.department, users.student_number, COUNT(attendance.id) AS times
            FROM users
            LEFT JOIN attendance ON attendance.user_id = users.id
            GROUP BY users.id
            ORDER BY times DESC
        """)
        return cur.fetchall()

    def attendance_per_department(self):
        cur = self.conn.execute("""
            SELECT users.department,
                   COUNT(DISTINCT users.id) AS registered,
                   COUNT(attendance.id) AS times
            FROM users
            LEFT JOIN attendance ON attendance.user_id = users.id
            GROUP BY users.department
            ORDER BY times DESC
        """)
        return cur.fetchall()

    def attendance_last_n_days(self, n=7):
        today = datetime.date.today()
        counts = {}
        for i in range(n - 1, -1, -1):
            day = (today - datetime.timedelta(days=i)).isoformat()
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM attendance WHERE scan_date = ?", (day,)
            )
            counts[day] = cur.fetchone()[0]
        return counts


# =====================================================================
# BARCODE GENERATION — STUDENT NUMBER = BARCODE ID
# =====================================================================
def generate_barcode_image(student_number):
    """Generates Code128 barcode using STUDENT NUMBER directly as barcode ID."""
    code128 = barcode.get_barcode_class("code128")
    writer = ImageWriter()
    writer.set_options({
        "module_width": 0.3,
        "module_height": 12.0,
        "font_size": 8,
        "text_distance": 3.0,
        "quiet_zone": 4.0,
    })
    safe_id = student_number.replace("/", "-")
    filename_base = os.path.join(BARCODE_DIR, safe_id)
    obj = code128(student_number, writer=writer)
    saved_path = obj.save(filename_base)
    return saved_path


# =====================================================================
# TAB 1 — REGISTRATION — DAGDAG: Contact, Year Level, Student Number
# =====================================================================
class RegisterTab(Frame):
    def __init__(self, parent, db: Database):
        super().__init__(parent, padx=16, pady=16)
        self.db = db
        self.current_barcode_image = None
        self._build_ui()
        self._refresh_table()

    def _build_ui(self):
        Label(self, text="Register a new user", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 10)
        )

        Label(self, text="Full name:").grid(row=1, column=0, sticky=W)
        self.name_var = StringVar()
        name_entry = Entry(self, textvariable=self.name_var, width=30)
        name_entry.grid(row=1, column=1, sticky=W, padx=(8, 0))

        Label(self, text="Department:").grid(row=2, column=0, sticky=W, pady=(8, 0))
        self.department_var = StringVar(value=DEPARTMENTS[0])
        dept_combo = ttk.Combobox(
            self, textvariable=self.department_var, values=DEPARTMENTS,
            state="readonly", width=27
        )
        dept_combo.grid(row=2, column=1, sticky=W, padx=(8, 0), pady=(8, 0))

        Label(self, text="Year Level:").grid(row=3, column=0, sticky=W, pady=(8, 0))
        self.year_var = StringVar(value=YEAR_LEVELS[0])
        year_combo = ttk.Combobox(
            self, textvariable=self.year_var, values=YEAR_LEVELS,
            state="readonly", width=27
        )
        year_combo.grid(row=3, column=1, sticky=W, padx=(8, 0), pady=(8, 0))

        Label(self, text="Contact Number:").grid(row=4, column=0, sticky=W, pady=(8, 0))
        self.contact_var = StringVar()
        Entry(self, textvariable=self.contact_var, width=30).grid(
            row=4, column=1, sticky=W, padx=(8, 0), pady=(8, 0)
        )

        Label(self, text="Student Number:").grid(row=5, column=0, sticky=W, pady=(8, 0))
        self.student_var = StringVar()
        Entry(self, textvariable=self.student_var, width=30).grid(
            row=5, column=1, sticky=W, padx=(8, 0), pady=(8, 0)
        )

        Button(self, text="Register & Generate Barcode", command=self._register).grid(
            row=6, column=0, columnspan=2, sticky=W, pady=(12, 16)
        )

        self.barcode_label = Label(self, text="", relief="groove", width=40, height=8)
        self.barcode_label.grid(row=7, column=0, columnspan=2, sticky=W)

        Label(self, text="Registered users", font=("Segoe UI", 11, "bold")).grid(
            row=8, column=0, columnspan=2, sticky=W, pady=(20, 6)
        )

        columns = ("id", "name", "department", "year_level", "contact", "student_number")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=6)
        for col, label, width in [
            ("id", "ID", 35),
            ("name", "Name", 130),
            ("department", "Dept.", 55),
            ("year_level", "Year", 70),
            ("contact", "Contact", 90),
            ("student_number", "Student No.", 110),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.grid(row=9, column=0, columnspan=2, sticky=(N, S, E, W))

    def _register(self):
        name = self.name_var.get().strip()
        department = self.department_var.get().strip()
        year_level = self.year_var.get().strip()
        contact = self.contact_var.get().strip()
        student_number = self.student_var.get().strip()

        if not name:
            messagebox.showwarning("Missing name", "Please enter a full name.")
            return
        if not student_number:
            messagebox.showwarning("Missing Student Number", "Please enter Student Number.")
            return

        try:
            barcode_id = self.db.register_user(name, department, contact, year_level, student_number)
            image_path = generate_barcode_image(barcode_id)

            img = Image.open(image_path)
            img.thumbnail((260, 120))
            self.current_barcode_image = ImageTk.PhotoImage(img)
            self.barcode_label.config(image=self.current_barcode_image, text="")

            messagebox.showinfo(
                "Registered",
                f"Name: {name}\nDept: {department}\nYear: {year_level}\n"
                f"Contact: {contact}\nStudent No. = Barcode ID: {barcode_id}\n\n"
                f"Saved to: {image_path}"
            )
            self.name_var.set("")
            self.contact_var.set("")
            self.student_var.set("")
            self._refresh_table()

        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate", f"Student Number {student_number} already exists!")

    def _refresh_table(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for user_id, name, dept, contact, year, student_no in self.db.all_users():
            self.tree.insert("", END, values=(user_id, name, dept, year, contact, student_no))


# =====================================================================
# TAB 2 — SCAN / ATTENDANCE — DAGDAG: Time In / Time Out
# =====================================================================
class ScanTab(Frame):
    def __init__(self, parent, db: Database):
        super().__init__(parent, padx=16, pady=16)
        self.db = db
        self._build_ui()
        self._refresh_recent()

    def _build_ui(self):
        Label(self, text="Scan Student Number — 1st = Time In | 2nd = Time Out", 
              font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 10)
        )

        Label(self, text="Scan here:").grid(row=1, column=0, sticky=W)
        self.scan_var = StringVar()
        self.scan_entry = Entry(self, textvariable=self.scan_var, width=30, font=("Consolas", 12))
        self.scan_entry.grid(row=1, column=1, sticky=W, padx=(8, 0))
        self.scan_entry.bind("<Return>", lambda e: self._process_scan())
        self.scan_entry.focus()

        self.status_label = Label(self, text="Waiting for scan...", font=("Segoe UI", 11))
        self.status_label.grid(row=2, column=0, columnspan=2, sticky=W, pady=(10, 16))

        Label(self, text="Recent attendance", font=("Segoe UI", 11, "bold")).grid(
            row=3, column=0, columnspan=2, sticky=W, pady=(10, 6)
        )

        columns = ("name", "department", "year_level", "time_in", "time_out", "date")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for col, label, width in [
            ("name", "Name", 120),
            ("department", "Dept", 50),
            ("year_level", "Year", 55),
            ("time_in", "Time In", 120),
            ("time_out", "Time Out", 120),
            ("date", "Date", 90),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.grid(row=4, column=0, columnspan=2, sticky=(N, S, E, W))

    def _process_scan(self):
        code = self.scan_var.get().strip()
        self.scan_var.set("")
        self.scan_entry.focus()

        if not code:
            return

        user = self.db.find_user_by_barcode(code)
        if user is None:
            self.status_label.config(text=f"Unknown Student No.: {code}", fg="#c0392b")
            return

        user_id, name, department, contact, year, student_no = user
        today = datetime.date.today().isoformat()

        active_session = self.db.get_active_session(user_id, today)
        if active_session:
            self.db.time_out(active_session[0])
            self.status_label.config(
                text=f"⏰ TIME OUT — {name} ({department} | {year})", fg="#1565c0"
            )
        else:
            self.db.time_in(user_id, today)
            self.status_label.config(
                text=f"✅ TIME IN — {name} ({department} | {year})", fg="#2e7d32"
            )

        self._refresh_recent()

    def _refresh_recent(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for name, dept, year, time_in, time_out, date in self.db.recent_attendance():
            self.tree.insert("", END, values=(name, dept, year, time_in or "-", time_out or "-", date))


# =====================================================================
# TAB 3 — STATISTICS — WALANG BINAGO, DAGDAG LANG
# =====================================================================
class StatsTab(Frame):
    def __init__(self, parent, db: Database):
        super().__init__(parent, padx=20, pady=20)
        self.db = db
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        Label(self, text="Attendance statistics", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=W, pady=(0, 10)
        )

        self.total_users_label = Label(self, text="", font=("Segoe UI", 10))
        self.total_users_label.grid(row=1, column=0, sticky=W)

        self.today_label = Label(self, text="", font=("Segoe UI", 10))
        self.today_label.grid(row=1, column=1, sticky=W)

        self.all_time_label = Label(self, text="", font=("Segoe UI", 10))
        self.all_time_label.grid(row=1, column=2, sticky=W)

        Button(self, text="Refresh", command=self.refresh).grid(
            row=2, column=0, sticky=W, pady=(8, 16)
        )

        Label(self, text="Last 7 days", font=("Segoe UI", 11, "bold")).grid(
            row=3, column=0, columnspan=3, sticky=W
        )
        self.chart_canvas = Canvas(self, width=420, height=160, bg="white", highlightthickness=1,
                                    highlightbackground="#cccccc")
        self.chart_canvas.grid(row=4, column=0, columnspan=3, pady=(6, 20), sticky=W)

        Label(self, text="Attendance per department", font=("Segoe UI", 11, "bold")).grid(
            row=5, column=0, columnspan=3, sticky=W, pady=(0, 6)
        )
        dept_columns = ("department", "registered", "times")
        self.dept_tree = ttk.Treeview(self, columns=dept_columns, show="headings", height=6)
        for col, label, width in [
            ("department", "Department", 110),
            ("registered", "Registered users", 130),
            ("times", "Total attendance", 130),
        ]:
            self.dept_tree.heading(col, text=label)
            self.dept_tree.column(col, width=width)
        self.dept_tree.grid(row=6, column=0, columnspan=3, sticky=(N, S, E, W), pady=(0, 20))

        Label(self, text="Attendance per user", font=("Segoe UI", 11, "bold")).grid(
            row=7, column=0, columnspan=3, sticky=W, pady=(0, 6)
        )
        columns = ("name", "department", "student_number", "times")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=8)
        for col, label, width in [
            ("name", "Name", 150),
            ("department", "Dept.", 60),
            ("student_number", "Student No.", 110),
            ("times", "Times attended", 100),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.grid(row=8, column=0, columnspan=3, sticky=(N, S, E, W))

    def refresh(self):
        self.total_users_label.config(text=f"Registered users: {self.db.total_users()}")
        self.today_label.config(text=f"Attendance today: {self.db.attendance_today()}")
        self.all_time_label.config(text=f"All-time attendance: {self.db.total_attendance()}")

        for row in self.dept_tree.get_children():
            self.dept_tree.delete(row)
        for department, registered, times in self.db.attendance_per_department():
            self.dept_tree.insert("", END, values=(department, registered, times))

        for row in self.tree.get_children():
            self.tree.delete(row)
        for name, department, student_no, times in self.db.attendance_per_user():
            self.tree.insert("", END, values=(name, department, student_no, times))

        self._draw_chart()

    def _draw_chart(self):
        self.chart_canvas.delete("all")
        counts = self.db.attendance_last_n_days(7)
        days = list(counts.keys())
        values = list(counts.values())
        max_val = max(values) if values and max(values) > 0 else 1

        bottom = 160 - 30
        bar_width = 40
        gap = 20
        x = 20

        for day, value in zip(days, values):
            bar_height = int((value / max_val) * (bottom - 20))
            y_top = bottom - bar_height
            self.chart_canvas.create_rectangle(
                x, y_top, x + bar_width, bottom, fill="#3b8bd4", outline=""
            )
            self.chart_canvas.create_text(
                x + bar_width / 2, y_top - 10, text=str(value), font=("Segoe UI",)
            )
            short_day = day[5:]
            self.chart_canvas.create_text(
                x + bar_width / 2, bottom + 12, text=short_day, font=("Segoe UI", 7)
            )
            x += bar_width + gap


# =====================================================================
# MAIN APP
# =====================================================================
class AttendanceApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("SLSU-JGE LIBRARY Attendance System")
        self.geometry("650x750")

        self.db = Database(DB_PATH)

        notebook = ttk.Notebook(self)
        notebook.pack(expand=True, fill="both")

        notebook.add(RegisterTab(notebook, self.db), text="Register")
        notebook.add(ScanTab(notebook, self.db), text="Scan / Attendance")
        notebook.add(StatsTab(notebook, self.db), text="Statistics")


if __name__ == "__main__":
    app = AttendanceApp()
    app.mainloop()