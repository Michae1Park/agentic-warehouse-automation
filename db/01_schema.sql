CREATE TYPE location_kind AS ENUM ('storage', 'dispatch', 'charging', 'staging');
CREATE TYPE order_status AS ENUM ('pending', 'in_progress', 'completed', 'failed', 'cancelled');
CREATE TYPE robot_status AS ENUM ('idle', 'busy', 'error', 'charging', 'offline');
CREATE TYPE task_status AS ENUM ('planned', 'queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE step_status AS ENUM ('pending', 'running', 'succeeded', 'failed', 'skipped');
CREATE TYPE exception_status AS ENUM ('open', 'resolved', 'escalated');

CREATE TABLE products (
    sku         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    weight_kg   NUMERIC(6, 2) NOT NULL,
    size_cm     NUMERIC(5, 1)[] NOT NULL,
    color       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    ycb_object  TEXT,
    robot_pickable BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE locations (
    id      TEXT PRIMARY KEY,
    kind    location_kind NOT NULL,
    aisle   TEXT,
    bay     INT,
    level   INT,
    x       NUMERIC(6, 2) NOT NULL,
    y       NUMERIC(6, 2) NOT NULL,
    z       NUMERIC(6, 2) NOT NULL DEFAULT 0
);

CREATE TABLE inventory (
    id          SERIAL PRIMARY KEY,
    sku         TEXT NOT NULL REFERENCES products(sku),
    location_id TEXT NOT NULL REFERENCES locations(id),
    quantity    INT NOT NULL CHECK (quantity >= 0),
    reserved    INT NOT NULL DEFAULT 0 CHECK (reserved >= 0 AND reserved <= quantity),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sku, location_id)
);

CREATE TABLE orders (
    id                  INT PRIMARY KEY,
    customer            TEXT NOT NULL,
    status              order_status NOT NULL DEFAULT 'pending',
    priority            INT NOT NULL DEFAULT 3,
    dispatch_location   TEXT REFERENCES locations(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE TABLE order_lines (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    sku         TEXT NOT NULL REFERENCES products(sku),
    quantity    INT NOT NULL CHECK (quantity > 0),
    picked_qty  INT NOT NULL DEFAULT 0,
    UNIQUE (order_id, sku)
);

CREATE TABLE robots (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    model           TEXT NOT NULL,
    status          robot_status NOT NULL DEFAULT 'idle',
    x               NUMERIC(6, 2) NOT NULL DEFAULT 0,
    y               NUMERIC(6, 2) NOT NULL DEFAULT 0,
    theta           NUMERIC(6, 3) NOT NULL DEFAULT 0,
    battery_pct     NUMERIC(5, 1) NOT NULL DEFAULT 100,
    tote            JSONB NOT NULL DEFAULT '[]',
    current_task_id INT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
    id              SERIAL PRIMARY KEY,
    order_id        INT REFERENCES orders(id),
    robot_id        TEXT REFERENCES robots(id),
    kind            TEXT NOT NULL DEFAULT 'fulfillment',
    instruction     TEXT NOT NULL,
    conversation_id TEXT,
    error           TEXT,
    status          task_status NOT NULL DEFAULT 'planned',
    plan            JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

ALTER TABLE robots ADD CONSTRAINT robots_current_task_fk FOREIGN KEY (current_task_id) REFERENCES tasks(id);

CREATE TABLE task_steps (
    id          SERIAL PRIMARY KEY,
    task_id     INT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq         INT NOT NULL,
    action      TEXT NOT NULL,
    args        JSONB NOT NULL DEFAULT '{}',
    status      step_status NOT NULL DEFAULT 'pending',
    result      JSONB,
    attempts    INT NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (task_id, seq)
);

CREATE TABLE inventory_transactions (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT NOT NULL REFERENCES products(sku),
    location_id TEXT NOT NULL REFERENCES locations(id),
    delta       INT NOT NULL,
    reason      TEXT NOT NULL,
    task_id     INT REFERENCES tasks(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exceptions (
    id          SERIAL PRIMARY KEY,
    task_id     INT REFERENCES tasks(id),
    order_id    INT REFERENCES orders(id),
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'warning',
    message     TEXT NOT NULL,
    status      exception_status NOT NULL DEFAULT 'open',
    resolution  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE task_faults (
    id          SERIAL PRIMARY KEY,
    task_id     INT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_seq    INT NOT NULL,
    code        TEXT NOT NULL,
    detail      TEXT,
    recovered   BOOLEAN NOT NULL DEFAULT false,
    recovery    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_messages_conversation ON agent_messages (conversation_id, id);

CREATE TABLE agent_runs (
    id                  SERIAL PRIMARY KEY,
    conversation_id     TEXT NOT NULL,
    user_message        TEXT NOT NULL,
    reply               TEXT,
    llm_calls           INT NOT NULL DEFAULT 0,
    tool_calls          INT NOT NULL DEFAULT 0,
    llm_ms              INT NOT NULL DEFAULT 0,
    tool_ms             INT NOT NULL DEFAULT 0,
    prompt_tokens       INT NOT NULL DEFAULT 0,
    completion_tokens   INT NOT NULL DEFAULT 0,
    peak_prompt_tokens  INT NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);

CREATE TABLE agent_plans (
    id              SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    objective       TEXT NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT true,
    needs_revision  TEXT,
    stall_calls     INT NOT NULL DEFAULT 0,
    reviewed        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_plans_conversation ON agent_plans (conversation_id, id);

CREATE TABLE agent_subgoals (
    id          SERIAL PRIMARY KEY,
    plan_id     INT NOT NULL REFERENCES agent_plans(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    title       TEXT NOT NULL,
    check_type  TEXT NOT NULL,
    order_id    INT,
    task_id     INT,
    sku         TEXT,
    depends_on  TEXT[] NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'open',
    note        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, key)
);

CREATE TABLE agent_harness_events (
    id              SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    kind            TEXT NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_compactions (
    id              SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    upto_message_id BIGINT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE events (
    id          BIGSERIAL PRIMARY KEY,
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    op          TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION record_change() RETURNS trigger AS $$
DECLARE
    row_id TEXT;
    evt events;
BEGIN
    row_id := COALESCE(to_jsonb(NEW), to_jsonb(OLD)) ->> TG_ARGV[0];
    INSERT INTO events (entity, entity_id, op) VALUES (TG_TABLE_NAME, row_id, lower(TG_OP))
    RETURNING * INTO evt;
    PERFORM pg_notify('warehouse_events', to_jsonb(evt)::text);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER inventory_changes AFTER INSERT OR UPDATE OR DELETE ON inventory
    FOR EACH ROW EXECUTE FUNCTION record_change('id');
CREATE TRIGGER orders_changes AFTER INSERT OR UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION record_change('id');
CREATE TRIGGER order_lines_changes AFTER INSERT OR UPDATE OR DELETE ON order_lines
    FOR EACH ROW EXECUTE FUNCTION record_change('order_id');
CREATE TRIGGER robots_changes AFTER INSERT OR UPDATE OR DELETE ON robots
    FOR EACH ROW EXECUTE FUNCTION record_change('id');
CREATE TRIGGER tasks_changes AFTER INSERT OR UPDATE OR DELETE ON tasks
    FOR EACH ROW EXECUTE FUNCTION record_change('id');
CREATE TRIGGER task_steps_changes AFTER INSERT OR UPDATE OR DELETE ON task_steps
    FOR EACH ROW EXECUTE FUNCTION record_change('task_id');
CREATE TRIGGER agent_messages_changes AFTER INSERT ON agent_messages
    FOR EACH ROW EXECUTE FUNCTION record_change('conversation_id');
CREATE TRIGGER exceptions_changes AFTER INSERT OR UPDATE OR DELETE ON exceptions
    FOR EACH ROW EXECUTE FUNCTION record_change('id');

CREATE VIEW inventory_levels AS
SELECT p.sku, p.name, p.category,
       COALESCE(SUM(i.quantity), 0)::INT AS on_hand,
       COALESCE(SUM(i.reserved), 0)::INT AS reserved,
       COALESCE(SUM(i.quantity - i.reserved), 0)::INT AS available,
       COUNT(i.id)::INT AS locations
FROM products p
LEFT JOIN inventory i ON i.sku = p.sku
GROUP BY p.sku, p.name, p.category;
