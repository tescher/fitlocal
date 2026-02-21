"""
One-time migration script for FitLocal.

Adds new columns to existing tables and creates new tables.
Since FitLocal is in early development, the simplest approach is to
delete the database and let it recreate. But if you want to preserve
existing data, run this script first.

Usage:
    python migrate.py
"""
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "fitlocal.db")


def migrate():
    if not os.path.exists(DB_PATH):
        print("No database found. Just run the app and tables will be created automatically.")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Helper to check if a column exists
    def column_exists(table, column):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        return column in columns

    # Helper to check if a table exists
    def table_exists(table):
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return cursor.fetchone() is not None

    print("Running FitLocal migration...")

    # --- UserProfile new columns ---
    if not column_exists("user_profile", "current_streak"):
        cursor.execute("ALTER TABLE user_profile ADD COLUMN current_streak INTEGER DEFAULT 0")
        print("  Added user_profile.current_streak")

    if not column_exists("user_profile", "longest_streak"):
        cursor.execute("ALTER TABLE user_profile ADD COLUMN longest_streak INTEGER DEFAULT 0")
        print("  Added user_profile.longest_streak")

    if not column_exists("user_profile", "last_workout_date"):
        cursor.execute("ALTER TABLE user_profile ADD COLUMN last_workout_date DATE")
        print("  Added user_profile.last_workout_date")

    # --- WorkoutPlan new columns ---
    if not column_exists("workout_plan", "total_weeks"):
        cursor.execute("ALTER TABLE workout_plan ADD COLUMN total_weeks INTEGER DEFAULT 12")
        print("  Added workout_plan.total_weeks")

    if not column_exists("workout_plan", "current_week"):
        cursor.execute("ALTER TABLE workout_plan ADD COLUMN current_week INTEGER DEFAULT 1")
        print("  Added workout_plan.current_week")

    if not column_exists("workout_plan", "start_date"):
        cursor.execute("ALTER TABLE workout_plan ADD COLUMN start_date DATE")
        print("  Added workout_plan.start_date")

    # --- PlannedExercise new columns ---
    if not column_exists("planned_exercise", "exercise_type"):
        cursor.execute("ALTER TABLE planned_exercise ADD COLUMN exercise_type VARCHAR(20) DEFAULT 'main'")
        print("  Added planned_exercise.exercise_type")

    if not column_exists("planned_exercise", "form_cues"):
        cursor.execute("ALTER TABLE planned_exercise ADD COLUMN form_cues TEXT")
        print("  Added planned_exercise.form_cues")

    # --- New tables ---
    if not table_exists("fitness_test"):
        cursor.execute("""
            CREATE TABLE fitness_test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES user_profile(id),
                test_date DATE NOT NULL DEFAULT (date('now')),
                pushups INTEGER,
                pullups INTEGER,
                wall_sit_seconds INTEGER,
                toe_touch_inches FLOAT,
                plank_seconds INTEGER,
                vertical_jump_inches FLOAT,
                notes TEXT
            )
        """)
        print("  Created fitness_test table")

    if not table_exists("training_phase"):
        cursor.execute("""
            CREATE TABLE training_phase (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES workout_plan(id),
                phase_name VARCHAR(100) NOT NULL,
                phase_type VARCHAR(20) NOT NULL,
                week_start INTEGER NOT NULL,
                week_end INTEGER NOT NULL,
                description TEXT,
                nutrition_guide TEXT,
                order_index INTEGER DEFAULT 0
            )
        """)
        print("  Created training_phase table")

    if not table_exists("exercise_library"):
        cursor.execute("""
            CREATE TABLE exercise_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) UNIQUE NOT NULL,
                muscle_group VARCHAR(100),
                equipment VARCHAR(100),
                description TEXT,
                form_cues TEXT,
                difficulty VARCHAR(20)
            )
        """)
        print("  Created exercise_library table")

    conn.commit()
    conn.close()
    print("Migration complete!")


if __name__ == "__main__":
    migrate()
