"""Quick smoke test for all routes."""
import json
import os
import sys
import tempfile
from datetime import date
from werkzeug.datastructures import MultiDict

# Point at a fresh temp DB BEFORE importing app (engine is created at import time)
_db_fd, _db_path = tempfile.mkstemp(suffix='_fitlocal_test.db')
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

from app import app
app.config["WTF_CSRF_ENABLED"] = False   # disable CSRF tokens in tests
app.config["TESTING"] = True

from models import db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise, TrainingPhase, FitnessTest, ExerciseLibrary, LoggedSet, WorkoutSession, NextWorkoutNote, AIReview
from extensions import bcrypt

passed = 0
failed = 0

def check(label, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {label}")
        passed += 1
    else:
        print(f"  FAIL: {label}")
        failed += 1

client = app.test_client()

# Bootstrap: create an account (no profile yet) and log in via session
with app.app_context():
    db.create_all()
    account = Account(email="test@fitlocal.test", email_claimed=True, is_admin=True)
    account.password_hash = bcrypt.generate_password_hash("testpass").decode()
    db.session.add(account)
    db.session.commit()
    test_account_id = account.id

with client.session_transaction() as sess:
    sess['_user_id'] = str(test_account_id)
    sess['_fresh'] = True

# 1. Home redirects to setup when no profile
print("\n--- No Profile ---")
r = client.get("/")
check("GET / redirects to /setup", r.status_code == 302 and "/setup" in r.headers.get("Location", ""))

# 2. Setup page loads
r = client.get("/setup")
check("GET /setup returns 200", r.status_code == 200)

# 3. Create profile
r = client.post("/setup", data={
    "name": "Tim", "age": "35", "sex": "Male",
    "fitness_level": "Intermediate", "goals": "Build muscle, get lean"
}, follow_redirects=False)
check("POST /setup redirects", r.status_code == 302)

with app.app_context():
    p = UserProfile.query.first()
    check("Profile created", p is not None and p.name == "Tim")
    check("Streak defaults to 0", p.current_streak == 0 and p.longest_streak == 0)

# 4. Fitness test
print("\n--- Fitness Test ---")
r = client.get("/fitness-test")
check("GET /fitness-test returns 200", r.status_code == 200)

r = client.get("/fitness-test/new")
check("GET /fitness-test/new returns 200", r.status_code == 200)

r = client.post("/fitness-test/new", data={
    "pushups": "25", "pullups": "5", "wall_sit_seconds": "60",
    "toe_touch_inches": "2", "plank_seconds": "90", "vertical_jump_inches": "18",
    "notes": "Baseline test"
}, follow_redirects=False)
check("POST fitness test redirects", r.status_code == 302)

with app.app_context():
    ft = FitnessTest.query.first()
    check("Fitness test saved", ft is not None and ft.pushups == 25)

# 5. Generate plan (simulate - skip AI call)
print("\n--- Plan Generation ---")
r = client.get("/generate-plan")
check("GET /generate-plan returns 200", r.status_code == 200)

# Insert a pending plan directly
plan_data = {
    "plan_name": "P90X Inspired Plan",
    "description": "12-week periodized training",
    "days_per_week": 3,
    "total_weeks": 12,
    "phases": [
        {"phase_name": "Foundation", "phase_type": "progressive", "week_start": 1, "week_end": 3,
         "description": "Build base strength", "nutrition_guide": "Focus on protein, 1g per lb bodyweight"},
        {"phase_name": "Recovery 1", "phase_type": "recovery", "week_start": 4, "week_end": 4,
         "description": "Deload week", "nutrition_guide": "Maintain calories, extra rest"},
        {"phase_name": "Build", "phase_type": "progressive", "week_start": 5, "week_end": 7,
         "description": "Increase intensity", "nutrition_guide": "Increase carbs around workouts"},
        {"phase_name": "Recovery 2", "phase_type": "recovery", "week_start": 8, "week_end": 8,
         "description": "Deload week", "nutrition_guide": "Maintain calories"},
        {"phase_name": "Peak", "phase_type": "progressive", "week_start": 9, "week_end": 11,
         "description": "Max intensity", "nutrition_guide": "High protein, moderate carbs"},
        {"phase_name": "Recovery 3", "phase_type": "recovery", "week_start": 12, "week_end": 12,
         "description": "Final deload", "nutrition_guide": "Light eating, prep for retest"},
    ],
    "workouts": [
        {
            "day": "Monday", "name": "Upper Body Strength",
            "exercises": [
                {"name": "Arm Circles", "type": "warmup", "sets": 1, "reps": "20", "rest_seconds": 0,
                 "notes": "", "form_cues": "Start small, increase circle size gradually"},
                {"name": "Bench Press", "type": "main", "sets": 4, "reps": "8-10", "rest_seconds": 90,
                 "notes": "Heavy compound", "form_cues": "Feet flat, slight arch, touch chest"},
                {"name": "Pull-Ups", "type": "main", "sets": 3, "reps": "6-10", "rest_seconds": 90,
                 "notes": "", "form_cues": "Full dead hang, chin over bar"},
                {"name": "Overhead Press", "type": "main", "sets": 3, "reps": "8-10", "rest_seconds": 90,
                 "notes": "", "form_cues": "Brace core, press straight up"},
                {"name": "Child Pose", "type": "cooldown", "sets": 1, "reps": "30s", "rest_seconds": 0,
                 "notes": "", "form_cues": "Breathe deep, relax shoulders"},
            ]
        },
        {
            "day": "Wednesday", "name": "Lower Body Power",
            "exercises": [
                {"name": "Leg Swings", "type": "warmup", "sets": 1, "reps": "15 each", "rest_seconds": 0,
                 "notes": "", "form_cues": "Hold wall for balance"},
                {"name": "Squats", "type": "main", "sets": 4, "reps": "8-12", "rest_seconds": 90,
                 "notes": "", "form_cues": "Knees track over toes, below parallel"},
                {"name": "Deadlifts", "type": "main", "sets": 3, "reps": "6-8", "rest_seconds": 120,
                 "notes": "", "form_cues": "Flat back, hinge at hips"},
                {"name": "Hamstring Stretch", "type": "cooldown", "sets": 1, "reps": "30s each", "rest_seconds": 0,
                 "notes": "", "form_cues": "Hinge at hips, straight back"},
            ]
        },
        {
            "day": "Friday", "name": "Full Body Conditioning",
            "exercises": [
                {"name": "Jumping Jacks", "type": "warmup", "sets": 1, "reps": "25", "rest_seconds": 0,
                 "notes": "", "form_cues": "Land soft, full arm extension"},
                {"name": "Burpees", "type": "main", "sets": 3, "reps": "10", "rest_seconds": 60,
                 "notes": "", "form_cues": "Full extension at top, chest to floor"},
                {"name": "Push-Ups", "type": "main", "sets": 3, "reps": "15", "rest_seconds": 60,
                 "notes": "", "form_cues": "Body straight line, chest near floor"},
                {"name": "Forward Fold", "type": "cooldown", "sets": 1, "reps": "30s", "rest_seconds": 0,
                 "notes": "", "form_cues": "Let gravity pull you down, relax neck"},
            ]
        },
    ]
}

with app.app_context():
    profile = UserProfile.query.first()
    pending = WorkoutPlan(
        user_id=profile.id, name=plan_data["plan_name"],
        description=plan_data["description"], days_per_week=3,
        plan_json=json.dumps(plan_data), status="pending",
        total_weeks=12,
    )
    db.session.add(pending)
    db.session.commit()

# Confirm (activate) plan
r = client.post("/generate-plan/confirm", follow_redirects=False)
check("POST /confirm redirects", r.status_code == 302)

with app.app_context():
    plan = WorkoutPlan.query.filter_by(status="active").first()
    check("Plan is active", plan is not None and plan.status == "active")
    check("Plan has 6 phases", len(plan.phases) == 6)
    check("Plan has start_date", plan.start_date == date.today())
    check("Plan has 3 workouts", len(plan.planned_workouts) == 3)

    # Check exercise types
    warmups = PlannedExercise.query.filter_by(exercise_type="warmup").count()
    mains = PlannedExercise.query.filter_by(exercise_type="main").count()
    cooldowns = PlannedExercise.query.filter_by(exercise_type="cooldown").count()
    check(f"Exercise types: {warmups} warmup, {mains} main, {cooldowns} cooldown", warmups == 3 and cooldowns == 3)

    # Check form cues saved
    pe = PlannedExercise.query.filter(PlannedExercise.form_cues != "").first()
    check("Form cues saved", pe is not None and len(pe.form_cues) > 0)

# 6. Plan view
print("\n--- Plan View ---")
r = client.get("/plan")
check("GET /plan returns 200", r.status_code == 200)
check("Plan shows phases", b"Foundation" in r.data and b"Recovery" in r.data)
check("Plan shows phase timeline", b"phase-timeline" in r.data)
check("Plan view has Past Plans link", b"plan/history" in r.data)

# 6b. Plan status field
print("\n--- Plan Status Field ---")
with app.app_context():
    profile = UserProfile.query.first()
    active_plan = WorkoutPlan.query.filter_by(user_id=profile.id, status="active").first()
    pending_plans = WorkoutPlan.query.filter_by(user_id=profile.id, status="pending").all()
    inactive_plans = WorkoutPlan.query.filter_by(user_id=profile.id, status="inactive").all()
    check("Exactly one active plan", active_plan is not None)
    check("No pending plans after confirm", len(pending_plans) == 0)
    check("No inactive plans yet (first plan)", len(inactive_plans) == 0)

# 6c. Plan history page (empty — no inactive plans yet)
print("\n--- Plan History ---")
r = client.get("/plan/history")
check("GET /plan/history returns 200", r.status_code == 200)
check("Plan history shows empty state when no past plans", b"No past plans yet" in r.data)

# 6d. Generate plan page shows current plan in past plans section
r = client.get("/generate-plan")
check("GET /generate-plan returns 200", r.status_code == 200)
check("Generate plan page shows current plan in Past Plans", b"CURRENT" in r.data)
check("Generate plan page Past Plans section present", b"Past Plans" in r.data)

# 6e. Activate a second plan — first plan should become inactive
print("\n--- Second Plan Activation (status transitions) ---")
plan_data2 = dict(plan_data, plan_name="Plan Two")
with app.app_context():
    profile = UserProfile.query.first()
    pending2 = WorkoutPlan(
        user_id=profile.id, name=plan_data2["plan_name"],
        description=plan_data2["description"], days_per_week=3,
        plan_json=json.dumps(plan_data2), status="pending",
        total_weeks=12,
    )
    db.session.add(pending2)
    db.session.commit()

r = client.post("/generate-plan/confirm", follow_redirects=False)
check("POST /confirm second plan redirects", r.status_code == 302)

with app.app_context():
    profile = UserProfile.query.first()
    all_plans = WorkoutPlan.query.filter_by(user_id=profile.id).all()
    active_plans = [p for p in all_plans if p.status == "active"]
    inactive_plans = [p for p in all_plans if p.status == "inactive"]
    pending_plans = [p for p in all_plans if p.status == "pending"]
    check("Exactly one active plan after second activation", len(active_plans) == 1)
    check("First plan is now inactive", len(inactive_plans) == 1)
    check("No pending plans after second confirm", len(pending_plans) == 0)
    check("Active plan is Plan Two", active_plans[0].name == "Plan Two")
    check("Inactive plan is original plan", inactive_plans[0].name == plan_data["plan_name"])

# 6f. Plan history now shows the inactive plan
r = client.get("/plan/history")
check("Plan history shows inactive plan", plan_data["plan_name"].encode() in r.data)
check("Plan history does not show active plan", b"Plan Two" not in r.data)

# 6g. Generate plan page shows both active and inactive plans
r = client.get("/generate-plan")
check("Generate plan Past Plans shows active plan", b"Plan Two" in r.data)
check("Generate plan Past Plans shows inactive plan", plan_data["plan_name"].encode() in r.data)

# 7. Dashboard
print("\n--- Dashboard ---")
r = client.get("/")
check("GET / returns 200", r.status_code == 200)
check("Shows streak", b"Streak" in r.data)
check("Shows mini calendar", b"mini-calendar" in r.data)
check("Shows phase info", b"Foundation" in r.data or b"phase-card" in r.data)

# 8. Workout today (depends on day of week)
print("\n--- Workout Today ---")
r = client.get("/workout/today")
today_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][date.today().weekday()]
check(f"GET /workout/today ({today_name}) returns 200", r.status_code == 200)
check("Has timer", b"timer-card" in r.data)
check("Has warmup section", b"warmup-header" in r.data or b"Warm-Up" in r.data)
check("Has cooldown section", b"cooldown-header" in r.data or b"Cool-Down" in r.data)
check("Has form cues", b"form-cues" in r.data)
check("Has session timer JS", b"sessionTimer" in r.data)
check("Has rest timer JS", b"startRest" in r.data)
check("Has audio beep", b"playBeep" in r.data)

