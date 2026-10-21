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
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from werkzeug.datastructures import MultiDict

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
# Must be set before importing app: its startup code creates tables and runs
# migrations against DATABASE_URL, which otherwise defaults to instance/fitlocal.db.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import app as flask_app
from models import (
    db, Account, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, ExerciseLibrary,
    TrainerChatMessage, AIUsage,
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
# Birthdate / computed age
# ---------------------------------------------------------------------------

def _setup_form(**overrides):
    data = {
        "name": "Tim", "age": "35", "birthdate": "", "sex": "Male",
        "fitness_level": "Intermediate", "goals": "Build muscle",
    }
    data.update(overrides)
    return data


def _mock_usage(input_tokens=1000, output_tokens=200, cache_read=0, cache_write=0):
    return MagicMock(input_tokens=input_tokens, output_tokens=output_tokens,
                     cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)


def _mock_ai_message(text, stop_reason="end_turn", thinking_first=False, model="claude-opus-5-5"):
    """A fake Messages API response: optional empty thinking block, then text."""
    blocks = []
    if thinking_first:
        blocks.append(MagicMock(type="thinking", thinking=""))
    blocks.append(MagicMock(type="text", text=text))
    return MagicMock(content=blocks, stop_reason=stop_reason, model=model, usage=_mock_usage())


def _mock_chat_client(*replies):
    """Mock client whose successive stream calls return each reply as plain text."""
    client = MagicMock()
    stream = client.beta.messages.stream.return_value.__enter__.return_value
    stream.get_final_message.side_effect = [
        _mock_ai_message(r, model="claude-sonnet-5-5") for r in replies
    ]
    return client


def _mock_ai_client(response_json, **message_kwargs):
    """Mock Anthropic client whose beta.messages.stream returns response_json as text."""
    client = MagicMock()
    stream = client.beta.messages.stream.return_value.__enter__.return_value
    stream.get_final_message.return_value = _mock_ai_message(json.dumps(response_json), **message_kwargs)
    return client


def _ai_call_kwargs(mock_client):
    """Keyword arguments of the last Claude request made through the mock client."""
    return mock_client.beta.messages.stream.call_args.kwargs


class TestBirthdate:
    def test_calculate_age_before_and_after_birthday(self):
        from models import calculate_age
        bd = date(1980, 6, 15)
        assert calculate_age(bd, date(2026, 6, 14)) == 45
        assert calculate_age(bd, date(2026, 6, 15)) == 46
        assert calculate_age(bd, date(2026, 12, 31)) == 46

    def test_calculate_age_leap_day_birthdate(self):
        from models import calculate_age
        bd = date(2000, 2, 29)
        assert calculate_age(bd, date(2025, 2, 28)) == 24
        assert calculate_age(bd, date(2025, 3, 1)) == 25
        assert calculate_age(bd, date(2028, 2, 29)) == 28

    def test_current_age_uses_birthdate(self, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            p.birthdate = date(date.today().year - 40, 1, 1)
            db.session.commit()
            assert p.current_age == 40

    def test_current_age_falls_back_to_manual_age(self, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            assert p.birthdate is None
            assert p.current_age == 35

    def test_setup_with_birthdate_saves_it_and_computes_age(self, client, application):
        bd = date(date.today().year - 40, 1, 1)
        r = client.post("/setup", data=_setup_form(age="", birthdate=bd.isoformat()))
        assert r.status_code == 302
        with application.app_context():
            p = UserProfile.query.first()
            assert p.birthdate == bd
            assert p.age == 40
            assert p.current_age == 40

    def test_birthdate_overrides_submitted_age(self, client, application):
        bd = date(date.today().year - 40, 1, 1)
        client.post("/setup", data=_setup_form(age="99", birthdate=bd.isoformat()))
        with application.app_context():
            assert UserProfile.query.first().age == 40

    def test_clearing_birthdate_reverts_to_manual_age(self, client, application):
        bd = date(date.today().year - 40, 1, 1)
        client.post("/setup", data=_setup_form(birthdate=bd.isoformat()))
        client.post("/setup", data=_setup_form(age="37", birthdate=""))
        with application.app_context():
            p = UserProfile.query.first()
            assert p.birthdate is None
            assert p.age == 37
            assert p.current_age == 37

    def test_neither_age_nor_birthdate_rejected(self, client, application):
        r = client.post("/setup", data=_setup_form(age="", birthdate=""))
        assert r.status_code == 200
        assert b"age or birthdate" in r.data
        with application.app_context():
            assert UserProfile.query.count() == 0

    def test_future_birthdate_rejected(self, client, application):
        future = date.today() + timedelta(days=30)
        r = client.post("/setup", data=_setup_form(birthdate=future.isoformat()))
        assert r.status_code == 200
        with application.app_context():
            assert UserProfile.query.count() == 0

    def test_invalid_birthdate_rejected_without_changing_profile(self, client, application, profile):
        r = client.post("/setup", data=_setup_form(age="50", birthdate="not-a-date"))
        assert r.status_code == 200
        with application.app_context():
            p = UserProfile.query.get(profile)
            assert p.age == 35
            assert p.birthdate is None

    def test_setup_form_shows_saved_birthdate(self, client, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            p.birthdate = date(1985, 3, 7)
            db.session.commit()
        r = client.get("/setup")
        assert b'name="birthdate"' in r.data
        assert b'value="1985-03-07"' in r.data

    def test_generate_plan_page_shows_computed_age(self, client, application, profile):
        with application.app_context():
            p = UserProfile.query.get(profile)
            p.birthdate = date(date.today().year - 40, 1, 1)
            db.session.commit()
        r = client.get("/generate-plan")
        assert b"<strong>Age:</strong> 40" in r.data

    def test_plan_prompt_uses_computed_age(self, application, profile):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            p.birthdate = date(date.today().year - 40, 1, 1)
            mock_client = _mock_ai_client({"plan_name": "X", "workouts": []})
            with patch("ai.get_client", return_value=mock_client):
                ai.generate_workout_plan(p)
        prompt = _ai_call_kwargs(mock_client)["messages"][0]["content"]
        assert "Age: 40," in prompt

    def test_review_prompt_uses_computed_age(self, application, profile):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            p.birthdate = date(date.today().year - 40, 1, 1)
            mock_client = _mock_ai_client({"whats_working": "", "watch_out_for": "",
                                           "suggestions": [], "overall_assessment": ""})
            with patch("ai.get_client", return_value=mock_client):
                ai.generate_progress_review(p, [], plan_name="Plan")
        prompt = _ai_call_kwargs(mock_client)["messages"][0]["content"]
        assert "Age: 40," in prompt


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

    def test_session_detail_hidden_from_other_users(self, application, profile, active_plan):
        with application.app_context():
            pw_id = PlannedWorkout.query.filter_by(day_of_week="Workout A").first().id
            other = Account(email="other@example.com", email_claimed=True)
            db.session.add(other)
            db.session.flush()
            db.session.add(UserProfile(account_id=other.id, name="Other", age=30, sex="Female",
                                       fitness_level="Beginner", goals="Run a 5K"))
            db.session.commit()
            other_id = other.id
        sid = log_session(application, profile, pw_id, [("Bench Press", 3)])

        other_client = application.test_client()
        with other_client.session_transaction() as sess:
            sess["_user_id"] = str(other_id)
            sess["_fresh"] = True
        r = other_client.get(f"/history/{sid}")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/history")
        assert b"Bench Press" not in r.data

    def test_session_detail_without_profile_redirects_to_setup(self, client, application, profile, active_plan):
        with application.app_context():
            pw_id = PlannedWorkout.query.filter_by(day_of_week="Workout A").first().id
            sid = log_session(application, profile, pw_id, [("Bench Press", 3)])
            # A fresh account with no profile of its own
            lonely = Account(email="lonely@example.com", email_claimed=True)
            db.session.add(lonely)
            db.session.commit()
            lonely_id = lonely.id
        lonely_client = application.test_client()
        with lonely_client.session_transaction() as sess:
            sess["_user_id"] = str(lonely_id)
            sess["_fresh"] = True
        r = lonely_client.get(f"/history/{sid}")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/setup")


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
            status="active", total_weeks=4, current_week=1,
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
# Review range (1 month / 3 months / entire plan) and progress overlay
# ---------------------------------------------------------------------------

_MOCK_REVIEW = {
    "whats_working": "Good", "watch_out_for": "Form",
    "suggestions": ["More sleep"], "overall_assessment": "Bring it!",
}


@pytest.fixture
def range_sessions(application, profile, active_plan):
    """Sessions spread across time and plans. Returns dict of label -> session id.

    current plan: 10, 60, 120 days ago
    old plan:     20 days ago
    unplanned:    5 days ago
    """
    with application.app_context():
        old = WorkoutPlan(
            user_id=profile, name="Old Plan", description="", days_per_week=3,
            plan_json="{}", status="inactive", total_weeks=12,
        )
        db.session.add(old)
        db.session.flush()
        old_pw = PlannedWorkout(plan_id=old.id, day_of_week="Workout A",
                                workout_name="Old Upper", order_index=0)
        db.session.add(old_pw)
        db.session.commit()
        old_pw_id = old_pw.id
        cur_pw_id = PlannedWorkout.query.filter_by(plan_id=active_plan).first().id

    ids = {}
    for label, pw_id, days in [("cur_10", cur_pw_id, 10), ("cur_60", cur_pw_id, 60),
                               ("cur_120", cur_pw_id, 120), ("old_20", old_pw_id, 20),
                               ("unplanned_5", None, 5)]:
        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=days)
        with application.app_context():
            ids[label] = WorkoutSession.query.order_by(WorkoutSession.id.desc()).first().id
    return ids


def _ids(sessions):
    return {s.id for s in sessions}


class TestReviewRange:
    def test_entire_plan_range_is_current_plan_only(self, application, profile, active_plan, range_sessions):
        with application.app_context():
            got = _ids(flask_app._get_review_sessions(profile, active_plan, "plan"))
        assert got == {range_sessions[k] for k in ("cur_10", "cur_60", "cur_120")}

    def test_default_range_is_entire_plan(self, application, profile, active_plan, range_sessions):
        with application.app_context():
            assert (_ids(flask_app._get_review_sessions(profile, active_plan))
                    == _ids(flask_app._get_review_sessions(profile, active_plan, "plan")))

    def test_one_month_spans_all_plans_within_30_days(self, application, profile, active_plan, range_sessions):
        with application.app_context():
            got = _ids(flask_app._get_review_sessions(profile, active_plan, "1m"))
        assert got == {range_sessions[k] for k in ("cur_10", "old_20", "unplanned_5")}

    def test_three_months_spans_all_plans_within_90_days(self, application, profile, active_plan, range_sessions):
        with application.app_context():
            got = _ids(flask_app._get_review_sessions(profile, active_plan, "3m"))
        assert got == {range_sessions[k] for k in ("cur_10", "cur_60", "old_20", "unplanned_5")}

    def test_one_month_boundary_is_30_days(self, application, profile, active_plan):
        with application.app_context():
            pw_id = PlannedWorkout.query.filter_by(plan_id=active_plan).first().id
        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=30)
        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=31)
        with application.app_context():
            dates = [s.date for s in flask_app._get_review_sessions(profile, active_plan, "1m")]
        assert dates == [date.today() - timedelta(days=30)]

    def test_generate_review_sends_only_sessions_in_range(self, client, application, range_sessions):
        with patch("ai.generate_progress_review", return_value=_MOCK_REVIEW) as mock_review:
            client.post("/review/generate", data={"range": "1m"})
        sessions_data = mock_review.call_args.args[1]
        assert len(sessions_data) == 3
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        assert all(s["date"] >= cutoff for s in sessions_data)
        assert mock_review.call_args.kwargs["range_label"] == "the last month"

    def test_generate_review_tags_sessions_with_plan_name(self, client, application, range_sessions):
        with patch("ai.generate_progress_review", return_value=_MOCK_REVIEW) as mock_review:
            client.post("/review/generate", data={"range": "3m"})
        plans = sorted(str(s["plan"]) for s in mock_review.call_args.args[1])
        assert plans == ["None", "Old Plan", "Test Plan", "Test Plan"]

    def test_generate_review_records_range_in_data_summary(self, client, application, range_sessions):
        with patch("ai.generate_progress_review", return_value=_MOCK_REVIEW):
            client.post("/review/generate", data={"range": "3m"})
        with application.app_context():
            summary = json.loads(AIReview.query.first().data_summary)
        assert summary == {"sessions_count": 4, "range": "3m"}

    def test_generate_review_invalid_range_falls_back_to_plan(self, client, application, range_sessions):
        with patch("ai.generate_progress_review", return_value=_MOCK_REVIEW) as mock_review:
            client.post("/review/generate", data={"range": "bogus"})
        assert len(mock_review.call_args.args[1]) == 3
        assert mock_review.call_args.kwargs["range_label"] is None
        with application.app_context():
            assert json.loads(AIReview.query.first().data_summary)["range"] == "plan"

    def test_generate_review_empty_range_creates_no_review(self, client, application, profile, active_plan):
        with application.app_context():
            pw_id = PlannedWorkout.query.filter_by(plan_id=active_plan).first().id
        log_session(application, profile, pw_id, [("Bench Press", 1)], delta_days=45)
        with patch("ai.generate_progress_review", return_value=_MOCK_REVIEW) as mock_review:
            r = client.post("/review/generate", data={"range": "1m"}, follow_redirects=True)
        mock_review.assert_not_called()
        assert b"No completed sessions" in r.data
        with application.app_context():
            assert AIReview.query.count() == 0

    def test_review_page_shows_range_selector_with_counts(self, client, range_sessions):
        r = client.get("/review")
        html = r.data.decode()
        assert 'name="range"' in html
        assert 'value="1m" data-count="3"' in html
        assert 'value="3m" data-count="4"' in html
        assert 'value="plan" data-count="3" selected' in html
        assert "Last 1 month" in html and "Last 3 months" in html and "Entire plan" in html

    def test_review_page_shows_last_review_coverage(self, client, application, profile):
        with application.app_context():
            db.session.add(AIReview(user_id=profile, review_text="x",
                                    suggestions_json=json.dumps(_MOCK_REVIEW),
                                    data_summary=json.dumps({"sessions_count": 14, "range": "3m"})))
            db.session.commit()
        html = client.get("/review").data.decode()
        assert "Last 3 months · 14 sessions" in html

    def test_review_page_handles_legacy_data_summary(self, client, application, profile):
        with application.app_context():
            db.session.add(AIReview(user_id=profile, review_text="x",
                                    suggestions_json=json.dumps(_MOCK_REVIEW),
                                    data_summary=json.dumps({"sessions_count": 36})))
            db.session.commit()
        r = client.get("/review")
        assert r.status_code == 200
        assert "36 sessions" in r.data.decode()

    def test_review_prompt_describes_selected_range(self, application, profile):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            mock_client = _mock_ai_client(_MOCK_REVIEW)
            with patch("ai.get_client", return_value=mock_client):
                ai.generate_progress_review(p, [{}, {}], plan_name="Test Plan",
                                            range_label="the last 3 months")
        prompt = _ai_call_kwargs(mock_client)["messages"][0]["content"]
        assert "2 completed sessions from the last 3 months" in prompt
        assert 'current plan is "Test Plan"' in prompt

    def test_review_prompt_without_range_describes_plan(self, application, profile):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            mock_client = _mock_ai_client(_MOCK_REVIEW)
            with patch("ai.get_client", return_value=mock_client):
                ai.generate_progress_review(p, [{}, {}], plan_name="Test Plan")
        prompt = _ai_call_kwargs(mock_client)["messages"][0]["content"]
        assert 'all 2 completed sessions from their "Test Plan" plan' in prompt

    def test_review_page_has_progress_overlay(self, client, range_sessions):
        html = client.get("/review").data.decode()
        assert 'id="loadingOverlay"' in html
        assert "Reviewing Your Progress" in html
        assert 'onsubmit="showLoading(this)"' in html

    def test_generate_plan_page_still_has_progress_overlay(self, client, profile):
        html = client.get("/generate-plan").data.decode()
        assert 'id="loadingOverlay"' in html
        assert "Building Your Plan" in html
        assert 'onsubmit="showLoading(this)"' in html


# ---------------------------------------------------------------------------
# Claude models: Opus 5.5 with streaming, effort, and refusal fallbacks
# ---------------------------------------------------------------------------

_MOCK_PLAN = {"plan_name": "X", "workouts": []}


class TestClaudeModels:
    def _generate_plan(self, application, profile, mock_client):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            with patch("ai.get_client", return_value=mock_client):
                return ai.generate_workout_plan(p)

    def _generate_review(self, application, profile, mock_client):
        import ai
        with application.app_context():
            p = UserProfile.query.get(profile)
            with patch("ai.get_client", return_value=mock_client):
                return ai.generate_progress_review(p, [], plan_name="Plan")

    def test_model_constants_are_opus_5_5(self):
        import ai
        assert ai.MODEL_PLAN == "claude-opus-5-5"
        assert ai.MODEL_REVIEW == "claude-opus-5-5"

    def test_plan_generation_request(self, application, profile):
        mock_client = _mock_ai_client(_MOCK_PLAN)
        self._generate_plan(application, profile, mock_client)
        kwargs = _ai_call_kwargs(mock_client)
        assert kwargs["model"] == "claude-opus-5-5"
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["max_tokens"] >= 64000  # thinking counts toward max_tokens
        assert kwargs["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in kwargs["betas"]

    def test_review_request(self, application, profile):
        mock_client = _mock_ai_client(_MOCK_REVIEW)
        self._generate_review(application, profile, mock_client)
        kwargs = _ai_call_kwargs(mock_client)
        assert kwargs["model"] == "claude-opus-5-5"
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["max_tokens"] >= 16000
        assert kwargs["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in kwargs["betas"]

    def test_requests_send_no_sampling_parameters(self, application, profile):
        # Opus 5.5 rejects temperature/top_p/top_k with a 400.
        mock_client = _mock_ai_client(_MOCK_PLAN)
        self._generate_plan(application, profile, mock_client)
        kwargs = _ai_call_kwargs(mock_client)
        assert not {"temperature", "top_p", "top_k", "thinking"} & kwargs.keys()

    def test_plan_reads_text_after_thinking_block(self, application, profile):
        mock_client = _mock_ai_client(_MOCK_PLAN, thinking_first=True)
        assert self._generate_plan(application, profile, mock_client)["plan_name"] == "X"

    def test_review_reads_text_after_thinking_block(self, application, profile):
        mock_client = _mock_ai_client(_MOCK_REVIEW, thinking_first=True)
        assert self._generate_review(application, profile, mock_client) == _MOCK_REVIEW

    def test_refusal_raises_clear_error(self, application, profile):
        import ai
        mock_client = _mock_ai_client(_MOCK_PLAN, stop_reason="refusal")
        with pytest.raises(ai.AIRefusalError, match="declined"):
            self._generate_plan(application, profile, mock_client)

    def test_plan_route_flashes_refusal(self, client, profile):
        mock_client = _mock_ai_client(_MOCK_PLAN, stop_reason="refusal")
        with patch("ai.get_client", return_value=mock_client):
            r = client.post("/generate-plan/generate", follow_redirects=True)
        assert "declined" in r.data.decode()
        assert "Plan generated" not in r.data.decode()


# ---------------------------------------------------------------------------
# In-workout trainer chat
# ---------------------------------------------------------------------------

CHAT_KEY = "0123456789abcdef" * 2

_SNAPSHOT = {
    "elapsed_seconds": 1380,
    "rest_remaining": 45,
    "exercises": [{"name": "Bench Press", "sets": [
        {"set": 1, "weight": "135", "reps": "10", "rpe": "8", "notes": ""},
        {"set": 2, "weight": "", "reps": "", "rpe": "", "notes": ""},
    ]}],
}


@pytest.fixture
def upper_body(application, active_plan):
    with application.app_context():
        return PlannedWorkout.query.filter_by(workout_name="Upper Body").first().id


def _chat(client, planned_workout_id, message="Should I add weight?", key=CHAT_KEY, snapshot=_SNAPSHOT):
    return client.post("/api/workout/chat", json={
        "chat_key": key, "planned_workout_id": planned_workout_id,
        "message": message, "snapshot": snapshot,
    })


def _add_chat_messages(application, profile_id, key=CHAT_KEY):
    with application.app_context():
        db.session.add(TrainerChatMessage(user_id=profile_id, chat_key=key, role="user",
                                          content="Is 135 too light?", context="[status]"))
        db.session.add(TrainerChatMessage(user_id=profile_id, chat_key=key, role="assistant",
                                          content="Go up to 145 next set."))
        db.session.commit()


class TestTrainerChatAI:
    def _workout_context(self, application, upper_body):
        with application.app_context():
            return flask_app.build_trainer_context(
                UserProfile.query.first(), PlannedWorkout.query.get(upper_body))

    def test_chat_model_is_sonnet_5_5(self):
        import ai
        assert ai.MODEL_CHAT == "claude-sonnet-5-5"

    def test_chat_request_uses_low_effort_and_fallbacks(self, application, profile, upper_body):
        import ai
        mock_client = _mock_chat_client("Add 5 lbs.")
        ctx = self._workout_context(application, upper_body)
        with application.app_context():
            with patch("ai.get_client", return_value=mock_client):
                reply = ai.chat_with_trainer(UserProfile.query.get(profile), ctx,
                                             [{"role": "user", "content": "Hi"}])
        assert reply == "Add 5 lbs."
        kwargs = _ai_call_kwargs(mock_client)
        assert kwargs["model"] == "claude-sonnet-5-5"
        assert kwargs["output_config"] == {"effort": "low"}
        assert kwargs["fallbacks"] == "default"

    def test_system_prompt_has_profile_plan_and_cache_marker(self, application, profile, upper_body):
        import ai
        mock_client = _mock_chat_client("ok")
        ctx = self._workout_context(application, upper_body)
        with application.app_context():
            with patch("ai.get_client", return_value=mock_client):
                ai.chat_with_trainer(UserProfile.query.get(profile), ctx,
                                     [{"role": "user", "content": "Hi"}])
        system = _ai_call_kwargs(mock_client)["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        text = system[0]["text"]
        assert "Build muscle" in text
        assert "Upper Body" in text
        assert "Bench Press: 3 sets x 8-10 reps, rest 90s" in text

    def test_system_prompt_includes_recent_history(self, application, profile, upper_body):
        log_session(application, profile, upper_body, [("Bench Press", 2)], delta_days=3)
        ctx = self._workout_context(application, upper_body)
        assert "135 lbs x 10" in ctx["exercises"][0]["recent"]

    def test_format_live_snapshot(self):
        import ai
        text = ai.format_live_snapshot(_SNAPSHOT)
        assert "23 min in" in text
        assert "rest timer: 45s left" in text
        assert "Bench Press" in text
        assert "set 1: 135 lbs x 10 reps, RPE 8" in text
        assert "set 2: not logged yet" in text

    def test_format_live_snapshot_ignores_junk(self):
        import ai
        text = ai.format_live_snapshot({"elapsed_seconds": "abc", "exercises": "nope"})
        assert "Live workout status" in text

    def test_on_usage_gets_model_and_usage(self, application, profile, upper_body):
        import ai
        mock_client = _mock_chat_client("ok")
        seen = []
        ctx = self._workout_context(application, upper_body)
        with application.app_context():
            with patch("ai.get_client", return_value=mock_client):
                ai.chat_with_trainer(UserProfile.query.get(profile), ctx,
                                     [{"role": "user", "content": "Hi"}],
                                     on_usage=lambda model, usage: seen.append((model, usage.input_tokens)))
        assert seen == [("claude-sonnet-5-5", 1000)]

    def test_estimate_cost(self):
        import ai
        usage = _mock_usage(1_000_000, 100_000, cache_read=1_000_000, cache_write=1_000_000)
        assert ai.estimate_cost("claude-sonnet-5-5", usage) == pytest.approx(2 + 1 + 0.2 + 2.5)
        assert ai.estimate_cost("claude-opus-5-5", usage) == pytest.approx(4 + 2 + 0.2 + 5)


class TestTrainerChatRoute:
    def test_requires_login(self, application, profile, upper_body):
        r = _chat(application.test_client(), upper_body)
        assert r.status_code in (302, 401)

    def test_reply_saves_messages_and_usage(self, client, application, profile, upper_body):
        with patch("ai.get_client", return_value=_mock_chat_client("Add 5 lbs.")):
            r = _chat(client, upper_body)
        assert r.status_code == 200
        body = r.get_json()
        assert body["reply"] == "Add 5 lbs."
        assert body["usage"]["messages"] == 1
        assert body["usage"]["cost"] == pytest.approx(0.004)
        assert body["usage"]["budget"] == pytest.approx(1.0)
        with application.app_context():
            msgs = TrainerChatMessage.query.filter_by(chat_key=CHAT_KEY).order_by(TrainerChatMessage.id).all()
            assert [(m.role, m.content) for m in msgs] == [
                ("user", "Should I add weight?"), ("assistant", "Add 5 lbs.")]
            assert "set 1: 135 lbs x 10 reps" in msgs[0].context
            usage = AIUsage.query.one()
            assert (usage.feature, usage.model, usage.chat_key) == ("chat", "claude-sonnet-5-5", CHAT_KEY)
            assert usage.input_tokens == 1000 and usage.output_tokens == 200

    def test_second_turn_replays_history_unchanged(self, client, application, profile, upper_body):
        mock_client = _mock_chat_client("Add 5 lbs.", "Nice work.")
        with patch("ai.get_client", return_value=mock_client):
            _chat(client, upper_body, "Should I add weight?")
            _chat(client, upper_body, "Done, felt good")
        first, second = mock_client.beta.messages.stream.call_args_list
        first_msgs = first.kwargs["messages"]
        second_msgs = second.kwargs["messages"]
        assert [m["role"] for m in second_msgs] == ["user", "assistant", "user"]
        # Append-only: earlier turns are byte-identical so the prompt cache hits.
        assert second_msgs[0] == first_msgs[0]
        assert second_msgs[1]["content"] == "Add 5 lbs."
        assert "Done, felt good" in second_msgs[2]["content"]
        assert "Live workout status" in second_msgs[2]["content"]

    def test_rejects_empty_message(self, client, profile, upper_body):
        assert _chat(client, upper_body, message="   ").status_code == 400

    def test_rejects_overlong_message(self, client, profile, upper_body):
        assert _chat(client, upper_body, message="x" * 2001).status_code == 400

    def test_rejects_bad_chat_key(self, client, profile, upper_body):
        assert _chat(client, upper_body, key="not-a-key").status_code == 400

    def test_rejects_unknown_planned_workout(self, client, profile, upper_body):
        assert _chat(client, 99999).status_code == 404

    def test_ai_error_returns_502_and_saves_nothing(self, client, application, profile, upper_body):
        mock_client = MagicMock()
        mock_client.beta.messages.stream.side_effect = RuntimeError("boom")
        with patch("ai.get_client", return_value=mock_client):
            r = _chat(client, upper_body)
        assert r.status_code == 502
        assert "error" in r.get_json()
        with application.app_context():
            assert TrainerChatMessage.query.count() == 0

    def test_refusal_returns_502_with_message(self, client, application, profile, upper_body):
        mock_client = MagicMock()
        stream = mock_client.beta.messages.stream.return_value.__enter__.return_value
        stream.get_final_message.return_value = _mock_ai_message("", stop_reason="refusal")
        with patch("ai.get_client", return_value=mock_client):
            r = _chat(client, upper_body)
        assert r.status_code == 502
        assert "declined" in r.get_json()["error"]


class TestTrainerChatWorkoutFlow:
    def _form(self, application, upper_body, key=CHAT_KEY):
        items = [("overall_feeling", "4"), ("session_notes", ""),
                 ("planned_workout_id", str(upper_body)), ("chat_key", key),
                 ("exercise_name", "Bench Press"), ("set_number", "1"), ("weight", "135"),
                 ("reps", "10"), ("rpe", "7"), ("set_notes", "")]
        return MultiDict(items)

    def test_workout_page_has_chat_panel_and_key(self, client, profile, active_plan):
        html = client.get("/workout/today").data.decode()
        assert 'id="coachPanel"' in html
        assert 'id="coachMeter"' in html
        import re
        assert re.search(r'name="chat_key" value="[0-9a-f]{32}"', html)

    def test_log_links_chat_to_session(self, client, application, profile, upper_body):
        _add_chat_messages(application, profile)
        client.post("/workout/log", data=self._form(application, upper_body))
        with application.app_context():
            ws = WorkoutSession.query.one()
            assert ws.chat_key == CHAT_KEY
            assert {m.session_id for m in TrainerChatMessage.query.all()} == {ws.id}

    def test_session_detail_shows_chat(self, client, application, profile, upper_body):
        _add_chat_messages(application, profile)
        client.post("/workout/log", data=self._form(application, upper_body))
        with application.app_context():
            ws_id = WorkoutSession.query.one().id
        html = client.get(f"/history/{ws_id}").data.decode()
        assert "Trainer Chat" in html
        assert "Go up to 145 next set." in html

    def test_resume_restores_chat(self, client, application, profile, upper_body):
        _add_chat_messages(application, profile)
        client.post("/workout/pause", data=self._form(application, upper_body))
        with application.app_context():
            ws_id = WorkoutSession.query.one().id
        html = client.get(f"/workout/resume/{ws_id}").data.decode()
        assert f'name="chat_key" value="{CHAT_KEY}"' in html
        assert "Go up to 145 next set." in html

    def test_delete_session_deletes_chat(self, client, application, profile, upper_body):
        _add_chat_messages(application, profile)
        client.post("/workout/log", data=self._form(application, upper_body))
        with application.app_context():
            ws_id = WorkoutSession.query.one().id
        client.post(f"/history/{ws_id}/delete")
        with application.app_context():
            assert TrainerChatMessage.query.count() == 0

    def test_delete_account_deletes_chat_and_usage(self, client, application, profile, upper_body):
        _add_chat_messages(application, profile)
        with application.app_context():
            db.session.add(AIUsage(user_id=profile, feature="chat", model="claude-sonnet-5-5", cost_usd=0.01))
            db.session.commit()
        client.post("/settings/delete-account", data={"confirm_email": "test@example.com"})
        with application.app_context():
            assert TrainerChatMessage.query.count() == 0
            assert AIUsage.query.count() == 0


class TestTrainerVoice:
    def test_permissions_policy_allows_microphone_for_this_site_only(self, client, profile):
        policy = client.get("/settings").headers["Permissions-Policy"]
        assert "microphone=(self)" in policy
        assert "camera=()" in policy
        assert "geolocation=()" in policy

    def test_workout_page_has_voice_toggles_and_mic(self, client, profile, active_plan):
        html = client.get("/workout/today").data.decode()
        assert 'id="coachVoiceIn"' in html
        assert 'id="coachVoiceOut"' in html
        assert 'id="coachMic"' in html
        assert "Voice input" in html
        assert "Speak replies" in html


class TestAIUsageTracking:
    def test_plan_generation_records_usage(self, client, application, profile):
        with patch("ai.get_client", return_value=_mock_ai_client(_MOCK_PLAN)):
            client.post("/generate-plan/generate")
        with application.app_context():
            usage = AIUsage.query.one()
            assert (usage.feature, usage.model) == ("plan", "claude-opus-5-5")
            assert usage.cost_usd == pytest.approx(1000 * 4 / 1e6 + 200 * 20 / 1e6)

    def test_review_records_usage(self, client, application, profile, active_plan, upper_body):
        log_session(application, profile, upper_body, [("Bench Press", 1)])
        with patch("ai.get_client", return_value=_mock_ai_client(_MOCK_REVIEW)):
            client.post("/review/generate", data={"range": "plan"})
        with application.app_context():
            assert AIUsage.query.one().feature == "review"

    def test_settings_shows_this_months_usage(self, client, application, profile):
        with application.app_context():
            old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=40)
            db.session.add_all([
                AIUsage(user_id=profile, feature="chat", model="claude-sonnet-5-5", cost_usd=0.50),
                AIUsage(user_id=profile, feature="plan", model="claude-opus-5-5", cost_usd=0.25),
                AIUsage(user_id=profile, feature="plan", model="claude-opus-5-5", cost_usd=9.99, created_at=old),
            ])
            db.session.commit()
        html = client.get("/settings").data.decode()
        assert "$0.75" in html
        assert "Trainer chat" in html
        assert "$9.99" not in html
        assert 'name="budget" value="1.00"' in html

    def test_update_budget(self, client, application, profile):
        client.post("/settings/ai-budget", data={"budget": "2.50"})
        with application.app_context():
            assert UserProfile.query.get(profile).ai_budget_per_workout == pytest.approx(2.5)

    def test_rejects_invalid_budget(self, client, application, profile):
        r = client.post("/settings/ai-budget", data={"budget": "-1"}, follow_redirects=True)
        assert "Budget must be" in r.data.decode()
        with application.app_context():
            assert UserProfile.query.get(profile).ai_budget_per_workout == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Stored UTC timestamps are shown in the viewer's local time
# ---------------------------------------------------------------------------

class TestLocalTimes:
    """created_at columns hold naive UTC. Templates emit them via the local_time
    filter as <time datetime="...Z">, which base.html converts in the browser."""

    def test_filter_marks_naive_datetime_as_utc(self, application):
        from datetime import datetime
        out = str(flask_app.local_time(datetime(2026, 9, 30, 0, 23), "datetime"))
        assert 'class="local-time"' in out
        assert 'datetime="2026-09-30T00:23:00Z"' in out
        assert 'data-format="datetime"' in out
        assert "September 30, 2026 at 12:23 AM UTC" in out  # no-JS fallback

    def test_filter_accepts_aware_utc_datetime(self, application):
        from datetime import datetime, timezone
        out = str(flask_app.local_time(datetime(2026, 9, 30, 0, 23, tzinfo=timezone.utc), "date"))
        assert 'datetime="2026-09-30T00:23:00Z"' in out
        assert 'data-format="date"' in out
        assert "Sep 30, 2026" in out

    def test_filter_handles_none(self, application):
        assert str(flask_app.local_time(None)) == ""

    def test_base_template_converts_local_times(self, client, profile):
        html = client.get("/review").data.decode()
        assert "document.querySelectorAll('time.local-time')" in html

    def _add_review(self, application, profile):
        from datetime import datetime
        with application.app_context():
            db.session.add(AIReview(user_id=profile, review_text="x",
                                    suggestions_json=json.dumps(_MOCK_REVIEW),
                                    data_summary=json.dumps({"sessions_count": 1, "range": "plan"}),
                                    created_at=datetime(2026, 9, 30, 0, 23)))
            db.session.commit()

    def test_review_page_last_review_time_is_local(self, client, application, profile):
        self._add_review(application, profile)
        html = client.get("/review").data.decode()
        assert '<time class="local-time" datetime="2026-09-30T00:23:00Z" data-format="datetime">' in html

    def test_generate_plan_last_review_date_is_local(self, client, application, profile):
        self._add_review(application, profile)
        html = client.get("/generate-plan").data.decode()
        assert '<time class="local-time" datetime="2026-09-30T00:23:00Z" data-format="date">' in html

    def test_plan_history_created_at_fallback_is_local(self, client, application, profile):
        from datetime import datetime
        with application.app_context():
            db.session.add(WorkoutPlan(user_id=profile, name="Old", description="", days_per_week=3,
                                       plan_json="{}", status="inactive", total_weeks=12,
                                       start_date=None, created_at=datetime(2026, 9, 30, 0, 23)))
            db.session.commit()
        html = client.get("/plan/history").data.decode()
        assert '<time class="local-time" datetime="2026-09-30T00:23:00Z" data-format="date">' in html


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

    def test_reactivate_rejects_another_users_plan(self, client, application, two_plans):
        """A plan belonging to a different profile must not be reactivatable.

        two_plans is required so the caller actually has a profile and an
        active plan of their own -- without it the route short-circuits on
        `if not profile` and the ownership check is never reached.
        """
        caller_active_id, _ = two_plans
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
            # The foreign plan stays inactive AND the caller's own active plan
            # must not be demoted as a side effect.
            assert WorkoutPlan.query.get(foreign_id).status == "inactive"
            assert WorkoutPlan.query.get(caller_active_id).status == "active"

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


# ---------------------------------------------------------------------------
# Test runs must never open the real database (instance/fitlocal.db)
# ---------------------------------------------------------------------------

import subprocess
import sys
import textwrap

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Runs in a subprocess: makes any sqlite connection to the real database fail,
# then executes the given code. Prints "ok" if nothing tried to open it.
_REAL_DB_GUARD = textwrap.dedent('''
    import os, sys, sqlite3
    app_dir = sys.argv[1]
    real_db = os.path.normcase(os.path.abspath(os.path.join(app_dir, "instance", "fitlocal.db")))

    def guard(orig):
        def connect(database, *args, **kwargs):
            path = str(database)
            if path.startswith("file:"):
                path = path[len("file:"):].split("?")[0]
            if path != ":memory:" and os.path.normcase(os.path.abspath(path)) == real_db:
                raise RuntimeError("opened the real database: " + path)
            return orig(database, *args, **kwargs)
        return connect

    sqlite3.connect = guard(sqlite3.connect)
    sqlite3.dbapi2.connect = guard(sqlite3.dbapi2.connect)
    sys.path.insert(0, app_dir)
    os.chdir(app_dir)
''')


def _run_isolated(code, env_overrides=None):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "-c", _REAL_DB_GUARD + textwrap.dedent(code), _APP_DIR],
        capture_output=True, text=True, env=env, timeout=120,
    )


class TestDatabaseIsolation:
    def test_importing_tests_module_never_opens_real_db(self):
        result = _run_isolated('''
            import tests
            print("ok")
        ''')
        assert result.returncode == 0, result.stderr[-2000:]
        assert "ok" in result.stdout

    def test_migrate_uses_given_db_path(self, tmp_path):
        import migrate
        db_file = tmp_path / "old.db"
        engine = sa.create_engine(f"sqlite:///{db_file}")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE exercise_library DROP COLUMN video_url"))
        engine.dispose()

        migrate.migrate(str(db_file))

        engine = sa.create_engine(f"sqlite:///{db_file}")
        cols = [c["name"] for c in sa.inspect(engine).get_columns("exercise_library")]
        engine.dispose()
        assert "video_url" in cols

    def test_app_startup_migrates_the_configured_db(self, tmp_path):
        db_file = tmp_path / "configured.db"
        result = _run_isolated('''
            import json, migrate
            calls = []
            migrate.migrate = lambda *a, **k: calls.append([list(a), k])
            import app
            print("CALLS=" + json.dumps(calls))
        ''', {"DATABASE_URL": f"sqlite:///{db_file}"})
        assert result.returncode == 0, result.stderr[-2000:]
        calls = json.loads(result.stdout.split("CALLS=")[1])
        assert len(calls) == 1
        args, kwargs = calls[0]
        called_path = (args + list(kwargs.values()))[0]
        assert os.path.normcase(os.path.abspath(called_path)) == os.path.normcase(str(db_file))

    def test_app_startup_skips_file_migration_for_in_memory_db(self):
        result = _run_isolated('''
            import json, migrate
            calls = []
            migrate.migrate = lambda *a, **k: calls.append([list(a), k])
            import app
            print("CALLS=" + json.dumps(calls))
        ''', {"DATABASE_URL": "sqlite:///:memory:"})
        assert result.returncode == 0, result.stderr[-2000:]
        assert json.loads(result.stdout.split("CALLS=")[1]) == []
