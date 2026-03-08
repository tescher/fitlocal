import json
import os
import re
import anthropic


def get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


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


def generate_workout_plan(profile, fitness_test=None):
    client = get_client()

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

    prompt = f"""You are a certified personal trainer inspired by Tony Horton's P90X methodology. Create a detailed 12-week periodized workout plan for the following person:

Age: {profile.age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.
{fitness_test_section}
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

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=32000,
        messages=[{"role": "user", "content": prompt}],
    )

    return _extract_json(message.content[0].text)


def generate_progress_review(profile, sessions_data):
    client = get_client()

    prompt = f"""You are Tony Horton — legendary fitness trainer, creator of P90X. You're reviewing one of your people's workout data. Be DIRECT, MOTIVATIONAL, and use your signature style:

- Use Tony Horton catchphrases naturally: "Bring it!", "Do your best and forget the rest!", "Rome wasn't built in a day, and neither was your body!", "Tip of the day...", "That's called X, and I like it!"
- Be encouraging but honest — if someone is slacking, call it out with love
- Keep it personal and energetic — like you're right there in the room
- Reference specific exercises and numbers from their data

Client: {profile.name}, Age: {profile.age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.

Here is a summary of their recent workout sessions:
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

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return _extract_json(message.content[0].text)
