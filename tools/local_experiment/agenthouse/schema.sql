CREATE TABLE IF NOT EXISTS agenthouse.pawprints
(
    event_id UUID,
    timestamp DateTime64(3, 'UTC'),
    agent_id String,
    task_id String,
    repo LowCardinality(String),
    issue Nullable(UInt64),
    pr Nullable(UInt64),
    action_type LowCardinality(String),
    target_system LowCardinality(String),
    risk_level LowCardinality(String),
    approval_tier LowCardinality(String),
    model_provider LowCardinality(String),
    model_name String,
    prompt_tokens Nullable(UInt64),
    completion_tokens Nullable(UInt64),
    inference_cost_estimate Nullable(Float64),
    cpu_seconds Nullable(Float64),
    memory_mb_seconds Nullable(Float64),
    status LowCardinality(String),
    blocked_by_policy Bool,
    human_override Bool,
    trace_id String,
    duration_ms UInt64
)
ENGINE = ReplacingMergeTree
ORDER BY event_id
