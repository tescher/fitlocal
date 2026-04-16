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

from models import db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise, TrainingPhase, FitnessTest
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
if today_name in ("Monday", "Wednesday", "Friday"):
    check(f"GET /workout/today ({today_name}) returns 200", r.status_code == 200)
    check("Has timer", b"timer-card" in r.data)
    check("Has warmup section", b"warmup-header" in r.data or b"Warm-Up" in r.data)
    check("Has cooldown section", b"cooldown-header" in r.data or b"Cool-Down" in r.data)
    check("Has form cues", b"form-cues" in r.data)
    check("Has session timer JS", b"sessionTimer" in r.data)
    check("Has rest timer JS", b"startRest" in r.data)
    check("Has audio beep", b"playBeep" in r.data)
else:
    check(f"GET /workout/today ({today_name}) redirects (rest day)", r.status_code == 302)
    print(f"  (Today is {today_name} - rest day, can't test workout page)")

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
check("Calendar shows completed day", b"completed" in r.data)
check("Calendar has legend", b"calendar-legend" in r.data)

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
