import json
import os
import calendar as cal_module
from datetime import datetime, date, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_login import login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)

_secret = os.environ.get("SECRET_KEY", "fitlocal-dev-key-change-me")
if _secret == "fitlocal-dev-key-change-me" and os.environ.get("FLASK_ENV") == "production":
    raise RuntimeError("SECRET_KEY must be set to a strong random value in production.")
app.secret_key = _secret

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///fitlocal.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# Session / cookie hardening
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Only mark cookies Secure when running behind HTTPS (set HTTPS=true in prod .env)
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS", "false").lower() == "true"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["WTF_CSRF_TIME_LIMIT"] = 8 * 3600  # 8 hours in seconds

# Trust one layer of reverse-proxy headers (Opalstack nginx)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

from models import (  # noqa: E402
    db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, TrainingPhase, ExerciseLibrary,
    NextWorkoutNote,
)
from extensions import login_manager, bcrypt, csrf, limiter, oauth_client  # noqa: E402

db.init_app(app)
login_manager.init_app(app)
bcrypt.init_app(app)
csrf.init_app(app)
limiter.init_app(app)
oauth_client.init_app(app)

# Register Google OAuth provider (skipped if credentials not set)
if os.environ.get("GOOGLE_CLIENT_ID"):
    oauth_client.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

from auth import auth as auth_blueprint  # noqa: E402
app.register_blueprint(auth_blueprint)


@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Strict CSP — relaxed only enough for inline styles used in templates
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "frame-ancestors 'none';"
    )
    if app.config.get("SESSION_COOKIE_SECURE"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


with app.app_context():
    # Add account_id column to user_profile if it doesn't exist yet
    # (db.create_all won't alter existing tables)
    with db.engine.connect() as conn:
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        if insp.has_table("user_profile"):
            cols = [c["name"] for c in insp.get_columns("user_profile")]
            if "account_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE user_profile ADD COLUMN account_id INTEGER REFERENCES account(id)"
                ))
                conn.commit()

        # Add status column to workout_plan if it doesn't exist, backfilling from is_active + notes
        if insp.has_table("workout_plan"):
            wp_cols = [c["name"] for c in insp.get_columns("workout_plan")]
            if "status" not in wp_cols:
                conn.execute(text(
                    "ALTER TABLE workout_plan ADD COLUMN status VARCHAR(20) DEFAULT 'inactive'"
                ))
                conn.execute(text("""
                    UPDATE workout_plan SET status = CASE
                        WHEN is_active = 1 THEN 'active'
                        WHEN notes = 'pending' THEN 'pending'
                        ELSE 'inactive'
                    END
                """))
                conn.commit()

        # Add status, elapsed_seconds, superset_exercises columns to workout_session if missing
        if insp.has_table("workout_session"):
            ws_cols = [c["name"] for c in insp.get_columns("workout_session")]
            if "status" not in ws_cols:
                conn.execute(text(
                    "ALTER TABLE workout_session ADD COLUMN status VARCHAR(20) DEFAULT 'completed'"
                ))
                conn.commit()
            if "elapsed_seconds" not in ws_cols:
                conn.execute(text(
                    "ALTER TABLE workout_session ADD COLUMN elapsed_seconds INTEGER DEFAULT 0"
                ))
                conn.commit()
            if "superset_exercises" not in ws_cols:
                conn.execute(text(
                    "ALTER TABLE workout_session ADD COLUMN superset_exercises TEXT"
                ))
                conn.commit()
            if "phase_name" not in ws_cols:
                conn.execute(text(
                    "ALTER TABLE workout_session ADD COLUMN phase_name VARCHAR(100)"
                ))
                conn.commit()

        # Add order_index and is_superset_default to planned_exercise if missing
        if insp.has_table("planned_exercise"):
            pe_cols = [c["name"] for c in insp.get_columns("planned_exercise")]
            if "order_index" not in pe_cols:
                conn.execute(text(
                    "ALTER TABLE planned_exercise ADD COLUMN order_index INTEGER DEFAULT 0"
                ))
                conn.commit()
            if "is_superset_default" not in pe_cols:
                conn.execute(text(
                    "ALTER TABLE planned_exercise ADD COLUMN is_superset_default BOOLEAN DEFAULT 0"
                ))
                conn.commit()

        # Add weight_b and reps_b to logged_set if missing
        if insp.has_table("logged_set"):
            ls_cols = [c["name"] for c in insp.get_columns("logged_set")]
            if "weight_b" not in ls_cols:
                conn.execute(text("ALTER TABLE logged_set ADD COLUMN weight_b FLOAT"))
                conn.commit()
            if "reps_b" not in ls_cols:
                conn.execute(text("ALTER TABLE logged_set ADD COLUMN reps_b INTEGER"))
                conn.commit()

    db.create_all()

    from migrate import migrate as _run_migrations
    _run_migrations()

    # One-time migration: link an existing UserProfile to a new Account
    # so legacy data is not lost when auth is first enabled.
    if Account.query.count() == 0:
        existing_profile = UserProfile.query.first()
        if existing_profile and existing_profile.account_id is None:
            migrated = Account(
                email="local@fitlocal.local",
                email_claimed=False,
                is_admin=True,
            )
            db.session.add(migrated)
            db.session.flush()
            existing_profile.account_id = migrated.id
            db.session.commit()


@app.before_request
def redirect_unclaimed_account():
    """Block all non-auth requests until the legacy migrated account is claimed."""
    if request.endpoint is None:
        return
    if request.endpoint.startswith("static") or request.endpoint.startswith("auth."):
        return
    unclaimed = Account.query.filter_by(email_claimed=False).first()
    if unclaimed:
        return redirect(url_for("auth.claim_account"))


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SESSION_STATUS_COMPLETED = 'completed'
SESSION_STATUS_PAUSED = 'paused'


def get_profile():
    if not current_user.is_authenticated:
        return None
    return UserProfile.query.filter_by(account_id=current_user.id).first()


def get_active_plan(user_id):
    return WorkoutPlan.query.filter_by(status="active", user_id=user_id).first()


def _phase_workout_count(phase_data, days_per_week):
    """Number of workouts in a phase. Read from plan_json: a phase spanning
    `num_weeks` weeks contains num_weeks * days_per_week workouts. This is a
    plain count of workouts — no calendar/date math is used for positioning."""
    week_start = phase_data.get("week_start", 1)
    week_end = phase_data.get("week_end", week_start)
    num_weeks = (week_end - week_start) + 1
    return max(num_weeks, 1) * days_per_week


