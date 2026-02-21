import json
import os
import calendar as cal_module
from datetime import datetime, date, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fitlocal-dev-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fitlocal.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

from models import (
    db, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, TrainingPhase, ExerciseLibrary
)

db.init_app(app)

with app.app_context():
    db.create_all()

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_profile():
    return UserProfile.query.first()


def get_active_plan():
    return WorkoutPlan.query.filter_by(is_active=True).first()


def get_current_phase(plan):
    """Get the current training phase based on plan's current_week."""
    if not plan or not plan.phases:
        return None
    for phase in plan.phases:
        if phase.week_start <= plan.current_week <= phase.week_end:
            return phase
    return None


def update_plan_week(plan):
    """Update the plan's current_week based on start_date."""
    if not plan or not plan.start_date:
        return
    days_elapsed = (date.today() - plan.start_date).days
    week = (days_elapsed // 7) + 1
    plan.current_week = min(week, plan.total_weeks)


def get_last_performance(user_id, exercise_name):
    """Get the last logged sets for a specific exercise."""
    last_session = (
        WorkoutSession.query
        .join(LoggedSet)
        .filter(
            WorkoutSession.user_id == user_id,
            LoggedSet.exercise_name == exercise_name,
        )
        .order_by(WorkoutSession.date.desc())
        .first()
    )
    if not last_session:
        return None
    sets = [s for s in last_session.logged_sets if s.exercise_name == exercise_name]
    if not sets:
        return None
    best_weight = max((s.weight_lbs or 0) for s in sets)
    best_reps = max((s.reps_completed or 0) for s in sets)
    return {"weight": best_weight, "reps": best_reps, "date": last_session.date}


def update_streak(profile):
    """Update the user's workout streak after logging a session."""
    today = date.today()
    if profile.last_workout_date == today:
        return  # Already logged today

    if profile.last_workout_date:
        days_gap = (today - profile.last_workout_date).days
        # Allow gaps for rest days (up to 3 days gap for Mon/Wed/Fri schedule)
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


@app.route("/")
def index():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan()
    today_name = DAY_NAMES[date.today().weekday()]

    if active_plan:
        update_plan_week(active_plan)
        db.session.commit()

    todays_workout = None
    if active_plan:
        todays_workout = PlannedWorkout.query.filter_by(
            plan_id=active_plan.id, day_of_week=today_name
        ).first()

    # Current phase and nutrition
    current_phase = get_current_phase(active_plan) if active_plan else None

    # Stats
    week_start = date.today()
    while week_start.weekday() != 0:
        week_start = week_start.replace(day=week_start.day - 1)

    days_trained = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.date >= week_start
    ).count()

    last_session = WorkoutSession.query.filter_by(user_id=profile.id).order_by(
        WorkoutSession.date.desc()
    ).first()

    mini_cal = get_mini_calendar(profile.id)

    return render_template(
        "index.html",
        profile=profile,
        active_plan=active_plan,
        todays_workout=todays_workout,
        today_name=today_name,
        days_trained=days_trained,
        last_session=last_session,
        current_phase=current_phase,
        mini_cal=mini_cal,
    )


@app.route("/setup", methods=["GET", "POST"])
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
        user_id=profile.id, is_active=False, notes="pending"
    ).order_by(WorkoutPlan.created_at.desc()).first()


@app.route("/generate-plan")
def generate_plan():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    pending = get_pending_plan(profile)
    pending_plan = json.loads(pending.plan_json) if pending else None

    return render_template("generate_plan.html", profile=profile, pending_plan=pending_plan)


@app.route("/generate-plan/generate", methods=["POST"])
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
        WorkoutPlan.query.filter_by(user_id=profile.id, is_active=False, notes="pending").delete()

        pending = WorkoutPlan(
            user_id=profile.id,
            name=plan_data["plan_name"],
            description=plan_data.get("description", ""),
            days_per_week=plan_data.get("days_per_week", 3),
            plan_json=json.dumps(plan_data),
            is_active=False,
            notes="pending",
            total_weeks=plan_data.get("total_weeks", 12),
        )
        db.session.add(pending)
        db.session.commit()
        flash("Plan generated! Review it below.", "success")
    except Exception as e:
        flash(f"Error generating plan: {str(e)}", "error")

    return redirect(url_for("generate_plan"))


