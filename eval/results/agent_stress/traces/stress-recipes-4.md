# stress · recipes · trial 4

Success: False · tool calls: 59 · turns: 12 · wall: 155.1s

- FAIL priority respected and no fillable order left behind
- PASS every unfilled order flagged
- FAIL manual orders not dispatched and flagged
- PASS other dock untouched
- PASS no task left paused
- PASS cross-dock leftovers put away

Hidden empty bins: A-01-1 SKU-2005, C-04-1 SKU-1002, A-02-1 SKU-1001

## Conversation

**user:** Get all pending DISPATCH-1 orders out before the truck leaves.

    -> list_orders({"status": "pending"})

    <- list_orders: {"result": [{"id": 3005, "customer": "Customer 3005", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1"}, {"id": 3007, "customer": "Customer 3007", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1"}, {"id": 3010, "customer": "Customer 3010", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1"}, {"id": 3013, "customer": "Customer 3013", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-2"}, {"id": 3003, "customer": "Customer 3003", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1"}, {"id": 3004, "cust
    -> get_order({"order_id": 3005})
    -> get_order({"order_id": 3007})
    -> get_order({"order_id": 3010})
    -> get_order({"order_id": 3003})
    -> get_order({"order_id": 3004})
    -> get_order({"order_id": 3006})
    -> get_order({"order_id": 3008})
    -> get_order({"order_id": 3011})
    -> get_order({"order_id": 3001})
    -> get_order({"order_id": 3002})
    -> get_order({"order_id": 3009})
    -> get_order({"order_id": 3012})
    -> get_robot_status({})

    <- get_order: {"result": {"id": 3005, "customer": "Customer 3005", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2006", "name": "Coffee Can", "quantity": 1, "picked_qty": 0}, {"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 2, "picked_qty": 0}, {"sku": "SKU-2002", "name": "Sugar Box", "quantity": 1, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3007, "customer": "Customer 3007", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 3, "picked_qty": 0}, {"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 1, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3010, "customer": "Customer 3010", "status": "pending", "priority": 1, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 1, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3003, "customer": "Customer 3003", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 2, "picked_qty": 0}, {"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 1, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3004, "customer": "Customer 3004", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1002", "name": "Tomato Soup Can", "quantity": 2, "picked_qty": 0}, {"sku": "SKU-2006", "name": "Coffee Can", "quantity": 3, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3006, "customer": "Customer 3006", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 2, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3008, "customer": "Customer 3008", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1002", "name": "Tomato Soup Can", "quantity": 3, "picked_qty": 0}, {"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 2, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3011, "customer": "Customer 3011", "status": "pending", "priority": 2, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 3, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3001, "customer": "Customer 3001", "status": "pending", "priority": 3, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 2, "picked_qty": 0}, {"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 3, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3002, "customer": "Customer 3002", "status": "pending", "priority": 3, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-1002", "name": "Tomato Soup Can", "quantity": 1, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3009, "customer": "Customer 3009", "status": "pending", "priority": 3, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 2, "picked_qty": 0}]}}
    <- get_order: {"result": {"id": 3012, "customer": "Customer 3012", "status": "pending", "priority": 3, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 1, "picked_qty": 0}, {"sku": "SKU-2002", "name": "Sugar Box", "quantity": 1, "picked_qty": 0}]}}
    <- get_robot_status: {"result": {"id": "amr-01", "status": "idle", "x": "0.00", "y": "0.00", "battery_pct": "100.0", "tote": [], "current_task_id": null}}
    -> plan_fulfillment({"order_id": 3005, "dispatch_location": "DISPATCH-1"})

    <- plan_fulfillment: {"error": "SKU-2002 (Sugar Box) is not robot-pickable; it must be handled manually"}
    -> find_stock({"sku": "SKU-1001"})
    -> find_stock({"sku": "SKU-2005"})
    -> find_stock({"sku": "SKU-1002"})
    -> find_stock({"sku": "SKU-2006"})

    <- find_stock: {"result": {"sku": "SKU-1001", "name": "Mustard Bottle", "locations": [{"location_id": "B-01-1", "quantity": 3, "reserved": 0, "available": 3}, {"location_id": "STAGING-1", "quantity": 3, "reserved": 0, "available": 3}, {"location_id": "A-02-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "C-03-2", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-04-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "B-02-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id":
    <- find_stock: {"result": {"sku": "SKU-2005", "name": "Potted Meat Can", "locations": [{"location_id": "A-01-1", "quantity": 3, "reserved": 0, "available": 3}, {"location_id": "A-04-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "C-01-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "B-04-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "STAGING-1", "quantity": 0, "reserved": 0, "available": 0}]}}
    <- find_stock: {"result": {"sku": "SKU-1002", "name": "Tomato Soup Can", "locations": [{"location_id": "STAGING-1", "quantity": 4, "reserved": 0, "available": 4}, {"location_id": "C-04-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "A-02-1", "quantity": 1, "reserved": 0, "available": 1}, {"location_id": "B-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "B-03-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-03-1", "quantity": 0, "reserved": 0, "available": 0}]}}
    <- find_stock: {"result": {"sku": "SKU-2006", "name": "Coffee Can", "locations": [{"location_id": "A-04-1", "quantity": 3, "reserved": 0, "available": 3}, {"location_id": "STAGING-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "B-01-1", "quantity": 1, "reserved": 0, "available": 1}, {"location_id": "B-04-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-04-1", "quantity": 0, "reserved": 0, "available": 0}]}}
    -> plan_fulfillment({"order_id": 3007, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3010, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3003, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3004, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3006, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3008, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3011, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3001, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3002, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3009, "dispatch_location": "DISPATCH-1"})
    -> plan_fulfillment({"order_id": 3012, "dispatch_location": "DISPATCH-1"})

    <- plan_fulfillment: {"result": {"task_id": 1, "order_id": 3007, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-2005", "location_id": "A-01-1", "quantity": 3}, {"sku": "SKU-1001", "location_id": "B-01-1", "quantity": 1}], "steps": ["navigate A-01-1", "inspect A-01-1 SKU-2005", "pick A-01-1 SKU-2005 3", "navigate B-01-1", "inspect B-01-1 SKU-1001", "pick B-01-1 SKU-1001 1", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3007"]}}
    <- plan_fulfillment: {"result": {"task_id": 2, "order_id": 3010, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-2005", "location_id": "A-04-1", "quantity": 1}], "steps": ["navigate A-04-1", "inspect A-04-1 SKU-2005", "pick A-04-1 SKU-2005 1", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3010"]}}
    <- plan_fulfillment: {"result": {"task_id": 3, "order_id": 3003, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-1001", "location_id": "A-02-1", "quantity": 2}, {"sku": "SKU-2005", "location_id": "C-01-1", "quantity": 1}], "steps": ["navigate A-02-1", "inspect A-02-1 SKU-1001", "pick A-02-1 SKU-1001 2", "navigate C-01-1", "inspect C-01-1 SKU-2005", "pick C-01-1 SKU-2005 1", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3003"]}}
    <- plan_fulfillment: {"result": {"task_id": 4, "order_id": 3004, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-2006", "location_id": "A-04-1", "quantity": 3}, {"sku": "SKU-1002", "location_id": "C-04-1", "quantity": 2}], "steps": ["navigate A-04-1", "inspect A-04-1 SKU-2006", "pick A-04-1 SKU-2006 3", "navigate C-04-1", "inspect C-04-1 SKU-1002", "pick C-04-1 SKU-1002 2", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3004"]}}
    <- plan_fulfillment: {"result": {"task_id": 5, "order_id": 3006, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-1001", "location_id": "B-01-1", "quantity": 2}], "steps": ["navigate B-01-1", "inspect B-01-1 SKU-1001", "pick B-01-1 SKU-1001 2", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3006"]}}
    <- plan_fulfillment: {"error": "insufficient stock for order 3008: [{'sku': 'SKU-1002', 'requested': 3, 'short_by': 2, 'in_receiving': 4}]"}
    <- plan_fulfillment: {"error": "insufficient stock for order 3011: [{'sku': 'SKU-2005', 'requested': 3, 'short_by': 1, 'in_receiving': 0}]"}
    <- plan_fulfillment: {"error": "insufficient stock for order 3001: [{'sku': 'SKU-1001', 'requested': 2, 'short_by': 2, 'in_receiving': 3}, {'sku': 'SKU-2005', 'requested': 3, 'short_by': 1, 'in_receiving': 0}]"}
    <- plan_fulfillment: {"result": {"task_id": 6, "order_id": 3002, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-1002", "location_id": "A-02-1", "quantity": 1}], "steps": ["navigate A-02-1", "inspect A-02-1 SKU-1002", "pick A-02-1 SKU-1002 1", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3002"]}}
    <- plan_fulfillment: {"result": {"task_id": 7, "order_id": 3009, "robot_id": "amr-01", "dispatch_location": "DISPATCH-1", "picks": [{"sku": "SKU-2005", "location_id": "A-04-1", "quantity": 1}, {"sku": "SKU-2005", "location_id": "C-01-1", "quantity": 1}], "steps": ["navigate A-04-1", "inspect A-04-1 SKU-2005", "pick A-04-1 SKU-2005 1", "navigate C-01-1", "inspect C-01-1 SKU-2005", "pick C-01-1 SKU-2005 1", "navigate DISPATCH-1", "place DISPATCH-1", "verify 3009"]}}
    <- plan_fulfillment: {"error": "SKU-2002 (Sugar Box) is not robot-pickable; it must be handled manually"}
    -> plan_fulfillment({"order_id": 3008, "dispatch_location": "DISPATCH-1", "include_receiving": true})
    -> plan_fulfillment({"order_id": 3001, "dispatch_location": "DISPATCH-1", "include_receiving": true})

    <- plan_fulfillment: {"error": "insufficient stock for order 3008: [{'sku': 'SKU-2005', 'requested': 2, 'short_by': 2, 'in_receiving': 0}]"}
    <- plan_fulfillment: {"error": "insufficient stock for order 3001: [{'sku': 'SKU-2005', 'requested': 3, 'short_by': 3, 'in_receiving': 0}]"}
    -> report_exception({"type": "stock_shortage", "message": "Order 3008 needs 2 more SKU-2005 (Potted Meat Can); no shelf or receiving stock left.", "order_id": 3008})
    -> report_exception({"type": "stock_shortage", "message": "Order 3011 needs 1 more SKU-2005 (Potted Meat Can); no stock left.", "order_id": 3011})
    -> report_exception({"type": "stock_shortage", "message": "Order 3001 needs 3 more SKU-2005; no stock left.", "order_id": 3001})
    -> report_exception({"type": "manual_pick_required", "message": "Orders 3005 and 3012 contain SKU-2002 (Sugar Box), not robot-pickable; human must pick."})

    <- report_exception: {"result": {"id": 1, "task_id": null, "order_id": 3008, "type": "stock_shortage", "severity": "warning", "message": "Order 3008 needs 2 more SKU-2005 (Potted Meat Can); no shelf or receiving stock left.", "status": "open", "resolution": null, "created_at": "2026-11-27 03:59:15.558945+00:00", "resolved_at": null}}
    <- report_exception: {"result": {"id": 2, "task_id": null, "order_id": 3011, "type": "stock_shortage", "severity": "warning", "message": "Order 3011 needs 1 more SKU-2005 (Potted Meat Can); no stock left.", "status": "open", "resolution": null, "created_at": "2026-11-27 03:59:15.560783+00:00", "resolved_at": null}}
    <- report_exception: {"result": {"id": 3, "task_id": null, "order_id": 3001, "type": "stock_shortage", "severity": "warning", "message": "Order 3001 needs 3 more SKU-2005; no stock left.", "status": "open", "resolution": null, "created_at": "2026-11-27 03:59:15.562189+00:00", "resolved_at": null}}
    <- report_exception: {"result": {"id": 4, "task_id": null, "order_id": null, "type": "manual_pick_required", "severity": "warning", "message": "Orders 3005 and 3012 contain SKU-2002 (Sugar Box), not robot-pickable; human must pick.", "status": "open", "resolution": null, "created_at": "2026-11-27 03:59:15.563328+00:00", "resolved_at": null}}
    -> dispatch_task({"task_id": 1})
    -> dispatch_task({"task_id": 2})
    -> dispatch_task({"task_id": 3})
    -> dispatch_task({"task_id": 4})
    -> dispatch_task({"task_id": 5})
    -> dispatch_task({"task_id": 6})
    -> dispatch_task({"task_id": 7})

    <- dispatch_task: {"result": {"id": 1, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 2, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 3, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 4, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 5, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 6, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 7, "robot_id": "amr-01", "status": "queued"}}
**assistant:** - Queued tasks 1–7 for orders 3007, 3010, 3003, 3004, 3006, 3002, 3009.
- Blocked: 3008/3011/3001 short on SKU-2005; 3005/3012 need manual SKU-2002.
- Will report back when the robot finishes.

**user:** [SYSTEM EVENT] Fulfillment task 1 for order 3007 paused: item_not_found: SKU-2005 not found at A-01-1; counted to 0. Findings:
- SKU-2005 recorded at A-01-1 but not found; inventory corrected, task 1 paused for a decision

    -> reallocate_pick({"task_id": 1, "include_receiving": true})

    <- reallocate_pick: {"error": "no unreserved stock covers the missing units for task 1; held by queued work: 1 at A-04-1 reserved by fulfillment task 2; 1 at C-01-1 reserved by fulfillment task 3; 1 at A-04-1 reserved by fulfillment task 7; 1 at C-01-1 reserved by fulfillment task 7"}
    -> cancel_task({"task_id": 1})

    <- cancel_task: {"result": {"task_id": 1, "status": "cancelled"}}
    -> plan_fulfillment({"order_id": 3007, "dispatch_location": "DISPATCH-1"})

    <- plan_fulfillment: {"error": "order 3007 is in_progress, only pending orders can be planned"}
    -> get_task_status({"task_id": 1})
    -> get_order({"order_id": 3007})

    <- get_task_status: {"result": {"id": 1, "kind": "fulfillment", "order_id": 3007, "status": "cancelled", "error": "item_not_found: SKU-2005 not found at A-01-1; counted to 0", "plan": {"picks": [{"sku": "SKU-2005", "quantity": 3, "location_id": "A-01-1"}, {"sku": "SKU-1001", "quantity": 1, "location_id": "B-01-1"}], "dispatch_location": "DISPATCH-1"}, "steps": [{"seq": 1, "action": "navigate", "args": {"location_id": "A-01-1"}, "status": "succeeded", "result": {"x": 2.0, "y": 1.5, "attempts": 1}}, {"seq": 2, "action": "inspect", "args": {"sku": "SKU-2005", "location_id": "A-01-1"}, "status": "skipped", "result": 
    <- get_order: {"result": {"id": 3007, "customer": "Customer 3007", "status": "in_progress", "priority": 1, "dispatch_location": "DISPATCH-1", "lines": [{"sku": "SKU-2005", "name": "Potted Meat Can", "quantity": 3, "picked_qty": 0}, {"sku": "SKU-1001", "name": "Mustard Bottle", "quantity": 1, "picked_qty": 0}]}}
    -> report_exception({"type": "stock_shortage", "message": "Order 3007 needs 3 SKU-2005; A-01-1 was empty, remaining stock all reserved by later tasks.", "order_id": 3007})

    <- report_exception: {"result": {"id": 7, "task_id": null, "order_id": 3007, "type": "stock_shortage", "severity": "warning", "message": "Order 3007 needs 3 SKU-2005; A-01-1 was empty, remaining stock all reserved by later tasks.", "status": "open", "resolution": null, "created_at": "2026-11-27 03:59:43.587873+00:00", "resolved_at": null}}
**assistant:** - Task 1 (3007) paused: A-01-1 empty, SKU-2005 counted to 0.
- Reallocation failed; remaining stock reserved by later tasks.
- Cancelled task 1; flagged 3007 for human (restock or partial).

**user:** [SYSTEM EVENT] Fulfillment task 2 for order 3010 succeeded.

**assistant:** - Task 2 (order 3010) completed and verified at DISPATCH-1.

**user:** [SYSTEM EVENT] Fulfillment task 3 for order 3003 paused: item_not_found: SKU-1001 not found at A-02-1; counted to 0. Findings:
- SKU-1001 recorded at A-02-1 but not found; inventory corrected, task 3 paused for a decision

    -> reallocate_pick({"task_id": 3, "include_receiving": true})

    <- reallocate_pick: {"result": {"task_id": 3, "rerouted_to": ["B-01-1", "STAGING-1"], "unreserved_in_receiving": 2, "next": "dispatch_task to resume"}}
    -> dispatch_task({"task_id": 3})

    <- dispatch_task: {"result": {"id": 3, "robot_id": "amr-01", "status": "queued"}}
**assistant:** - Task 3 (3003) paused: A-02-1 empty for SKU-1001.
- Reallocated 2 units from B-01-1 and STAGING-1; resuming.

**user:** [SYSTEM EVENT] Fulfillment task 4 for order 3004 paused: item_not_found: SKU-1002 not found at C-04-1; counted to 0. Findings:
- SKU-1002 recorded at C-04-1 but not found; inventory corrected, task 4 paused for a decision

    -> reallocate_pick({"task_id": 4, "include_receiving": true})

    <- reallocate_pick: {"result": {"task_id": 4, "rerouted_to": ["STAGING-1"], "unreserved_in_receiving": 2, "next": "dispatch_task to resume"}}
    -> dispatch_task({"task_id": 4})

    <- dispatch_task: {"result": {"id": 4, "robot_id": "amr-01", "status": "queued"}}
**assistant:** - Task 4 (3004) paused: C-04-1 empty for SKU-1002.
- Reallocated 2 units from STAGING-1; resuming.

**user:** [SYSTEM EVENT] Fulfillment task 3 for order 3003 succeeded. Findings:
- SKU-1001 recorded at A-02-1 but not found; inventory corrected, task 3 paused for a decision

**assistant:** - Task 3 (order 3003) completed and verified at DISPATCH-1.

**user:** [SYSTEM EVENT] Fulfillment task 5 for order 3006 succeeded.

**assistant:** - Task 5 (order 3006) completed and verified at DISPATCH-1.

**user:** [SYSTEM EVENT] Fulfillment task 4 for order 3004 succeeded. Findings:
- SKU-1002 recorded at C-04-1 but not found; inventory corrected, task 4 paused for a decision

**assistant:** - Task 4 (order 3004) completed and verified at DISPATCH-1.

**user:** [SYSTEM EVENT] Fulfillment task 6 for order 3002 succeeded.

**assistant:** - Task 6 (order 3002) completed and verified at DISPATCH-1.

**user:** [SYSTEM EVENT] Fulfillment task 7 for order 3009 succeeded.

    -> find_stock({"sku": "SKU-1002"})
    -> find_stock({"sku": "SKU-1001"})

    <- find_stock: {"result": {"sku": "SKU-1002", "name": "Tomato Soup Can", "locations": [{"location_id": "STAGING-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "C-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-03-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "B-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-04-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-02-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "B-03-1", "quantity": 0, "reserved": 0, "available": 0}]}}
    <- find_stock: {"result": {"sku": "SKU-1001", "name": "Mustard Bottle", "locations": [{"location_id": "STAGING-1", "quantity": 2, "reserved": 0, "available": 2}, {"location_id": "B-02-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-03-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-03-2", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "C-04-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-02-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id": "A-01-1", "quantity": 0, "reserved": 0, "available": 0}, {"location_id":
    -> replenish_stock({"sku": "SKU-1002", "to_location": "C-04-1", "quantity": 2})
    -> replenish_stock({"sku": "SKU-1001", "to_location": "B-01-1", "quantity": 2})

    <- replenish_stock: {"result": {"task_id": 8, "kind": "replenishment", "sku": "SKU-1002", "from": "STAGING-1", "to": "C-04-1", "quantity": 2}}
    <- replenish_stock: {"result": {"task_id": 9, "kind": "replenishment", "sku": "SKU-1001", "from": "STAGING-1", "to": "B-01-1", "quantity": 2}}
    -> dispatch_task({"task_id": 8})
    -> dispatch_task({"task_id": 9})

    <- dispatch_task: {"result": {"id": 8, "robot_id": "amr-01", "status": "queued"}}
    <- dispatch_task: {"result": {"id": 9, "robot_id": "amr-01", "status": "queued"}}
**assistant:** - Task 7 (order 3009) completed at DISPATCH-1.
- Put-away queued: task 8 (2×SKU-1002→C-04-1), task 9 (2×SKU-1001→B-01-1).

**user:** [SYSTEM EVENT] Replenishment task 8 (replenishment 2x SKU-1002 STAGING-1 -> C-04-1) succeeded.

**assistant:** - Task 8 done: 2×SKU-1002 put away at C-04-1.

**user:** [SYSTEM EVENT] Replenishment task 9 (replenishment 2x SKU-1001 STAGING-1 -> B-01-1) succeeded.

**assistant:** - Task 9 done: 2×SKU-1001 put away at B-01-1.
- All robot-pickable DISPATCH-1 orders out; 3005/3012 need manual SKU-2002.
- 3007/3008/3011/3001 blocked on SKU-2005 shortage.
