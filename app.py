import os
import re
from pymongo import ReturnDocument
from datetime import date, datetime
from functools import wraps
import calendar as calendar_module
from datetime import date

from bson import ObjectId
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)


from pymongo import MongoClient, ASCENDING
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__) 
app.config["SECRET_KEY"] = os.environ.get("TIMESYNC_SECRET_KEY", "local-development-key-change-before-deployment")

if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "Set TIMESYNC_SECRET_KEY in your terminal before starting TimeSync." )

mongo_uri = os.environ.get("MONGODB_URI", "")
if not mongo_uri:
    raise RuntimeError(
        "Set MONGODB_URI to your MongoDB Atlas connection string."  )

client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
db = client["timesync"]

users = db["users"]
tasks = db["tasks"]

# Check the connection and create useful indexes.
client.admin.command("ping")
users.create_index("email", unique=True)
users.create_index("username", unique=True)
tasks.create_index([("user_id", ASCENDING), ("due_date", ASCENDING)])

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access your personal workspace.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def current_user_id():
    try:
        return ObjectId(session["user_id"])
    except (KeyError, ValueError):
        session.clear()
        abort(401)

def task_for_template(task):
    """Provide familiar fields to existing Jinja templates."""
    task["id"] = str(task["_id"])
    task["completed"] = bool(task.get("completed", False))
    task["title"] = task.get("title", "")
    task["subject"] = task.get("subject", "")
    task["due_date"] = task.get("due_date", "")
    task["priority"] = task.get("priority", "Medium")
    return task

def password_is_strong(password):
    return (
        len(password) >= 12
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password))