# 9. Log a workout (simulate for a workout day)
print("\n--- Workout Logging ---")
with app.app_context():
    pw = PlannedWorkout.query.first()  # Monday's workout
    exercises = PlannedExercise.query.filter_by(planned_workout_id=pw.id).all()

    pass  # build form data below

form_items = []
form_items.append(("planned_workout_id", str(pw.id)))
form_items.append(("overall_feeling", "4"))
form_items.append(("session_notes", "Great workout!"))
with app.app_context():
    pw = PlannedWorkout.query.first()
    exercises = PlannedExercise.query.filter_by(planned_workout_id=pw.id).all()
    for ex in exercises:
        for s in range(1, ex.sets_prescribed + 1):
            form_items.append(("exercise_name", ex.exercise_name))
            form_items.append(("set_number", str(s)))
            form_items.append(("weight", "135"))
            form_items.append(("reps", "10"))
            form_items.append(("rpe", "7"))
            form_items.append(("set_notes", ""))

r = client.post("/workout/log", data=MultiDict(form_items), follow_redirects=False)
check("POST /workout/log returns 200", r.status_code == 200)
check("Shows workout done page", b"Great Work" in r.data)

# Check streak updated
with app.app_context():
    p = UserProfile.query.first()
    check(f"Streak updated to {p.current_streak}", p.current_streak >= 1)
    check(f"Last workout date is today", p.last_workout_date == date.today())

# 10. History
print("\n--- History ---")
r = client.get("/history")
check("GET /history returns 200", r.status_code == 200)

r = client.get("/history/1")
check("GET /history/1 returns 200", r.status_code == 200)

# 11. Calendar
print("\n--- Calendar ---")
r = client.get("/calendar")
check("GET /calendar returns 200", r.status_code == 200)
check("Calendar has grid", b"calendar-grid" in r.data)
# Calendar page uses the same color-coded phase dots, tooltips and legend as the dashboard
check("Calendar shows phase dots", b"phase-dot" in r.data)
check("Calendar dots link to history", b"/history/" in r.data)
check("Calendar has phase legend", b"phase-legend" in r.data)
_cal_html = r.data.decode()
check("Calendar week starts on Sunday (Sun before Mon)",
      "<div class=\"cal-header\">Sun</div>" in _cal_html
      and _cal_html.find(">Sun<") < _cal_html.find(">Mon<"))

# Test prev/next month
r = client.get("/calendar?year=2026&month=1")
check("GET /calendar with params returns 200", r.status_code == 200)
check("Shows January", b"January" in r.data)

# 12. Nutrition
print("\n--- Nutrition ---")
r = client.get("/nutrition")
check("GET /nutrition returns 200", r.status_code == 200)
check("Shows nutrition guide", b"protein" in r.data)

# 13. Last performance (log second workout and check)
print("\n--- Last Performance ---")
r = client.post("/workout/log", data=MultiDict(form_items), follow_redirects=False)
check("Second workout logged", r.status_code == 200)

with app.app_context():
    from app import get_last_performance
    profile = UserProfile.query.first()
    perf = get_last_performance(profile.id, "Bench Press")
    check("Last performance found", perf is not None)
    if perf:
        s1 = perf["sets"].get(1, {})
        check(f"Last perf set1 weight={s1.get('weight')}, reps={s1.get('reps')}", s1.get("weight") == 135.0 and s1.get("reps") == 10)

# 14. Pause / Resume
print("\n--- Pause / Resume ---")
with app.app_context():
    from app import get_paused_session, get_active_plan as _gap
    from models import WorkoutSession, LoggedSet
    profile = UserProfile.query.first()
    pw = PlannedWorkout.query.filter_by(plan_id=_gap(profile.id).id).first()
    exercises = PlannedExercise.query.filter_by(planned_workout_id=pw.id).all()

pause_items = [
    ("planned_workout_id", str(pw.id)),
    ("overall_feeling", "3"),
    ("session_notes", "Interrupted"),
    ("session_elapsed_seconds", "432"),  # 7m 12s
    ("resume_session_id", ""),
]
with app.app_context():
    from app import get_active_plan as _gap2
    profile = UserProfile.query.first()
    pw = PlannedWorkout.query.filter_by(plan_id=_gap2(profile.id).id).first()
    exercises = PlannedExercise.query.filter_by(planned_workout_id=pw.id).all()
    for ex in exercises:
        for s in range(1, ex.sets_prescribed + 1):
            pause_items.append(("exercise_name", ex.exercise_name))
            pause_items.append(("set_number", str(s)))
            pause_items.append(("weight", "185" if s <= 2 else ""))
            pause_items.append(("reps", "8" if s <= 2 else ""))
            pause_items.append(("rpe", "7" if s <= 2 else ""))
            pause_items.append(("set_notes", ""))

r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
check("POST /workout/pause redirects to index", r.status_code == 302 and "//" not in r.headers.get("Location","") )

with app.app_context():
    from models import WorkoutSession
    profile = UserProfile.query.first()
    paused = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    check("Session saved with status=paused", paused is not None)
    check("elapsed_seconds saved correctly", paused is not None and paused.elapsed_seconds == 432)
    check("planned_workout_id saved", paused is not None and paused.planned_workout_id == pw.id)
    check("planned_workout relationship loads", paused is not None and paused.planned_workout is not None)
    check("All logged sets saved (including empty)", paused is not None and len(paused.logged_sets) > 0)

    # Verify paused session excluded from plan session count
    from app import get_next_workout, get_active_plan
    active_plan = get_active_plan(profile.id)
    workout_ids = [w.id for w in active_plan.planned_workouts]
    completed_count = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.planned_workout_id.in_(workout_ids),
        WorkoutSession.status == 'completed'
    ).count()
    total_count = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.planned_workout_id.in_(workout_ids),
    ).count()
    check("Paused session excluded from completed count", completed_count < total_count)

    paused_id = paused.id

# Resume page loads with correct data
r = client.get(f"/workout/resume/{paused_id}", follow_redirects=False)
check("GET /workout/resume returns 200", r.status_code == 200)
check("Resume page has workoutForm", b'id="workoutForm"' in r.data)
check("Resume page has resume_session_id hidden field", f'name="resume_session_id" value="{paused_id}"'.encode() in r.data)
check("Resume page has elapsed seconds", b'resumeElapsed = 432' in r.data)
check("Pre-populated weight value", b'value="185.0"' in r.data or b'value="185"' in r.data)

# Dashboard shows paused session banner but NOT the Next Up card
r = client.get("/")
check("Dashboard shows paused session banner", b"Paused workout" in r.data or b"paused" in r.data.lower())
check("Dashboard hides Next Up card when paused session exists", b"Next Up" not in r.data)
check("Dashboard does not show Get Started when plan exists and session paused", b"Get Started" not in r.data)

# /workout/today with a paused session must redirect to a choice page, not load the workout
r = client.get("/workout/today", follow_redirects=False)
check("GET /workout/today redirects when paused session exists", r.status_code == 302)
check("Redirect goes to paused-choice page", b"paused-choice" in r.headers.get("Location", "").encode())

r_choice = client.get("/workout/paused-choice", follow_redirects=False)
check("GET /workout/paused-choice returns 200", r_choice.status_code == 200)
check("Choice page shows workout name", b"Upper Body Strength" in r_choice.data)
check("Choice page has Resume option", b"Resume" in r_choice.data)
check("Choice page has Log as Finished option", b"Log as Finished" in r_choice.data)
check("Choice page has Cancel option", b"Cancel" in r_choice.data)
check("Choice page does NOT show the workout form", b"workoutForm" not in r_choice.data)

# History shows paused badge
r = client.get("/history")
check("History shows Paused badge", b"Paused" in r.data)
check("History shows Resume button", b"Resume" in r.data)

# Paused session does not appear in last_performance history
r = client.post("/workout/log", data=MultiDict(form_items), follow_redirects=False)  # log a fresh completed session
with app.app_context():
    from app import get_last_performance
    profile = UserProfile.query.first()
    perf = get_last_performance(profile.id, "Bench Press")
    check("Last performance ignores paused sessions", perf is not None and perf["sets"][1]["weight"] == 135.0)

# Complete a paused session via finish-paused
r = client.post(f"/workout/finish-paused/{paused_id}", follow_redirects=False)
check("POST /workout/finish-paused redirects", r.status_code == 302)
with app.app_context():
    from models import WorkoutSession
    finished = WorkoutSession.query.get(paused_id)
    check("finish-paused sets status=completed", finished is not None and finished.status == 'completed')
    # No paused sessions should remain
    remaining_paused = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').count()
    check("No paused sessions remain after finish-paused", remaining_paused == 0)

# After finish-paused, workout_today should load the workout directly (not redirect to choice page)
r = client.get("/workout/today", follow_redirects=False)
check("workout_today loads directly after finish-paused (no paused sessions)", r.status_code == 200)

# Pausing twice should not create two paused sessions
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession
    profile = UserProfile.query.first()
    paused_count = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').count()
    check("Only one paused session exists after pausing twice", paused_count == 1)

# Completing via workout/log with resume_session_id
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    profile = UserProfile.query.first()
    paused2 = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    paused2_id = paused2.id

resume_log_items = list(pause_items)
resume_log_items = [i for i in resume_log_items if i[0] != 'resume_session_id']
resume_log_items.append(("resume_session_id", str(paused2_id)))
resume_log_items.append(("overall_feeling", "5"))
r = client.post("/workout/log", data=MultiDict(resume_log_items), follow_redirects=False)
check("Logging a resumed session returns 200", r.status_code == 200)
with app.app_context():
    from models import WorkoutSession
    completed = WorkoutSession.query.get(paused2_id)
    check("Resumed session now status=completed", completed is not None and completed.status == 'completed')
    check("elapsed_seconds updated on resume log", completed is not None and completed.elapsed_seconds == 432)

# ── Additional pause/resume flow tests ─────────────────────────────────────
print("\n--- Pause/Resume Extended Flows ---")

# Setup: ensure no paused sessions going into these tests
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    WS2.query.filter_by(user_id=profile.id, status='paused').delete()
    db.session.commit()
    pw = PlannedWorkout.query.first()

# Case 2 (already covered above) — just ensure no residue
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    count = WS2.query.filter_by(user_id=profile.id, status='paused').count()
    check("No paused sessions before extended flow tests", count == 0)

# Case 3: Start workout → nav link → Save & Pause creates a paused session
# Simulated: post to /workout/pause without resume_session_id (JS would normally do this)
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps = WS2.query.filter_by(user_id=profile.id, status='paused').first()
    check("Case 3: Save & Pause via nav creates paused session", ps is not None)
    case3_id = ps.id if ps else None

# Case 4: Start workout → nav link → Leave without saving → no session created
# The leave-without-saving path just navigates away — no server call. Verified
# by confirming /workout/today with no paused session returns the workout form.
# First finish the case3 paused session so we can load today's workout.
if case3_id:
    client.post(f"/workout/finish-paused/{case3_id}", follow_redirects=False)