def resolve_plan_position(profile_id, active_plan):
    """Resolve where the user is in the plan, purely from completed-session
    phase tags — never from weeks, dates, or session_offset.

    The current phase is the phase of the most recently completed session. The
    number of completed sessions tagged with that phase says how far through it
    the user is; once that count reaches the phase's workout total, the next
    workout becomes the first workout of the next phase. Within a phase, the
    next workout is the one AFTER the last logged workout in plan order (so the
    workout switcher / out-of-order logging behaves correctly).

    Returns a dict (or None if there's no usable plan) with keys:
      phase_data, phase_index (0-based), workouts_done_in_phase,
      phase_total_workouts, next_workout (PlannedWorkout), workouts.
    """
    if not active_plan:
        return None

    workouts = PlannedWorkout.query.filter_by(
        plan_id=active_plan.id
    ).order_by(PlannedWorkout.order_index).all()
    if not workouts:
        return None

    days_per_week = active_plan.days_per_week or 3
    workout_ids = [w.id for w in workouts]
    pos_by_id = {w.id: i for i, w in enumerate(workouts)}

    try:
        plan_data = json.loads(active_plan.plan_json or "{}")
        phases_data = plan_data.get("phases", [])
    except Exception:
        phases_data = []

    base_filter = (
        WorkoutSession.user_id == profile_id,
        WorkoutSession.planned_workout_id.in_(workout_ids),
        WorkoutSession.status == SESSION_STATUS_COMPLETED,
    )

    def _last_session(*extra):
        return (
            WorkoutSession.query
            .filter(*base_filter, *extra)
            .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())
            .first()
        )

    def _next_after(session):
        """The workout after `session`'s workout in plan order, or workouts[0]."""
        if session is not None and session.planned_workout_id in pos_by_id:
            return workouts[(pos_by_id[session.planned_workout_id] + 1) % len(workouts)]
        return workouts[0]

    # No phases at all — simple sequential cycling after the last logged workout.
    if not phases_data:
        total = WorkoutSession.query.filter(*base_filter).count()
        return {
            "phase_data": None,
            "phase_index": 0,
            "workouts_done_in_phase": total,
            "phase_total_workouts": None,
            "next_workout": _next_after(_last_session()) if total else workouts[0],
            "workouts": workouts,
        }

    name_to_index = {p.get("phase_name"): i for i, p in enumerate(phases_data)}

    def _count_in_phase(phase_name):
        return WorkoutSession.query.filter(
            *base_filter, WorkoutSession.phase_name == phase_name
        ).count()

    # Current phase = the phase of the most recently completed, tagged session.
    last_tagged = _last_session(WorkoutSession.phase_name.isnot(None))
    if last_tagged and last_tagged.phase_name in name_to_index:
        idx = name_to_index[last_tagged.phase_name]
    else:
        idx = 0  # legacy / untagged history: start at the first phase

    done = _count_in_phase(phases_data[idx].get("phase_name"))

    # Advance past any phase whose workout total is already complete.
    guard = 0
    while done >= _phase_workout_count(phases_data[idx], days_per_week) and guard <= len(phases_data):
        if idx + 1 < len(phases_data):
            idx += 1
            done = _count_in_phase(phases_data[idx].get("phase_name"))
        else:
            idx = 0   # plan complete — wrap to the first phase
            done = 0
            break
        guard += 1

    # Within-phase: next workout follows the last one logged in THIS phase.
    # Freshly entered phase (done == 0) starts at the first workout.
    if done == 0:
        next_workout = workouts[0]
    else:
        next_workout = _next_after(
            _last_session(WorkoutSession.phase_name == phases_data[idx].get("phase_name"))
        )

    return {
        "phase_data": phases_data[idx],
        "phase_index": idx,
        "workouts_done_in_phase": done,
        "phase_total_workouts": _phase_workout_count(phases_data[idx], days_per_week),
        "next_workout": next_workout,
        "workouts": workouts,
    }


def get_current_phase(profile_id, active_plan):
    """Return the TrainingPhase ORM row for the user's current phase, resolved
    from completed-session phase tags (not the calendar). Matched by name so
    templates keep working; returns None if no match."""
    pos = resolve_plan_position(profile_id, active_plan)
    if not pos or not pos.get("phase_data"):
        return None
    phase_name = pos["phase_data"].get("phase_name")
    for phase in active_plan.phases:
        if phase.phase_name == phase_name:
            return phase
    return None


def update_plan_week(plan):
    """Maintain the calendar-derived current_week column for the descriptive
    "Week N" label shown on the plan/nutrition pages. NOTE: this is purely
    cosmetic — plan position and the next workout are determined from logged
    workouts, never from current_week."""
    if not plan or not plan.start_date:
        return
    days_elapsed = (date.today() - plan.start_date).days
    week = (days_elapsed // 7) + 1
    plan.current_week = min(week, plan.total_weeks or week)


def _plan_total_sessions(plan_json_data):
    """Total expected sessions for a plan (phases × weeks × days_per_week)."""
    phases = plan_json_data.get("phases", [])
    days_per_week = plan_json_data.get("days_per_week", 3)
    if phases:
        return sum(
            (p.get("week_end", p.get("week_start", 1)) - p.get("week_start", 1) + 1) * days_per_week
            for p in phases
        )
    return len(plan_json_data.get("workouts", []))


def _session_offset_for_workout(workout_index, phases, days_per_week):
    """Effective session count that makes get_next_workout return the workout at workout_index."""
    if not phases:
        return workout_index
    block_index = workout_index // days_per_week
    within_block = workout_index % days_per_week
    sessions_before = 0
    for i, phase in enumerate(phases):
        if i >= block_index:
            break
        num_weeks = phase.get("week_end", phase.get("week_start", 1)) - phase.get("week_start", 1) + 1
        sessions_before += num_weeks * days_per_week
    return sessions_before + within_block


def _compute_suggested_start(old_plan, old_session_count, new_plan_json):
    """Suggest a starting workout index in the new plan proportional to progress in the old plan."""
    old_pj = json.loads(old_plan.plan_json or "{}")
    old_total = _plan_total_sessions(old_pj)
    if old_total == 0:
        return 0
    fraction = min(old_session_count / old_total, 1.0)

    new_phases = new_plan_json.get("phases", [])
    new_workouts = new_plan_json.get("workouts", [])
    new_days = new_plan_json.get("days_per_week", 3)
    new_total = _plan_total_sessions(new_plan_json)
    if new_total == 0 or not new_workouts:
        return 0

    target_session = int(fraction * new_total)

    if new_phases:
        workout_offset = 0
        sessions_accounted = 0
        for phase in new_phases:
            num_weeks = phase.get("week_end", phase.get("week_start", 1)) - phase.get("week_start", 1) + 1
            phase_sessions = num_weeks * new_days
            if target_session < sessions_accounted + phase_sessions:
                within_block = (target_session - sessions_accounted) % new_days
                return min(workout_offset + within_block, len(new_workouts) - 1)
            sessions_accounted += phase_sessions
            workout_offset += new_days
        return 0
    else:
        return min(target_session % len(new_workouts), len(new_workouts) - 1)


def get_paused_session(profile_id):
    """Return the most recent paused WorkoutSession for this user, or None."""
    return (
        WorkoutSession.query
        .filter_by(user_id=profile_id, status=SESSION_STATUS_PAUSED)
        .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())
        .first()
    )


def get_plan_position(profile_id, active_plan):
    """Where the user is in their plan, as a display dict, or None.

    Workout-based and date-free. Keys: phase_name, is_recovery, phase_index
    (1-based), workout_in_phase (1-based position of the upcoming workout),
    phase_total_workouts (or None when the plan has no phases).
    """
    pos = resolve_plan_position(profile_id, active_plan)
    if not pos:
        return None

    phase_data = pos["phase_data"]
    done = pos["workouts_done_in_phase"]

    if phase_data is None:
        return {
            'phase_index': 1,
            'phase_name': 'Training',
            'is_recovery': False,
            'workout_in_phase': done + 1,
            'phase_total_workouts': None,
        }

    total = pos["phase_total_workouts"]
    return {
        'phase_index': pos["phase_index"] + 1,
        'phase_name': phase_data.get("phase_name", f"Phase {pos['phase_index'] + 1}"),
        'is_recovery': phase_data.get("phase_type") == 'recovery',
        'workout_in_phase': min(done + 1, total) if total else done + 1,
        'phase_total_workouts': total,
    }


