import json
import os
from datetime import datetime, date, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fitlocal-dev-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fitlocal.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

from models import db, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise, WorkoutSession, LoggedSet, AIReview

db.init_app(app)

with app.app_context():
    db.create_all()

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_profile():
    return UserProfile.query.first()


def get_active_plan():
    return WorkoutPlan.query.filter_by(is_active=True).first()


@app.route("/")
def index():
    profile = get_profile()
    if not profile:
        return redirect(url_for("setup"))

    active_plan = get_active_plan()
    today_name = DAY_NAMES[date.today().weekday()]

    todays_workout = None
    if active_plan:
        todays_workout = PlannedWorkout.query.filter_by(
            plan_id=active_plan.id, day_of_week=today_name
        ).first()

    # Stats
    from sqlalchemy import func
    week_start = date.today()
    while week_start.weekday() != 0:  # Monday
        week_start = week_start.replace(day=week_start.day - 1)

    days_trained = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.date >= week_start
    ).count()

    last_session = WorkoutSession.query.filter_by(user_id=profile.id).order_by(
        WorkoutSession.date.desc()
    ).first()

    return render_template(
        "index.html",
        profile=profile,
        active_plan=active_plan,
        todays_workout=todays_workout,
        today_name=today_name,
        days_trained=days_trained,
        last_session=last_session,
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

    from ai import generate_workout_plan
    try:
        plan_data = generate_workout_plan(profile)

        # Remove any old pending plans
        WorkoutPlan.query.filter_by(user_id=profile.id, is_active=False, notes="pending").delete()

        # Store the generated plan in the database as pending
        pending = WorkoutPlan(
            user_id=profile.id,
            name=plan_data["plan_name"],
            description=plan_data.get("description", ""),
            days_per_week=plan_data.get("days_per_week", 0),
            plan_json=json.dumps(plan_data),
            is_active=False,
            notes="pending",
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

    # Remove the pending plan row — we'll create a fresh active one
    db.session.delete(pending)

    # Deactivate existing plans
    WorkoutPlan.query.filter_by(is_active=True).update({"is_active": False})

    plan = WorkoutPlan(
        user_id=profile.id,
        name=pending_plan["plan_name"],
        description=pending_plan.get("description", ""),
        days_per_week=pending_plan.get("days_per_week", 0),
        plan_json=json.dumps(pending_plan),
        is_active=True,
    )
    db.session.add(plan)
    db.session.flush()

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

    exercises = PlannedExercise.query.filter_by(
        planned_workout_id=todays_workout.id
    ).all()

    return render_template(
        "workout_today.html",
        workout=todays_workout,
        exercises=exercises,
        today_name=today_name,
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

    # Group sets by exercise
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
    if active_plan:
        workouts = (
            PlannedWorkout.query
            .filter_by(plan_id=active_plan.id)
            .order_by(PlannedWorkout.order_index)
            .all()
        )

    return render_template("plan.html", plan=active_plan, workouts=workouts)


@app.route("/settings")
def settings():
    profile = get_profile()
    return render_template("settings.html", profile=profile)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