@app.route("/")
def welcome():
    stats = db.stats.find_one_and_update(
        {"_id": "homepage_visits"},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )

    visitor_count = stats["count"]

    return render_template(
        "home.html",
        visitor_count=visitor_count
    )

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or len(username) > 40:
            flash("Enter a username (maximum 40 characters).", "error")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address format.", "error")
        elif not password_is_strong(password):
            flash(
                "Password must be at least 12 characters and include "
                "uppercase, lowercase, a number, and a symbol.",
                "error",
            )
        elif users.find_one({
            "$or": [{"username": username}, {"email": email}]
        }):
            flash("That username or email is already registered.", "error")
        else:
            try:
                result = users.insert_one({
                    "username": username,
                    "email": email,
                    "password_hash": generate_password_hash(password),
                    "email_verified": False,
                    "created_at": datetime.utcnow(),
                })
            except Exception:
                flash("Could not create account. Check the database connection.", "error")
                return render_template("signup.html")

            session.clear()
            session["user_id"] = str(result.inserted_id)
            session["username"] = username
            flash(
                "Account created. Email format was checked, but inbox ownership "
                "has not been verified yet.",
                "info", )
            return redirect(url_for("dashboard"))
            user = db.users.find_one({
    "_id": ObjectId(session["user_id"])
})
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = users.find_one({"email": email})

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = str(user["_id"])
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))

        flash("Email or password is incorrect.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("You have logged out.", "info")
    return redirect(url_for("welcome"))

@app.route("/dashboard")
@login_required
def dashboard():
    uid = current_user_id()
    tasks_list = [
        task_for_template(t)
        for t in tasks.find({"user_id": uid}).sort("_id", -1)]

    total_tasks = len(tasks_list)
    completed_tasks = sum(1 for t in tasks_list if t["completed"])
    pending_tasks = total_tasks - completed_tasks
    progress = round(completed_tasks / total_tasks * 100) if total_tasks else 0
    today = date.today().isoformat()
    today_tasks = [
        t for t in tasks_list
        if t["due_date"] == today and not t["completed"]]
     
    return render_template(
        "index.html",
        tasks=tasks_list,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        progress=progress,
        today_tasks=today_tasks, )

@app.route("/add-task", methods=["POST"])
@login_required
def add_task():
    title = request.form.get("title", "").strip()
    subject = request.form.get("subject", "").strip()
    due_date = request.form.get("due_date", "").strip()
    priority = request.form.get("priority", "Medium")

    if not title or len(title) > 150:
        flash("Enter a task title (maximum 150 characters).", "error")
    elif due_date and not valid_date(due_date):
        flash("Choose a valid due date.", "error")
    else:
        if priority not in ("Low", "Medium", "High"):
            priority = "Medium"
        tasks.insert_one({
            "title": title,
            "subject": subject[:100],
            "due_date": due_date,
            "priority": priority,
            "completed": False,
            "user_id": current_user_id(),
            "created_at": datetime.utcnow(),
        })
        flash("Task added!", "success")

    return redirect(url_for("dashboard"))

def valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False

@app.route("/edit-task/<task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    try:
        oid = ObjectId(task_id)
    except Exception:
        abort(404)

    task = tasks.find_one({"_id": oid, "user_id": current_user_id()})
    if not task:
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        due_date = request.form.get("due_date", "").strip()
        priority = request.form.get("priority", "Medium")

        if not title or len(title) > 150:
            flash("Enter a task title (maximum 150 characters).", "error")
        elif due_date and not valid_date(due_date):
            flash("Choose a valid due date.", "error")
        else:
            if priority not in ("Low", "Medium", "High"):
                priority = "Medium"

            tasks.update_one(
                {"_id": oid, "user_id": current_user_id()},
                {"$set": {
                    "title": title,
                    "subject": subject[:100],
                    "due_date": due_date,
                    "priority": priority,
                    "updated_at": datetime.utcnow(),
                }},
            )
            flash("Task updated!", "success")
            return redirect(url_for("dashboard"))

    return render_template("edit_task.html", task=task_for_template(task))

@app.route("/toggle-task/<task_id>", methods=["POST"])
@login_required
def toggle_task(task_id):
    try:
        oid = ObjectId(task_id)
    except Exception:
        abort(404)

    task = tasks.find_one({"_id": oid, "user_id": current_user_id()})
    if not task:
        abort(404)

    tasks.update_one(
        {"_id": oid, "user_id": current_user_id()},
        {"$set": {"completed": not task.get("completed", False)}},
    )
    return redirect(url_for("dashboard"))

@app.route("/delete-task/<task_id>", methods=["POST"])
@login_required
def delete_task(task_id):
    try:
        oid = ObjectId(task_id)
    except Exception:
        abort(404)

    tasks.delete_one({"_id": oid, "user_id": current_user_id()})
    flash("Task deleted.", "info")
    return redirect(url_for("dashboard"))

@app.route("/calendar")
def calendar():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    if month < 1 or month > 12 or year < 1900 or year > 2100:
        year = today.year
        month = today.month

    # Create the calendar weeks, starting Monday
    monthdays = calendar_module.Calendar(
        firstweekday=0
    ).monthdayscalendar(year, month)

    # Calculate previous and next months
    prev_month = (
        date(year - 1, 12, 1) if month == 1
        else date(year, month - 1, 1)
    )

    next_month = (
        date(year + 1, 1, 1) if month == 12
        else date(year, month + 1, 1)
    )

    # Fetch the logged-in user's tasks from MongoDB
    tasks = list(db.tasks.find({
        "user_id": session["user_id"]
    }))
    
 
    # Group tasks by their due date
    by_day = {}

    for task in tasks:
        due_date = task.get("due_date")

        if not due_date:
            continue

        try:
            task_date = date.fromisoformat(str(due_date)[:10])

            if task_date.year == year and task_date.month == month:
                by_day.setdefault(task_date.day, []).append(task)

        except (ValueError, TypeError):
            continue

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_name=calendar_module.month_name[month],
        monthdays=monthdays,
        by_day=by_day,
        today=today,
        prev_month=prev_month,
        next_month=next_month
    )

@app.route("/timer")
@login_required
def timer():
    return render_template("timer.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        return render_template(
            "forgot_password.html",
            message="Password reset email is not configured yet."
        )

    return render_template("forgot_password.html")

@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = db.users.find_one({
        "_id": ObjectId(session["user_id"])
    })
    if user is None:
        session.clear()
        return redirect(url_for("login"))
    return render_template("settings.html", user=user)

if __name__ == "__main__":
    app.run(debug=True)