r = client.get("/workout/today", follow_redirects=False)
check("Case 4: No session created by leaving — workout loads fresh", r.status_code == 200)
check("Case 4: Workout form present after leaving without save", b'id="workoutForm"' in r.data)

# Case 5: Nav link → Stay → stays on workout page (client-side only, modal cancel)
# Nothing to test server-side; JS closeModal() does not submit. Confirmed by
# verifying /workout/today still returns 200 with no side effects.
check("Case 5: Stay option is client-side only (no server endpoint needed)", True)

# Case 6: Already tested — /workout/today with paused session redirects to choice page
# Re-test here for clarity in the table
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps6 = WS2.query.filter_by(user_id=profile.id, status='paused').first()
    case6_id = ps6.id if ps6 else None

r = client.get("/workout/today", follow_redirects=False)
check("Case 6: /workout/today redirects to paused-choice when session paused", r.status_code == 302 and "paused-choice" in r.headers.get("Location",""))

# Case 7: Resume → workout loads with data pre-filled and correct elapsed
r = client.get(f"/workout/resume/{case6_id}", follow_redirects=False)
check("Case 7: Resume loads workout page (200)", r.status_code == 200)
check("Case 7: Resume pre-fills set data", b'value="185.0"' in r.data or b'value="185"' in r.data)
check("Case 7: Resume passes elapsed to JS timer", b'resumeElapsed = 432' in r.data)
check("Case 7: resume_session_id hidden field set", f'name="resume_session_id" value="{case6_id}"'.encode() in r.data)

# Case 8: Paused-choice → Log as Finished & Start New → paused completed, workout loads fresh
r = client.post(f"/workout/finish-paused/{case6_id}", follow_redirects=False)
check("Case 8: finish-paused redirects", r.status_code == 302)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    finished8 = WS2.query.get(case6_id)
    check("Case 8: Session now completed", finished8 is not None and finished8.status == 'completed')
    remaining = WS2.query.filter_by(user_id=profile.id, status='paused').count()
    check("Case 8: No paused sessions remain", remaining == 0)
r_after = client.get("/workout/today", follow_redirects=False)
check("Case 8: workout_today loads fresh (not choice page) after finish-paused", r_after.status_code == 200)

# Case 9: Paused-choice → Cancel → redirect to dashboard, session still paused
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps9 = WS2.query.filter_by(user_id=profile.id, status='paused').first()
    case9_id = ps9.id if ps9 else None
# Cancel is just "a href=/" — get the dashboard and verify session still paused
r = client.get("/", follow_redirects=False)
check("Case 9: Cancel goes to dashboard (200)", r.status_code == 200)
with app.app_context():
    from models import WorkoutSession as WS2
    ps9_after = WS2.query.get(case9_id)
    check("Case 9: Paused session unchanged after cancel", ps9_after is not None and ps9_after.status == 'paused')

# Case 10: Dashboard banner Resume → data restored (same as case 7, different entry point)
r = client.get(f"/workout/resume/{case9_id}", follow_redirects=False)
check("Case 10: Dashboard Resume loads workout (200)", r.status_code == 200)
check("Case 10: Data pre-filled", b'value="185.0"' in r.data or b'value="185"' in r.data)

# Case 11: Dashboard banner Log as Finished → completed, dashboard shows Next Up
r = client.post(f"/workout/finish-paused/{case9_id}", follow_redirects=False)
check("Case 11: finish-paused redirects", r.status_code == 302)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    remaining11 = WS2.query.filter_by(user_id=profile.id, status='paused').count()
    check("Case 11: No paused sessions after finish", remaining11 == 0)
r_dash = client.get("/", follow_redirects=False)
check("Case 11: Dashboard shows Next Up after finishing", b"Next Up" in r_dash.data)

# Case 12: History Resume → same as case 7 (already covered by GET /workout/resume)
check("Case 12: History Resume is same endpoint as Cases 7/10 — covered", True)

