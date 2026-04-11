"""
Export a workout session from this machine's database to a JSON file.
Run on the SOURCE machine (the wrong one you logged on).

Usage:
    python export_session.py <session_id> [output_file]

Example:
    python export_session.py 42
    python export_session.py 42 session_42.json
"""

import sys
import json
from app import app
from models import WorkoutSession, LoggedSet

def export_session(session_id: int, output_path: str):
    with app.app_context():
        session = WorkoutSession.query.get(session_id)
        if not session:
            print(f"ERROR: No session with id={session_id} found.")
            sys.exit(1)

        sets = LoggedSet.query.filter_by(session_id=session_id).order_by(
            LoggedSet.exercise_name, LoggedSet.set_number
        ).all()

        payload = {
            "session": {
                "id": session.id,
                "date": str(session.date),
                "start_time": session.start_time.isoformat() if session.start_time else None,
                "end_time": session.end_time.isoformat() if session.end_time else None,
                "overall_feeling": session.overall_feeling,
                "session_notes": session.session_notes,
                # Store the planned_workout name so the import script can look it up
                "planned_workout_name": (
                    session.planned_workout.workout_name
                    if session.planned_workout else None
                ),
            },
            "logged_sets": [
                {
                    "exercise_name": s.exercise_name,
                    "set_number": s.set_number,
                    "weight_lbs": s.weight_lbs,
                    "reps_completed": s.reps_completed,
                    "rpe": s.rpe,
                    "notes": s.notes,
                }
                for s in sets
            ],
        }

        with open(output_path, "w") as f:
            json.dump(payload, f, indent=2)

        print(f"Exported session {session_id} ({session.date}) with {len(sets)} sets -> {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    session_id = int(sys.argv[1])
    output_path = sys.argv[2] if len(sys.argv) > 2 else f"session_{session_id}.json"
    export_session(session_id, output_path)
