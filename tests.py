"""
FitLocal pytest test suite.
Uses an in-memory SQLite database — never touches the production DB.
AI calls are mocked throughout.
"""
import io
import json
import os
import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from werkzeug.datastructures import MultiDict

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app as flask_app
from models import (
    db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, ExerciseLibrary,
)
from extensions import bcrypt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def application():
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False

    # Flask-SQLAlchemy bakes the engine URI at init_app() time, so we must
    # directly swap the cached engine with an in-memory one.
    # StaticPool ensures all pool connections share the same in-memory DB.
    mem_engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    original_engines = db._app_engines.get(flask_app.app)
    db._app_engines[flask_app.app] = {None: mem_engine}

    with flask_app.app.app_context():
        db.create_all()
        yield flask_app.app
        db.session.remove()
        db.drop_all()

    mem_engine.dispose()
    # Restore so the real app still works after tests
    if original_engines is not None:
        db._app_engines[flask_app.app] = original_engines
    else:
        db._app_engines.pop(flask_app.app, None)


@pytest.fixture
def account(application):
    """Create a claimed account and return its id."""
    with application.app_context():
        acc = Account(
            email="test@example.com",
            password_hash=bcrypt.generate_password_hash("password123").decode("utf-8"),
            email_claimed=True,
        )
        db.session.add(acc)
        db.session.commit()
        return acc.id


@pytest.fixture
def client(application, account):
    """Test client pre-logged-in as the test account."""
    c = application.test_client()
    # Inject Flask-Login session directly (avoids password round-trip)
    with c.session_transaction() as sess:
        sess["_user_id"] = str(account)
        sess["_fresh"] = True
    return c


@pytest.fixture
def profile(application, account):
    with application.app_context():
        p = UserProfile(
            account_id=account,
            name="Tim", age=35, sex="Male",
            fitness_level="Intermediate", goals="Build muscle"
        )
        db.session.add(p)
        db.session.commit()
        return p.id


@pytest.fixture
def active_plan(application, profile):
    """Create an active plan with Mon/Wed/Fri workouts."""
    plan_data = {
        "plan_name": "Test Plan", "description": "desc",
        "days_per_week": 3, "total_weeks": 12,
        "phases": [],
        "workouts": [
            {"day": "Workout A", "name": "Upper Body", "exercises": [
                {"name": "Bench Press", "type": "main", "sets": 3, "reps": "8-10",
                 "rest_seconds": 90, "notes": "", "form_cues": "Feet flat"},
                {"name": "Pull-Ups", "type": "main", "sets": 3, "reps": "6-10",
                 "rest_seconds": 90, "notes": "", "form_cues": "Full hang"},
            ]},
            {"day": "Workout B", "name": "Lower Body", "exercises": [
                {"name": "Squats", "type": "main", "sets": 4, "reps": "8-12",
                 "rest_seconds": 90, "notes": "", "form_cues": "Below parallel"},
            ]},
            {"day": "Workout C", "name": "Conditioning", "exercises": [
                {"name": "Burpees", "type": "main", "sets": 3, "reps": "10",
                 "rest_seconds": 60, "notes": "", "form_cues": "Full extension"},
            ]},
        ]
    }
    with application.app_context():
        p = UserProfile.query.get(profile)
        plan = WorkoutPlan(
            user_id=p.id, name="Test Plan", description="desc",
            days_per_week=3, plan_json=json.dumps(plan_data),
            status="active", total_weeks=12, current_week=1,
            start_date=date.today(),
        )
        db.session.add(plan)
        db.session.flush()
        for i, wd in enumerate(plan_data["workouts"]):
            pw = PlannedWorkout(
                plan_id=plan.id, day_of_week=wd["day"],
                workout_name=wd["name"], order_index=i,
            )
            db.session.add(pw)
            db.session.flush()
            for ex in wd["exercises"]:
                pe = PlannedExercise(
                    planned_workout_id=pw.id,
                    exercise_name=ex["name"],
                    sets_prescribed=ex["sets"],
                    reps_prescribed=ex["reps"],
                    rest_seconds=ex["rest_seconds"],
                    notes=ex["notes"],
                    exercise_type=ex["type"],
                    form_cues=ex["form_cues"],
                )
                db.session.add(pe)
        db.session.commit()
        return plan.id


def log_session(application, profile_id, planned_workout_id, exercises, delta_days=0):
    """Helper: directly insert a WorkoutSession + LoggedSets.
    Mirrors the FK lookup done in the /workout/log route.
    """
    with application.app_context():
        session_date = date.today() - timedelta(days=delta_days)
        ws = WorkoutSession(
            user_id=profile_id,
            planned_workout_id=planned_workout_id,
            date=session_date,
            overall_feeling=4,
            session_notes="Good session",
        )
        db.session.add(ws)
        db.session.flush()
        for name, sets in exercises:
            lib_entry = ExerciseLibrary.query.filter(
                db.func.lower(ExerciseLibrary.name) == name.lower()
            ).first()
            for s in range(1, sets + 1):
                ls = LoggedSet(
                    session_id=ws.id,
                    exercise_name=name,
                    exercise_library_id=lib_entry.id if lib_entry else None,
                    set_number=s,
                    weight_lbs=135.0,
                    reps_completed=10,
                    rpe=7,
                )
                db.session.add(ls)
        db.session.commit()
        return ws.id


# ---------------------------------------------------------------------------
# Route tests — no profile
# ---------------------------------------------------------------------------