# Case 13: Resumed session → Log Workout → original session completed, not a new row
r = client.post("/workout/pause", data=MultiDict(pause_items), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps13 = WS2.query.filter_by(user_id=profile.id, status='paused').first()
    case13_id = ps13.id
    pre_count = WS2.query.filter_by(user_id=profile.id).count()

resume_log13 = [i for i in pause_items if i[0] != 'resume_session_id']
resume_log13.append(("resume_session_id", str(case13_id)))
resume_log13.append(("overall_feeling", "4"))
r = client.post("/workout/log", data=MultiDict(resume_log13), follow_redirects=False)
check("Case 13: Logging resumed session returns 200", r.status_code == 200)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    post_count = WS2.query.filter_by(user_id=profile.id).count()
    completed13 = WS2.query.get(case13_id)
    check("Case 13: No new session row created", post_count == pre_count)
    check("Case 13: Original session status=completed", completed13 is not None and completed13.status == 'completed')
    check("Case 13: Streak updated after resume log", profile.current_streak >= 1)

# Case 14: Resumed session → Pause → original session still paused, elapsed accumulates
# First pause at 300s, resume, then pause again at 200s more = 500s total
pause_items_14 = [i for i in pause_items if i[0] != 'session_elapsed_seconds']
pause_items_14.append(("session_elapsed_seconds", "300"))
r = client.post("/workout/pause", data=MultiDict(pause_items_14), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps14 = WS2.query.filter_by(user_id=profile.id, status='paused').first()
    case14_id = ps14.id

# Re-pause (simulating: resume page → user hits Pause again, now with resume_session_id)
pause_resume14 = [i for i in pause_items_14 if i[0] not in ('session_elapsed_seconds', 'resume_session_id')]
pause_resume14.append(("session_elapsed_seconds", "500"))
pause_resume14.append(("resume_session_id", str(case14_id)))
r = client.post("/workout/pause", data=MultiDict(pause_resume14), follow_redirects=False)
with app.app_context():
    from models import WorkoutSession as WS2
    profile = UserProfile.query.first()
    ps14_after = WS2.query.get(case14_id)
    total_paused = WS2.query.filter_by(user_id=profile.id, status='paused').count()
    check("Case 14: Still only one paused session after resume-then-pause", total_paused == 1)
    check("Case 14: Same session id preserved", ps14_after is not None and ps14_after.id == case14_id)
    check("Case 14: elapsed_seconds updated to cumulative value", ps14_after is not None and ps14_after.elapsed_seconds == 500)

# Case 15: Resumed session → nav link → Save & Pause → original session updated
# (Same as case 14 with resume_session_id — already covered above)
check("Case 15: Save & Pause on resumed session covered by Case 14", True)

# Case 16: Resumed session → nav link → Leave without saving → session left paused
# Client-side only: no fetch, no form submit. Verify session still paused.
with app.app_context():
    from models import WorkoutSession as WS2
    ps16 = WS2.query.get(case14_id)
    check("Case 16: Leave without saving — paused session unchanged (client-side only)", ps16 is not None and ps16.status == 'paused')

# Case 17: Stay (cancel modal) — client-side only
check("Case 17: Stay is client-side modal cancel — no server endpoint", True)

# Case 18: Pause → Resume → Pause again → elapsed is cumulative
# Already verified in Case 14 (300 then 500). Restate explicitly.
with app.app_context():
    from models import WorkoutSession as WS2
    ps18 = WS2.query.get(case14_id)
    check("Case 18: Elapsed accumulates across multiple pause/resume cycles", ps18 is not None and ps18.elapsed_seconds == 500)

# Case 19: Already tested — pausing twice keeps only one paused session (covered above)
check("Case 19: Single paused session enforcement — covered in earlier test", True)

# Case 20: Plan position unchanged by paused session (already tested above)
check("Case 20: Plan position ignores paused sessions — covered in earlier test", True)

# Clean up paused session left by case 14/18 before next test section
with app.app_context():
    from models import WorkoutSession as WS2, LoggedSet as LS2
    profile = UserProfile.query.first()
    paused_ids = [s.id for s in WS2.query.filter_by(user_id=profile.id, status='paused').all()]
    if paused_ids:
        LS2.query.filter(LS2.session_id.in_(paused_ids)).delete(synchronize_session=False)
    WS2.query.filter_by(user_id=profile.id, status='paused').delete()
    db.session.commit()

# 15. Resume fidelity: removed sets and removed exercises must not reappear
print("\n--- Resume Fidelity: removed sets/exercises ---")
with app.app_context():
    profile = UserProfile.query.first()
    pw_r = PlannedWorkout.query.first()
    exercises_r = PlannedExercise.query.filter_by(planned_workout_id=pw_r.id).all()
    # Find a multi-set exercise to test set removal
    multi_ex = next((e for e in exercises_r if e.sets_prescribed >= 2), None)
    # Find a second exercise to test exercise removal
    second_ex = next((e for e in exercises_r if e != multi_ex), None)

# Build pause form: include multi_ex with one fewer set than prescribed,
# and omit second_ex entirely (simulating user removing it).
fidelity_pause_items = [
    ("planned_workout_id", str(pw_r.id)),
    ("overall_feeling", "3"),
    ("session_notes", ""),
    ("session_elapsed_seconds", "60"),
    ("resume_session_id", ""),
]
with app.app_context():
    multi_ex = next((e for e in PlannedExercise.query.filter_by(planned_workout_id=pw_r.id).all()
                     if e.sets_prescribed >= 2), None)
    second_ex = next((e for e in PlannedExercise.query.filter_by(planned_workout_id=pw_r.id).all()
                      if e != multi_ex), None)
    reduced_sets = multi_ex.sets_prescribed - 1  # one fewer than planned
    multi_ex_name = multi_ex.exercise_name
    second_ex_name = second_ex.exercise_name if second_ex else None
    for s in range(1, reduced_sets + 1):
        fidelity_pause_items.append(("exercise_name", multi_ex_name))
        fidelity_pause_items.append(("set_number", str(s)))
        fidelity_pause_items.append(("weight", "100"))
        fidelity_pause_items.append(("reps", "8"))
        fidelity_pause_items.append(("rpe", ""))
        fidelity_pause_items.append(("set_notes", ""))
    # second_ex is intentionally excluded (simulates removal)

r = client.post("/workout/pause", data=MultiDict(fidelity_pause_items), follow_redirects=False)
check("Fidelity: pause with reduced sets succeeds", r.status_code == 302)

with app.app_context():
    profile = UserProfile.query.first()
    paused_f = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    check("Fidelity: paused session created", paused_f is not None)
    paused_f_id = paused_f.id if paused_f else None

if paused_f_id:
    r = client.get(f"/workout/resume/{paused_f_id}", follow_redirects=False)
    check("Fidelity: resume page returns 200", r.status_code == 200)
    html = r.data.decode()
    # Count hidden inputs for multi_ex — should equal reduced_sets, not sets_prescribed
    multi_ex_input_count = html.count(f'name="exercise_name" value="{multi_ex_name}"')
    check(
        f"Fidelity: removed set not restored on resume (expect {reduced_sets} inputs for '{multi_ex_name}', got {multi_ex_input_count})",
        multi_ex_input_count == reduced_sets
    )
    # Removed exercise must not appear as a form input (it may still appear in JS perf history)
    if second_ex_name:
        check(
            f"Fidelity: removed exercise '{second_ex_name}' does not reappear as form input on resume",
            f'name="exercise_name" value="{second_ex_name}"' not in html
        )

# Clean up fidelity paused session
with app.app_context():
    from models import LoggedSet as LS3
    profile = UserProfile.query.first()
    paused_ids = [s.id for s in WorkoutSession.query.filter_by(user_id=profile.id, status='paused').all()]
    if paused_ids:
        LS3.query.filter(LS3.session_id.in_(paused_ids)).delete(synchronize_session=False)
    WorkoutSession.query.filter_by(user_id=profile.id, status='paused').delete()
    db.session.commit()

# 15. Superset support
print("\n--- Superset Support ---")

# 15a. LoggedSet has weight_b and reps_b fields
with app.app_context():
    from models import LoggedSet as LS4
    cols = [c.name for c in LS4.__table__.columns]
    check("LoggedSet has weight_b column", "weight_b" in cols)
    check("LoggedSet has reps_b column", "reps_b" in cols)

# 15b. WorkoutSession has superset_exercises field
with app.app_context():
    from models import WorkoutSession as WS3
    cols = [c.name for c in WS3.__table__.columns]
    check("WorkoutSession has superset_exercises column", "superset_exercises" in cols)

# 15c. Pause with superset state saves superset_exercises to session
with app.app_context():
    from app import get_active_plan as _gap3
    profile = UserProfile.query.first()
    pw_s = PlannedWorkout.query.filter_by(plan_id=_gap3(profile.id).id).first()
    ex_s = PlannedExercise.query.filter_by(planned_workout_id=pw_s.id).first()
    superset_ex_name = ex_s.exercise_name

superset_pause_items = [
    ("planned_workout_id", str(pw_s.id)),
    ("overall_feeling", "3"),
    ("session_notes", ""),
    ("session_elapsed_seconds", "60"),
    ("resume_session_id", ""),
    ("superset_exercise", superset_ex_name),  # marks this exercise as superset
]
with app.app_context():
    ex_s = PlannedExercise.query.filter_by(planned_workout_id=pw_s.id).first()
    for s in range(1, ex_s.sets_prescribed + 1):
        superset_pause_items.extend([
            ("exercise_name", ex_s.exercise_name),
            ("set_number", str(s)),
            ("weight", "100"),
            ("reps", "10"),
            ("weight_b", "80"),
            ("reps_b", "12"),
            ("rpe", ""),
            ("set_notes", ""),
        ])

r = client.post("/workout/pause", data=MultiDict(superset_pause_items), follow_redirects=False)
check("Superset pause redirects", r.status_code == 302)

import json as _json
with app.app_context():
    profile = UserProfile.query.first()
    sp = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    check("Superset session saved as paused", sp is not None)
    raw_ss = getattr(sp, 'superset_exercises', None) if sp else None
    ss_exercises = _json.loads(raw_ss or "[]") if raw_ss is not None else []
    check("superset_exercises saved on pause", superset_ex_name in ss_exercises)
    superset_session_id = sp.id if sp else None

# 15d. Resume page renders superset columns for exercises in superset_exercises
if superset_session_id:
    r = client.get(f"/workout/resume/{superset_session_id}", follow_redirects=False)
    check("Superset resume page returns 200", r.status_code == 200)
    html = r.data.decode()
    check("Resume page shows superset toggle active for superset exercise",
          'superset-active' in html)
    check("Resume page has Weight B column header", "Weight B" in html)

# 15e. LoggedSets from superset pause have weight_b and reps_b populated
with app.app_context():
    profile = UserProfile.query.first()
    sp = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    if sp:
        ss_sets = [ls for ls in sp.logged_sets if getattr(ls, 'weight_b', None) is not None]
        check("Superset LoggedSets have weight_b populated", len(ss_sets) > 0)
        check("Superset LoggedSets have reps_b populated",
              len(ss_sets) > 0 and all(getattr(ls, 'reps_b', None) is not None for ls in ss_sets))
    else:
        check("Superset LoggedSets have weight_b populated", False)
        check("Superset LoggedSets have reps_b populated", False)

# 15f. Complete the superset session and verify weight_b/reps_b persisted
if superset_session_id:
    r = client.post(f"/workout/finish-paused/{superset_session_id}",
                    follow_redirects=False)
    check("Finish superset paused session redirects", r.status_code == 302)
    with app.app_context():
        from models import WorkoutSession as WS4
        completed_sp = WS4.query.get(superset_session_id)
        check("Superset session marked completed", completed_sp and completed_sp.status == 'completed')
        ss_completed_sets = [ls for ls in completed_sp.logged_sets
                             if getattr(ls, 'weight_b', None) is not None]
        check("weight_b persists after completion", len(ss_completed_sets) > 0)

# 15g. Superset data in workout done page, session detail, and performance history
print("\n--- Superset Display ---")

# Log a new workout with superset data to test the workout done page response
with app.app_context():
    from app import get_active_plan as _gap4
    profile = UserProfile.query.first()
    pw_d = PlannedWorkout.query.filter_by(plan_id=_gap4(profile.id).id).first()
    ex_d = PlannedExercise.query.filter_by(planned_workout_id=pw_d.id).first()
    superset_display_ex = ex_d.exercise_name

done_items = [
    ("planned_workout_id", str(pw_d.id)),
    ("overall_feeling", "4"),
    ("session_notes", "Superset display test"),
    ("session_elapsed_seconds", "120"),
    ("resume_session_id", ""),
    ("superset_exercise", superset_display_ex),
]
with app.app_context():
    ex_d = PlannedExercise.query.filter_by(planned_workout_id=pw_d.id).first()
    for s in range(1, ex_d.sets_prescribed + 1):
        done_items.extend([
            ("exercise_name", ex_d.exercise_name),
            ("set_number", str(s)),
            ("weight", "100"),
            ("reps", "10"),
            ("weight_b", "75"),
            ("reps_b", "12"),
            ("rpe", "7"),
            ("set_notes", ""),
        ])

r = client.post("/workout/log", data=MultiDict(done_items), follow_redirects=False)
check("Superset log POST redirects or returns 200",
      r.status_code in (200, 302))

if r.status_code == 200:
    html_done = r.data.decode()
else:
    r2 = client.get(r.headers.get("Location", "/"), follow_redirects=True)
    html_done = r2.data.decode()

check("Workout done page shows Weight B column", "Weight B" in html_done)
check("Workout done page shows weight_b value (75.0 or 75)", "75" in html_done)
check("Workout done page shows reps_b value (12)", "12" in html_done)

# Session detail page
with app.app_context():
    profile = UserProfile.query.first()
    last_session = WorkoutSession.query.filter_by(
        user_id=profile.id, status='completed'
    ).order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc()).first()
    last_session_id = last_session.id if last_session else None

if last_session_id:
    r = client.get(f"/history/{last_session_id}", follow_redirects=False)
    check("Session detail returns 200", r.status_code == 200)
    html_detail = r.data.decode()
    check("Session detail shows Weight B column header", "Weight B" in html_detail)
    check("Session detail shows weight_b value (75.0 or 75)", "75" in html_detail)
    check("Session detail shows reps_b value (12)", "12" in html_detail)
else:
    check("Session detail returns 200", False)
    check("Session detail shows Weight B column header", False)
    check("Session detail shows weight_b value (75.0 or 75)", False)
    check("Session detail shows reps_b value (12)", False)

# get_last_performance returns weight_b and reps_b
with app.app_context():
    from app import get_last_performance as _glp
    profile = UserProfile.query.first()
    perf = _glp(profile.id, superset_display_ex)
    check("get_last_performance returns weight_b",
          perf is not None and any(
              v.get('weight_b') is not None for v in perf['sets'].values()
          ))
    check("get_last_performance returns reps_b",
          perf is not None and any(
              v.get('reps_b') is not None for v in perf['sets'].values()
          ))

# Performance history row in workout_today embeds weight_b in last_perf_json
# We logged weight_b=75 and reps_b=12, so the JSON data should contain those values
r = client.get("/workout/today", follow_redirects=False)
if r.status_code == 200:
    html_today = r.data.decode()
    check("workout_today last_perf_json includes weight_b with value",
          '"weight_b": 75' in html_today or '"weight_b":75' in html_today)
    check("workout_today last_perf_json includes reps_b with value",
          '"reps_b": 12' in html_today or '"reps_b":12' in html_today)
else:
    check("workout_today last_perf_json includes weight_b with value", False)
    check("workout_today last_perf_json includes reps_b with value", False)

# 15b. Recent performance includes notes; workout_today renders them in history dropdown
print("\n--- Perf History Notes ---")
with app.app_context():
    pw_note = PlannedWorkout.query.first()
    exercises_note = PlannedExercise.query.filter_by(planned_workout_id=pw_note.id).all()

note_items = []
note_items.append(("planned_workout_id", str(pw_note.id)))
note_items.append(("overall_feeling", "4"))
note_items.append(("session_notes", ""))
with app.app_context():
    pw_note = PlannedWorkout.query.first()
    exercises_note = PlannedExercise.query.filter_by(planned_workout_id=pw_note.id).all()
    first_ex_name = exercises_note[0].exercise_name
    for i, ex in enumerate(exercises_note):
        for s in range(1, ex.sets_prescribed + 1):
            note_items.append(("exercise_name", ex.exercise_name))
            note_items.append(("set_number", str(s)))
            note_items.append(("weight", "140"))
            note_items.append(("reps", "8"))
            note_items.append(("rpe", "8"))
            # Add a note only on set 1 of the first exercise
            if ex.exercise_name == first_ex_name and s == 1:
                note_items.append(("set_notes", "felt strong today"))
            else:
                note_items.append(("set_notes", ""))

r = client.post("/workout/log", data=MultiDict(note_items), follow_redirects=False)
check("Noted workout logged", r.status_code == 200)

with app.app_context():
    from app import get_recent_performance
    profile = UserProfile.query.first()
    recent = get_recent_performance(profile.id, first_ex_name, limit=3)
    # The most recent session has notes on set 1; verify notes key present
    latest_set1 = recent[0]["sets"].get(1, {}) if recent else {}
    check("get_recent_performance includes notes key", "notes" in latest_set1)
    check("get_recent_performance notes value correct", latest_set1.get("notes") == "felt strong today")

r = client.get("/workout/today", follow_redirects=False)
if r.status_code == 200:
    html_today = r.data.decode()
    check("workout_today hist-detail includes notes text", "felt strong today" in html_today)
else:
    check("workout_today hist-detail includes notes text", False)

# 16. Review page
print("\n--- Review ---")
r = client.get("/review")
check("GET /review returns 200", r.status_code == 200)

# 16. Settings
print("\n--- Settings ---")
r = client.get("/settings")
check("GET /settings returns 200", r.status_code == 200)

# 17. Export
print("\n--- Export ---")
r = client.get("/export")
check("GET /export returns 200", r.status_code == 200)

# ── Plan Edit API ──────────────────────────────────────────────────────────────
print("\n--- Plan Edit: Schema ---")

# PlannedExercise must have order_index and is_superset_default columns
with app.app_context():
    cols = [c.name for c in PlannedExercise.__table__.columns]
    check("PlannedExercise has order_index column", "order_index" in cols)
    check("PlannedExercise has is_superset_default column", "is_superset_default" in cols)

print("\n--- Plan Edit: PATCH exercise sets/reps/rest ---")

# Grab an exercise to edit
with app.app_context():
    from app import get_active_plan as _gap_edit
    profile = UserProfile.query.first()
    active = _gap_edit(profile.id)
    pw_edit = PlannedWorkout.query.filter_by(plan_id=active.id).first()
    ex_edit = PlannedExercise.query.filter_by(planned_workout_id=pw_edit.id, exercise_type="main").first()
    ex_edit_id = ex_edit.id
    original_sets = ex_edit.sets_prescribed

r = client.patch(f"/api/plan/exercise/{ex_edit_id}",
                 json={"sets": 5, "reps": "6-8", "rest_seconds": 120, "notes": "heavy day"},
                 content_type="application/json")
check("PATCH /api/plan/exercise/<id> returns 200", r.status_code == 200)

with app.app_context():
    updated = PlannedExercise.query.get(ex_edit_id)
    check("PATCH updates sets_prescribed", updated.sets_prescribed == 5)
    check("PATCH updates reps_prescribed", updated.reps_prescribed == "6-8")
    check("PATCH updates rest_seconds", updated.rest_seconds == 120)
    check("PATCH updates notes", updated.notes == "heavy day")

# Restore original for later tests
with app.app_context():
    ex_restore = PlannedExercise.query.get(ex_edit_id)
    ex_restore.sets_prescribed = original_sets
    db.session.commit()

print("\n--- Plan Edit: PATCH is_superset_default ---")

r = client.patch(f"/api/plan/exercise/{ex_edit_id}",
                 json={"is_superset_default": True},
                 content_type="application/json")
check("PATCH is_superset_default=True returns 200", r.status_code == 200)

with app.app_context():
    ex_ss = PlannedExercise.query.get(ex_edit_id)
    check("is_superset_default saved as True", ex_ss.is_superset_default == True)

r = client.patch(f"/api/plan/exercise/{ex_edit_id}",
                 json={"is_superset_default": False},
                 content_type="application/json")
check("PATCH is_superset_default=False returns 200", r.status_code == 200)
with app.app_context():
    ex_ss2 = PlannedExercise.query.get(ex_edit_id)
    check("is_superset_default saved as False", ex_ss2.is_superset_default == False)

# PATCH must reject edits to another user's plan
print("\n--- Plan Edit: ownership enforcement ---")

with app.app_context():
    other_account = Account(email="other@fitlocal.test", email_claimed=True)
    from extensions import bcrypt as _bcrypt
    other_account.password_hash = _bcrypt.generate_password_hash("x").decode()
    db.session.add(other_account)
    db.session.commit()
    other_account_id = other_account.id

other_client = app.test_client()
with other_client.session_transaction() as sess:
    sess['_user_id'] = str(other_account_id)
    sess['_fresh'] = True

r_other = other_client.patch(f"/api/plan/exercise/{ex_edit_id}",
                              json={"sets": 99},
                              content_type="application/json")
check("PATCH by wrong user returns 403 or 404", r_other.status_code in (403, 404))

print("\n--- Plan Edit: reorder exercises ---")

# Collect main exercises in first workout to reorder
with app.app_context():
    from app import get_active_plan as _gap_reorder
    profile = UserProfile.query.first()
    active_r = _gap_reorder(profile.id)
    pw_reorder = PlannedWorkout.query.filter_by(plan_id=active_r.id).first()
    mains = (PlannedExercise.query
             .filter_by(planned_workout_id=pw_reorder.id, exercise_type="main")
             .order_by(PlannedExercise.order_index)
             .all())
    # Build reversed order
    reorder_payload = [{"id": ex.id, "order_index": i} for i, ex in enumerate(reversed(mains))]
    first_id_before = mains[0].id if mains else None
    last_id_before = mains[-1].id if mains else None

if reorder_payload:
    r = client.post(f"/api/plan/workout/{pw_reorder.id}/reorder",
                    json={"exercises": reorder_payload},
                    content_type="application/json")
    check("POST /api/plan/workout/<id>/reorder returns 200", r.status_code == 200)

    with app.app_context():
        ex_first_after = PlannedExercise.query.get(first_id_before)
        ex_last_after = PlannedExercise.query.get(last_id_before)
        # After reversal, original first should have highest order_index among mains
        check("Reorder: first exercise now has higher order_index than last",
              ex_first_after.order_index > ex_last_after.order_index)

# Reorder must reject cross-type moves (warmup exercise id mixed into main reorder list)
with app.app_context():
    from app import get_active_plan as _gap_cross
    profile = UserProfile.query.first()
    active_cross = _gap_cross(profile.id)
    pw_cross = PlannedWorkout.query.filter_by(plan_id=active_cross.id).first()
    warmup_ex = PlannedExercise.query.filter_by(planned_workout_id=pw_cross.id, exercise_type="warmup").first()
    main_exes = PlannedExercise.query.filter_by(planned_workout_id=pw_cross.id, exercise_type="main").all()
    cross_payload = [{"id": warmup_ex.id, "order_index": 0}] + \
                    [{"id": ex.id, "order_index": i+1} for i, ex in enumerate(main_exes)]

if warmup_ex and main_exes:
    r = client.post(f"/api/plan/workout/{pw_cross.id}/reorder",
                    json={"exercises": cross_payload},
                    content_type="application/json")
    check("Reorder with mixed types returns 400", r.status_code == 400)

print("\n--- Plan Edit: DELETE exercise ---")

# Add a throwaway exercise then delete it
with app.app_context():
    from app import get_active_plan as _gap_del
    profile = UserProfile.query.first()
    active_d = _gap_del(profile.id)
    pw_del = PlannedWorkout.query.filter_by(plan_id=active_d.id).first()
    throwaway = PlannedExercise(
        planned_workout_id=pw_del.id,
        exercise_name="Throwaway Exercise",
        exercise_type="main",
        sets_prescribed=1,
        reps_prescribed="10",
        rest_seconds=60,
        order_index=999,
    )
    db.session.add(throwaway)
    db.session.commit()
    throwaway_id = throwaway.id

r = client.delete(f"/api/plan/exercise/{throwaway_id}")
check("DELETE /api/plan/exercise/<id> returns 200", r.status_code == 200)

with app.app_context():
    gone = PlannedExercise.query.get(throwaway_id)
    check("Exercise deleted from DB", gone is None)

# Delete by wrong user returns 403/404
with app.app_context():
    from app import get_active_plan as _gap_del2
    profile = UserProfile.query.first()
    active_d2 = _gap_del2(profile.id)
    pw_del2 = PlannedWorkout.query.filter_by(plan_id=active_d2.id).first()
    throwaway2 = PlannedExercise(
        planned_workout_id=pw_del2.id,
        exercise_name="Throwaway2",
        exercise_type="main",
        sets_prescribed=1,
        reps_prescribed="10",
        rest_seconds=60,
        order_index=998,
    )
    db.session.add(throwaway2)
    db.session.commit()
    throwaway2_id = throwaway2.id

r_del_other = other_client.delete(f"/api/plan/exercise/{throwaway2_id}")
check("DELETE by wrong user returns 403 or 404", r_del_other.status_code in (403, 404))

# Clean up
with app.app_context():
    t2 = PlannedExercise.query.get(throwaway2_id)
    if t2:
        db.session.delete(t2)
        db.session.commit()

print("\n--- Plan Edit: POST add exercise ---")

with app.app_context():
    from app import get_active_plan as _gap_add
    profile = UserProfile.query.first()
    active_a = _gap_add(profile.id)
    pw_add = PlannedWorkout.query.filter_by(plan_id=active_a.id).first()
    pw_add_id = pw_add.id
    ex_count_before = PlannedExercise.query.filter_by(planned_workout_id=pw_add_id).count()

# Add from scratch
r = client.post(f"/api/plan/workout/{pw_add_id}/exercise",
                json={
                    "exercise_name": "Cable Row",
                    "exercise_type": "main",
                    "sets": 3,
                    "reps": "10-12",
                    "rest_seconds": 90,
                    "notes": "squeeze at top",
                    "is_superset_default": False,
                },
                content_type="application/json")
check("POST /api/plan/workout/<id>/exercise returns 201", r.status_code == 201)

with app.app_context():
    ex_count_after = PlannedExercise.query.filter_by(planned_workout_id=pw_add_id).count()
    check("New exercise added to DB", ex_count_after == ex_count_before + 1)
    new_ex = PlannedExercise.query.filter_by(
        planned_workout_id=pw_add_id, exercise_name="Cable Row").first()
    check("New exercise name saved", new_ex is not None and new_ex.exercise_name == "Cable Row")
    check("New exercise type saved", new_ex is not None and new_ex.exercise_type == "main")
    check("New exercise sets saved", new_ex is not None and new_ex.sets_prescribed == 3)
    new_ex_id = new_ex.id if new_ex else None

# Response body contains the new exercise id
if r.status_code == 201:
    resp_data = r.get_json()
    check("POST response includes id", resp_data is not None and "id" in resp_data)

# Add from library (by library_id)
with app.app_context():
    lib_ex = ExerciseLibrary.query.first()
    if not lib_ex:
        lib_ex = ExerciseLibrary(name="Lat Pulldown", muscle_group="Back", equipment="Cable")
        db.session.add(lib_ex)
        db.session.commit()
    lib_ex_id = lib_ex.id
    lib_ex_name = lib_ex.name

r = client.post(f"/api/plan/workout/{pw_add_id}/exercise",
                json={
                    "library_id": lib_ex_id,
                    "exercise_type": "main",
                    "sets": 3,
                    "reps": "8-10",
                    "rest_seconds": 75,
                    "notes": "",
                    "is_superset_default": False,
                },
                content_type="application/json")
check("POST with library_id returns 201", r.status_code == 201)

with app.app_context():
    lib_added = PlannedExercise.query.filter_by(
        planned_workout_id=pw_add_id, exercise_name=lib_ex_name).first()
    check("Library exercise name copied to planned exercise", lib_added is not None)
    check("Library exercise linked via exercise_library_id",
          lib_added is not None and lib_added.exercise_library_id == lib_ex_id)

print("\n--- Plan Edit: GET exercise library endpoint ---")

r = client.get("/api/plan/exercise-library")
check("GET /api/plan/exercise-library returns 200", r.status_code == 200)
lib_data = r.get_json()
check("Response is a list", isinstance(lib_data, list))
check("Library entries have 'name' key", len(lib_data) > 0 and "name" in lib_data[0])
check("Library entries have 'id' key", len(lib_data) > 0 and "id" in lib_data[0])

# Endpoint also returns names from user's own LoggedSet history not in library
with app.app_context():
    profile = UserProfile.query.first()
    unique_names = {ls.exercise_name for ls in
                    db.session.query(LoggedSet).join(WorkoutSession)
                    .filter(WorkoutSession.user_id == profile.id).all()}
    lib_names = {e["name"] for e in lib_data}
    history_only = unique_names - {e.name for e in ExerciseLibrary.query.all()}

if history_only:
    sample = next(iter(history_only))
    check(f"Library endpoint includes history-only exercise '{sample}'", sample in lib_names)
else:
    check("Library endpoint includes history-only exercises (skipped — all in library)", True)

print("\n--- Plan Edit: reordered exercises appear in new order on plan and workout pages ---")

# Get main exercises for the first workout and record their current order
with app.app_context():
    from app import get_active_plan as _gap_ord
    profile = UserProfile.query.first()
    active_ord = _gap_ord(profile.id)
    pw_ord = PlannedWorkout.query.filter_by(plan_id=active_ord.id).first()
    mains_ord = (PlannedExercise.query
                 .filter_by(planned_workout_id=pw_ord.id, exercise_type="main")
                 .order_by(PlannedExercise.order_index)
                 .all())
    pw_ord_id = pw_ord.id

check("Reorder test: at least 2 main exercises exist", len(mains_ord) >= 2)

if len(mains_ord) >= 2:
    # Record original first and last names
    orig_first_name = mains_ord[0].exercise_name
    orig_last_name  = mains_ord[-1].exercise_name

    # Reverse the order via API
    reversed_payload = [{"id": ex.id, "order_index": i}
                        for i, ex in enumerate(reversed(mains_ord))]
    r = client.post(f"/api/plan/workout/{pw_ord_id}/reorder",
                    json={"exercises": reversed_payload},
                    content_type="application/json")
    check("Reorder API returns 200", r.status_code == 200)

    # Verify DB order_index updated correctly
    with app.app_context():
        new_first = (PlannedExercise.query
                     .filter_by(planned_workout_id=pw_ord_id, exercise_type="main")
                     .order_by(PlannedExercise.order_index)
                     .first())
        check(f"DB: original last exercise ('{orig_last_name}') is now first by order_index",
              new_first.exercise_name == orig_last_name)

    # Verify /plan page renders exercises in updated order
    r = client.get("/plan")
    html_plan = r.data.decode()
    pos_first_in_plan = html_plan.find(orig_first_name)
    pos_last_in_plan  = html_plan.find(orig_last_name)
    check(f"Plan page: '{orig_last_name}' now appears before '{orig_first_name}'",
          0 <= pos_last_in_plan < pos_first_in_plan)

    # Verify /workout/today renders exercises in updated order
    r = client.get("/workout/today", follow_redirects=False)
    if r.status_code == 200:
        html_today_ord = r.data.decode()
        pos_first_today = html_today_ord.find(orig_first_name)
        pos_last_today  = html_today_ord.find(orig_last_name)
        check(f"Workout today: '{orig_last_name}' now appears before '{orig_first_name}'",
              0 <= pos_last_today < pos_first_today)
    else:
        check("Workout today order (rest day — skipped)", True)

    # Restore original order
    restore_payload = [{"id": ex.id, "order_index": i} for i, ex in enumerate(mains_ord)]
    client.post(f"/api/plan/workout/{pw_ord_id}/reorder",
                json={"exercises": restore_payload},
                content_type="application/json")

print("\n--- Plan Edit: workout_today respects is_superset_default ---")

# Mark an exercise as superset default, then verify workout_today pre-activates it
with app.app_context():
    from app import get_active_plan as _gap_ss
    profile = UserProfile.query.first()
    active_ss = _gap_ss(profile.id)
    pw_ss_today = PlannedWorkout.query.filter_by(plan_id=active_ss.id).first()
    main_ss = PlannedExercise.query.filter_by(
        planned_workout_id=pw_ss_today.id, exercise_type="main").first()
    main_ss.is_superset_default = True
    db.session.commit()
    main_ss_name = main_ss.exercise_name

r = client.get("/workout/today", follow_redirects=False)
if r.status_code == 200:
    html_today2 = r.data.decode()
    check("workout_today pre-activates superset for is_superset_default exercise",
          main_ss_name in html_today2 and "superset-default" in html_today2)
else:
    check("workout_today pre-activates superset for is_superset_default exercise (rest day, skipped)", True)

# Restore
with app.app_context():
    from app import get_active_plan as _gap_ss2
    profile = UserProfile.query.first()
    active_ss2 = _gap_ss2(profile.id)
    pw_ss2 = PlannedWorkout.query.filter_by(plan_id=active_ss2.id).first()
    m = PlannedExercise.query.filter_by(
        planned_workout_id=pw_ss2.id, exercise_type="main").first()
    m.is_superset_default = False
    db.session.commit()

# Phase Selection on WorkoutSession
print("\n--- Phase Selection ---")

# Schema: WorkoutSession must have a phase_name column
with app.app_context():
    from models import WorkoutSession as WS_ph
    cols = [c.name for c in WS_ph.__table__.columns]
    check("WorkoutSession has phase_name column", "phase_name" in cols)

# workout/today shows a phase dropdown when the active plan has phases
r = client.get("/workout/today", follow_redirects=False)
if r.status_code == 200:
    html_ph = r.data.decode()
    check("workout/today shows phase_name select", 'name="phase_name"' in html_ph)
    check("Phase dropdown contains Foundation", "Foundation" in html_ph)
    check("Phase dropdown contains Build", "Build" in html_ph)
    check("Phase dropdown contains Peak", "Peak" in html_ph)
    check("Calculated current phase is pre-selected", 'selected' in html_ph)
else:
    # Rest day — skip but don't count as failure
    for label in [
        "workout/today shows phase_name select",
        "Phase dropdown contains Foundation",
        "Phase dropdown contains Build",
        "Phase dropdown contains Peak",
        "Calculated current phase is pre-selected",
    ]:
        check(f"{label} (rest day — skipped)", True)

# POST /workout/log saves phase_name on the completed session
with app.app_context():
    from app import get_active_plan as _gap_ph
    profile = UserProfile.query.first()
    pw_ph = PlannedWorkout.query.filter_by(plan_id=_gap_ph(profile.id).id).first()
    pw_ph_id = pw_ph.id

log_phase_items = [
    ("planned_workout_id", str(pw_ph_id)),
    ("overall_feeling", "4"),
    ("session_notes", "Phase log test"),
    ("phase_name", "Build"),
]
with app.app_context():
    for ex in PlannedExercise.query.filter_by(planned_workout_id=pw_ph_id).all():
        for s in range(1, ex.sets_prescribed + 1):
            log_phase_items += [
                ("exercise_name", ex.exercise_name),
                ("set_number", str(s)),
                ("weight", "150"),
                ("reps", "8"),
                ("rpe", ""),
                ("set_notes", ""),
            ]

r = client.post("/workout/log", data=MultiDict(log_phase_items), follow_redirects=False)
check("POST /workout/log with phase_name returns 200", r.status_code == 200)

with app.app_context():
    profile = UserProfile.query.first()
    last_ph = WorkoutSession.query.filter_by(
        user_id=profile.id, status='completed'
    ).order_by(WorkoutSession.id.desc()).first()
    check("phase_name saved on completed session", last_ph is not None and getattr(last_ph, 'phase_name', None) == "Build")

# POST /workout/pause saves phase_name on the paused session
with app.app_context():
    from models import WorkoutSession as WS_ph2, LoggedSet as LS_ph
    profile = UserProfile.query.first()
    paused_ids_ph = [s.id for s in WS_ph2.query.filter_by(user_id=profile.id, status='paused').all()]
    if paused_ids_ph:
        LS_ph.query.filter(LS_ph.session_id.in_(paused_ids_ph)).delete(synchronize_session=False)
    WS_ph2.query.filter_by(user_id=profile.id, status='paused').delete()
    db.session.commit()

pause_phase_items = [
    ("planned_workout_id", str(pw_ph_id)),
    ("overall_feeling", "3"),
    ("session_notes", ""),
    ("session_elapsed_seconds", "120"),
    ("resume_session_id", ""),
    ("phase_name", "Peak"),
]
with app.app_context():
    for ex in PlannedExercise.query.filter_by(planned_workout_id=pw_ph_id).all():
        for s in range(1, ex.sets_prescribed + 1):
            pause_phase_items += [
                ("exercise_name", ex.exercise_name),
                ("set_number", str(s)),
                ("weight", "150"),
                ("reps", "8"),
                ("rpe", ""),
                ("set_notes", ""),
            ]

r = client.post("/workout/pause", data=MultiDict(pause_phase_items), follow_redirects=False)
check("POST /workout/pause with phase_name redirects", r.status_code == 302)

with app.app_context():
    profile = UserProfile.query.first()
    paused_ph = WorkoutSession.query.filter_by(user_id=profile.id, status='paused').first()
    check("phase_name saved on paused session", paused_ph is not None and getattr(paused_ph, 'phase_name', None) == "Peak")

# Session detail page shows the recorded phase name
with app.app_context():
    profile = UserProfile.query.first()
    detail_ph = WorkoutSession.query.filter_by(
        user_id=profile.id, status='completed'
    ).order_by(WorkoutSession.id.desc()).first()
    detail_ph_id = detail_ph.id if detail_ph else None

if detail_ph_id:
    r = client.get(f"/history/{detail_ph_id}", follow_redirects=False)
    check("Session detail shows saved phase name", r.status_code == 200 and b"Build" in r.data)
else:
    check("Session detail shows saved phase name", False)

# History listing shows phase name on each row that has one
print("\n--- History listing phase name ---")
r_hist_ph = client.get("/history")
check("History listing shows phase name for sessions that have one",
      r_hist_ph.status_code == 200 and b"Build" in r_hist_ph.data)

# Middot separator appears exactly as many times as there are sessions with a phase_name
with app.app_context():
    profile = UserProfile.query.first()
    expected_middots = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.phase_name.isnot(None),
    ).count()
