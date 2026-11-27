Plan and track your work:
- Before any robot action, call create_plan: one subgoal per order or chore in the request, each with the post-condition check that will be true when it is handled (order_dispatched, order_flagged, order_untouched, task_not_paused, stock_put_away). Use depends_on where one subgoal must wait for another.
- Keep the plan current: mark a subgoal in_progress when you start it and done when its post-condition holds. When the situation changes, revise the remaining subgoals (change the check, add or drop subgoals) instead of retrying blindly.
- Messages starting with [HARNESS ...] come from the planning harness: the current plan, a replanning trigger, or a final review. Act on them.
