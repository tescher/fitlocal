"""Read-only workout history lookups for the trainer chat (Claude tool use).

Each tool is a fixed ORM query filtered to one user's sessions, so the coach
can only read that user's own workout log: no SQL, no writes, no other tables.
Results are small JSON documents with times converted to the browser's time
zone (the DB stores naive UTC), since only the browser knows where the user is.
"""
import json
from datetime import date, datetime, timezone

from models import LoggedSet, PlannedWorkout, WorkoutSession

from zoneinfo import ZoneInfo

DEFAULT_LIMIT = 10
MAX_LIMIT = 30
_NOTES_MAX_CHARS = 300
_NAME_MAX_CHARS = 100

# Sent with every chat and check-in request, unchanged, so it stays in the
# prompt cache. Numeric ranges are in the descriptions because strict tool
# schemas don't support minimum/maximum; the code clamps them.
TOOL_DEFINITIONS = [
    {
        "name": "list_past_workouts",
        "description": (
            "List the athlete's past workout sessions, newest first, excluding the workout in progress. "
            "Each one has its date, weekday, days ago, local start and end time, duration, workout name, "
            "plan, phase, status (completed or paused), feeling (1-5), exercise names and notes. "
            "Use it for questions like when they last did a kind of workout, what time of day they train, "
            "or how often they've trained. Filters are optional and combine."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "workout_name": {"type": "string", "description": (
                    "Case-insensitive text the workout name contains, e.g. \"pull\" or \"upper\".")},
                "exercise_name": {"type": "string", "description": (
                    "Only workouts with an exercise whose name contains this text, e.g. \"deadlift\".")},
                "date_from": {"type": "string", "description": "Earliest date, YYYY-MM-DD (inclusive)."},
                "date_to": {"type": "string", "description": "Latest date, YYYY-MM-DD (inclusive)."},
                "limit": {"type": "integer", "description": f"How many to return, 1-{MAX_LIMIT}. Default {DEFAULT_LIMIT}."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_workout_details",
        "description": (
            "Every logged set of one past workout session (weight x reps, superset partner, RPE, set notes), "
            "grouped by exercise, plus its times, feeling and session notes. "
            "Get session_id from list_past_workouts or get_exercise_history."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"session_id": {"type": "integer"}},
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_exercise_history",
        "description": (
            "The athlete's past sets of one exercise, newest session first: the sets, total volume "
            "(bodyweight reps count as 1 lb) and average RPE per session. The name matches loosely, "
            "so \"bench\" also finds \"Incline Bench Press\"; each session says which exercise matched."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise_name": {"type": "string", "description": "Text the exercise name contains."},
                "limit": {"type": "integer", "description": f"How many sessions, 1-{MAX_LIMIT}. Default {DEFAULT_LIMIT}."},
            },
            "required": ["exercise_name"],
            "additionalProperties": False,
        },
    },
]


def resolve_time_zone(name):
    """(tzinfo, name) for an IANA zone name from the browser, or UTC if it's unknown."""
    if name:
        try:
            return ZoneInfo(str(name)[:64]), str(name)[:64]
        except Exception:  # unknown or malformed zone name
            pass
    return timezone.utc, "UTC"


def _clock(dt):
    """'8:30 AM' style, without a leading zero."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _text_filter(tool_input, key):
    value = str(tool_input.get(key) or "").strip()[:_NAME_MAX_CHARS]
    return value or None


def _limit(tool_input):
    try:
        value = int(tool_input.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _date_filter(tool_input, key):
    value = tool_input.get(key)
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"{key} must be a date like 2026-07-15, not {str(value)[:30]!r}.")


def _like(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def format_set(s):
    """One logged set as text, e.g. '135 lbs x 8 reps, RPE 8'."""
    if s.weight_lbs is None:
        text = f"bodyweight x {s.reps_completed or 0} reps"
    else:
        text = f"{s.weight_lbs:g} lbs x {s.reps_completed or 0} reps"
    if s.reps_b is not None:
        weight_b = "bodyweight" if s.weight_b is None else f"{s.weight_b:g} lbs"
        text += f", superset B {weight_b} x {s.reps_b} reps"
    if s.rpe is not None:
        text += f", RPE {s.rpe:g}"
    if s.notes:
        text += f', note: "{s.notes.strip()[:100]}"'
    return text


class HistoryTools:
    """The history tools for one user. Pass to ai.chat_with_trainer(history=...)."""

    definitions = TOOL_DEFINITIONS

    def __init__(self, user_id, time_zone=None, exclude_chat_key=None):
        self.user_id = user_id
        self.tz, self.tz_name = resolve_time_zone(time_zone)
        self.exclude_chat_key = exclude_chat_key

    def run(self, name, tool_input):
        """Run a tool and return its JSON result. Raises ValueError for bad input,
        which the chat sends back to Claude as a tool error."""
        handler = {
            "list_past_workouts": self.list_past_workouts,
            "get_workout_details": self.get_workout_details,
            "get_exercise_history": self.get_exercise_history,
        }.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool {name!r}.")
        result = handler(tool_input if isinstance(tool_input, dict) else {})
        return json.dumps({"today": self._today_label(), "time_zone": self.tz_name, **result})

    # -- helpers ---------------------------------------------------------

    def _today(self):
        return datetime.now(timezone.utc).astimezone(self.tz).date()

    def _today_label(self):
        today = self._today()
        return f"{today.isoformat()} ({today.strftime('%A')})"

    def _local_clock(self, dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return _clock(dt.astimezone(self.tz))

    def _sessions(self):
        query = WorkoutSession.query.filter(WorkoutSession.user_id == self.user_id)
        if self.exclude_chat_key:
            query = query.filter(
                (WorkoutSession.chat_key.is_(None)) | (WorkoutSession.chat_key != self.exclude_chat_key))
        return query

    def _summary(self, ws):
        pw = ws.planned_workout
        duration = None
        if ws.elapsed_seconds:
            duration = round(ws.elapsed_seconds / 60)
        elif ws.start_time and ws.end_time:
            duration = round((ws.end_time - ws.start_time).total_seconds() / 60)
        return {
            "session_id": ws.id,
            "date": ws.date.isoformat() if ws.date else None,
            "weekday": ws.date.strftime("%A") if ws.date else None,
            "days_ago": (self._today() - ws.date).days if ws.date else None,
            "start_time": self._local_clock(ws.start_time),
            "end_time": self._local_clock(ws.end_time),
            "duration_minutes": duration,
            "workout_name": pw.workout_name if pw else None,
            "plan_name": pw.plan.name if pw and pw.plan else None,
            "phase": ws.phase_name,
            "status": ws.status,
            "feeling": ws.overall_feeling,
        }

    def _session_query(self, tool_input):
        query = self._sessions()
        workout_name = _text_filter(tool_input, "workout_name")
        if workout_name:
            query = query.join(PlannedWorkout, WorkoutSession.planned_workout_id == PlannedWorkout.id).filter(
                PlannedWorkout.workout_name.ilike(_like(workout_name), escape="\\"))
        exercise_name = _text_filter(tool_input, "exercise_name")
        if exercise_name:
            query = query.filter(WorkoutSession.logged_sets.any(
                LoggedSet.exercise_name.ilike(_like(exercise_name), escape="\\")))
        date_from, date_to = _date_filter(tool_input, "date_from"), _date_filter(tool_input, "date_to")
        if date_from:
            query = query.filter(WorkoutSession.date >= date_from)
        if date_to:
            query = query.filter(WorkoutSession.date <= date_to)
        return query.order_by(WorkoutSession.date.desc(), WorkoutSession.start_time.desc(),
                              WorkoutSession.id.desc())

    # -- tools -----------------------------------------------------------

    def list_past_workouts(self, tool_input):
        limit = _limit(tool_input)
        rows = self._session_query(tool_input).limit(limit + 1).all()
        workouts = []
        for ws in rows[:limit]:
            item = self._summary(ws)
            item["exercises"] = list(dict.fromkeys(
                s.exercise_name for s in sorted(ws.logged_sets, key=lambda s: s.id)))
            item["notes"] = (ws.session_notes or "").strip()[:_NOTES_MAX_CHARS] or None
            workouts.append(item)
        return {"workouts": workouts, "more_available": len(rows) > limit}

    def get_workout_details(self, tool_input):
        try:
            session_id = int(tool_input.get("session_id"))
        except (TypeError, ValueError):
            raise ValueError("session_id must be a number from list_past_workouts.")
        ws = self._sessions().filter(WorkoutSession.id == session_id).first()
        if ws is None:
            raise ValueError(f"No past workout with session_id {session_id}.")
        exercises = {}
        for s in sorted(ws.logged_sets, key=lambda s: (s.id, s.set_number or 0)):
            exercises.setdefault(s.exercise_name, []).append(format_set(s))
        result = self._summary(ws)
        result["notes"] = (ws.session_notes or "").strip() or None
        result["exercises"] = [{"name": name, "sets": sets} for name, sets in exercises.items()]
        return result

    def get_exercise_history(self, tool_input):
        exercise_name = _text_filter(tool_input, "exercise_name")
        if not exercise_name:
            raise ValueError("exercise_name is required.")
        limit = _limit(tool_input)
        rows = self._session_query({"exercise_name": exercise_name}).limit(limit + 1).all()
        sessions = []
        for ws in rows[:limit]:
            sets = sorted((s for s in ws.logged_sets if _matches(s.exercise_name, exercise_name)),
                          key=lambda s: (s.id, s.set_number or 0))
            # Same volume rule as app.get_exercise_history(): a null weight is bodyweight (x1).
            volume = sum((1 if s.weight_lbs is None else s.weight_lbs) * (s.reps_completed or 0)
                         + (1 if s.weight_b is None else s.weight_b) * (s.reps_b or 0) for s in sets)
            rpes = [s.rpe for s in sets if s.rpe is not None]
            item = self._summary(ws)
            item.update({
                "exercise": ", ".join(dict.fromkeys(s.exercise_name for s in sets)),
                "sets": [format_set(s) for s in sets],
                "volume": volume,
                "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
            })
            sessions.append(item)
        return {"sessions": sessions, "more_available": len(rows) > limit}


def _matches(name, text):
    return text.lower() in (name or "").lower()
