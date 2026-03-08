import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(1, 1, figsize=(22, 16))
ax.set_xlim(0, 22)
ax.set_ylim(0, 16)
ax.axis('off')
fig.patch.set_facecolor('#1e1e2e')
ax.set_facecolor('#1e1e2e')

COLORS = {
    'header_user':  '#cba6f7',
    'header_core':  '#89b4fa',
    'header_lib':   '#a6e3a1',
    'body':         '#313244',
    'text':         '#cdd6f4',
    'pk':           '#f9e2af',
    'fk':           '#a6e3a1',
    'line':         '#6c7086',
    'title':        '#cba6f7',
    'header_text':  '#1e1e2e',
}


def draw_table(ax, x, y, w, title, fields, hcolor):
    header_h = 0.55
    row_h = 0.35
    total_h = header_h + len(fields) * row_h

    shadow = FancyBboxPatch(
        (x + 0.06, y - total_h - 0.06), w, total_h,
        boxstyle='round,pad=0.04', linewidth=0,
        facecolor='#000000', alpha=0.35, zorder=1)
    ax.add_patch(shadow)

    header = FancyBboxPatch(
        (x, y - header_h), w, header_h,
        boxstyle='round,pad=0.04', linewidth=1.5,
        edgecolor=hcolor, facecolor=hcolor, zorder=2)
    ax.add_patch(header)
    ax.text(x + w / 2, y - header_h / 2, title,
            ha='center', va='center', fontsize=8.5,
            fontweight='bold', color=COLORS['header_text'], zorder=3)

    body = FancyBboxPatch(
        (x, y - total_h), w, total_h - header_h,
        boxstyle='round,pad=0.04', linewidth=1.5,
        edgecolor=hcolor, facecolor=COLORS['body'], zorder=2)
    ax.add_patch(body)

    for i, (tag, name, ftype) in enumerate(fields):
        fy = y - header_h - (i + 0.5) * row_h
        if i % 2 == 0:
            row_bg = FancyBboxPatch(
                (x + 0.04, fy - row_h / 2 + 0.02), w - 0.08, row_h - 0.04,
                boxstyle='round,pad=0.01', linewidth=0,
                facecolor='#1e1e2e', alpha=0.3, zorder=2)
            ax.add_patch(row_bg)

        if tag == 'PK':
            tag_color = COLORS['pk']
        elif tag == 'FK':
            tag_color = COLORS['fk']
        else:
            tag_color = COLORS['text']

        if tag:
            ax.text(x + 0.12, fy, f'[{tag}]', ha='left', va='center',
                    fontsize=5.5, color=tag_color, fontfamily='monospace', zorder=3)
        ax.text(x + 0.55, fy, name, ha='left', va='center',
                fontsize=6.5, color=COLORS['text'], zorder=3)
        ax.text(x + w - 0.1, fy, ftype, ha='right', va='center',
                fontsize=5.5, color='#585b70', fontfamily='monospace', zorder=3)

    return total_h


def rel(ax, x1, y1, x2, y2, label='', color=None, rad=0.0):
    c = color or COLORS['line']
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=c, lw=1.3,
                                connectionstyle=f'arc3,rad={rad}'), zorder=4)
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.1, label,
                ha='center', fontsize=5.5, color='#6c7086')


# ── Row 1: plan/exercise tables ──────────────────────────────────────────────
TOP = 15.6

draw_table(ax, 0.25, TOP, 3.65, 'UserProfile', [
    ('PK', 'id',               'int'),
    ('',   'name',             'str'),
    ('',   'age',              'int'),
    ('',   'sex',              'str'),
    ('',   'fitness_level',    'str'),
    ('',   'goals',            'text'),
    ('',   'current_streak',   'int'),
    ('',   'longest_streak',   'int'),
    ('',   'last_workout_date','date'),
    ('',   'created_at',       'datetime'),
    ('',   'updated_at',       'datetime'),
], COLORS['header_user'])

draw_table(ax, 4.2, TOP, 3.75, 'WorkoutPlan', [
    ('PK', 'id',           'int'),
    ('FK', 'user_id',      'int'),
    ('',   'name',         'str'),
    ('',   'description',  'text'),
    ('',   'days_per_week','int'),
    ('',   'plan_json',    'text'),
    ('',   'is_active',    'bool'),
    ('',   'total_weeks',  'int'),
    ('',   'current_week', 'int'),
    ('',   'start_date',   'date'),
    ('',   'notes',        'text'),
    ('',   'created_at',   'datetime'),
], COLORS['header_core'])

draw_table(ax, 8.2, TOP, 3.55, 'PlannedWorkout', [
    ('PK', 'id',           'int'),
    ('FK', 'plan_id',      'int'),
    ('',   'day_of_week',  'str'),
    ('',   'workout_name', 'str'),
    ('',   'order_index',  'int'),
], COLORS['header_core'])

