import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session as flask_session
from flask_login import login_user, logout_user, login_required, current_user
from models import (
    db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, TrainingPhase,
)
from extensions import bcrypt, oauth_client, login_manager, limiter

auth = Blueprint("auth", __name__)

_WHITELIST_PATH = os.path.join(
    os.path.dirname(__file__), "instance", "whitelist.txt"
)


def _email_whitelisted(email: str) -> bool:
    """Return True if email appears in instance/whitelist.txt (case-insensitive).
    If the file doesn't exist, registration is closed to everyone.
    """
    try:
        with open(_WHITELIST_PATH) as f:
            allowed = {line.strip().lower() for line in f if line.strip()}
        return email.lower() in allowed
    except FileNotFoundError:
        return False


@login_manager.user_loader
def load_user(user_id):
    return Account.query.get(int(user_id))


# ---------------------------------------------------------------------------
# Email / password routes
# ---------------------------------------------------------------------------

@auth.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    unclaimed = Account.query.filter_by(email_claimed=False).first()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        account = Account.query.filter_by(email=email).first()
        if account and account.password_hash and bcrypt.check_password_hash(account.password_hash, password):
            login_user(account, remember=bool(request.form.get("remember")))
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        flash("Invalid email or password.", "error")

    return render_template("login.html", unclaimed=unclaimed)


@auth.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
        elif not _email_whitelisted(email):
            flash("That email address is not on the invite list.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif Account.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
        else:
            account = Account(
                email=email,
                password_hash=bcrypt.generate_password_hash(password).decode("utf-8"),
                email_claimed=True,
            )
            db.session.add(account)
            db.session.commit()
            login_user(account)
            flash("Account created! Set up your profile.", "success")
            return redirect(url_for("setup"))

    return render_template("register.html")


@auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Claim flow — for the migrated legacy account
# ---------------------------------------------------------------------------

@auth.route("/claim-account", methods=["GET", "POST"])
def claim_account():
    unclaimed = Account.query.filter_by(email_claimed=False).first()
    if not unclaimed:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif Account.query.filter(Account.email == email, Account.id != unclaimed.id).first():
            flash("That email is already in use.", "error")
        else:
            unclaimed.email = email
            unclaimed.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            unclaimed.email_claimed = True
            db.session.commit()
            login_user(unclaimed)
            flash("Profile claimed! Welcome to FitLocal.", "success")
            return redirect(url_for("index"))

    return render_template("claim_account.html")


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------

@auth.route("/settings/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    account = Account.query.get(current_user.id)

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif new_password != confirm:
            flash("Passwords do not match.", "error")
        else:
            # If account already has a password, verify the current one first
            if account.password_hash:
                current_pw = request.form.get("current_password", "")
                if not bcrypt.check_password_hash(account.password_hash, current_pw):
                    flash("Current password is incorrect.", "error")
                    return render_template("change_password.html", account=account)

            account.password_hash = bcrypt.generate_password_hash(new_password).decode("utf-8")
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("settings"))

    return render_template("change_password.html", account=account)


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

@auth.route("/auth/google")
def google_login():
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        flash("Google login is not configured on this server.", "error")
        return redirect(url_for("auth.login"))

    mode = request.args.get("mode", "login")
    flask_session["oauth_mode"] = mode
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth_client.google.authorize_redirect(redirect_uri)


@auth.route("/auth/google/callback")
def google_callback():
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        flash("Google login is not configured on this server.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = oauth_client.google.authorize_access_token()
    except Exception:
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    user_info = token.get("userinfo") or oauth_client.google.userinfo()
    email = user_info["email"].lower()
    google_id = user_info["sub"]
    mode = flask_session.pop("oauth_mode", "login")

    # --- Claim mode: link Google to the unclaimed legacy account ---
    if mode == "claim":
        unclaimed = Account.query.filter_by(email_claimed=False).first()
        if unclaimed:
            conflict = Account.query.filter(
                Account.email == email, Account.id != unclaimed.id
            ).first()
            if conflict:
                flash("That Google account's email is already registered.", "error")
                return redirect(url_for("auth.claim_account"))
            unclaimed.email = email
            unclaimed.google_id = google_id
            unclaimed.email_claimed = True
            db.session.commit()
            login_user(unclaimed)
            flash("Profile claimed! Welcome to FitLocal.", "success")
            return redirect(url_for("index"))

    # --- Login / register mode ---
    account = Account.query.filter_by(google_id=google_id).first()
    if not account:
        account = Account.query.filter_by(email=email).first()
        if account:
            account.google_id = google_id  # link Google to existing email account
            db.session.commit()

    if account:
        login_user(account)
        flash("Logged in with Google.", "success")
        profile = UserProfile.query.filter_by(account_id=account.id).first()
        return redirect(url_for("setup") if not profile else url_for("index"))

    # New account via Google — check whitelist before creating
    if not _email_whitelisted(email):
        flash("That Google account is not on the invite list.", "error")
        return redirect(url_for("auth.login"))

    new_account = Account(email=email, google_id=google_id, email_claimed=True)
    db.session.add(new_account)
    db.session.commit()
    login_user(new_account)
    flash("Account created with Google! Set up your profile.", "success")
    return redirect(url_for("setup"))


# ---------------------------------------------------------------------------
# Delete account
# ---------------------------------------------------------------------------

@auth.route("/settings/delete-account", methods=["GET", "POST"])
@login_required
def delete_account():
    account = Account.query.get(current_user.id)

    if request.method == "POST":
        confirmed_email = request.form.get("confirm_email", "").strip().lower()
        if confirmed_email != account.email.lower():
            flash("Email did not match. Account not deleted.", "error")
            return render_template("delete_account.html", account=account)

        profile = UserProfile.query.filter_by(account_id=account.id).first()
        if profile:
            # Collect IDs before deleting
            session_ids = [s.id for s in WorkoutSession.query.filter_by(user_id=profile.id).all()]
            plan_ids = [p.id for p in WorkoutPlan.query.filter_by(user_id=profile.id).all()]
            planned_workout_ids = [
                pw.id for pw in PlannedWorkout.query.filter(
                    PlannedWorkout.plan_id.in_(plan_ids)
                ).all()
            ] if plan_ids else []

            # Delete in dependency order
            if session_ids:
                LoggedSet.query.filter(LoggedSet.session_id.in_(session_ids)).delete(synchronize_session=False)
            WorkoutSession.query.filter_by(user_id=profile.id).delete()
            AIReview.query.filter_by(user_id=profile.id).delete()
            FitnessTest.query.filter_by(user_id=profile.id).delete()
            if planned_workout_ids:
                PlannedExercise.query.filter(
                    PlannedExercise.planned_workout_id.in_(planned_workout_ids)
                ).delete(synchronize_session=False)
            if plan_ids:
                PlannedWorkout.query.filter(PlannedWorkout.plan_id.in_(plan_ids)).delete(synchronize_session=False)
                TrainingPhase.query.filter(TrainingPhase.plan_id.in_(plan_ids)).delete(synchronize_session=False)
                WorkoutPlan.query.filter(WorkoutPlan.id.in_(plan_ids)).delete(synchronize_session=False)
            db.session.delete(profile)

        logout_user()
        db.session.delete(account)
        db.session.commit()

        flash("Your account and all data have been permanently deleted.", "info")
        return redirect(url_for("auth.login"))

    return render_template("delete_account.html", account=account)