def get_next_workout(profile_id, active_plan):
    """Return the next PlannedWorkout: the workout after the last one logged in
    the current phase, or the first workout of the next phase once the current
    phase's workout total is complete. Purely workout-count based — no weeks,
    dates, or session_offset."""
    pos = resolve_plan_position(profile_id, active_plan)
    if not pos:
        return None
    return pos["next_workout"]


def get_last_performance(user_id, exercise_name):
    """Get the last logged sets for a specific exercise."""
    last_session = (
        WorkoutSession.query
        .join(LoggedSet)
        .filter(
            WorkoutSession.user_id == user_id,
            LoggedSet.exercise_name == exercise_name,
            WorkoutSession.status == SESSION_STATUS_COMPLETED,
        )
        .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())
        .first()
    )
    if not last_session:
        return None
    sets = [s for s in last_session.logged_sets if s.exercise_name == exercise_name]
    if not sets:
        return None
    return {
        "date": last_session.date,
        "sets": {
            s.set_number: {
                "weight": s.weight_lbs,
                "reps": s.reps_completed,
                "weight_b": s.weight_b,
                "reps_b": s.reps_b,
                "rpe": s.rpe,
                "notes": s.notes or "",
            }
            for s in sets
        },
    }


def get_recent_performance(user_id, exercise_name, limit=3):
    """Get the last N sessions for a specific exercise, for history tooltips."""
    sessions = (
        WorkoutSession.query
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.logged_sets.any(LoggedSet.exercise_name == exercise_name),
            WorkoutSession.status == SESSION_STATUS_COMPLETED,
        )
        .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for session in sessions:
        sets = [s for s in session.logged_sets if s.exercise_name == exercise_name]
        if sets:
            result.append({
                "date": session.date,
                "sets": {
                    s.set_number: {
                        "weight": s.weight_lbs,
                        "reps": s.reps_completed,
                        "weight_b": s.weight_b,
                        "reps_b": s.reps_b,
                        "rpe": s.rpe,
                        "notes": s.notes or "",
                    }
                    for s in sets
                },
            })
    return result


def update_streak(profile):
    """Update the user's workout streak after logging a session."""
    today = date.today()
    if profile.last_workout_date == today:
        return  # Already logged today

    if profile.last_workout_date:
        days_gap = (today - profile.last_workout_date).days
        # Allow up to 3 days between workouts before breaking the streak
        if days_gap <= 3:
            profile.current_streak += 1
        else:
            profile.current_streak = 1
    else:
        profile.current_streak = 1

    if profile.current_streak > profile.longest_streak:
        profile.longest_streak = profile.current_streak

    profile.last_workout_date = today


def get_mini_calendar(user_id):
    """Get last 7 days with workout completion status."""
    today = date.today()
    days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        has_workout = WorkoutSession.query.filter_by(
            user_id=user_id, date=d
        ).first() is not None
        days.append({"date": d, "completed": has_workout, "is_today": d == today})
    return days


PHASE_COLORS = [
    "#4CAF50", "#2196F3", "#FF9800", "#9C27B0",
    "#F44336", "#00BCD4", "#FF5722", "#607D8B",
]
NO_PHASE_COLOR = "#555555"


def build_phase_color_map(active_plan):
    """Return {phase_name: color} from the plan's phases list."""
    if not active_plan:
        return {}
    try:
        plan_data = json.loads(active_plan.plan_json or "{}")
        phases = [p.get("phase_name", "") for p in plan_data.get("phases", []) if p.get("phase_name")]
    except Exception:
        phases = []
    return {name: PHASE_COLORS[i % len(PHASE_COLORS)] for i, name in enumerate(phases)}


def build_month_calendar(profile_id, year, month, phase_color_map):
    """Build a Sun-Sat calendar grid for the given month.

    Each day is None (padding) or a dict with keys:
        day, date, is_today, sessions=[{id, phase_name, color}]
    """
    today = date.today()
    first_day = date(year, month, 1)
    last_day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year + 1, 1, 1) - timedelta(days=1)

    sessions = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile_id,
        WorkoutSession.date >= first_day,
        WorkoutSession.date <= last_day,
        WorkoutSession.status == SESSION_STATUS_COMPLETED,
    ).order_by(WorkoutSession.date, WorkoutSession.id).all()

    # Group sessions by date
    sessions_by_date = {}
    for s in sessions:
        sessions_by_date.setdefault(s.date, []).append({
            "id": s.id,
            "phase_name": s.phase_name,
            "workout_name": s.planned_workout.workout_name if s.planned_workout else None,
            "color": phase_color_map.get(s.phase_name, NO_PHASE_COLOR) if s.phase_name else NO_PHASE_COLOR,
        })

    # Build Sun-Sat grid (firstweekday=6 means Sunday)
    cal = cal_module.Calendar(firstweekday=6)
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        week_data = []
        for day_num in week:
            if day_num == 0:
                week_data.append(None)
            else:
                d = date(year, month, day_num)
                week_data.append({
                    "day": day_num,
                    "date": d,
                    "is_today": d == today,
                    "sessions": sessions_by_date.get(d, []),
                })
        weeks.append(week_data)
    return weeks


def _prev_next_month(year, month):
    """Return (prev_year, prev_month, next_year, next_month) for calendar nav."""
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return prev_year, prev_month, next_year, next_month


@app.route("/")
@login_required
def index():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan(profile.id)

    if active_plan:
        update_plan_week(active_plan)
        db.session.commit()

    next_workout = get_next_workout(profile.id, active_plan) if active_plan else None

    # Current phase and nutrition
    current_phase = get_current_phase(profile.id, active_plan) if active_plan else None

    # Stats
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    days_trained = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.date >= week_start,
        WorkoutSession.status == SESSION_STATUS_COMPLETED
    ).count()

    last_session = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.status == SESSION_STATUS_COMPLETED
    ).order_by(
        WorkoutSession.date.desc()
    ).first()

    mini_cal = get_mini_calendar(profile.id)

    paused_session = get_paused_session(profile.id)
    plan_position = get_plan_position(profile.id, active_plan) if active_plan else None

    # Monthly calendar
    cal_year = request.args.get("cal_year", today.year, type=int)
    cal_month = request.args.get("cal_month", today.month, type=int)
    phase_color_map = build_phase_color_map(active_plan)
    cal_weeks = build_month_calendar(profile.id, cal_year, cal_month, phase_color_map)
    cal_month_name = cal_module.month_name[cal_month]
    cal_prev_year, cal_prev_month, cal_next_year, cal_next_month = _prev_next_month(cal_year, cal_month)

    next_workout_name = next_workout.workout_name if next_workout else None
    next_general_note, next_specific_note = (
        _get_next_workout_notes(profile.id, next_workout_name)
        if next_workout_name else (None, None)
    )

    return render_template(
        "index.html",
        profile=profile,
        active_plan=active_plan,
        next_workout=next_workout,
        days_trained=days_trained,
        last_session=last_session,
        current_phase=current_phase,
        mini_cal=mini_cal,
        paused_session=paused_session,
        plan_position=plan_position,
        cal_weeks=cal_weeks,
        cal_year=cal_year,
        cal_month=cal_month,
        cal_month_name=cal_month_name,
        cal_prev_year=cal_prev_year,
        cal_prev_month=cal_prev_month,
        cal_next_year=cal_next_year,
        cal_next_month=cal_next_month,
        phase_color_map=phase_color_map,
        next_general_note=next_general_note,
        next_specific_note=next_specific_note,
    )


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    profile = get_profile()
    if request.method == "POST":
        if profile:
            profile.name = request.form["name"]
            profile.age = int(request.form["age"])
            profile.sex = request.form["sex"]
            profile.fitness_level = request.form["fitness_level"]
            profile.goals = request.form["goals"]
            profile.updated_at = datetime.now(timezone.utc)
        else:
            profile = UserProfile(
                account_id=current_user.id,
                name=request.form["name"],
                age=int(request.form["age"]),
                sex=request.form["sex"],
                fitness_level=request.form["fitness_level"],
                goals=request.form["goals"],
            )
            db.session.add(profile)
        db.session.commit()
        flash("Profile saved!", "success")
        return redirect(url_for("generate_plan"))
    return render_template("setup.html", profile=profile)


