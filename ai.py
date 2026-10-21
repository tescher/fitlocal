import json
import os
import re
import anthropic

MODEL_PLAN = "claude-opus-5-5"
MODEL_REVIEW = "claude-opus-5-5"
MODEL_CHAT = "claude-sonnet-5-5"
MODEL_CHECKIN = "claude-sonnet-5-5"  # same model as chat so both share one prompt cache

# Server-side refusal fallback: if a safety classifier declines a request, the
# API re-runs it on Anthropic's recommended fallback model in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens: (input, output, cache read, 5-minute cache write).
# List prices as of 2026-10; update when Anthropic's pricing page changes.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),  # a possible refusal fallback
}
_UNKNOWN_MODEL_PRICE = PRICES["claude-opus-4-8"]  # err on the high side


class AIRefusalError(Exception):
    """Claude declined the request (stop_reason "refusal")."""


def get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def estimate_cost(model, usage):
    """Estimated USD cost of one response from its usage block.

    input_tokens counts only uncached input; thinking is billed as output.
    """
    price_in, price_out, price_cache_read, price_cache_write = PRICES.get(model, _UNKNOWN_MODEL_PRICE)
    return (
        (usage.input_tokens or 0) * price_in
        + (usage.output_tokens or 0) * price_out
        + (usage.cache_read_input_tokens or 0) * price_cache_read
        + (usage.cache_creation_input_tokens or 0) * price_cache_write
    ) / 1_000_000


def _send(model, messages, max_tokens, effort, system=None, on_usage=None):
    """Send a request and return the reply text.

    Streams so long replies don't hit HTTP timeouts. Thinking is always on for
    the 5.5 models, so max_tokens covers thinking plus the reply, and the reply
    text is read from the text blocks rather than content[0].

    on_usage(model, usage) is called once the response arrives, before the
    refusal check, so declined requests are still counted.
    """
    request = dict(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
        messages=messages,
    )
    if system is not None:
        request["system"] = system

    client = get_client()
    with client.beta.messages.stream(**request) as stream:
        message = stream.get_final_message()

    if on_usage:
        on_usage(message.model, message.usage)
    if message.stop_reason == "refusal":
        raise AIRefusalError("Claude declined this request. Try rewording it.")
    return "".join(block.text for block in message.content if block.type == "text")


def _ask_claude(model, prompt, max_tokens, effort, on_usage=None):
    """Send a single-prompt request and return the reply text."""
    return _send(model, [{"role": "user", "content": prompt}], max_tokens, effort, on_usage=on_usage)


def _extract_json(text):
    """Extract the first complete JSON object from a response string.

    Handles raw JSON, ```json ... ``` blocks, and ``` ... ``` blocks.
    Raises ValueError with a clear message if no valid JSON is found.
    """
    # Strip markdown code fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    # Find the outermost { ... } in case there is surrounding commentary
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in response")
    text = text[start: end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}") from e