class TestNoProfile:
    def test_home_redirects_to_setup(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert "/setup" in r.headers["Location"]

    def test_setup_page_loads(self, client):
        r = client.get("/setup")
        assert r.status_code == 200
        assert b"Setup" in r.data or b"setup" in r.data


# ---------------------------------------------------------------------------
# Profile creation
# ---------------------------------------------------------------------------

class TestProfileSetup:
    def test_create_profile_redirects(self, client):
        r = client.post("/setup", data={
            "name": "Tim", "age": "35", "sex": "Male",
            "fitness_level": "Intermediate", "goals": "Build muscle",
        })
        assert r.status_code == 302

    def test_profile_saved_to_db(self, client, application):
        client.post("/setup", data={
            "name": "Tim", "age": "35", "sex": "Male",
            "fitness_level": "Intermediate", "goals": "Build muscle",
        })
        with application.app_context():
            p = UserProfile.query.first()
            assert p is not None
            assert p.name == "Tim"
            assert p.age == 35
            assert p.current_streak == 0
            assert p.longest_streak == 0

    def test_duplicate_setup_updates_profile(self, client, application):
        client.post("/setup", data={
            "name": "Tim", "age": "35", "sex": "Male",
            "fitness_level": "Intermediate", "goals": "Build muscle",
        })
        client.post("/setup", data={
            "name": "Tim Updated", "age": "36", "sex": "Male",
            "fitness_level": "Advanced", "goals": "Compete",
        })
        with application.app_context():
            assert UserProfile.query.count() == 1
            p = UserProfile.query.first()
            assert p.name == "Tim Updated"
            assert p.age == 36


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_home_loads_with_profile(self, client, profile):
        r = client.get("/")
        assert r.status_code == 200

    def test_home_with_active_plan(self, client, active_plan, profile):
        r = client.get("/")
        assert r.status_code == 200
        # Dashboard shows the "Next Up" card when a plan is active (not the "Get Started" card)
        assert b"Next Up" in r.data
        assert b"Get Started" not in r.data

    def test_week_start_calculation(self, application, profile):
        """Regression: week_start must not use replace(day=day-1) which crashes on month boundary."""
        with application.app_context():
            from app import get_mini_calendar
            p = UserProfile.query.get(profile)
            # Just verify it doesn't raise
            days = get_mini_calendar(p.id)
            assert len(days) == 7
            assert days[-1]["is_today"] is True


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

class TestPlanGeneration:
    def test_generate_plan_page_loads(self, client, profile):
        r = client.get("/generate-plan")
        assert r.status_code == 200

    def test_confirm_plan_activates_it(self, client, application, profile):
        plan_json = json.dumps({
            "plan_name": "AI Plan", "description": "desc",
            "days_per_week": 3, "total_weeks": 12,
            "phases": [],
            "workouts": [
                {"day": "Workout A", "name": "Upper Body", "exercises": [
                    {"name": "Push-Ups", "type": "main", "sets": 3, "reps": "15",
                     "rest_seconds": 60, "notes": "", "form_cues": "Body straight"}
                ]}
            ]
        })
        with application.app_context():
            p = UserProfile.query.get(profile)
            pending = WorkoutPlan(
                user_id=p.id, name="AI Plan", description="desc",
                days_per_week=3, plan_json=plan_json,
                status="pending", total_weeks=12,
            )
            db.session.add(pending)
            db.session.commit()

        r = client.post("/generate-plan/confirm")
        assert r.status_code == 302

        with application.app_context():
            plan = WorkoutPlan.query.filter_by(status="active").first()
            assert plan is not None
            assert plan.name == "AI Plan"
            assert plan.start_date == date.today()

    def test_confirm_creates_planned_workouts(self, client, application, profile):
        plan_json = json.dumps({
            "plan_name": "Plan", "description": "", "days_per_week": 3,
            "total_weeks": 12, "phases": [],
            "workouts": [
                {"day": "Workout A", "name": "Day A", "exercises": [
                    {"name": "Squat", "type": "main", "sets": 3, "reps": "10",
                     "rest_seconds": 90, "notes": "", "form_cues": ""}
                ]},
                {"day": "Wednesday", "name": "Day B", "exercises": [
                    {"name": "Press", "type": "main", "sets": 3, "reps": "10",
                     "rest_seconds": 90, "notes": "", "form_cues": ""}
                ]},
            ]
        })
        with application.app_context():
            p = UserProfile.query.get(profile)
            pending = WorkoutPlan(
                user_id=p.id, name="Plan", description="",
                days_per_week=3, plan_json=plan_json,
                status="pending", total_weeks=12,
            )
            db.session.add(pending)
            db.session.commit()

        client.post("/generate-plan/confirm")

        with application.app_context():
            assert PlannedWorkout.query.count() == 2
            assert PlannedExercise.query.count() == 2


# ---------------------------------------------------------------------------
# Exercise library FK
# ---------------------------------------------------------------------------

class TestExerciseLibraryFK:
    def test_fk_populated_when_name_matches(self, client, application, profile):
        """Exercise library FK is set when exercise name matches library entry."""
        with application.app_context():
            lib = ExerciseLibrary(name="Bench Press", muscle_group="Chest", equipment="Barbell")
            db.session.add(lib)
            db.session.commit()
            lib_id = lib.id

        plan_json = json.dumps({
            "plan_name": "Plan", "description": "", "days_per_week": 3,
            "total_weeks": 12, "phases": [],
            "workouts": [{"day": "Workout A", "name": "Upper", "exercises": [
                {"name": "Bench Press", "type": "main", "sets": 3, "reps": "8",
                 "rest_seconds": 90, "notes": "", "form_cues": ""}
            ]}]
        })
        with application.app_context():
            p = UserProfile.query.get(profile)
            pending = WorkoutPlan(
                user_id=p.id, name="Plan", description="", days_per_week=3,
                plan_json=plan_json, status="pending", total_weeks=12,
            )
            db.session.add(pending)
            db.session.commit()

        client.post("/generate-plan/confirm")

        with application.app_context():
            pe = PlannedExercise.query.filter_by(exercise_name="Bench Press").first()
            assert pe is not None
            assert pe.exercise_library_id == lib_id

    def test_fk_null_when_no_library_match(self, client, application, profile):
        """FK stays null for free-text exercise names not in the library."""
        plan_json = json.dumps({
            "plan_name": "Plan", "description": "", "days_per_week": 3,
            "total_weeks": 12, "phases": [],
            "workouts": [{"day": "Workout A", "name": "Upper", "exercises": [
                {"name": "Some Custom Exercise", "type": "main", "sets": 2, "reps": "10",
                 "rest_seconds": 60, "notes": "", "form_cues": ""}
            ]}]
        })
        with application.app_context():
            p = UserProfile.query.get(profile)
            pending = WorkoutPlan(
                user_id=p.id, name="Plan", description="", days_per_week=3,
                plan_json=plan_json, status="pending", total_weeks=12,
            )
            db.session.add(pending)
            db.session.commit()

        client.post("/generate-plan/confirm")

        with application.app_context():
            pe = PlannedExercise.query.filter_by(exercise_name="Some Custom Exercise").first()
            assert pe is not None
            assert pe.exercise_library_id is None

    def test_logged_set_fk_populated(self, application, profile, active_plan):
        """LoggedSet.exercise_library_id is set when name matches library."""
        with application.app_context():
            lib = ExerciseLibrary(name="Bench Press", muscle_group="Chest", equipment="Barbell")
            db.session.add(lib)
            db.session.commit()
            lib_id = lib.id
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
            p_id = profile

        log_session(application, p_id, pw_id, [("Bench Press", 3)])

        with application.app_context():
            ls = LoggedSet.query.filter_by(exercise_name="Bench Press").first()
            assert ls is not None
            assert ls.exercise_library_id == lib_id


class TestExerciseVideoCsvExport:
    def test_export_returns_csv_of_active_plan_names(self, client, application, profile, active_plan):
        r = client.get("/settings/export-exercises")
        assert r.status_code == 200
        assert "text/csv" in r.content_type
        body = r.data.decode("utf-8")
        for name in ["Bench Press", "Pull-Ups", "Squats", "Burpees"]:
            assert name in body

    def test_export_dedups_exercise_names_across_days(self, client, application, profile):
        plan_data = {
            "plan_name": "Plan", "description": "", "days_per_week": 2, "total_weeks": 12, "phases": [],
            "workouts": [
                {"day": "Mon", "name": "A", "exercises": [
                    {"name": "Squats", "type": "main", "sets": 3, "reps": "8", "rest_seconds": 90, "notes": "", "form_cues": ""},
                ]},
                {"day": "Wed", "name": "B", "exercises": [
                    {"name": "Squats", "type": "main", "sets": 3, "reps": "8", "rest_seconds": 90, "notes": "", "form_cues": ""},
                ]},
            ],
        }
        with application.app_context():
            p = UserProfile.query.get(profile)
            plan = WorkoutPlan(
                user_id=p.id, name="Plan", description="", days_per_week=2,
                plan_json=json.dumps(plan_data), status="active", total_weeks=12,
            )
            db.session.add(plan)
            db.session.flush()
            for i, wd in enumerate(plan_data["workouts"]):
                pw = PlannedWorkout(plan_id=plan.id, day_of_week=wd["day"], workout_name=wd["name"], order_index=i)
                db.session.add(pw)
                db.session.flush()
                for ex in wd["exercises"]:
                    pe = PlannedExercise(
                        planned_workout_id=pw.id, exercise_name=ex["name"],
                        sets_prescribed=ex["sets"], reps_prescribed=ex["reps"],
                        rest_seconds=ex["rest_seconds"], notes=ex["notes"],
                        exercise_type=ex["type"], form_cues=ex["form_cues"],
                    )
                    db.session.add(pe)
            db.session.commit()

        r = client.get("/settings/export-exercises")
        body = r.data.decode("utf-8")
        assert body.count("Squats") == 1

    def test_export_no_active_plan_flashes_error(self, client, profile):
        r = client.get("/settings/export-exercises", follow_redirects=True)
        assert b"No active plan" in r.data


class TestExerciseVideoCsvImport:
    def _upload(self, client, csv_text, filename="exercises.csv"):
        data = {"csv_file": (io.BytesIO(csv_text.encode("utf-8")), filename)}
        return client.post("/settings/import-exercise-videos", data=data, content_type="multipart/form-data")

    def test_import_creates_library_entries_and_sets_video_url(self, client, application, profile):
        csv_text = "Bench Press,https://example.com/bench\nSquats,https://example.com/squats\n"
        r = self._upload(client, csv_text)
        assert r.status_code == 302
        with application.app_context():
            bench = ExerciseLibrary.query.filter_by(name="Bench Press").first()
            squats = ExerciseLibrary.query.filter_by(name="Squats").first()
            assert bench is not None and bench.video_url == "https://example.com/bench"
            assert squats is not None and squats.video_url == "https://example.com/squats"

    def test_import_backfills_planned_exercise_fk_across_plans(self, client, application, profile):
        """Two plans (only one active) each have an unlinked 'Squats' exercise.
        Import must link both, not just the active plan's -- this is the exact
        gap the earlier (reverted) feature's own bug report was about."""
        with application.app_context():
            p = UserProfile.query.get(profile)
            for status, plan_name in [("active", "Active Plan"), ("inactive", "Old Plan")]:
                plan = WorkoutPlan(
                    user_id=p.id, name=plan_name, description="", days_per_week=1,
                    plan_json="{}", status=status, total_weeks=12,
                )
                db.session.add(plan)
                db.session.flush()
                pw = PlannedWorkout(plan_id=plan.id, day_of_week="Mon", workout_name="A", order_index=0)
                db.session.add(pw)
                db.session.flush()
                pe = PlannedExercise(
                    planned_workout_id=pw.id, exercise_name="Squats",
                    sets_prescribed=3, reps_prescribed="8", rest_seconds=90,
                    notes="", exercise_type="main", form_cues="",
                )
                db.session.add(pe)
            db.session.commit()

        self._upload(client, "Squats,https://example.com/squats\n")

        with application.app_context():
            exercises = PlannedExercise.query.filter_by(exercise_name="Squats").all()
            assert len(exercises) == 2
            assert all(pe.exercise_library_id is not None for pe in exercises)

    def test_import_skips_malformed_rows(self, client, application, profile):
        csv_text = "Bench Press,https://example.com/bench\nMissing URL Row\nSquats,https://example.com/squats\n"
        r = self._upload(client, csv_text)
        assert r.status_code == 302
        with application.app_context():
            assert ExerciseLibrary.query.filter_by(name="Bench Press").first() is not None
            assert ExerciseLibrary.query.filter_by(name="Squats").first() is not None
            assert ExerciseLibrary.query.filter_by(name="Missing URL Row").first() is None

    def test_import_no_file_flashes_error(self, client, profile):
        r = client.post(
            "/settings/import-exercise-videos", data={}, content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Please choose a CSV file" in r.data

    def test_import_does_not_call_any_network_request(self, client, profile):
        """Import must be pure local CSV parsing + DB writes -- no outbound
        network calls, guarding against ever reintroducing the kind of
        request-blocking call that made the earlier feature need
        background threading in the first place."""
        with patch("requests.get", side_effect=AssertionError("import must not make network requests")):
            r = self._upload(client, "Bench Press,https://example.com/bench\n")
        assert r.status_code == 302


class TestWorkoutTodayVideoLink:
    def test_shows_watch_video_link_when_library_has_video_url(self, client, application, profile, active_plan):
        with application.app_context():
            lib = ExerciseLibrary(name="Bench Press", video_url="https://example.com/bench")
            db.session.add(lib)
            db.session.commit()
            pe = PlannedExercise.query.filter_by(exercise_name="Bench Press").first()
            pe.exercise_library_id = lib.id
            db.session.commit()

        r = client.get("/workout/today")
        assert b"Watch form video" in r.data
        assert b"https://example.com/bench" in r.data

    def test_no_video_link_when_not_linked(self, client, application, profile, active_plan):
        r = client.get("/workout/today")
        assert b"Watch form video" not in r.data


# ---------------------------------------------------------------------------
# Workout logging
# ---------------------------------------------------------------------------

class TestWorkoutLogging:
    def _build_form(self, application, active_plan):
        items = [("overall_feeling", "4"), ("session_notes", "Good")]
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            items.append(("planned_workout_id", str(pw.id)))
            for ex in PlannedExercise.query.filter_by(planned_workout_id=pw.id).all():
                for s in range(1, ex.sets_prescribed + 1):
                    items += [
                        ("exercise_name", ex.exercise_name),
                        ("set_number", str(s)),
                        ("weight", "135"),
                        ("reps", "10"),
                        ("rpe", "7"),
                        ("set_notes", ""),
                    ]
        return items

    def test_log_returns_done_page(self, client, application, profile, active_plan):
        form = self._build_form(application, active_plan)
        r = client.post("/workout/log", data=MultiDict(form))
        assert r.status_code == 200
        assert b"Great Work" in r.data

    def test_logged_sets_saved(self, client, application, profile, active_plan):
        form = self._build_form(application, active_plan)
        client.post("/workout/log", data=MultiDict(form))
        with application.app_context():
            assert WorkoutSession.query.count() == 1
            assert LoggedSet.query.count() == 6  # 3 sets × 2 exercises

    def test_streak_increments(self, client, application, profile, active_plan):
        form = self._build_form(application, active_plan)
        client.post("/workout/log", data=MultiDict(form))
        with application.app_context():
            p = UserProfile.query.get(profile)
            assert p.current_streak == 1
            assert p.last_workout_date == date.today()

    def test_duplicate_log_same_day_no_double_streak(self, client, application, profile, active_plan):
        form = self._build_form(application, active_plan)
        client.post("/workout/log", data=MultiDict(form))
        client.post("/workout/log", data=MultiDict(form))
        with application.app_context():
            p = UserProfile.query.get(profile)
            assert p.current_streak == 1  # Not 2

    # ------------------------------------------------------------------
    # Add / Remove Set (variable set count submitted by the JS feature)
    # ------------------------------------------------------------------

    def test_extra_set_saved_when_user_adds_set(self, client, application, profile, active_plan):
        """Submitting more sets than prescribed (JS 'Add Set') saves all of them."""
        items = [("overall_feeling", "4"), ("session_notes", "")]
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            items.append(("planned_workout_id", str(pw.id)))
            # Bench Press is prescribed for 3 sets — submit 4 (user added one)
            for s in range(1, 5):
                items += [
                    ("exercise_name", "Bench Press"),
                    ("set_number", str(s)),
                    ("weight", "135"),
                    ("reps", "10"),
                    ("rpe", "7"),
                    ("set_notes", ""),
                ]
            # Pull-Ups at prescribed 3 sets
            for s in range(1, 4):
                items += [
                    ("exercise_name", "Pull-Ups"),
                    ("set_number", str(s)),
                    ("weight", "0"),
                    ("reps", "8"),
                    ("rpe", ""),
                    ("set_notes", ""),
                ]

        r = client.post("/workout/log", data=MultiDict(items))
        assert r.status_code == 200

        with application.app_context():
            bench_sets = LoggedSet.query.filter_by(exercise_name="Bench Press").count()
            assert bench_sets == 4  # extra set was saved

    def test_fewer_sets_saved_when_user_removes_set(self, client, application, profile, active_plan):
        """Submitting fewer sets than prescribed (JS 'Remove Set') saves only submitted sets."""
        items = [("overall_feeling", "3"), ("session_notes", "")]
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            items.append(("planned_workout_id", str(pw.id)))
            # Bench Press prescribed 3 sets — user removed one, only 2 submitted
            for s in range(1, 3):
                items += [
                    ("exercise_name", "Bench Press"),
                    ("set_number", str(s)),
                    ("weight", "135"),
                    ("reps", "10"),
                    ("rpe", "8"),
                    ("set_notes", ""),
                ]
            # Pull-Ups at prescribed 3 sets — also reduced to 1
            items += [
                ("exercise_name", "Pull-Ups"),
                ("set_number", "1"),
                ("weight", "0"),
                ("reps", "6"),
                ("rpe", ""),
                ("set_notes", ""),
            ]

        r = client.post("/workout/log", data=MultiDict(items))
        assert r.status_code == 200

        with application.app_context():
            bench_sets = LoggedSet.query.filter_by(exercise_name="Bench Press").count()
            pullup_sets = LoggedSet.query.filter_by(exercise_name="Pull-Ups").count()
            assert bench_sets == 2
            assert pullup_sets == 1

    def test_set_number_stored_correctly_for_added_set(self, client, application, profile, active_plan):
        """The set_number on the extra row is stored as submitted (JS sets it sequentially)."""
        items = [("overall_feeling", "4"), ("session_notes", "")]
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            items.append(("planned_workout_id", str(pw.id)))
            for s in range(1, 5):  # 4 sets, 4th is the added one
                items += [
                    ("exercise_name", "Bench Press"),
                    ("set_number", str(s)),
                    ("weight", str(100 + s * 5)),
                    ("reps", "10"),
                    ("rpe", ""),
                    ("set_notes", ""),
                ]
            # Pull-Ups minimal
            items += [
                ("exercise_name", "Pull-Ups"), ("set_number", "1"),
                ("weight", "0"), ("reps", "8"), ("rpe", ""), ("set_notes", ""),
            ]

        client.post("/workout/log", data=MultiDict(items))

        with application.app_context():
            fourth = LoggedSet.query.filter_by(
                exercise_name="Bench Press", set_number=4
            ).first()
            assert fourth is not None
            assert fourth.weight_lbs == 120.0  # 100 + 4*5


# ---------------------------------------------------------------------------
# Streak logic
# ---------------------------------------------------------------------------

class TestStreakLogic:
    def test_first_workout_sets_streak_to_1(self, application, profile):
        with application.app_context():
            from app import update_streak
            p = UserProfile.query.get(profile)
            update_streak(p)
            db.session.commit()
            assert p.current_streak == 1
            assert p.longest_streak == 1

    def test_consecutive_day_increments(self, application, profile):
        with application.app_context():
            from app import update_streak
            p = UserProfile.query.get(profile)
            p.last_workout_date = date.today() - timedelta(days=1)
            p.current_streak = 1
            p.longest_streak = 1
            update_streak(p)
            db.session.commit()
            assert p.current_streak == 2
            assert p.longest_streak == 2

    def test_gap_within_3_days_keeps_streak(self, application, profile):
        with application.app_context():
            from app import update_streak
            p = UserProfile.query.get(profile)
            p.last_workout_date = date.today() - timedelta(days=3)
            p.current_streak = 5
            p.longest_streak = 5
            update_streak(p)
            db.session.commit()
            assert p.current_streak == 6

    def test_gap_over_3_days_resets_streak(self, application, profile):
        with application.app_context():
            from app import update_streak
            p = UserProfile.query.get(profile)
            p.last_workout_date = date.today() - timedelta(days=4)
            p.current_streak = 10
            p.longest_streak = 10
            update_streak(p)
            db.session.commit()
            assert p.current_streak == 1
            assert p.longest_streak == 10  # Longest preserved

    def test_same_day_no_change(self, application, profile):
        with application.app_context():
            from app import update_streak
            p = UserProfile.query.get(profile)
            p.last_workout_date = date.today()
            p.current_streak = 5
            update_streak(p)
            db.session.commit()
            assert p.current_streak == 5


# ---------------------------------------------------------------------------
# Last performance
# ---------------------------------------------------------------------------

class TestLastPerformance:
    def test_returns_none_when_no_history(self, application, profile):
        with application.app_context():
            from app import get_last_performance
            result = get_last_performance(profile, "Bench Press")
            assert result is None

    def test_returns_best_set(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id

        log_session(application, profile, pw_id, [("Bench Press", 3)])

        with application.app_context():
            from app import get_last_performance
            result = get_last_performance(profile, "Bench Press")
            assert result is not None
            assert result["sets"][1]["weight"] == 135.0
            assert result["sets"][1]["reps"] == 10

    def test_returns_most_recent_session(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id

        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=7)
        # Second session with higher weight
        with application.app_context():
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw_id,
                                date=date.today(), overall_feeling=4)
            db.session.add(ws)
            db.session.flush()
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=1, weight_lbs=155.0, reps_completed=8))
            db.session.commit()

        with application.app_context():
            from app import get_last_performance
            result = get_last_performance(profile, "Bench Press")
            assert result["sets"][1]["weight"] == 155.0