def get_pending_plan(profile):
    """Get a pending (not yet activated) plan from the database."""
    return WorkoutPlan.query.filter_by(
        user_id=profile.id, status="pending"
    ).order_by(WorkoutPlan.created_at.desc()).first()


@app.route("/generate-plan")
@login_required
def generate_plan():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    pending = get_pending_plan(profile)
    pending_plan = json.loads(pending.plan_json) if pending else None

    suggested_start_index = 0
    if pending_plan:
        old_plan = get_active_plan(profile.id)
        if old_plan:
            old_session_count = WorkoutSession.query.filter(
                WorkoutSession.user_id == profile.id,
                WorkoutSession.planned_workout_id.in_([w.id for w in old_plan.planned_workouts])
            ).count()
            suggested_start_index = _compute_suggested_start(old_plan, old_session_count, pending_plan)

    past_plan_records = (
        WorkoutPlan.query
        .filter(WorkoutPlan.user_id == profile.id, WorkoutPlan.status.in_(["active", "inactive"]))
        .order_by(WorkoutPlan.created_at.desc())
        .all()
    )
    past_plans = []
    for p in past_plan_records:
        try:
            pj = json.loads(p.plan_json or "{}")
        except Exception:
            pj = {}
        session_count = WorkoutSession.query.filter(
            WorkoutSession.user_id == profile.id,
            WorkoutSession.planned_workout_id.in_([w.id for w in p.planned_workouts]),
            WorkoutSession.status == SESSION_STATUS_COMPLETED,
        ).count()
        past_plans.append({
            "plan": p,
            "plan_json": pj,
            "session_count": session_count,
            "workouts": PlannedWorkout.query.filter_by(plan_id=p.id).order_by(PlannedWorkout.order_index).all(),
        })

    return render_template("generate_plan.html", profile=profile, pending_plan=pending_plan,
                           suggested_start_index=suggested_start_index, past_plans=past_plans)


@app.route("/generate-plan/generate", methods=["POST"])
@login_required
def generate_plan_api():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    # Get latest fitness test if available
    fitness_test = FitnessTest.query.filter_by(
        user_id=profile.id
    ).order_by(FitnessTest.test_date.desc()).first()

    from ai import generate_workout_plan
    try:
        plan_data = generate_workout_plan(profile, fitness_test=fitness_test)

        # Remove any old pending plans
        WorkoutPlan.query.filter_by(user_id=profile.id, status="pending").delete()

        pending = WorkoutPlan(
            user_id=profile.id,
            name=plan_data["plan_name"],
            description=plan_data.get("description", ""),
            days_per_week=plan_data.get("days_per_week", 3),
            plan_json=json.dumps(plan_data),
            status="pending",
            total_weeks=plan_data.get("total_weeks", 12),
        )
        db.session.add(pending)
        db.session.commit()
        flash("Plan generated! Review it below.", "success")
    except Exception as e:
        flash(f"Error generating plan: {str(e)}", "error")

    return redirect(url_for("generate_plan"))


@app.route("/generate-plan/confirm", methods=["POST"])
@login_required
def confirm_plan():
    profile = get_profile()
    pending = get_pending_plan(profile) if profile else None
    if not pending or not profile:
        flash("No plan to activate.", "error")
        return redirect(url_for("generate_plan"))

    pending_plan = json.loads(pending.plan_json)

    start_workout_index = request.form.get("start_workout_index", 0, type=int)
    plan_phases = pending_plan.get("phases", [])
    plan_days_per_week = pending_plan.get("days_per_week", 3)
    offset = _session_offset_for_workout(start_workout_index, plan_phases, plan_days_per_week)

    db.session.delete(pending)

    # Deactivate existing plans for this user only
    WorkoutPlan.query.filter_by(status="active", user_id=profile.id).update({"status": "inactive"})

    plan = WorkoutPlan(
        user_id=profile.id,
        name=pending_plan["plan_name"],
        description=pending_plan.get("description", ""),
        days_per_week=pending_plan.get("days_per_week", 3),
        plan_json=json.dumps(pending_plan),
        status="active",
        total_weeks=pending_plan.get("total_weeks", 12),
        current_week=1,
        start_date=date.today(),
        session_offset=offset,
    )
    db.session.add(plan)
    db.session.flush()

    # Create training phases
    for i, phase_data in enumerate(pending_plan.get("phases", [])):
        tp = TrainingPhase(
            plan_id=plan.id,
            phase_name=phase_data["phase_name"],
            phase_type=phase_data.get("phase_type", "progressive"),
            week_start=phase_data["week_start"],
            week_end=phase_data["week_end"],
            description=phase_data.get("description", ""),
            nutrition_guide=phase_data.get("nutrition_guide", ""),
            order_index=i,
        )
        db.session.add(tp)

    # Create planned workouts and exercises
    for i, workout_data in enumerate(pending_plan.get("workouts", [])):
        pw = PlannedWorkout(
            plan_id=plan.id,
            day_of_week=workout_data["day"],
            workout_name=workout_data["name"],
            order_index=i,
        )
        db.session.add(pw)
        db.session.flush()

        for exercise_data in workout_data.get("exercises", []):
            lib_entry = ExerciseLibrary.query.filter(
                db.func.lower(ExerciseLibrary.name) == exercise_data["name"].lower()
            ).first()
            pe = PlannedExercise(
                planned_workout_id=pw.id,
                exercise_name=exercise_data["name"],
                exercise_library_id=lib_entry.id if lib_entry else None,
                sets_prescribed=exercise_data["sets"],
                reps_prescribed=str(exercise_data["reps"]),
                rest_seconds=exercise_data.get("rest_seconds"),
                notes=exercise_data.get("notes", ""),
                exercise_type=exercise_data.get("type", "main"),
                form_cues=exercise_data.get("form_cues", ""),
            )
            db.session.add(pe)

    db.session.commit()
    flash("Workout plan activated!", "success")
    return redirect(url_for("index"))