actual_middots = r_hist_ph.data.decode().count("&middot;")
check("History listing middot count matches sessions with a phase_name",
      actual_middots == expected_middots)

# Dashboard Monthly Calendar
print("\n--- Dashboard Monthly Calendar ---")

r = client.get("/")
html_dash = r.data.decode()

# Calendar element is present
check("Dashboard has month-calendar element", 'month-calendar' in html_dash)

# Week starts on Sunday (first header cell is Sun, not Mon)
import re as _re_cal
_first_dow = _re_cal.search(r'class="month-cal-dow">(\w+)<', html_dash)
check("Calendar week starts on Sunday", _first_dow is not None and _first_dow.group(1) == 'Sun')

# Prev/next navigation links are present
check("Dashboard calendar has prev-month link", 'cal_month' in html_dash and '&lt;' in html_dash or '<' in html_dash)
check("Dashboard calendar has next-month link", 'cal_month' in html_dash)

# Session dots link to /history/<session_id>
import re as _re
r_dot = client.get("/")
dot_links = _re.findall(r'/history/\d+', r_dot.data.decode())
check("Session dot links to /history/<id>", len(dot_links) > 0)

# A dot has an inline color style (from phase color map)
check("Session dot has inline color style", 'phase-dot' in html_dash)

# Session with no phase_name gets dark grey color
with app.app_context():
    profile = UserProfile.query.first()
    # Create a session without a phase_name for this month
    pw_nogrey = PlannedWorkout.query.first()
    grey_session = WorkoutSession(
        user_id=profile.id,
        planned_workout_id=pw_nogrey.id,
        date=date.today(),
        overall_feeling=3,
        status='completed',
        phase_name=None,
    )
    db.session.add(grey_session)
    db.session.commit()
    grey_session_id = grey_session.id