def generate_workout_plan(profile, fitness_test=None, prior_review=None, extra_context=None, on_usage=None):
    fitness_test_section = ""
    if fitness_test:
        fitness_test_section = f"""
Recent Fitness Test Results (use these to calibrate difficulty):
- Push-ups: {fitness_test.pushups}
- Pull-ups: {fitness_test.pullups}
- Wall Sit: {fitness_test.wall_sit_seconds} seconds
- Toe Touch: {fitness_test.toe_touch_inches} inches
- Plank: {fitness_test.plank_seconds} seconds
- Vertical Jump: {fitness_test.vertical_jump_inches} inches
"""

    prior_review_section = ""
    if prior_review:
        suggestions = prior_review.get("suggestions", [])
        suggestions_text = "\n".join(f"  - {s}" for s in suggestions) if suggestions else "  None provided"
        prior_review_section = f"""
Prior Plan Review (use these insights to inform the new plan — build on what worked, address what didn't):
- What was working well: {prior_review.get("whats_working", "N/A")}
- Watch out for: {prior_review.get("watch_out_for", "N/A")}
- Suggested adjustments:
{suggestions_text}
- Overall assessment: {prior_review.get("overall_assessment", "N/A")}
"""

    extra_context_section = ""
    if extra_context and extra_context.strip():
        extra_context_section = f"""
Additional context from the user (treat this as high-priority input when building the plan):
{extra_context.strip()}
"""

    prompt = f"""You are a certified personal trainer inspired by Tony Horton's P90X methodology. Create a detailed 12-week periodized workout plan for the following person:

Age: {profile.current_age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.
{fitness_test_section}{prior_review_section}{extra_context_section}
Requirements:
- Create 3 workouts per cycle labeled "Workout A", "Workout B", "Workout C"
- The user will do them in sequence on whatever days they choose — do NOT use day names like Monday/Wednesday/Friday
- Create a 12-week periodized plan with these phases:
  - Weeks 1-3: Foundation (build base strength and form)
  - Week 4: Recovery (deload week - lighter weights, fewer sets)
  - Weeks 5-7: Build (increase intensity and volume)
  - Week 8: Recovery (deload week)
  - Weeks 9-11: Peak (highest intensity, advanced movements)
  - Week 12: Recovery (final deload before retest)
- Each workout MUST include warm-up exercises (type: "warmup"), main exercises (type: "main"), and cool-down exercises (type: "cooldown")
- Include form_cues for EVERY exercise (brief tips on proper form)
- Include a nutrition_guide for each phase (simple tips, not a meal plan)

Return a JSON object with this structure:
{{
  "plan_name": string,
  "description": string,
  "days_per_week": 3,
  "total_weeks": 12,
  "phases": [
    {{
      "phase_name": string,
      "phase_type": "progressive" or "recovery",
      "week_start": int,
      "week_end": int,
      "description": string,
      "nutrition_guide": string
    }}
  ],
  "workouts": [
    {{
      "day": "Workout A",
      "name": string,
      "exercises": [
        {{
          "name": string,
          "type": "warmup" or "main" or "cooldown",
          "sets": int,
          "reps": string,
          "rest_seconds": int,
          "notes": string,
          "form_cues": string
        }}
      ]
    }}
  ]
}}

Return only valid JSON, no commentary."""

    return _extract_json(_ask_claude(MODEL_PLAN, prompt, max_tokens=64000, effort="high", on_usage=on_usage))


def generate_progress_review(profile, sessions_data, plan_name=None, range_label=None, on_usage=None):
    """range_label (e.g. "the last 3 months") reviews a time window that may span
    several plans; without it, the sessions are all from the current plan."""
    plan_label = f'"{plan_name}"' if plan_name else "their current"
    if range_label:
        sessions_label = (
            f"{len(sessions_data)} completed sessions from {range_label} "
            f"(their current plan is {plan_label}; each session is tagged with the plan it belonged to, "
            f"so focus your plan-adjustment suggestions on the current plan)"
        )
    else:
        sessions_label = f"all {len(sessions_data)} completed sessions from their {plan_label} plan"

    prompt = f"""You are Tony Horton — legendary fitness trainer, creator of P90X. You're reviewing one of your people's workout data. Be DIRECT, MOTIVATIONAL, and use your signature style:

- Use Tony Horton catchphrases naturally: "Bring it!", "Do your best and forget the rest!", "Rome wasn't built in a day, and neither was your body!", "Tip of the day...", "That's called X, and I like it!"
- Be encouraging but honest — if someone is slacking, call it out with love
- Keep it personal and energetic — like you're right there in the room
- Reference specific exercises and numbers from their data

Client: {profile.name}, Age: {profile.current_age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.

Here is a summary of {sessions_label}:
{json.dumps(sessions_data, indent=2)}

Please analyze this data and provide your Tony Horton-style review.

Return your review as JSON with these keys:
{{
  "whats_working": string (what they're crushing — be specific and encouraging),
  "watch_out_for": string (what needs attention — be direct but supportive),
  "suggestions": [string] (3-5 specific adjustments, Tony Horton style),
  "overall_assessment": string (your big-picture motivational assessment, sign off as Tony)
}}

Return only valid JSON, no commentary."""

    return _extract_json(_ask_claude(MODEL_REVIEW, prompt, max_tokens=16000, effort="high", on_usage=on_usage))


