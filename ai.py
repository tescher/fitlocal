import json
import os
import anthropic


def get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def generate_workout_plan(profile):
    client = get_client()

    prompt = f"""You are a certified personal trainer. Create a detailed workout plan for the following person:
Age: {profile.age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.

Return a JSON object with this structure:
{{
  "plan_name": string,
  "description": string,
  "days_per_week": int,
  "workouts": [
    {{
      "day": "Monday",
      "name": string,
      "exercises": [
        {{
          "name": string,
          "sets": int,
          "reps": string,
          "rest_seconds": int,
          "notes": string
        }}
      ]
    }}
  ]
}}

Return only valid JSON, no commentary."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        lines = lines[1:]  # remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)

    return json.loads(response_text)


def generate_progress_review(profile, sessions_data):
    client = get_client()

    prompt = f"""You are a certified personal trainer reviewing a client's workout history.

Client: {profile.name}, Age: {profile.age}, Sex: {profile.sex}, Fitness Level: {profile.fitness_level}, Goals: {profile.goals}.

Here is a summary of their recent workout sessions:
{json.dumps(sessions_data, indent=2)}

Please analyze this data and:
1. Identify strength trends
2. Flag any plateaus or concerning patterns
3. Note themes from session comments
4. Suggest 3-5 specific adjustments to the workout plan

Return your review as JSON with these keys:
{{
  "whats_working": string,
  "watch_out_for": string,
  "suggestions": [string],
  "overall_assessment": string
}}

Return only valid JSON, no commentary."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)

    return json.loads(response_text)