@app.route("/workout/paused-choice")
@login_required
def workout_paused_choice():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))
    paused_session = get_paused_session(profile.id)
    if not paused_session:
        return redirect(url_for("workout_today"))
    return render_template("workout_paused_choice.html", paused_session=paused_session)


@app.route("/workout/today")
@login_required
def workout_today():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    paused_session = get_paused_session(profile.id)
    if paused_session:
        return redirect(url_for("workout_paused_choice"))

    active_plan = get_active_plan(profile.id)
    if not active_plan:
        flash("No active plan. Generate one first!", "error")
        return redirect(url_for("generate_plan"))

    # The switcher passes ?show=<index> to display a chosen workout for today.
    # This is a display-only override — it does NOT change the plan position,
    # which is driven entirely by what gets logged.
    show_idx = request.args.get("show", type=int)
    next_workout = None
    if show_idx is not None:
        plan_workouts = (
            PlannedWorkout.query.filter_by(plan_id=active_plan.id)
            .order_by(PlannedWorkout.order_index).all()
        )
        if 0 <= show_idx < len(plan_workouts):
            next_workout = plan_workouts[show_idx]
    if next_workout is None:
        next_workout = get_next_workout(profile.id, active_plan)
    if not next_workout:
        flash("No workouts found in your plan. Try regenerating it.", "error")
        return redirect(url_for("index"))

    ctx = _build_workout_context(profile, next_workout, active_plan)
    return render_template("workout_today.html", **ctx)


@app.route("/workout/choose", methods=["POST"])
@login_required
def choose_workout():
    """Switcher: show a different workout for today. Display-only — it does not
    persist any position change (the plan position follows what you log)."""
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))
    active_plan = get_active_plan(profile.id)
    if not active_plan:
        return redirect(url_for("generate_plan"))

    workout_index = request.form.get("workout_index", 0, type=int)
    return redirect(url_for("workout_today", show=workout_index))


def _parse_logged_sets_from_form():
    """Parse the list-based set fields from the current request form."""
    exercise_names = request.form.getlist("exercise_name")
    set_numbers = request.form.getlist("set_number")
    weights = request.form.getlist("weight")
    reps = request.form.getlist("reps")
    rpes = request.form.getlist("rpe")
    set_notes = request.form.getlist("set_notes")
    weights_b = request.form.getlist("weight_b")
    reps_b = request.form.getlist("reps_b")
    return exercise_names, set_numbers, weights, reps, rpes, set_notes, weights_b, reps_b


def _build_logged_sets(session_id, exercise_names, set_numbers, weights, reps, rpes, set_notes,
                       weights_b=None, reps_b=None, skip_empty=False):
    """Create LoggedSet objects for the given session; returns list of LoggedSet instances."""
    if not exercise_names:
        return []

    unique_lower = list({n.lower() for n in exercise_names})
    lib_entries = ExerciseLibrary.query.filter(
        db.func.lower(ExerciseLibrary.name).in_(unique_lower)
    ).all()
    lib_by_name = {e.name.lower(): e for e in lib_entries}

    logged_list = []
    for i in range(len(exercise_names)):
        weight_val = None
        if i < len(weights) and weights[i]:
            try:
                weight_val = float(weights[i])
            except ValueError:
                pass

        reps_val = None
        if i < len(reps) and reps[i]:
            try:
                reps_val = int(reps[i])
            except ValueError:
                pass

        rpe_val = None
        if i < len(rpes) and rpes[i]:
            try:
                rpe_val = int(rpes[i])
            except ValueError:
                pass

        if skip_empty and weight_val is None and reps_val is None:
            continue

        weight_b_val = None
        if weights_b and i < len(weights_b) and weights_b[i]:
            try:
                weight_b_val = float(weights_b[i])
            except ValueError:
                pass

        reps_b_val = None
        if reps_b and i < len(reps_b) and reps_b[i]:
            try:
                reps_b_val = int(reps_b[i])
            except ValueError:
                pass

        lib_entry = lib_by_name.get(exercise_names[i].lower())
        logged = LoggedSet(
            session_id=session_id,
            exercise_name=exercise_names[i],
            exercise_library_id=lib_entry.id if lib_entry else None,
            set_number=int(set_numbers[i]) if i < len(set_numbers) and set_numbers[i] else 1,
            weight_lbs=weight_val,
            reps_completed=reps_val,
            weight_b=weight_b_val,
            reps_b=reps_b_val,
            rpe=rpe_val,
            notes=set_notes[i] if i < len(set_notes) else "",
        )
        logged_list.append(logged)
    return logged_list


def _upsert_workout_session(profile, status):
    """Create or update a WorkoutSession from the current request form.

    Returns the upserted WorkoutSession (flushed, not committed), or None if the
    resume_session_id belongs to a different user.
    """
    planned_workout_id = request.form.get("planned_workout_id")
    overall_feeling = request.form.get("overall_feeling", type=int)
    session_notes = request.form.get("session_notes", "")
    elapsed_seconds = request.form.get("session_elapsed_seconds", 0, type=int)
    resume_session_id = request.form.get("resume_session_id", type=int)
    superset_exercises = json.dumps(request.form.getlist("superset_exercise"))
    phase_name = request.form.get("phase_name") or None

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(seconds=elapsed_seconds)

    if resume_session_id:
        workout_session = WorkoutSession.query.get_or_404(resume_session_id)
        if workout_session.user_id != profile.id:
            return None
        LoggedSet.query.filter_by(session_id=workout_session.id).delete()
        workout_session.status = status
        workout_session.end_time = end_time
        workout_session.start_time = start_time
        workout_session.overall_feeling = overall_feeling
        workout_session.session_notes = session_notes
        workout_session.elapsed_seconds = elapsed_seconds
        workout_session.superset_exercises = superset_exercises
        workout_session.phase_name = phase_name
        if planned_workout_id:
            workout_session.planned_workout_id = int(planned_workout_id)
    else:
        if status == SESSION_STATUS_PAUSED:
            WorkoutSession.query.filter_by(
                user_id=profile.id, status=SESSION_STATUS_PAUSED
            ).update({"status": SESSION_STATUS_COMPLETED, "end_time": end_time})
        workout_session = WorkoutSession(
            user_id=profile.id,
            planned_workout_id=int(planned_workout_id) if planned_workout_id else None,
            date=date.today(),
            start_time=start_time,
            end_time=end_time,
            overall_feeling=overall_feeling,
            session_notes=session_notes,
            status=status,
            elapsed_seconds=elapsed_seconds,
            superset_exercises=superset_exercises,
            phase_name=phase_name,
        )
        db.session.add(workout_session)
    db.session.flush()
    return workout_session


def _get_next_workout_notes(profile_id, workout_name):
    """Return (general_note_text, specific_note_text) for the given workout, or None each."""
    general = NextWorkoutNote.query.filter_by(user_id=profile_id, workout_name=None).first()
    specific = NextWorkoutNote.query.filter_by(user_id=profile_id, workout_name=workout_name).first()
    return (general.note if general else None), (specific.note if specific else None)