# ---------------------------------------------------------------------------
# In-workout trainer chat
# ---------------------------------------------------------------------------

CHAT_MAX_TOKENS = 4000  # thinking (kept short at low effort) plus a short reply
CHECKIN_PASS = "PASS"  # what the model replies when a check-in isn't worth saying

# The trainer's personality, chosen per user (UserProfile.trainer_style).
COACH_STYLES = {
    "encouraging": {
        "label": "Encouraging",
        "instruction": ("Your coaching style is warm and upbeat. Celebrate effort and progress, "
                        "and frame problems as the next step forward."),
    },
    "tough_love": {
        "label": "Tough love",
        "instruction": ("Your coaching style is direct and demanding, like a no-nonsense coach. Push them "
                        "and call out slacking (long rests, skipped sets) bluntly, but never insult them "
                        "and never push them through pain."),
    },
    "calm": {
        "label": "Calm & technical",
        "instruction": ("Your coaching style is calm, steady and technical. Focus on form, pacing and the "
                        "numbers, and keep the emotional tone low-key."),
    },
}
DEFAULT_COACH_STYLE = "encouraging"
_SNAPSHOT_MAX_EXERCISES = 30
_SNAPSHOT_MAX_SETS = 20


def _number(value):
    """A float from user-entered form text, or None when blank or invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value):
    return f"{value:g}"


def _format_logged_set(s):
    weight, reps = _number(s.get("weight")), _number(s.get("reps"))
    if reps is None and weight is None:
        return "not logged yet"
    parts = []
    if weight is not None and weight > 0:
        parts.append(f"{_fmt_number(weight)} lbs x {_fmt_number(reps or 0)} reps")
    else:
        parts.append(f"bodyweight x {_fmt_number(reps or 0)} reps")
    weight_b, reps_b = _number(s.get("weight_b")), _number(s.get("reps_b"))
    if reps_b is not None:
        parts.append(f"superset B {_fmt_number(weight_b or 0)} lbs x {_fmt_number(reps_b)} reps")
    rpe = _number(s.get("rpe"))
    if rpe is not None:
        parts.append(f"RPE {_fmt_number(rpe)}")
    notes = str(s.get("notes") or "").strip()[:100]
    if notes:
        parts.append(f'note: "{notes}"')
    return ", ".join(parts)


def format_live_snapshot(snapshot):
    """Text summary of the workout page's current state, sent ahead of each
    user chat message. The snapshot comes from the browser, so every field is
    treated as untrusted: unknown keys are ignored and sizes are capped."""
    if not isinstance(snapshot, dict):
        snapshot = {}
    header = "[Live workout status"
    elapsed = _number(snapshot.get("elapsed_seconds"))
    if elapsed is not None:
        header += f", {int(elapsed // 60)} min in"
    rest = _number(snapshot.get("rest_remaining"))
    if rest is not None and rest > 0:
        header += f", rest timer: {int(rest)}s left"
    lines = [header + "]"]

    exercises = snapshot.get("exercises")
    if not isinstance(exercises, list):
        exercises = []
    for ex in exercises[:_SNAPSHOT_MAX_EXERCISES]:
        if not isinstance(ex, dict):
            continue
        name = str(ex.get("name") or "").strip()[:100]
        sets = ex.get("sets") if isinstance(ex.get("sets"), list) else []
        set_texts = [
            f"set {i}: {_format_logged_set(s)}"
            for i, s in enumerate(sets[:_SNAPSHOT_MAX_SETS], start=1)
            if isinstance(s, dict)
        ]
        if name:
            lines.append(f"{name}: " + ("; ".join(set_texts) or "no sets"))
    return "\n".join(lines)


def _trainer_system_prompt(profile, workout_context):
    exercise_lines = []
    for ex in workout_context.get("exercises", []):
        line = f"- {ex['name']}: {ex['sets']} sets x {ex['reps']} reps"
        if ex.get("rest_seconds"):
            line += f", rest {ex['rest_seconds']}s"
        if ex.get("notes"):
            line += f". Notes: {ex['notes']}"
        if ex.get("recent"):
            line += f". Recent sessions: {ex['recent']}"
        exercise_lines.append(line)

    review = workout_context.get("review_suggestions") or "None yet."
    style = COACH_STYLES.get(getattr(profile, "trainer_style", None), COACH_STYLES[DEFAULT_COACH_STYLE])
    return f"""You are a personal trainer coaching {profile.name} through a workout in real time, by text chat between sets. Sometimes you also speak up unprompted, like a trainer watching from nearby.