# ---------------------------------------------------------------------------
# Exercise history (for the exercise history popup chart)
# ---------------------------------------------------------------------------

class TestExerciseHistory:
    def test_returns_empty_list_when_no_history(self, application, profile):
        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert result == []

    def test_computes_volume_and_avg_rpe(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw.id,
                                 date=date.today(), overall_feeling=4)
            db.session.add(ws)
            db.session.flush()
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=1, weight_lbs=135.0, reps_completed=10, rpe=7))
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=2, weight_lbs=145.0, reps_completed=8, rpe=9))
            db.session.commit()

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert len(result) == 1
            assert result[0]["volume"] == 135.0 * 10 + 145.0 * 8
            assert result[0]["avg_rpe"] == 8.0

    def test_includes_drop_set_b_values_in_volume(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw.id,
                                 date=date.today(), overall_feeling=4)
            db.session.add(ws)
            db.session.flush()
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=1, weight_lbs=135.0, reps_completed=10,
                                     weight_b=95.0, reps_b=6, rpe=8))
            db.session.commit()

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert result[0]["volume"] == 135.0 * 10 + 95.0 * 6

    def test_avg_rpe_is_none_when_no_rpe_recorded(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw.id,
                                 date=date.today(), overall_feeling=4)
            db.session.add(ws)
            db.session.flush()
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=1, weight_lbs=135.0, reps_completed=10))
            db.session.commit()

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert result[0]["avg_rpe"] is None

    def test_orders_sessions_oldest_first(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id

        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=7)
        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=0)

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert len(result) == 2
            assert result[0]["date"] < result[1]["date"]

    def test_null_weight_uses_multiplier_of_one_for_bodyweight_exercises(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw.id,
                                 date=date.today(), overall_feeling=4)
            db.session.add(ws)
            db.session.flush()
            # Bodyweight set: no weight logged at all (weight_lbs is None)
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Pull-Ups",
                                     set_number=1, weight_lbs=None, reps_completed=12, rpe=7))
            # Weighted set with a bodyweight drop set (weight_b is None)
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Pull-Ups",
                                     set_number=2, weight_lbs=25.0, reps_completed=6,
                                     weight_b=None, reps_b=5, rpe=8))
            db.session.commit()

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Pull-Ups")
            assert len(result) == 1
            # (1 * 12) + (25.0 * 6) + (1 * 5) -- null weight treated as bodyweight (multiplier 1)
            assert result[0]["volume"] == 12 + 150.0 + 5

    def test_ignores_other_exercises_and_incomplete_sessions(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id

        log_session(application, profile, pw_id, [("Pull-Ups", 2)])

        with application.app_context():
            ws = WorkoutSession(user_id=profile, planned_workout_id=pw_id,
                                 date=date.today(), status="in_progress")
            db.session.add(ws)
            db.session.flush()
            db.session.add(LoggedSet(session_id=ws.id, exercise_name="Bench Press",
                                     set_number=1, weight_lbs=999.0, reps_completed=1))
            db.session.commit()

        with application.app_context():
            from app import get_exercise_history
            result = get_exercise_history(profile, "Bench Press")
            assert result == []


# ---------------------------------------------------------------------------
# Fitness test
# ---------------------------------------------------------------------------

class TestFitnessTest:
    def test_fitness_test_page_loads(self, client, profile):
        r = client.get("/fitness-test")
        assert r.status_code == 200

    def test_no_tests_days_since_is_none(self, client, application, profile):
        """Regression: days_since must be initialized to None before conditional."""
        r = client.get("/fitness-test")
        assert r.status_code == 200
        assert b"Take Fitness Test" in r.data

    def test_fitness_test_saved(self, client, application, profile):
        r = client.post("/fitness-test/new", data={
            "pushups": "30", "pullups": "8", "wall_sit_seconds": "90",
            "toe_touch_inches": "3", "plank_seconds": "120",
            "vertical_jump_inches": "20", "notes": "Baseline",
        })
        assert r.status_code == 302
        with application.app_context():
            ft = FitnessTest.query.first()
            assert ft is not None
            assert ft.pushups == 30
            assert ft.plank_seconds == 120

    def test_retest_blocked_within_30_days(self, client, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            ft = FitnessTest(
                user_id=p.id, test_date=date.today() - timedelta(days=5),
                pushups=20, pullups=5, wall_sit_seconds=60,
                toe_touch_inches=2, plank_seconds=90, vertical_jump_inches=18,
            )
            db.session.add(ft)
            db.session.commit()

        r = client.get("/fitness-test")
        assert r.status_code == 200
        assert b"Next retest available" in r.data

    def test_retest_allowed_after_30_days(self, client, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            ft = FitnessTest(
                user_id=p.id, test_date=date.today() - timedelta(days=31),
                pushups=20, pullups=5, wall_sit_seconds=60,
                toe_touch_inches=2, plank_seconds=90, vertical_jump_inches=18,
            )
            db.session.add(ft)
            db.session.commit()

        r = client.get("/fitness-test")
        assert r.status_code == 200
        assert b"Take Fitness Test" in r.data


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class TestHistory:
    def test_history_loads_empty(self, client, profile):
        r = client.get("/history")
        assert r.status_code == 200

    def test_history_shows_sessions(self, client, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
        log_session(application, profile, pw_id, [("Bench Press", 3)])
        r = client.get("/history")
        assert r.status_code == 200
        assert b"Upper Body" in r.data

    def test_session_detail_loads(self, client, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
        sid = log_session(application, profile, pw_id, [("Bench Press", 3)])
        r = client.get(f"/history/{sid}")
        assert r.status_code == 200
        assert b"Bench Press" in r.data

    def test_session_detail_404_on_missing(self, client, profile):
        r = client.get("/history/9999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Exercise ordering in workout summary and history detail
# ---------------------------------------------------------------------------

@pytest.fixture
def reversed_order_plan(application, profile):
    """Plan where exercise order is intentionally reverse-alphabetical
    (Squats at index 0, Bench Press at index 1), so plan order differs
    from alphabetical order and can be detected in tests."""
    plan_data = {
        "plan_name": "Order Test Plan", "description": "desc",
        "days_per_week": 1, "total_weeks": 4,
        "phases": [],
        "workouts": [
            {"day": "Workout A", "name": "Full Body", "exercises": [
                {"name": "Squats", "type": "main", "sets": 2, "reps": "10",
                 "rest_seconds": 90, "notes": "", "form_cues": ""},
                {"name": "Bench Press", "type": "main", "sets": 2, "reps": "10",
                 "rest_seconds": 90, "notes": "", "form_cues": ""},
            ]},
        ],
    }
    with application.app_context():
        p = UserProfile.query.get(profile)
        plan = WorkoutPlan(
            user_id=p.id, name="Order Test Plan", description="desc",
            days_per_week=1, plan_json=json.dumps(plan_data),
            is_active=True, total_weeks=4, current_week=1,
            start_date=date.today(),
        )
        db.session.add(plan)
        db.session.flush()
        pw = PlannedWorkout(
            plan_id=plan.id, day_of_week="Workout A",
            workout_name="Full Body", order_index=0,
        )
        db.session.add(pw)
        db.session.flush()
        for idx, ex in enumerate([
            {"name": "Squats", "type": "main", "sets": 2, "reps": "10",
             "rest_seconds": 90, "notes": "", "form_cues": ""},
            {"name": "Bench Press", "type": "main", "sets": 2, "reps": "10",
             "rest_seconds": 90, "notes": "", "form_cues": ""},
        ]):
            pe = PlannedExercise(
                planned_workout_id=pw.id,
                exercise_name=ex["name"],
                sets_prescribed=ex["sets"],
                reps_prescribed=ex["reps"],
                rest_seconds=ex["rest_seconds"],
                notes=ex["notes"],
                exercise_type=ex["type"],
                form_cues=ex["form_cues"],
                order_index=idx,
            )
            db.session.add(pe)
        db.session.commit()
        return plan.id


class TestExerciseOrdering:
    """Exercises in workout summary and history detail follow plan order, not alphabetical."""

    def _build_form(self, application):
        items = [("overall_feeling", "4"), ("session_notes", "")]
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(workout_name="Full Body").first()
            items.append(("planned_workout_id", str(pw.id)))
            for ex in (PlannedExercise.query
                       .filter_by(planned_workout_id=pw.id)
                       .order_by(PlannedExercise.order_index)
                       .all()):
                for s in range(1, ex.sets_prescribed + 1):
                    items += [
                        ("exercise_name", ex.exercise_name),
                        ("set_number", str(s)),
                        ("weight", "135"),
                        ("reps", "10"),
                        ("rpe", "7"),
                        ("set_notes", ""),
                    ]
        return items

    def test_workout_done_shows_exercises_in_plan_order(
        self, client, application, profile, reversed_order_plan
    ):
        """POST /workout/log summary must show Squats (order 0) before Bench Press (order 1)."""
        r = client.post("/workout/log", data=MultiDict(self._build_form(application)))
        assert r.status_code == 200
        html = r.data
        assert b"Squats" in html and b"Bench Press" in html
        assert html.index(b"Squats") < html.index(b"Bench Press"), (
            "Squats (plan order 0) should appear before Bench Press (plan order 1) "
            "but response shows alphabetical order (Bench Press first)"
        )

    def test_session_detail_shows_exercises_in_plan_order(
        self, client, application, profile, reversed_order_plan
    ):
        """GET /history/<id> must show Squats (order 0) before Bench Press (order 1)."""
        client.post("/workout/log", data=MultiDict(self._build_form(application)))
        with application.app_context():
            ws = WorkoutSession.query.first()
            sid = ws.id
        r = client.get(f"/history/{sid}")
        assert r.status_code == 200
        html = r.data
        assert b"Squats" in html and b"Bench Press" in html
        assert html.index(b"Squats") < html.index(b"Bench Press"), (
            "Squats (plan order 0) should appear before Bench Press (plan order 1) "
            "but response shows alphabetical order (Bench Press first)"
        )


# ---------------------------------------------------------------------------
# Review page
# ---------------------------------------------------------------------------

class TestReview:
    def test_review_page_loads_no_sessions(self, client, profile):
        r = client.get("/review")
        assert r.status_code == 200

    def test_generate_review(self, client, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
        for i in range(3):
            log_session(application, profile, pw_id, [("Bench Press", 3)], delta_days=i * 7)

        mock_response = {
            "whats_working": "Great consistency!",
            "watch_out_for": "Watch your form.",
            "suggestions": ["Add more volume", "Sleep more"],
            "overall_assessment": "Bring it!"
        }
        with patch("ai.generate_progress_review", return_value=mock_response):
            r = client.post("/review/generate")
            assert r.status_code in (200, 302)

        with application.app_context():
            review = AIReview.query.first()
            assert review is not None
            data = json.loads(review.suggestions_json)
            assert data["whats_working"] == "Great consistency!"

    def test_review_suggestions_none_safe(self, client, application, profile):
        """Regression: review page must not crash when suggestions is None."""
        with application.app_context():
            p = UserProfile.query.get(profile)
            rev = AIReview(
                user_id=p.id,
                review_text="Good job",
                suggestions_json=json.dumps({
                    "whats_working": "Good",
                    "watch_out_for": "Volume",
                    "suggestions": None,
                    "overall_assessment": "Keep going",
                }),
                data_summary="summary",
            )
            db.session.add(rev)
            db.session.commit()

        r = client.get("/review")
        assert r.status_code == 200  # Must not 500


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_page_loads(self, client, profile):
        r = client.get("/export")
        assert r.status_code == 200

    def test_export_xlsx_download(self, client, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
        log_session(application, profile, pw_id, [("Bench Press", 3)])

        r = client.get("/export/download")
        assert r.status_code == 200
        assert "spreadsheetml" in r.content_type or "officedocument" in r.content_type


# ---------------------------------------------------------------------------
# Settings / Plan view
# ---------------------------------------------------------------------------

class TestMiscRoutes:
    def test_settings_loads(self, client, profile):
        r = client.get("/settings")
        assert r.status_code == 200

    def test_plan_view_loads(self, client, profile, active_plan):
        r = client.get("/plan")
        assert r.status_code == 200
        assert b"Test Plan" in r.data

    def test_plan_view_no_plan(self, client, profile):
        r = client.get("/plan")
        assert r.status_code in (200, 302)

    def test_calendar_loads(self, client, profile):
        r = client.get("/calendar")
        assert r.status_code == 200

    def test_nutrition_loads(self, client, profile, active_plan):
        r = client.get("/nutrition")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Mini calendar
# ---------------------------------------------------------------------------

class TestMiniCalendar:
    def test_returns_7_days(self, application, profile):
        with application.app_context():
            from app import get_mini_calendar
            days = get_mini_calendar(profile)
            assert len(days) == 7

    def test_today_is_last_entry(self, application, profile):
        with application.app_context():
            from app import get_mini_calendar
            days = get_mini_calendar(profile)
            assert days[-1]["is_today"] is True
            assert days[-1]["date"] == date.today()

    def test_completed_day_marked(self, application, profile, active_plan):
        with application.app_context():
            pw = PlannedWorkout.query.filter_by(day_of_week="Workout A").first()
            pw_id = pw.id
        log_session(application, profile, pw_id, [("Bench Press", 1)])
        with application.app_context():
            from app import get_mini_calendar
            days = get_mini_calendar(profile)
            today_entry = next(d for d in days if d["is_today"])
            assert today_entry["completed"] is True


# ---------------------------------------------------------------------------
# Reactivating a previous plan
# ---------------------------------------------------------------------------

def _make_plan(profile_id, name, status, workout_days, start_date=None):
    """Create a WorkoutPlan (+ PlannedWorkouts/Exercises) and return its id.

    Name-agnostic: workout_days drives both the plan_json and the
    PlannedWorkout rows, so tests never assume a particular workout name.
    """
    plan_data = {
        "plan_name": name, "description": name + " desc",
        "days_per_week": len(workout_days), "total_weeks": 12,
        "phases": [],
        "workouts": [
            {"day": d, "name": d + " Session", "exercises": [
                {"name": "Bench Press", "type": "main", "sets": 3, "reps": "8-10",
                 "rest_seconds": 90, "notes": "", "form_cues": ""},
            ]}
            for d in workout_days
        ],
    }
    plan = WorkoutPlan(
        user_id=profile_id, name=name, description=name + " desc",
        days_per_week=len(workout_days), plan_json=json.dumps(plan_data),
        status=status, total_weeks=12, current_week=1,
        start_date=start_date or (date.today() - timedelta(days=200)),
    )
    db.session.add(plan)
    db.session.flush()
    for i, d in enumerate(workout_days):
        pw = PlannedWorkout(
            plan_id=plan.id, day_of_week=d,
            workout_name=d + " Session", order_index=i,
        )
        db.session.add(pw)
        db.session.flush()
        db.session.add(PlannedExercise(
            planned_workout_id=pw.id, exercise_name="Bench Press",
            sets_prescribed=3, reps_prescribed="8-10", rest_seconds=90,
            exercise_type="main", order_index=0,
        ))
    db.session.commit()
    return plan.id


@pytest.fixture
def two_plans(application, profile):
    """An active plan and an older inactive plan for the same user.

    Returns (active_plan_id, inactive_plan_id).
    """
    with application.app_context():
        old_id = _make_plan(profile, "Old Plan", "inactive", ["Workout A", "Workout B"])
        new_id = _make_plan(profile, "Current Plan", "active",
                            ["Workout A", "Workout B", "Workout C"])
        return new_id, old_id


class TestReactivatePlan:
    def test_reactivate_promotes_old_plan_and_demotes_current(self, client, application, two_plans):
        active_id, inactive_id = two_plans
        r = client.post("/plan/%d/reactivate" % inactive_id, follow_redirects=True)
        assert r.status_code == 200
        with application.app_context():
            assert WorkoutPlan.query.get(inactive_id).status == "active"
            assert WorkoutPlan.query.get(active_id).status == "inactive"

    def test_reactivate_resets_start_date_to_today(self, client, application, two_plans):
        _, inactive_id = two_plans
        client.post("/plan/%d/reactivate" % inactive_id, follow_redirects=True)
        with application.app_context():
            assert WorkoutPlan.query.get(inactive_id).start_date == date.today()

    def test_reactivate_leaves_pending_plan_untouched(self, client, application, profile, two_plans):
        _, inactive_id = two_plans
        with application.app_context():
            pending_id = _make_plan(profile, "Pending Plan", "pending", ["Workout A"])
        client.post("/plan/%d/reactivate" % inactive_id, follow_redirects=True)
        with application.app_context():
            assert WorkoutPlan.query.get(pending_id).status == "pending"

    def test_cannot_reactivate_a_pending_plan(self, client, application, profile, two_plans):
        active_id, _ = two_plans
        with application.app_context():
            pending_id = _make_plan(profile, "Pending Plan", "pending", ["Workout A"])
        client.post("/plan/%d/reactivate" % pending_id, follow_redirects=True)
        with application.app_context():
            assert WorkoutPlan.query.get(pending_id).status == "pending"
            assert WorkoutPlan.query.get(active_id).status == "active"

    def test_reactivating_the_active_plan_is_a_noop(self, client, application, two_plans):
        active_id, _ = two_plans
        client.post("/plan/%d/reactivate" % active_id, follow_redirects=True)
        with application.app_context():
            assert WorkoutPlan.query.get(active_id).status == "active"

    def test_reactivate_rejects_another_users_plan(self, client, application, account):
        """A plan belonging to a different profile must not be reactivatable."""
        with application.app_context():
            other_acc = Account(email="other@example.com", password_hash="x", email_claimed=True)
            db.session.add(other_acc)
            db.session.flush()
            other = UserProfile(
                account_id=other_acc.id, name="Other", age=40, sex="Female",
                fitness_level="Beginner", goals="Get fit",
            )
            db.session.add(other)
            db.session.flush()
            foreign_id = _make_plan(other.id, "Foreign Plan", "inactive", ["Workout A"])
        r = client.post("/plan/%d/reactivate" % foreign_id, follow_redirects=True)
        assert r.status_code in (200, 302, 404)
        with application.app_context():
            assert WorkoutPlan.query.get(foreign_id).status == "inactive"

    def test_reactivate_missing_plan_404s(self, client, profile):
        r = client.post("/plan/999999/reactivate")
        assert r.status_code == 404

    def test_reactivate_requires_login(self, application, two_plans):
        _, inactive_id = two_plans
        anon = application.test_client()
        r = anon.post("/plan/%d/reactivate" % inactive_id)
        assert r.status_code == 302
        with application.app_context():
            assert WorkoutPlan.query.get(inactive_id).status == "inactive"

    def test_reactivate_blocked_while_a_session_is_paused(self, client, application, profile, two_plans):
        """A paused session hijacks /workout/today, so reactivation must be
        blocked until it is resolved rather than silently switching plans."""
        active_id, inactive_id = two_plans
        with application.app_context():
            pw = (PlannedWorkout.query.filter_by(plan_id=active_id)
                  .order_by(PlannedWorkout.order_index).first())
            db.session.add(WorkoutSession(
                user_id=profile, planned_workout_id=pw.id, date=date.today(),
                status="paused",
            ))
            db.session.commit()
        client.post("/plan/%d/reactivate" % inactive_id, follow_redirects=True)
        with application.app_context():
            assert WorkoutPlan.query.get(inactive_id).status == "inactive"
            assert WorkoutPlan.query.get(active_id).status == "active"

    def test_position_resumes_from_the_reactivated_plans_own_history(
        self, client, application, profile, two_plans
    ):
        """Sessions logged under the old plan still drive its position, so
        reactivating resumes where that plan left off."""
        active_id, inactive_id = two_plans
        with application.app_context():
            old_workouts = (PlannedWorkout.query.filter_by(plan_id=inactive_id)
                            .order_by(PlannedWorkout.order_index).all())
            first_id, second_id = old_workouts[0].id, old_workouts[1].id
        log_session(application, profile, first_id, [("Bench Press", 3)])

        client.post("/plan/%d/reactivate" % inactive_id, follow_redirects=True)

        with application.app_context():
            from app import get_active_plan, get_next_workout
            plan = get_active_plan(profile)
            assert plan.id == inactive_id
            nxt = get_next_workout(profile, plan)
            assert nxt.id == second_id

    def test_plan_history_offers_a_reactivate_control(self, client, two_plans):
        r = client.get("/plan/history")
        assert r.status_code == 200
        assert b"reactivate" in r.data.lower()