def _save_next_workout_notes(profile_id, workout_name, general_text, specific_text):
    """Upsert next-workout notes for the just-logged workout.

    Deletes the current general note and current workout-specific note (both were
    shown in this session), then creates new rows for any non-empty replacement text.
    Notes for other workout names are left untouched.
    """
    gen = NextWorkoutNote.query.filter_by(user_id=profile_id, workout_name=None).first()
    if gen:
        db.session.delete(gen)
    if general_text:
        db.session.add(NextWorkoutNote(user_id=profile_id, workout_name=None, note=general_text))

    if workout_name:
        spec = NextWorkoutNote.query.filter_by(user_id=profile_id, workout_name=workout_name).first()
        if spec:
            db.session.delete(spec)
        if specific_text:
            db.session.add(NextWorkoutNote(user_id=profile_id, workout_name=workout_name, note=specific_text))


def _build_workout_context(profile, planned_workout, active_plan, *, resume_session=None):
    """Build the template context dict for rendering workout_today.html."""
    all_exercises = PlannedExercise.query.filter_by(
        planned_workout_id=planned_workout.id
    ).order_by(PlannedExercise.order_index).all()

    warmup = [e for e in all_exercises if e.exercise_type == "warmup"]
    main = [e for e in all_exercises if e.exercise_type == "main"]
    cooldown = [e for e in all_exercises if e.exercise_type == "cooldown"]

    last_perf = {}
    recent_perf = {}
    for ex in all_exercises:
        perf = get_last_performance(profile.id, ex.exercise_name)
        if perf:
            last_perf[ex.exercise_name] = perf
        recent = get_recent_performance(profile.id, ex.exercise_name, limit=3)
        if recent:
            recent_perf[ex.exercise_name] = recent

    all_plan_workouts = []
    if active_plan:
        all_plan_workouts = PlannedWorkout.query.filter_by(
            plan_id=active_plan.id
        ).order_by(PlannedWorkout.order_index).all()

    last_perf_json = json.dumps({
        name: {
            "date": data["date"].strftime("%b %d"),
            "sets": {str(k): {
                "weight": v["weight"], "reps": v["reps"],
                "weight_b": v.get("weight_b"), "reps_b": v.get("reps_b"),
                "rpe": v["rpe"], "notes": v.get("notes", ""),
            } for k, v in data["sets"].items()}
        }
        for name, data in last_perf.items()
    })
    recent_perf_json = json.dumps({
        name: [
            {"date": sess["date"].strftime("%b %d"),
             "sets": {str(k): {
                 "weight": v["weight"], "reps": v["reps"],
                 "weight_b": v.get("weight_b"), "reps_b": v.get("reps_b"),
                 "rpe": v["rpe"],
             } for k, v in sess["sets"].items()}}
            for sess in sessions
        ]
        for name, sessions in recent_perf.items()
    })

    # Phase list from plan_json; current phase as default selection
    plan_phases = []
    current_phase_name = None
    if active_plan:
        try:
            plan_data = json.loads(active_plan.plan_json or "{}")
            plan_phases = [p.get("phase_name", "") for p in plan_data.get("phases", []) if p.get("phase_name")]
        except Exception:
            plan_phases = []
        phase_info = get_plan_position(profile.id, active_plan)
        if phase_info:
            current_phase_name = phase_info.get("phase_name")

    resume_data = None
    resume_session_id = None
    resume_elapsed = 0
    overall_feeling = None
    session_notes = ''
    selected_phase_name = current_phase_name
    # Seed superset defaults from the plan; resume will override if applicable
    superset_exercises = [
        ex.exercise_name for ex in all_exercises if ex.is_superset_default
    ]

    if resume_session:
        resume_session_id = resume_session.id
        resume_elapsed = resume_session.elapsed_seconds or 0
        overall_feeling = resume_session.overall_feeling
        session_notes = resume_session.session_notes or ''
        superset_exercises = json.loads(resume_session.superset_exercises or "[]")
        if resume_session.phase_name:
            selected_phase_name = resume_session.phase_name
        resume_data = {}
        for ls in resume_session.logged_sets:
            if ls.exercise_name not in resume_data:
                resume_data[ls.exercise_name] = {}
            resume_data[ls.exercise_name][ls.set_number] = {
                'weight': ls.weight_lbs,
                'reps': ls.reps_completed,
                'rpe': ls.rpe,
                'notes': ls.notes or '',
                'weight_b': ls.weight_b,
                'reps_b': ls.reps_b,
            }
        # Only show exercises that were present in the saved session — removals must not reappear
        warmup = [e for e in warmup if e.exercise_name in resume_data]
        main = [e for e in main if e.exercise_name in resume_data]
        cooldown = [e for e in cooldown if e.exercise_name in resume_data]
        all_exercises = warmup + main + cooldown

    incoming_general_note, incoming_specific_note = _get_next_workout_notes(
        profile.id, planned_workout.workout_name
    )

    return dict(
        workout=planned_workout,
        warmup_exercises=warmup,
        main_exercises=main,
        cooldown_exercises=cooldown,
        all_exercises=all_exercises,
        last_perf=last_perf,
        recent_perf=recent_perf,
        last_perf_json=last_perf_json,
        recent_perf_json=recent_perf_json,
        all_plan_workouts=all_plan_workouts,
        paused_session=None,
        resume_session_id=resume_session_id,
        resume_data=resume_data,
        resume_elapsed=resume_elapsed,
        overall_feeling=overall_feeling,
        session_notes=session_notes,
        superset_exercises=superset_exercises,
        plan_phases=plan_phases,
        selected_phase_name=selected_phase_name,
        incoming_general_note=incoming_general_note,
        incoming_specific_note=incoming_specific_note,
    )


@app.route("/workout/log", methods=["POST"])
@login_required
def workout_log():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    workout_session = _upsert_workout_session(profile, SESSION_STATUS_COMPLETED)
    if workout_session is None:
        return redirect(url_for("index"))

    exercise_names, set_numbers, weights, reps, rpes, set_notes, weights_b, reps_b = _parse_logged_sets_from_form()
    for ls in _build_logged_sets(
            workout_session.id, exercise_names, set_numbers,
            weights, reps, rpes, set_notes, weights_b, reps_b):
        db.session.add(ls)

    workout_name = (
        workout_session.planned_workout.workout_name
        if workout_session.planned_workout else None
    )
    _save_next_workout_notes(
        profile.id,
        workout_name,
        request.form.get("notes_for_next_general", "").strip(),
        request.form.get("notes_for_next_workout", "").strip(),
    )

    update_streak(profile)
    db.session.commit()

    return render_template(
        "workout_done.html",
        session_obj=workout_session,
        logged_sets=workout_session.logged_sets,
    )


@app.route("/workout/pause", methods=["POST"])
@login_required
def workout_pause():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    workout_session = _upsert_workout_session(profile, SESSION_STATUS_PAUSED)
    if workout_session is None:
        return redirect(url_for("index"))

    exercise_names, set_numbers, weights, reps, rpes, set_notes, weights_b, reps_b = _parse_logged_sets_from_form()
    for ls in _build_logged_sets(
            workout_session.id, exercise_names, set_numbers,
            weights, reps, rpes, set_notes, weights_b, reps_b):
        db.session.add(ls)

    db.session.commit()
    flash("Workout paused. Resume it anytime from the dashboard.", "info")
    return redirect(url_for("index"))