{style['instruction']}

How to reply:
- Keep it short: one to three sentences, since they are reading between sets. Use a short list only when they ask for steps.
- Be specific. Use the numbers they have logged today and their recent sessions, not generic advice.
- Be encouraging and practical, like a good in-person trainer. When something needs adjusting (weight, reps, rest, form, or swapping an exercise), give one clear next step.
- If they describe pain beyond normal muscle effort (sharp or joint pain, chest pain, dizziness, numbness), tell them to stop that exercise rather than push through, and to get it checked if it persists.
- Plain text only: no markdown headings, tables, or emojis.

Athlete: Age {profile.current_age}, Sex {profile.sex}, Fitness level {profile.fitness_level}. Goals: {profile.goals}

Plan: {workout_context.get('plan_name') or 'Unknown'}. Today's workout: {workout_context.get('workout_name') or 'Unknown'}.
Planned exercises:
{chr(10).join(exercise_lines) or '- None listed'}

Latest progress review suggestions: {review}

Each of their messages starts with a [Live workout status] block showing what they have logged so far today. Use it, but don't read it back to them."""


def chat_with_trainer(profile, workout_context, messages, on_usage=None):
    """Reply to the in-workout chat. messages is the full conversation in API
    form (user turns already include their live status block), oldest first.

    The system prompt is the same for the whole workout and carries a cache
    breakpoint; history is append-only, so each turn reuses the cached prefix.
    """
    system = [{
        "type": "text",
        "text": _trainer_system_prompt(profile, workout_context),
        "cache_control": {"type": "ephemeral"},
    }]
    return _send(MODEL_CHAT, messages, CHAT_MAX_TOKENS, "low", system=system, on_usage=on_usage).strip()


def format_checkin_turn(moment, status_text):
    """The user turn for a check-in: the moment the workout page noticed, the
    instructions for speaking up, then the live workout status."""
    return f"""[Check-in: they haven't messaged you. You're deciding whether to speak up unprompted.]
Moment: {moment}

If it's worth saying, reply with one or two short sentences they will hear between sets. Be motivating and specific: mention a number, an exercise, or their goal, not generic hype on its own. When something needs fixing, pair the encouragement with one clear next step. Don't repeat the wording or opening of anything you've already said this workout. If nothing useful is worth saying right now, reply with exactly {CHECKIN_PASS}.

{status_text}"""


def trainer_checkin(profile, workout_context, messages, on_usage=None):
    """Compose an unprompted check-in. messages ends with the check-in turn from
    format_checkin_turn(). Returns "" when the model decides to stay quiet."""
    system = [{
        "type": "text",
        "text": _trainer_system_prompt(profile, workout_context),
        "cache_control": {"type": "ephemeral"},
    }]
    reply = _send(MODEL_CHECKIN, messages, CHAT_MAX_TOKENS, "low", system=system, on_usage=on_usage).strip()
    return "" if reply.strip(".").upper() == CHECKIN_PASS else reply