draw_table(ax, 12.0, TOP, 3.85, 'PlannedExercise', [
    ('PK', 'id',                  'int'),
    ('FK', 'planned_workout_id',  'int'),
    ('FK', 'exercise_library_id', 'int'),
    ('',   'exercise_name',       'str'),
    ('',   'sets_prescribed',     'int'),
    ('',   'reps_prescribed',     'str'),
    ('',   'rest_seconds',        'int'),
    ('',   'exercise_type',       'str'),
    ('',   'form_cues',           'text'),
    ('',   'notes',               'text'),
], COLORS['header_core'])

draw_table(ax, 16.1, TOP, 3.65, 'ExerciseLibrary', [
    ('PK', 'id',           'int'),
    ('',   'name',         'str'),
    ('',   'muscle_group', 'str'),
    ('',   'equipment',    'str'),
    ('',   'description',  'text'),
    ('',   'form_cues',    'text'),
    ('',   'difficulty',   'str'),
], COLORS['header_lib'])

# ── Row 2: session/review tables ─────────────────────────────────────────────
MID = 7.6

draw_table(ax, 0.25, MID, 3.65, 'WorkoutSession', [
    ('PK', 'id',                 'int'),
    ('FK', 'user_id',            'int'),
    ('FK', 'planned_workout_id', 'int'),
    ('',   'date',               'date'),
    ('',   'start_time',         'datetime'),
    ('',   'end_time',           'datetime'),
    ('',   'overall_feeling',    'int'),
    ('',   'session_notes',      'text'),
], COLORS['header_core'])

draw_table(ax, 4.2, MID, 3.75, 'LoggedSet', [
    ('PK', 'id',                  'int'),
    ('FK', 'session_id',          'int'),
    ('FK', 'exercise_library_id', 'int'),
    ('',   'exercise_name',       'str'),
    ('',   'set_number',          'int'),
    ('',   'weight_lbs',          'float'),
    ('',   'reps_completed',      'int'),
    ('',   'rpe',                 'int'),
    ('',   'notes',               'text'),
], COLORS['header_core'])

draw_table(ax, 8.2, MID, 3.55, 'AIReview', [
    ('PK', 'id',              'int'),
    ('FK', 'user_id',         'int'),
    ('',   'created_at',      'datetime'),
    ('',   'review_text',     'text'),
    ('',   'suggestions_json','text'),
    ('',   'data_summary',    'text'),
], COLORS['header_core'])

draw_table(ax, 12.0, MID, 3.85, 'FitnessTest', [
    ('PK', 'id',                   'int'),
    ('FK', 'user_id',              'int'),
    ('',   'test_date',            'date'),
    ('',   'pushups',              'int'),
    ('',   'pullups',              'int'),
    ('',   'wall_sit_seconds',     'int'),
    ('',   'toe_touch_inches',     'float'),
    ('',   'plank_seconds',        'int'),
    ('',   'vertical_jump_inches', 'float'),
    ('',   'notes',                'text'),
], COLORS['header_core'])

draw_table(ax, 16.1, MID, 3.65, 'TrainingPhase', [
    ('PK', 'id',             'int'),
    ('FK', 'plan_id',        'int'),
    ('',   'phase_name',     'str'),
    ('',   'phase_type',     'str'),
    ('',   'week_start',     'int'),
    ('',   'week_end',       'int'),
    ('',   'description',    'text'),
    ('',   'nutrition_guide','text'),
    ('',   'order_index',    'int'),
], COLORS['header_core'])

# ── Relationships ─────────────────────────────────────────────────────────────
LC = COLORS['line']
FC = COLORS['fk']

# UserProfile (right=3.9) → WorkoutPlan (left=4.2)
rel(ax, 3.9, 14.97, 4.2, 14.97, '1:N')
# WorkoutPlan (right=7.95) → PlannedWorkout (left=8.2)
rel(ax, 7.95, 14.97, 8.2, 14.97, '1:N')
# PlannedWorkout (right=11.75) → PlannedExercise (left=12.0)
rel(ax, 11.75, 14.97, 12.0, 14.97, '1:N')
# ExerciseLibrary (left=16.1) → PlannedExercise (right=15.85) — optional FK
rel(ax, 16.1, 14.22, 15.85, 14.22, '0:N', color=FC)

# WorkoutPlan (right=7.95) → TrainingPhase (left=16.1) via bridge line
ax.plot([6.08, 6.08], [11.6, 9.7], color=LC, lw=1.2, ls='--', zorder=3)
ax.plot([6.08, 17.93], [9.7, 9.7], color=LC, lw=1.2, ls='--', zorder=3)
ax.annotate('', xy=(17.93, 7.6), xytext=(17.93, 9.7),
            arrowprops=dict(arrowstyle='->', color=LC, lw=1.3), zorder=4)
