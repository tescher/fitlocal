from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timezone

db = SQLAlchemy()


class UserProfile(db.Model):
    __tablename__ = "user_profile"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    sex = db.Column(db.String(20), nullable=False)
    fitness_level = db.Column(db.String(20), nullable=False)
    goals = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WorkoutPlan(db.Model):
    __tablename__ = "workout_plan"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    days_per_week = db.Column(db.Integer)
    plan_json = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    notes = db.Column(db.Text)

    planned_workouts = db.relationship("PlannedWorkout", backref="plan", cascade="all, delete-orphan")


class Exercise(db.Model):
    __tablename__ = "exercise"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    muscle_group = db.Column(db.String(100))
    equipment = db.Column(db.String(100))
    description = db.Column(db.Text)


class PlannedWorkout(db.Model):
    __tablename__ = "planned_workout"
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("workout_plan.id"), nullable=False)
    day_of_week = db.Column(db.String(20), nullable=False)
    workout_name = db.Column(db.String(200), nullable=False)
    order_index = db.Column(db.Integer, default=0)

    planned_exercises = db.relationship("PlannedExercise", backref="planned_workout", cascade="all, delete-orphan")


class PlannedExercise(db.Model):
    __tablename__ = "planned_exercise"
    id = db.Column(db.Integer, primary_key=True)
    planned_workout_id = db.Column(db.Integer, db.ForeignKey("planned_workout.id"), nullable=False)
    exercise_name = db.Column(db.String(200), nullable=False)
    sets_prescribed = db.Column(db.Integer, nullable=False)
    reps_prescribed = db.Column(db.String(50), nullable=False)
    rest_seconds = db.Column(db.Integer)
    notes = db.Column(db.Text)


class WorkoutSession(db.Model):
    __tablename__ = "workout_session"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    planned_workout_id = db.Column(db.Integer, db.ForeignKey("planned_workout.id"), nullable=True)
    date = db.Column(db.Date, default=date.today)
    start_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    end_time = db.Column(db.DateTime)
    overall_feeling = db.Column(db.Integer)
    session_notes = db.Column(db.Text)

    logged_sets = db.relationship("LoggedSet", backref="session", cascade="all, delete-orphan")
    planned_workout = db.relationship("PlannedWorkout")


class LoggedSet(db.Model):
    __tablename__ = "logged_set"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("workout_session.id"), nullable=False)
    exercise_name = db.Column(db.String(200), nullable=False)
    set_number = db.Column(db.Integer, nullable=False)
    weight_lbs = db.Column(db.Float)
    reps_completed = db.Column(db.Integer)
    rpe = db.Column(db.Integer)
    notes = db.Column(db.Text)


class AIReview(db.Model):
    __tablename__ = "ai_review"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    review_text = db.Column(db.Text)
    suggestions_json = db.Column(db.Text)
    data_summary = db.Column(db.Text)
