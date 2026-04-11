"""
Import a workout session exported by export_session.py into this machine's database.
Run on the DESTINATION machine (the one you want the record on).

Usage:
    python import_session.py <json_file>

Example:
    python import_session.py session_42.json

The script remaps user_id and planned_workout_id to the correct local values.
It will NOT create a duplicate if a session with the same date already exists
(it will ask you to confirm before proceeding).
"""

import sys
import json
from datetime import datetime, date, timezone
from app import app
from models import db, WorkoutSession, LoggedSet, UserProfile, PlannedWorkout


def import_session(json_path: str):
    with open(json_path) as f:
        payload = json.load(f)

    with app.app_context():
        s = payload["session"]

        # --- resolve user ---
        users = UserProfile.query.all()
        if not users:
            print("ERROR: No user profile found in this database. Run setup first.")
            sys.exit(1)
        if len(users) == 1:
            user = users[0]
        else:
            print("Multiple profiles found:")
            for u in users:
                print(f"  [{u.id}] {u.name}")
            uid = int(input("Enter the user id to assign this session to: ").strip())
            user = UserProfile.query.get(uid)
            if not user:
                print("Invalid id.")
                sys.exit(1)

        # --- resolve planned_workout ---
        planned_workout_id = None
        if s.get("planned_workout_name"):
            pw = PlannedWorkout.query.filter_by(
                workout_name=s["planned_workout_name"]
            ).first()
            if pw:
                planned_workout_id = pw.id
            else:
                print(
                    f"WARNING: Could not find planned workout '{s['planned_workout_name']}' "
                    "in this database. Session will be imported without a plan link."
                )

        # --- check for duplicate ---
        existing = WorkoutSession.query.filter_by(
            user_id=user.id, date=s["date"]
        ).first()
        if existing:
            print(
                f"WARNING: A session already exists for {s['date']} (id={existing.id})."
            )
            confirm = input("Import anyway and create a second session for that date? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                sys.exit(0)

        # --- parse datetimes ---
        def parse_dt(val):
            if not val:
                return None
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                return None

        # --- insert session ---
        new_session = WorkoutSession(
            user_id=user.id,
            planned_workout_id=planned_workout_id,
            date=date.fromisoformat(s["date"]),
            start_time=parse_dt(s.get("start_time")),
            end_time=parse_dt(s.get("end_time")),
            overall_feeling=s.get("overall_feeling"),
            session_notes=s.get("session_notes"),
        )
        db.session.add(new_session)
        db.session.flush()  # get new_session.id before committing

        # --- insert sets ---
        for row in payload["logged_sets"]:
            ls = LoggedSet(
                session_id=new_session.id,
                exercise_name=row["exercise_name"],
                set_number=row["set_number"],
                weight_lbs=row.get("weight_lbs"),
                reps_completed=row.get("reps_completed"),
                rpe=row.get("rpe"),
                notes=row.get("notes"),
            )
            db.session.add(ls)

        db.session.commit()
        print(
            f"Imported session for {s['date']} as id={new_session.id} "
            f"with {len(payload['logged_sets'])} sets. User: {user.name}."
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    import_session(sys.argv[1])