@app.route("/generate-plan/confirm", methods=["POST"])
def confirm_plan():
    profile = get_profile()
    pending = get_pending_plan(profile) if profile else None
    if not pending or not profile:
        flash("No plan to activate.", "error")
        return redirect(url_for("generate_plan"))

    pending_plan = json.loads(pending.plan_json)

    db.session.delete(pending)

    # Deactivate existing plans
    WorkoutPlan.query.filter_by(is_active=True).update({"is_active": False})

    plan = WorkoutPlan(
        user_id=profile.id,
        name=pending_plan["plan_name"],
        description=pending_plan.get("description", ""),
        days_per_week=pending_plan.get("days_per_week", 3),
        plan_json=json.dumps(pending_plan),
        is_active=True,
        total_weeks=pending_plan.get("total_weeks", 12),
        current_week=1,
        start_date=date.today(),
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
            pe = PlannedExercise(
                planned_workout_id=pw.id,
                exercise_name=exercise_data["name"],
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


@app.route("/workout/today")
def workout_today():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan()
    if not active_plan:
        flash("No active plan. Generate one first!", "error")
        return redirect(url_for("generate_plan"))

    today_name = DAY_NAMES[date.today().weekday()]
    todays_workout = PlannedWorkout.query.filter_by(
        plan_id=active_plan.id, day_of_week=today_name
    ).first()

    if not todays_workout:
        flash(f"No workout scheduled for {today_name}. Enjoy your rest day!", "info")
        return redirect(url_for("index"))

    all_exercises = PlannedExercise.query.filter_by(
        planned_workout_id=todays_workout.id
    ).all()

    # Group by type
    warmup = [e for e in all_exercises if e.exercise_type == "warmup"]
    main = [e for e in all_exercises if e.exercise_type == "main"]
    cooldown = [e for e in all_exercises if e.exercise_type == "cooldown"]

    # Get last performance for each exercise
    last_perf = {}
    for ex in all_exercises:
        perf = get_last_performance(profile.id, ex.exercise_name)
        if perf:
            last_perf[ex.exercise_name] = perf

    return render_template(
        "workout_today.html",
        workout=todays_workout,
        warmup_exercises=warmup,
        main_exercises=main,
        cooldown_exercises=cooldown,
        all_exercises=all_exercises,
        today_name=today_name,
        last_perf=last_perf,
    )


@app.route("/workout/log", methods=["POST"])
def workout_log():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    planned_workout_id = request.form.get("planned_workout_id")
    overall_feeling = request.form.get("overall_feeling", type=int)
    session_notes = request.form.get("session_notes", "")

    workout_session = WorkoutSession(
        user_id=profile.id,
        planned_workout_id=int(planned_workout_id) if planned_workout_id else None,
        date=date.today(),
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        overall_feeling=overall_feeling,
        session_notes=session_notes,
    )
    db.session.add(workout_session)
    db.session.flush()

    # Parse logged sets from form
    exercise_names = request.form.getlist("exercise_name")
    set_numbers = request.form.getlist("set_number")
    weights = request.form.getlist("weight")
    reps = request.form.getlist("reps")
    rpes = request.form.getlist("rpe")
    set_notes = request.form.getlist("set_notes")

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

        logged = LoggedSet(
            session_id=workout_session.id,
            exercise_name=exercise_names[i],
            set_number=int(set_numbers[i]) if i < len(set_numbers) and set_numbers[i] else 1,
            weight_lbs=weight_val,
            reps_completed=reps_val,
            rpe=rpe_val,
            notes=set_notes[i] if i < len(set_notes) else "",
        )
        db.session.add(logged)

    # Update streak
    update_streak(profile)

    db.session.commit()

    return render_template(
        "workout_done.html",
        session_obj=workout_session,
        logged_sets=workout_session.logged_sets,
    )


@app.route("/history")
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


@app.route("/history/<int:session_id>")
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
def export_page():
    return render_template("export.html")


@app.route("/export/download")
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
def plan_view():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan()
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
        current_phase = get_current_phase(active_plan)

    return render_template(
        "plan.html",
        plan=active_plan,
        workouts=workouts,
        phases=phases,
        current_phase=current_phase,
    )


@app.route("/settings")
def settings():
    profile = get_profile()
    return render_template("settings.html", profile=profile)


# --- Fitness Test Routes ---

@app.route("/fitness-test")
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
    can_retest = True
    if tests:
        days_since = (date.today() - tests[0].test_date).days
        can_retest = days_since >= 30

    return render_template(
        "fitness_test.html",
        tests=tests,
        can_retest=can_retest,
        days_since=days_since if tests else None,
    )


@app.route("/fitness-test/new", methods=["GET", "POST"])
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
def calendar_view():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    # Get all sessions for this month
    first_day = date(year, month, 1)
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    sessions = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.date >= first_day,
        WorkoutSession.date <= last_day,
    ).all()
    completed_dates = {s.date for s in sessions}

    # Get the active plan's workout days
    active_plan = get_active_plan()
    planned_days = set()
    if active_plan:
        for pw in active_plan.planned_workouts:
            planned_days.add(pw.day_of_week)

    # Build calendar grid
    cal = cal_module.Calendar(firstweekday=0)  # Monday first
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        week_data = []
        for day_num in week:
            if day_num == 0:
                week_data.append(None)
            else:
                d = date(year, month, day_num)
                day_name = DAY_NAMES[d.weekday()]
                week_data.append({
                    "day": day_num,
                    "date": d,
                    "completed": d in completed_dates,
                    "planned": day_name in planned_days,
                    "is_today": d == today,
                    "is_past": d < today,
                    "missed": d < today and day_name in planned_days and d not in completed_dates,
                })
        weeks.append(week_data)

    # Prev/next month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

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
    )


# --- Nutrition Route ---

@app.route("/nutrition")
def nutrition():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan()
    phases = []
    current_phase = None

    if active_plan:
        update_plan_week(active_plan)
        db.session.commit()
        phases = active_plan.phases
        current_phase = get_current_phase(active_plan)

    return render_template(
        "nutrition.html",
        plan=active_plan,
        phases=phases,
        current_phase=current_phase,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