ax.text(11.5, 9.82, '1:N', fontsize=5.5, color='#6c7086')

# UserProfile (bottom) → WorkoutSession / AIReview / FitnessTest via bus
ax.plot([2.08, 2.08], [11.6, 9.3], color=LC, lw=1.2, ls='--', zorder=3)
ax.plot([2.08, 14.0], [9.3, 9.3], color=LC, lw=1.2, ls='--', zorder=3)
# → WorkoutSession
ax.annotate('', xy=(2.08, 7.6), xytext=(2.08, 9.3),
            arrowprops=dict(arrowstyle='->', color=LC, lw=1.3), zorder=4)
ax.text(2.2, 8.4, '1:N', fontsize=5.5, color='#6c7086')
# → AIReview
ax.annotate('', xy=(9.98, 7.6), xytext=(9.98, 9.3),
            arrowprops=dict(arrowstyle='->', color=LC, lw=1.3), zorder=4)
ax.text(10.1, 8.4, '1:N', fontsize=5.5, color='#6c7086')
# → FitnessTest
ax.annotate('', xy=(13.93, 7.6), xytext=(13.93, 9.3),
            arrowprops=dict(arrowstyle='->', color=LC, lw=1.3), zorder=4)
ax.text(14.05, 8.4, '1:N', fontsize=5.5, color='#6c7086')

# PlannedWorkout → WorkoutSession (diagonal)
rel(ax, 9.98, 13.45, 2.5, 7.6, '0:N', rad=-0.12)

# WorkoutSession (right=3.9) → LoggedSet (left=4.2)
rel(ax, 3.9, 4.72, 4.2, 4.72, '1:N')

# ExerciseLibrary → LoggedSet (optional FK — green line around the outside)
ax.plot([17.93, 17.93], [8.15, 8.5], color=FC, lw=1.2, zorder=3)
ax.plot([17.93, 5.5], [8.5, 8.5], color=FC, lw=1.2, zorder=3)
ax.annotate('', xy=(5.5, 7.6), xytext=(5.5, 8.5),
            arrowprops=dict(arrowstyle='->', color=FC, lw=1.3), zorder=4)
ax.text(11.5, 8.62, '0:N  (exercise_library_id)', fontsize=5.5, color='#6c7086')

# ── Legend ────────────────────────────────────────────────────────────────────
lx, ly = 0.3, 2.8
ax.text(lx, ly, 'Legend', fontsize=7.5, fontweight='bold', color=COLORS['title'])

ax.add_patch(FancyBboxPatch((lx - 0.1, ly - 2.25), 5.2, 2.0,
    boxstyle='round,pad=0.08', linewidth=1,
    edgecolor='#45475a', facecolor='#181825', zorder=1))

ax.text(lx + 0.15, ly - 0.4, '[PK]  Primary Key', fontsize=6.5, color=COLORS['pk'])
ax.text(lx + 0.15, ly - 0.75, '[FK]  Foreign Key (nullable)', fontsize=6.5, color=COLORS['fk'])

ax.plot([lx + 0.15, lx + 0.65], [ly - 1.15, ly - 1.15], color=LC, lw=1.3)
ax.annotate('', xy=(lx + 0.65, ly - 1.15), xytext=(lx + 0.6, ly - 1.15),
            arrowprops=dict(arrowstyle='->', color=LC, lw=1.3))
ax.text(lx + 0.72, ly - 1.15, 'Mandatory relationship', fontsize=6.5,
        color=COLORS['text'], va='center')

ax.plot([lx + 0.15, lx + 0.65], [ly - 1.5, ly - 1.5], color=LC, lw=1.3, ls='--')
ax.text(lx + 0.72, ly - 1.5, 'Ownership bus (1:N)', fontsize=6.5,
        color=COLORS['text'], va='center')

ax.plot([lx + 0.15, lx + 0.65], [ly - 1.85, ly - 1.85], color=FC, lw=1.3)
ax.annotate('', xy=(lx + 0.65, ly - 1.85), xytext=(lx + 0.6, ly - 1.85),
            arrowprops=dict(arrowstyle='->', color=FC, lw=1.3))
ax.text(lx + 0.72, ly - 1.85, 'Optional FK (exercise library)', fontsize=6.5,
        color=COLORS['text'], va='center')

# ── Title ─────────────────────────────────────────────────────────────────────
ax.text(11, 0.75, 'FitLocal  —  Database Schema', ha='center', fontsize=15,
        fontweight='bold', color=COLORS['title'])

plt.tight_layout(pad=0.2)
plt.savefig('schema.png', dpi=150, bbox_inches='tight',
            facecolor=fig.get_facecolor())
print('Saved schema.png')
