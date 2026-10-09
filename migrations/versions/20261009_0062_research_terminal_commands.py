"""Research terminal command requests and their outcome events (activation commands)

Revision ID: 20261009_0062
Revises: 20261009_0061
Create Date: 2026-10-09

* ``research_terminal_commands`` -- one immutable, identity-bound operator
  request per row (closed set of kinds, explicit inputs, idempotency key).
* ``research_terminal_command_events`` -- append-only outcome events
  (REQUESTED, BLOCKED, CLAIMED, RUNNING, SUCCEEDED, FAILED, STOPPED, EXITED).
  A command's state is its latest event; nothing is updated in place.

Every command is executed by an existing authority (Strategy Lab, the R6
registry, the watch list, account policies, the live/paper runners). No order,
broker or live-trading command exists. Additive; downgrade drops both tables.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20261009_0062"
down_revision = "20261009_0061"
branch_labels = None
depends_on = None

_KINDS = (
    "STRATEGY_SEARCH", "CANDIDATE_FREEZE", "DECIMAL_RERUN", "PREREGISTRATION_RECORD", "HOLDOUT_OPEN",
    "HOLDOUT_VALIDATE", "WATCHLIST_RECORD", "ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD", "RESEARCH_WATCH_START",
    "RESEARCH_WATCH_STOP", "PAPER_INCUBATION_START", "PAPER_INCUBATION_STOP",
)
_STATES = ("REQUESTED", "BLOCKED", "CLAIMED", "RUNNING", "SUCCEEDED", "FAILED", "STOPPED", "EXITED")


def upgrade() -> None:
    kinds = ",".join(f"'{k}'" for k in _KINDS)
    states = ",".join(f"'{s}'" for s in _STATES)
    op.execute(
        f"""CREATE TABLE research_terminal_commands (
        command_id UUID PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE CHECK(length(trim(idempotency_key)) BETWEEN 1 AND 200),
        kind TEXT NOT NULL CHECK(kind IN ({kinds})),
        inputs JSONB NOT NULL CHECK(jsonb_typeof(inputs)='object'),
        content_hash CHAR(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{{64}}$'),
        requested_by TEXT NOT NULL CHECK(length(trim(requested_by))>0),
        requested_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX research_terminal_commands_requested_idx ON research_terminal_commands(requested_at)")
    op.execute(immutable_trigger_sql("research_terminal_commands"))
    op.execute(
        f"""CREATE TABLE research_terminal_command_events (
        event_id UUID PRIMARY KEY,
        command_id UUID NOT NULL REFERENCES research_terminal_commands(command_id),
        state TEXT NOT NULL CHECK(state IN ({states})),
        detail JSONB NOT NULL CHECK(jsonb_typeof(detail)='object'),
        actor TEXT NOT NULL CHECK(length(trim(actor))>0),
        occurred_at TIMESTAMPTZ NOT NULL)"""
    )
    op.execute("CREATE INDEX research_terminal_command_events_idx "
               "ON research_terminal_command_events(command_id, occurred_at)")
    # At most one claim, one RUNNING and one terminal outcome per command: two workers can never
    # both run a command, and no command can end twice.
    op.execute("CREATE UNIQUE INDEX research_terminal_command_one_claim "
               "ON research_terminal_command_events(command_id) WHERE state='CLAIMED'")
    op.execute("CREATE UNIQUE INDEX research_terminal_command_one_running "
               "ON research_terminal_command_events(command_id) WHERE state='RUNNING'")
    op.execute("CREATE UNIQUE INDEX research_terminal_command_one_terminal "
               "ON research_terminal_command_events(command_id) "
               "WHERE state IN ('BLOCKED','SUCCEEDED','FAILED','STOPPED','EXITED')")
    op.execute(immutable_trigger_sql("research_terminal_command_events"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS research_terminal_command_events")
    op.execute("DROP TABLE IF EXISTS research_terminal_commands")