@app.route("/workout/resume/<int:session_id>")
@login_required
def workout_resume(session_id):
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    workout_session = WorkoutSession.query.get_or_404(session_id)
    if workout_session.user_id != profile.id or workout_session.status != SESSION_STATUS_PAUSED:
        return redirect(url_for("history"))

    active_plan = get_active_plan(profile.id)
    planned_workout = workout_session.planned_workout

    if not planned_workout and active_plan:
        planned_workout = get_next_workout(profile.id, active_plan)

    if not planned_workout:
        flash("Cannot resume: workout not found.", "error")
        return redirect(url_for("index"))

    ctx = _build_workout_context(profile, planned_workout, active_plan, resume_session=workout_session)
    return render_template("workout_today.html", **ctx)


@app.route("/workout/finish-paused/<int:session_id>", methods=["POST"])
@login_required
def workout_finish_paused(session_id):
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    workout_session = WorkoutSession.query.get_or_404(session_id)
    if workout_session.user_id != profile.id:
        return redirect(url_for("history"))

    workout_session.status = SESSION_STATUS_COMPLETED
    workout_session.end_time = datetime.now(timezone.utc)
    update_streak(profile)
    db.session.commit()

    flash("Workout logged as finished.", "success")
    return redirect(url_for("workout_today"))


@app.route("/history")
@login_required
def history():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    sessions = (
        WorkoutSession.query
        .filter_by(user_id=profile.id)
        .order_by(WorkoutSession.date.desc())
        .all()
    )
    return render_template("history.html", sessions=sessions)


@app.route("/history/<int:session_id>/delete", methods=["POST"])
@login_required
def delete_session(session_id):
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))
    workout_session = WorkoutSession.query.get_or_404(session_id)
    if workout_session.user_id != profile.id:
        return redirect(url_for("history"))
    LoggedSet.query.filter_by(session_id=session_id).delete()
    db.session.delete(workout_session)
    db.session.commit()
    flash("Workout deleted.", "success")
    return redirect(url_for("history"))


@app.route("/history/<int:session_id>")
@login_required
def session_detail(session_id):
    workout_session = WorkoutSession.query.get_or_404(session_id)
    logged_sets = sorted(workout_session.logged_sets, key=lambda s: (s.exercise_name, s.set_number))

    exercises = {}
    for s in logged_sets:
        exercises.setdefault(s.exercise_name, []).append(s)

    return render_template(
        "session_detail.html",
        session_obj=workout_session,
        exercises=exercises,
    )


@app.route("/review")
@login_required
def review():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    last_review = (
        AIReview.query
        .filter_by(user_id=profile.id)
        .order_by(AIReview.created_at.desc())
        .first()
    )

    review_data = None
    if last_review and last_review.suggestions_json:
        try:
            review_data = json.loads(last_review.suggestions_json)
        except json.JSONDecodeError:
            pass

    return render_template("review.html", last_review=last_review, review_data=review_data)


@app.route("/review/generate", methods=["POST"])
@login_required
def generate_review():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    sessions = (
        WorkoutSession.query
        .filter_by(user_id=profile.id)
        .order_by(WorkoutSession.date.desc())
        .limit(30)
        .all()
    )

    if not sessions:
        flash("No workout sessions to review yet!", "info")
        return redirect(url_for("review"))

    sessions_data = []
    for s in sessions:
        sets_data = []
        for ls in s.logged_sets:
            sets_data.append({
                "exercise": ls.exercise_name,
                "set": ls.set_number,
                "weight_lbs": ls.weight_lbs,
                "reps": ls.reps_completed,
                "rpe": ls.rpe,
            })
        sessions_data.append({
            "date": s.date.isoformat() if s.date else None,
            "workout_name": s.planned_workout.workout_name if s.planned_workout else "Unplanned",
            "feeling": s.overall_feeling,
            "notes": s.session_notes,
            "sets": sets_data,
        })

    from ai import generate_progress_review
    try:
        review_result = generate_progress_review(profile, sessions_data)

        ai_review = AIReview(
            user_id=profile.id,
            review_text=review_result.get("overall_assessment", ""),
            suggestions_json=json.dumps(review_result),
            data_summary=json.dumps({"sessions_count": len(sessions_data)}),
        )
        db.session.add(ai_review)
        db.session.commit()
        flash("Progress review generated!", "success")
    except Exception as e:
        flash(f"Error generating review: {str(e)}", "error")

    return redirect(url_for("review"))


@app.route("/export")
@login_required
def export_page():
    return render_template("export.html")


@app.route("/export/download")
@login_required
def export_download():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    from export import generate_xlsx
    output = generate_xlsx(profile.id)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"fitlocal_log_{date.today().isoformat()}.xlsx",
    )


@app.route("/plan")
@login_required
def plan_view():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan(profile.id)
    workouts = []
    phases = []
    current_phase = None

    if active_plan:
        update_plan_week(active_plan)
        db.session.commit()
        workouts = (
            PlannedWorkout.query
            .filter_by(plan_id=active_plan.id)
            .order_by(PlannedWorkout.order_index)
            .all()
        )
        phases = active_plan.phases
        current_phase = get_current_phase(profile.id, active_plan)

    return render_template(
        "plan.html",
        plan=active_plan,
        workouts=workouts,
        phases=phases,
        current_phase=current_phase,
    )


@app.route("/plan/history")
@login_required
def plan_history():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    past_plans = (
        WorkoutPlan.query
        .filter_by(user_id=profile.id, status="inactive")
        .order_by(WorkoutPlan.created_at.desc())
        .all()
    )

    # Attach parsed plan_json and workout counts for display
    plans_data = []
    for p in past_plans:
        try:
            pj = json.loads(p.plan_json or "{}")
        except Exception:
            pj = {}
        session_count = WorkoutSession.query.filter(
            WorkoutSession.user_id == profile.id,
            WorkoutSession.planned_workout_id.in_([w.id for w in p.planned_workouts]),
            WorkoutSession.status == SESSION_STATUS_COMPLETED,
        ).count()
        plans_data.append({
            "plan": p,
            "plan_json": pj,
            "session_count": session_count,
            "workouts": PlannedWorkout.query.filter_by(plan_id=p.id).order_by(PlannedWorkout.order_index).all(),
        })

    return render_template("plan_history.html", plans_data=plans_data)


# --- Plan Edit API ---

def _get_planned_exercise_for_user(exercise_id, profile_id):
    """Return PlannedExercise if it belongs to the current user's plan, else None."""
    ex = PlannedExercise.query.get(exercise_id)
    if ex is None:
        return None
    pw = PlannedWorkout.query.get(ex.planned_workout_id)
    if pw is None:
        return None
    plan = WorkoutPlan.query.get(pw.plan_id)
    if plan is None or plan.user_id != profile_id:
        return None
    return ex


def _get_planned_workout_for_user(workout_id, profile_id):
    """Return PlannedWorkout if it belongs to the current user's active plan, else None."""
    pw = PlannedWorkout.query.get(workout_id)
    if pw is None:
        return None
    plan = WorkoutPlan.query.get(pw.plan_id)
    if plan is None or plan.user_id != profile_id:
        return None
    return pw


