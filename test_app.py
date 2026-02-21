"""Quick smoke test for all routes."""
import json
import os
import sys
from datetime import date
from werkzeug.datastructures import MultiDict

# Delete DB first for clean test
db_path = os.path.join(os.path.dirname(__file__), "instance", "fitlocal.db")
if os.path.exists(db_path):
    os.remove(db_path)
    print("Deleted old database")

from app import app
from models import db, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise, TrainingPhase, FitnessTest

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
        plan_json=json.dumps(plan_data), is_active=False, notes="pending",
        total_weeks=12,
    )
    db.session.add(pending)
    db.session.commit()

# Confirm (activate) plan
r = client.post("/generate-plan/confirm", follow_redirects=False)
check("POST /confirm redirects", r.status_code == 302)

with app.app_context():
    plan = WorkoutPlan.query.filter_by(is_active=True).first()
    check("Plan is active", plan is not None and plan.is_active)
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
        check(f"Last perf weight={perf['weight']}, reps={perf['reps']}", perf["weight"] == 135 and perf["reps"] == 10)

# 14. Review page
print("\n--- Review ---")
r = client.get("/review")
check("GET /review returns 200", r.status_code == 200)

# 15. Settings
print("\n--- Settings ---")
r = client.get("/settings")
check("GET /settings returns 200", r.status_code == 200)

# 16. Export
print("\n--- Export ---")
r = client.get("/export")
check("GET /export returns 200", r.status_code == 200)

# Summary
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed} tests")
if failed > 0:
    sys.exit(1)
else:
    print("All tests passed!")