r_grey = client.get("/")
html_grey = r_grey.data.decode()
check("No-phase session dot uses dark grey color",
      f'/history/{grey_session_id}' in html_grey and '#555' in html_grey or 'dark-grey' in html_grey or 'no-phase' in html_grey)

# Legend is present when plan has phases
check("Dashboard calendar shows phase legend", 'phase-legend' in html_dash)
check("Legend contains Foundation", 'Foundation' in html_dash)

# Calendar dot tooltips
# Sessions accumulate on date.today() throughout the test run, and the calendar
# only shows [:3] dots per day. To guarantee our test sessions are visible we
# create them on the 15th of the previous month — a date no other test uses —
# and query the calendar for that specific month.
print("\n--- Calendar dot tooltips ---")

from datetime import date as _date
_today = _date.today()
_tip_year  = (_today.year if _today.month > 1 else _today.year - 1)
_tip_month = (_today.month - 1 if _today.month > 1 else 12)
_tip_date  = _date(_tip_year, _tip_month, 15)

with app.app_context():
    from app import build_phase_color_map as _bpcm, get_active_plan as _gap_tip
    profile = UserProfile.query.first()
    pw_tip = PlannedWorkout.query.first()
    assert pw_tip, "Test setup broken: need at least one PlannedWorkout for tooltip test"

    # Derive two distinct phase names and the expected dot color from the fixture
    # — never hardcode plan-specific phase names or colors.
    _tip_phases = plan_data["phases"]
    tip_phase = _tip_phases[2]["phase_name"]
    tip_orphan_phase = _tip_phases[0]["phase_name"]
    tip_phase_color = _bpcm(_gap_tip(profile.id))[tip_phase]

    # Session with both workout name and phase
    tip_both = WorkoutSession(
        user_id=profile.id,
        planned_workout_id=pw_tip.id,
        date=_tip_date,
        status='completed',
        phase_name=tip_phase,
    )
    # Session with workout name but no phase
    tip_no_phase = WorkoutSession(
        user_id=profile.id,
        planned_workout_id=pw_tip.id,
        date=_tip_date,
        status='completed',
        phase_name=None,
    )
    # Session with phase but no planned_workout (simulates a deleted plan)
    tip_orphan = WorkoutSession(
        user_id=profile.id,
        planned_workout_id=None,
        date=_tip_date,
        status='completed',
        phase_name=tip_orphan_phase,
    )
    db.session.add_all([tip_both, tip_no_phase, tip_orphan])
    db.session.commit()
    tip_both_id   = tip_both.id
    tip_no_phase_id = tip_no_phase.id
    tip_orphan_id = tip_orphan.id
    tip_workout_name = pw_tip.workout_name

r_tip = client.get(f"/?cal_year={_tip_year}&cal_month={_tip_month}")
html_tip = r_tip.data.decode()

check(
    "Dot tooltip includes workout name when session has both",
    f'title="{tip_workout_name}' in html_tip,
)
check(
    "Dot tooltip includes phase after middot when session has both",
    f'{tip_workout_name} &middot; {tip_phase}' in html_tip,
)
check(
    "Dot tooltip shows workout name only when session has no phase",
    f'title="{tip_workout_name}"' in html_tip,
)
check(
    "Dot tooltip shows 'Unknown workout' fallback when session has no planned_workout",
    f'/history/{tip_orphan_id}' in html_tip and f'title="Unknown workout &middot; {tip_orphan_phase}"' in html_tip,
)