@app.route("/api/plan/exercise/<int:exercise_id>", methods=["PATCH"])
@csrf.exempt
@login_required
def api_patch_exercise(exercise_id):
    profile = get_profile()
    ex = _get_planned_exercise_for_user(exercise_id, profile.id if profile else -1)
    if ex is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    if "sets" in data:
        ex.sets_prescribed = int(data["sets"])
    if "reps" in data:
        ex.reps_prescribed = str(data["reps"])
    if "rest_seconds" in data:
        ex.rest_seconds = int(data["rest_seconds"]) if data["rest_seconds"] is not None else None
    if "notes" in data:
        ex.notes = str(data["notes"])
    if "is_superset_default" in data:
        ex.is_superset_default = bool(data["is_superset_default"])
    if "form_cues" in data:
        ex.form_cues = str(data["form_cues"])

    db.session.commit()
    return jsonify({"id": ex.id, "ok": True})


@app.route("/api/plan/workout/<int:workout_id>/reorder", methods=["POST"])
@csrf.exempt
@login_required
def api_reorder_exercises(workout_id):
    profile = get_profile()
    if not profile:
        return jsonify({"error": "no profile"}), 401

    pw = _get_planned_workout_for_user(workout_id, profile.id)
    if pw is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    exercises = data.get("exercises", [])

    if not exercises:
        return jsonify({"error": "no exercises"}), 400

    # Load all exercises and verify they all belong to this workout
    ids = [item["id"] for item in exercises]
    db_exercises = {ex.id: ex for ex in PlannedExercise.query.filter(
        PlannedExercise.id.in_(ids),
        PlannedExercise.planned_workout_id == workout_id,
    ).all()}

    if len(db_exercises) != len(ids):
        return jsonify({"error": "invalid exercise ids"}), 400

    # All exercises in the payload must share the same exercise_type (no cross-group moves)
    types = {db_exercises[eid].exercise_type for eid in ids}
    if len(types) > 1:
        return jsonify({"error": "cannot mix exercise types in reorder"}), 400

    for item in exercises:
        db_exercises[item["id"]].order_index = int(item["order_index"])

    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/plan/exercise/<int:exercise_id>", methods=["DELETE"])
@csrf.exempt
@login_required
def api_delete_exercise(exercise_id):
    profile = get_profile()
    ex = _get_planned_exercise_for_user(exercise_id, profile.id if profile else -1)
    if ex is None:
        return jsonify({"error": "not found"}), 404

    db.session.delete(ex)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/plan/workout/<int:workout_id>/exercise", methods=["POST"])
@csrf.exempt
@login_required
def api_add_exercise(workout_id):
    profile = get_profile()
    if not profile:
        return jsonify({"error": "no profile"}), 401

    pw = _get_planned_workout_for_user(workout_id, profile.id)
    if pw is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}

    library_id = data.get("library_id")
    exercise_name = data.get("exercise_name")

    if library_id:
        lib = ExerciseLibrary.query.get(library_id)
        if lib is None:
            return jsonify({"error": "library entry not found"}), 404
        exercise_name = lib.name
    elif not exercise_name:
        return jsonify({"error": "exercise_name or library_id required"}), 400

    # Place at end of its type group
    ex_type = data.get("exercise_type", "main")
    max_order = db.session.query(db.func.max(PlannedExercise.order_index)).filter_by(
        planned_workout_id=workout_id, exercise_type=ex_type
    ).scalar() or 0

    new_ex = PlannedExercise(
        planned_workout_id=workout_id,
        exercise_name=exercise_name,
        exercise_library_id=library_id,
        exercise_type=ex_type,
        sets_prescribed=int(data.get("sets", 3)),
        reps_prescribed=str(data.get("reps", "10")),
        rest_seconds=int(data["rest_seconds"]) if data.get("rest_seconds") is not None else None,
        notes=data.get("notes", ""),
        form_cues=data.get("form_cues", ""),
        is_superset_default=bool(data.get("is_superset_default", False)),
        order_index=max_order + 1,
    )
    db.session.add(new_ex)
    db.session.commit()
    return jsonify({"id": new_ex.id, "ok": True}), 201


@app.route("/api/plan/exercise-library")
@login_required
def api_exercise_library():
    profile = get_profile()
    if not profile:
        return jsonify([]), 401

    lib_entries = ExerciseLibrary.query.order_by(ExerciseLibrary.name).all()
    lib_names = {e.name for e in lib_entries}

    # Also include exercise names from user's own history not already in library
    history_names = {
        row[0] for row in
        db.session.query(LoggedSet.exercise_name)
        .join(WorkoutSession, LoggedSet.session_id == WorkoutSession.id)
        .filter(WorkoutSession.user_id == profile.id)
        .distinct()
        .all()
    } - lib_names

    result = [{"id": e.id, "name": e.name, "muscle_group": e.muscle_group,
               "equipment": e.equipment} for e in lib_entries]
    result += [{"id": None, "name": n, "muscle_group": None, "equipment": None}
               for n in sorted(history_names)]

    return jsonify(result)


@app.route("/settings")
@login_required
def settings():
    profile = get_profile()
    return render_template("settings.html", profile=profile, account=current_user)


# --- Fitness Test Routes ---

@app.route("/fitness-test")
@login_required
def fitness_test():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    tests = (
        FitnessTest.query
        .filter_by(user_id=profile.id)
        .order_by(FitnessTest.test_date.desc())
        .all()
    )

    # Check retest eligibility (30+ days since last test)
    days_since = None
    can_retest = True
    if tests:
        days_since = (date.today() - tests[0].test_date).days
        can_retest = days_since >= 30

    return render_template(
        "fitness_test.html",
        tests=tests,
        can_retest=can_retest,
        days_since=days_since,
    )


@app.route("/fitness-test/new", methods=["GET", "POST"])
@login_required
def fitness_test_new():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    if request.method == "POST":
        ft = FitnessTest(
            user_id=profile.id,
            pushups=int(request.form.get("pushups") or 0),
            pullups=int(request.form.get("pullups") or 0),
            wall_sit_seconds=int(request.form.get("wall_sit_seconds") or 0),
            toe_touch_inches=float(request.form.get("toe_touch_inches") or 0),
            plank_seconds=int(request.form.get("plank_seconds") or 0),
            vertical_jump_inches=float(request.form.get("vertical_jump_inches") or 0),
            notes=request.form.get("notes", ""),
        )
        db.session.add(ft)
        db.session.commit()
        flash("Fitness test recorded!", "success")
        return redirect(url_for("fitness_test"))

    return render_template("fitness_test_form.html")


# --- Calendar Route ---

@app.route("/calendar")
@login_required
def calendar_view():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    # Reuse the dashboard's phase-colored calendar builder so both views match.
    active_plan = get_active_plan(profile.id)
    phase_color_map = build_phase_color_map(active_plan)
    weeks = build_month_calendar(profile.id, year, month, phase_color_map)

    prev_year, prev_month, next_year, next_month = _prev_next_month(year, month)
    month_name = cal_module.month_name[month]

    return render_template(
        "calendar.html",
        weeks=weeks,
        year=year,
        month=month,
        month_name=month_name,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        today=today,
        profile=profile,
        phase_color_map=phase_color_map,
    )


# --- Nutrition Route ---

@app.route("/nutrition")
@login_required
def nutrition():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan(profile.id)
    phases = []
    current_phase = None
    plan_position = None

    if active_plan:
        phases = active_plan.phases
        current_phase = get_current_phase(profile.id, active_plan)
        plan_position = get_plan_position(profile.id, active_plan)

    return render_template(
        "nutrition.html",
        plan=active_plan,
        phases=phases,
        current_phase=current_phase,
        plan_position=plan_position,
    )


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug)
