"""
FitLocal pytest test suite.
Uses an in-memory SQLite database — never touches the production DB.
AI calls are mocked throughout.
"""
import json
import os
import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from werkzeug.datastructures import MultiDict

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import app as flask_app
from models import (
    db, UserProfile, WorkoutPlan, PlannedWorkout, PlannedExercise,
    WorkoutSession, LoggedSet, AIReview, FitnessTest, ExerciseLibrary,
)


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
def client(application):
    return application.test_client()


@pytest.fixture
def profile(application):
    with application.app_context():
        p = UserProfile(
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
            is_active=True, total_weeks=12, current_week=1,
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
                is_active=False, notes="pending", total_weeks=12,
            )
            db.session.add(pending)
            db.session.commit()

        r = client.post("/generate-plan/confirm")
        assert r.status_code == 302

        with application.app_context():
            plan = WorkoutPlan.query.filter_by(is_active=True).first()
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
                is_active=False, notes="pending", total_weeks=12,
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
                plan_json=plan_json, is_active=False, notes="pending", total_weeks=12,
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
                plan_json=plan_json, is_active=False, notes="pending", total_weeks=12,
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