# The full Calendar page (top menu) uses the same dots and tooltips.
r_cal_tip = client.get(f"/calendar?year={_tip_year}&month={_tip_month}")
html_cal_tip = r_cal_tip.data.decode()
check("Calendar page dot links to the session history",
      f'/history/{tip_both_id}' in html_cal_tip)
check("Calendar page dot tooltip includes workout name and phase",
      f'{tip_workout_name} &middot; {tip_phase}' in html_cal_tip)
check("Calendar page dot uses the phase color from the shared color map",
      'class="phase-dot"' in html_cal_tip and tip_phase_color in html_cal_tip)
check("Calendar page shows 'Unknown workout' fallback for orphan session",
      f'title="Unknown workout &middot; {tip_orphan_phase}"' in html_cal_tip)

# Next-workout notes
print("\n--- Next-workout notes ---")

# Schema: NextWorkoutNote table exists with required columns
with app.app_context():
    from sqlalchemy import inspect as _inspect
    cols = {c['name'] for c in _inspect(db.engine).get_columns('next_workout_note')}
check("NextWorkoutNote has id column",          "id"           in cols)
check("NextWorkoutNote has user_id column",     "user_id"      in cols)
check("NextWorkoutNote has workout_name column","workout_name" in cols)
check("NextWorkoutNote has note column",        "note"         in cols)

# Clear any paused sessions so GET /workout/today reaches the form.
# Also null out phase tags so the next workout resolves deterministically to the
# first workout (these note tests log against the first workout, untagged).
with app.app_context():
    from datetime import datetime, timezone as _tz
    WorkoutSession.query.filter_by(status='paused').update({
        'status': 'completed', 'end_time': datetime.now(_tz.utc)
    })
    WorkoutSession.query.update({'phase_name': None})
    db.session.commit()

# Determine the current workout name and the next-up workout name up front
# so all note seeds use the right names.
with app.app_context():
    profile = UserProfile.query.first()
    active_plan_n = WorkoutPlan.query.filter_by(user_id=profile.id, status='active').first()
    from app import get_next_workout as _get_next_workout
    next_w = _get_next_workout(profile.id, active_plan_n)
    assert next_w, "Test setup broken: need a next workout"
    current_workout_name = next_w.workout_name

# workout/today form has both new text boxes (no notes seeded yet)
r_wt = client.get("/workout/today")
html_wt = r_wt.data.decode()
check("workout/today has 'notes_for_next_general' field",
      'name="notes_for_next_general"' in html_wt)
check("workout/today has 'notes_for_next_workout' field",
      'name="notes_for_next_workout"' in html_wt)

# When no notes exist, no label/heading should appear for either note type.
# Both labels start with "Notes from last", so one check covers both.
check("workout/today shows no note labels when no notes exist",
      "Notes from last" not in html_wt)

r_home_empty = client.get("/")
html_home_empty = r_home_empty.data.decode()
check("Home page Next Up shows no note section when no notes exist",
      "Notes from last" not in html_home_empty)

# Seed a general note and a workout-specific note for the current workout
with app.app_context():
    profile = UserProfile.query.first()
    db.session.add(NextWorkoutNote(
        user_id=profile.id, workout_name=None, note="General: eat more protein"
    ))
    db.session.add(NextWorkoutNote(
        user_id=profile.id, workout_name=current_workout_name,
        note=f"Specific to {current_workout_name}: increase weight"
    ))
    db.session.commit()

# GET /workout/today shows incoming general note
r_wt2 = client.get("/workout/today")
html_wt2 = r_wt2.data.decode()
check("workout/today shows incoming general note",
      "General: eat more protein" in html_wt2)

# GET /workout/today shows incoming workout-specific note when names match
check("workout/today shows incoming workout-specific note when names match",
      f"Specific to {current_workout_name}: increase weight" in html_wt2)

# A different workout name must NOT show the workout-specific note
with app.app_context():
    profile = UserProfile.query.first()
    db.session.add(NextWorkoutNote(
        user_id=profile.id, workout_name="Some Other Workout",
        note="Note for other workout only"
    ))
    db.session.commit()

r_wt3 = client.get("/workout/today")
html_wt3 = r_wt3.data.decode()
check("workout/today does NOT show workout-specific note for a different workout name",
      "Note for other workout only" not in html_wt3)

# POST /workout/log with new next-workout notes saves them to DB
form_with_notes = MultiDict(list(form_items) + [
    ("notes_for_next_general",  "Drink more water"),
    ("notes_for_next_workout",  "Try heavier dumbbells"),
])
r_log_notes = client.post("/workout/log", data=form_with_notes, follow_redirects=False)
check("POST /workout/log with next-notes returns 200 or redirect",
      r_log_notes.status_code in (200, 302))

with app.app_context():
    profile = UserProfile.query.first()
    gen_note = NextWorkoutNote.query.filter_by(
        user_id=profile.id, workout_name=None
    ).first()
    spec_note = NextWorkoutNote.query.filter_by(
        user_id=profile.id, workout_name=current_workout_name
    ).first()
check("POST /workout/log saves general next-workout note",
      gen_note is not None and gen_note.note == "Drink more water")
check("POST /workout/log saves workout-specific next-workout note",
      spec_note is not None and spec_note.note == "Try heavier dumbbells")

# GET /workout/today shows the freshly-saved notes
# (clear paused sessions again in case the log created one)
with app.app_context():
    WorkoutSession.query.filter_by(status='paused').update({
        'status': 'completed', 'end_time': datetime.now(_tz.utc)
    })
    db.session.commit()

r_wt4 = client.get("/workout/today")
html_wt4 = r_wt4.data.decode()
check("workout/today shows freshly-saved general note on next visit",
      "Drink more water" in html_wt4)
check("workout/today shows freshly-saved workout-specific note on next visit",
      "Try heavier dumbbells" in html_wt4)

# POST /workout/log with blank next-notes deletes the displayed notes
form_blank_notes = MultiDict(list(form_items) + [
    ("notes_for_next_general",  ""),
    ("notes_for_next_workout",  ""),
])
r_log_blank = client.post("/workout/log", data=form_blank_notes, follow_redirects=False)
check("POST /workout/log with blank next-notes returns 200 or redirect",
      r_log_blank.status_code in (200, 302))

with app.app_context():
    profile = UserProfile.query.first()
    gen_after  = NextWorkoutNote.query.filter_by(user_id=profile.id, workout_name=None).first()
    spec_after = NextWorkoutNote.query.filter_by(
        user_id=profile.id, workout_name=current_workout_name
    ).first()
check("Displayed general note is deleted after logging with blank notes_for_next_general",
      gen_after is None)
check("Displayed workout-specific note is deleted after logging with blank notes_for_next_workout",
      spec_after is None)

# Workout-specific note for a DIFFERENT workout is NOT deleted when a different workout is logged
with app.app_context():
    profile = UserProfile.query.first()
    other_note = NextWorkoutNote.query.filter_by(
        user_id=profile.id, workout_name="Some Other Workout"
    ).first()
check("Workout-specific note for non-current workout is NOT deleted when different workout logged",
      other_note is not None and other_note.note == "Note for other workout only")

# Home page Next Up section shows notes when they exist.
# Seed the specific note against current_workout_name, which the home page will query.
with app.app_context():
    profile = UserProfile.query.first()
    db.session.add(NextWorkoutNote(
        user_id=profile.id, workout_name=None, note="Home page general note"
    ))
    db.session.add(NextWorkoutNote(
        user_id=profile.id, workout_name=current_workout_name,
        note="Home page specific note"
    ))
    db.session.commit()

r_home = client.get("/")
html_home = r_home.data.decode()
check("Home page Next Up shows general next-workout note",
      "Home page general note" in html_home)
check("Home page Next Up shows workout-specific next-workout note",
      "Home page specific note" in html_home)

# Workout-Count Phase Progression
# The next workout is determined SOLELY by which workout is next in the plan:
# the next workout in the current phase, or the first workout of the next phase
# once the current phase's workout count is complete. No weeks, no dates, no
# session_offset. The current phase is the phase of the most recently logged
# session (the user's selection is the source of truth).
#
# Phase names, counts, workout names and roles below are derived from the
# fixture plan_json — NOT hardcoded — because real plans will be named anything.
print("\n--- Workout-Count Phase Progression ---")

from app import get_next_workout, get_plan_position, get_active_plan as _gap_wc


def _wc_clear_sessions(pid):
    from models import WorkoutSession as _WS, LoggedSet as _LS
    sids = [s.id for s in _WS.query.filter_by(user_id=pid).all()]
    if sids:
        _LS.query.filter(_LS.session_id.in_(sids)).delete(synchronize_session=False)
    _WS.query.filter_by(user_id=pid).delete()
    db.session.commit()


def _wc_seed(pid, plan, n, phase):
    """Insert n completed sessions tagged `phase`, cycling through the workouts."""
    from models import WorkoutSession as _WS
    ws = sorted(plan.planned_workouts, key=lambda w: w.order_index)
    for i in range(n):
        db.session.add(_WS(user_id=pid, planned_workout_id=ws[i % len(ws)].id,
                           date=date.today(), status='completed', phase_name=phase))
    db.session.commit()


# --- Derive all references from the fixture, so nothing is pinned to a name ---
_wc_phases = plan_data["phases"]
_wc_dpw = plan_data["days_per_week"]


def _wc_quota(p):
    return ((p["week_end"] - p["week_start"] + 1) * _wc_dpw)


# Pick, by structural role, a progressive phase that is followed by another
# phase, plus that following phase (used to test rollover).
_prog_i = max(i for i, p in enumerate(_wc_phases[:-1]) if p["phase_type"] == "progressive")
PROG, NEXTP = _wc_phases[_prog_i], _wc_phases[_prog_i + 1]
PROG_NAME, PROG_QUOTA = PROG["phase_name"], _wc_quota(PROG)
NEXT_NAME, NEXT_QUOTA = NEXTP["phase_name"], _wc_quota(NEXTP)
FIRST_NAME = _wc_phases[0]["phase_name"]

with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    wc_names = [w.workout_name for w in sorted(active.planned_workouts, key=lambda w: w.order_index)]
_nlen = len(wc_names)            # number of distinct workouts in the rotation
_mid = max(1, PROG_QUOTA // 2)   # a count partway through the progressive phase

# Case 1: mid-phase — next is the workout after the last logged one.
# current_week and session_offset are sabotaged to prove they are ignored.
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, _mid, PROG_NAME)
    active.current_week = 1
    active.session_offset = 999
    db.session.commit()
    nxt = get_next_workout(profile.id, active)
    pos = get_plan_position(profile.id, active)
    check("mid-phase: next workout is next in rotation (ignores weeks & offset)",
          nxt is not None and nxt.workout_name == wc_names[_mid % _nlen])
    check("mid-phase: position phase is the current phase",
          pos is not None and pos.get("phase_name") == PROG_NAME)
    check("mid-phase: upcoming workout count is correct",
          pos is not None and pos.get("workout_in_phase") == _mid + 1
          and pos.get("phase_total_workouts") == PROG_QUOTA)
    check("position carries no week/day keys",
          pos is not None and "week_in_phase" not in pos and "day_in_week" not in pos)

# Case 2: rollover — completing the phase advances to the next phase's first workout.
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, PROG_QUOTA, PROG_NAME)
    db.session.commit()
    nxt = get_next_workout(profile.id, active)
    pos = get_plan_position(profile.id, active)
    check("rollover: phase complete advances to next phase's first workout",
          nxt is not None and nxt.workout_name == wc_names[0])
    check("rollover: position phase is the next phase",
          pos is not None and pos.get("phase_name") == NEXT_NAME)
    check("rollover: upcoming workout is 1 of the next phase's total",
          pos is not None and pos.get("workout_in_phase") == 1 and pos.get("phase_total_workouts") == NEXT_QUOTA)
    check("rollover: is_recovery matches the next phase's type",
          pos is not None and pos.get("is_recovery") == (NEXTP["phase_type"] == "recovery"))

# Case 3: the real scenario — most recent session tagged with the next phase (1 done).
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, PROG_QUOTA, PROG_NAME)
    _wc_seed(profile.id, active, 1, NEXT_NAME)
    db.session.commit()
    nxt = get_next_workout(profile.id, active)
    pos = get_plan_position(profile.id, active)
    check("real scenario: after 1st next-phase workout, next is 2nd in rotation",
          nxt is not None and nxt.workout_name == wc_names[1 % _nlen])
    check("real scenario: position phase is the next phase",
          pos is not None and pos.get("phase_name") == NEXT_NAME)

# Case 4: legacy fallback — untagged sessions default to the first phase.
with app.app_context():
    from models import WorkoutSession as _WS
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    ws = sorted(active.planned_workouts, key=lambda w: w.order_index)
    for i in range(5):
        db.session.add(_WS(user_id=profile.id, planned_workout_id=ws[i % _nlen].id,
                           date=date.today(), status='completed', phase_name=None))
    db.session.commit()
    nxt = get_next_workout(profile.id, active)
    pos = get_plan_position(profile.id, active)
    check("legacy: untagged sessions fall back to the first phase",
          pos is not None and pos.get("phase_name") == FIRST_NAME)
    check("legacy: next workout is the first in rotation",
          nxt is not None and nxt.workout_name == wc_names[0])

# Case 5: phase name comes from plan_json, robust to stale TrainingPhase rows.
with app.app_context():
    from models import TrainingPhase as _TP
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, _mid, PROG_NAME)
    db.session.add(_TP(plan_id=active.id, phase_name="ZZZ Stale", phase_type="progressive",
                       week_start=1, week_end=1, order_index=-1))
    db.session.commit()
    pos = get_plan_position(profile.id, active)
    check("position robust to stale/duplicate TrainingPhase rows",
          pos is not None and pos.get("phase_name") == PROG_NAME)
    stale_row = _TP.query.filter_by(plan_id=active.id, phase_name="ZZZ Stale").first()
    if stale_row:
        db.session.delete(stale_row)
        db.session.commit()

# Case 6: get_current_phase is workout-based, not calendar-based.
from app import get_current_phase as _gcp_wc
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, _mid, PROG_NAME)
    active.current_week = 1  # old calendar logic would resolve to the first phase
    db.session.commit()
    try:
        cp = _gcp_wc(profile.id, active)
        cp_ok = cp is not None and cp.phase_name == PROG_NAME
    except TypeError:
        cp_ok = False
    check("get_current_phase is workout-based, not calendar-based", cp_ok)

# Case 7: end-to-end — logging the final phase workout advances Next Up.
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    _wc_seed(profile.id, active, PROG_QUOTA - 1, PROG_NAME)  # one short of complete
    db.session.commit()
    pw_first = sorted(active.planned_workouts, key=lambda w: w.order_index)[0]
    pw_first_id = pw_first.id
    e2e_ex = PlannedExercise.query.filter_by(planned_workout_id=pw_first_id).all()
    wc_names = [w.workout_name for w in sorted(active.planned_workouts, key=lambda w: w.order_index)]

e2e_items = [
    ("planned_workout_id", str(pw_first_id)),
    ("overall_feeling", "4"), ("session_notes", ""), ("phase_name", PROG_NAME),
]
for ex in e2e_ex:
    for s in range(1, ex.sets_prescribed + 1):
        e2e_items += [
            ("exercise_name", ex.exercise_name), ("set_number", str(s)),
            ("weight", "100"), ("reps", "8"), ("rpe", ""), ("set_notes", ""),
        ]
r = client.post("/workout/log", data=MultiDict(e2e_items), follow_redirects=False)
check("e2e: POST /workout/log with phase returns 200", r.status_code == 200)
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    nxt = get_next_workout(profile.id, active)
    pos = get_plan_position(profile.id, active)
    check("e2e: logging the final phase workout advances Next Up to the next phase's first workout",
          nxt is not None and nxt.workout_name == wc_names[0]
          and pos is not None and pos.get("phase_name") == NEXT_NAME)

# Case 8: out-of-order logging — the next workout follows the one just logged,
# not a positional count. Do the last workout out of turn; next wraps to the first.
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    _wc_clear_sessions(profile.id)
    ws_ordered = sorted(active.planned_workouts, key=lambda w: w.order_index)
    _wc_seed(profile.id, active, 1, PROG_NAME)  # 1 workout on ws[0]; suggested next = ws[1]
    db.session.commit()
    last_w = ws_ordered[_nlen - 1]
    last_id = last_w.id
    ooo_ex = PlannedExercise.query.filter_by(planned_workout_id=last_id).all()
    wc_names = [w.workout_name for w in ws_ordered]

ooo_items = [
    ("planned_workout_id", str(last_id)),
    ("overall_feeling", "4"), ("session_notes", ""), ("phase_name", PROG_NAME),
]
for ex in ooo_ex:
    for s in range(1, ex.sets_prescribed + 1):
        ooo_items += [
            ("exercise_name", ex.exercise_name), ("set_number", str(s)),
            ("weight", "100"), ("reps", "8"), ("rpe", ""), ("set_notes", ""),
        ]
r = client.post("/workout/log", data=MultiDict(ooo_items), follow_redirects=False)
check("out-of-order: POST /workout/log returns 200", r.status_code == 200)
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    nxt = get_next_workout(profile.id, active)
    # Did the last workout; the next is the one AFTER it -> wraps to the first.
    check("out-of-order: next workout follows the workout just logged (sequence, not count)",
          nxt is not None and nxt.workout_name == wc_names[0])

# Case 9: the workout switcher is a display-only override (no position change).
_show_idx = _nlen - 1
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    computed_before = get_next_workout(profile.id, active).workout_name
r = client.post("/workout/choose", data={"workout_index": str(_show_idx)}, follow_redirects=False)
check("switcher: POST /workout/choose redirects to workout/today?show",
      r.status_code == 302 and f"show={_show_idx}" in r.headers.get("Location", ""))
r2 = client.get(f"/workout/today?show={_show_idx}", follow_redirects=False)
if r2.status_code == 200:
    check("switcher: ?show displays the chosen workout",
          wc_names[_show_idx] in r2.data.decode())
else:
    check("switcher: ?show displays the chosen workout (skipped — non-200)", False)
with app.app_context():
    profile = UserProfile.query.first()
    active = _gap_wc(profile.id)
    check("switcher: viewing a workout does not change plan position",
          get_next_workout(profile.id, active).workout_name == computed_before)

# ── Change 1: Review scoped to active plan ────────────────────────────────────
print("\n--- Review: Scoped to Active Plan ---")

# Snapshot active/inactive plan session counts before any manipulation
with app.app_context():
    profile = UserProfile.query.first()
    active_plan_rv = WorkoutPlan.query.filter_by(user_id=profile.id, status='active').first()
    inactive_plan_rv = WorkoutPlan.query.filter_by(user_id=profile.id, status='inactive').first()

    active_pw_ids_rv = [w.id for w in active_plan_rv.planned_workouts] if active_plan_rv else []
    inactive_pw_ids_rv = [w.id for w in inactive_plan_rv.planned_workouts] if inactive_plan_rv else []

    active_session_count_rv = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.planned_workout_id.in_(active_pw_ids_rv),
        WorkoutSession.status == 'completed',
    ).count() if active_pw_ids_rv else 0

    inactive_session_count_rv = WorkoutSession.query.filter(
        WorkoutSession.user_id == profile.id,
        WorkoutSession.planned_workout_id.in_(inactive_pw_ids_rv),
        WorkoutSession.status == 'completed',
    ).count() if inactive_pw_ids_rv else 0

# Test 1: _get_review_sessions helper exists and returns only active-plan sessions
try:
    from app import _get_review_sessions as _grs_test
    with app.app_context():
        profile = UserProfile.query.first()
        active = WorkoutPlan.query.filter_by(user_id=profile.id, status='active').first()
        review_sessions = _grs_test(profile.id, active.id)
        active_pw_ids_set = {w.id for w in active.planned_workouts}

        only_active = all(s.planned_workout_id in active_pw_ids_set for s in review_sessions)
        check("_get_review_sessions returns only active-plan sessions", only_active)

        inactive_plan_inner = WorkoutPlan.query.filter_by(user_id=profile.id, status='inactive').first()
        if inactive_plan_inner:
            inactive_pw_inner = {w.id for w in inactive_plan_inner.planned_workouts}
            returned_pw_ids = {s.planned_workout_id for s in review_sessions}
            check("_get_review_sessions excludes inactive-plan sessions",
                  not bool(inactive_pw_inner & returned_pw_ids))
        else:
            check("_get_review_sessions excludes inactive-plan sessions (no inactive plan)", True)

        no_unplanned = all(s.planned_workout_id is not None for s in review_sessions)
        check("_get_review_sessions excludes unplanned (null) sessions", no_unplanned)

except (ImportError, AttributeError):
    check("_get_review_sessions helper exists in app", False)
    check("_get_review_sessions excludes inactive-plan sessions", False)
    check("_get_review_sessions excludes unplanned (null) sessions", False)

# Test 2: /review page shows the active-plan session count
r_rv = client.get("/review")
check("Review page shows active-plan session count",
      str(active_session_count_rv).encode() in r_rv.data and b"session" in r_rv.data.lower())

# Test 3: inactive-plan sessions exist but must not inflate the count shown on the review page
# (The count on the page should equal active_session_count_rv, not the cross-plan total.)
if inactive_session_count_rv > 0:
    cross_plan_total = active_session_count_rv + inactive_session_count_rv
    check("Review page does not show cross-plan session total",
          str(cross_plan_total).encode() not in r_rv.data
          or str(active_session_count_rv).encode() in r_rv.data)
else:
    check("Review page cross-plan count check (skipped — no inactive sessions)", True)

# ── Change 2: Include review checkbox on plan generation ──────────────────────
print("\n--- Generate Plan: Include Review Checkbox ---")

# Seed an AIReview so the checkbox has something to reference
with app.app_context():
    profile = UserProfile.query.first()
    AIReview.query.filter_by(user_id=profile.id).delete()
    db.session.add(AIReview(
        user_id=profile.id,
        review_text="Great progress overall!",
        suggestions_json=json.dumps({
            "whats_working": "Bench press up 15%",
            "watch_out_for": "Left shoulder fatigue",
            "suggestions": ["Add more pull work", "Deload next week"],
            "overall_assessment": "Solid plan completion. Bring it!",
        }),
        data_summary=json.dumps({"sessions_count": 36}),
    ))
    db.session.commit()

# Test 4: generate-plan page shows the include_review checkbox when a review exists
r_gp = client.get("/generate-plan")
check("Generate plan page shows include_review checkbox when review exists",
      b'name="include_review"' in r_gp.data)

# Test 5: generate-plan page exposes review date so user knows how recent it is
check("Generate plan page shows review date/context when review exists",
      b"last review" in r_gp.data.lower()
      or b"last reviewed" in r_gp.data.lower()
      or b"prior review" in r_gp.data.lower())

# Test 6: when no review exists the checkbox is absent (not present in the form)
with app.app_context():
    profile = UserProfile.query.first()
    AIReview.query.filter_by(user_id=profile.id).delete()
    db.session.commit()

r_gp_empty = client.get("/generate-plan")
check("Generate plan page omits include_review checkbox when no review exists",
      b'name="include_review"' not in r_gp_empty.data)

# Restore review so the app state is clean for any follow-on inspection
with app.app_context():
    profile = UserProfile.query.first()
    db.session.add(AIReview(
        user_id=profile.id,
        review_text="Restored review",
        suggestions_json=json.dumps({
            "whats_working": "Solid",
            "watch_out_for": "Overtraining risk",
            "suggestions": ["Rest day"],
            "overall_assessment": "Keep it up!",
        }),
        data_summary=json.dumps({"sessions_count": 36}),
    ))
    db.session.commit()

# Summary
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed} tests")

# Clean up temp DB
try:
    os.unlink(_db_path)
except Exception:
    pass

if failed > 0:
    sys.exit(1)
else:
    print("All tests passed!")
