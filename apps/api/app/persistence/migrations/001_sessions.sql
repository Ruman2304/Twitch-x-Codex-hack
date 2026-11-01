CREATE TABLE workspaces (
    key TEXT PRIMARY KEY,
    demo_mode BOOLEAN NOT NULL,
    active_session_id UUID
);
CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    workspace_key TEXT NOT NULL REFERENCES workspaces(key),
    creator_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'live', 'paused', 'ended')),
    version BIGINT NOT NULL CHECK (version >= 1),
    payload JSONB NOT NULL,
    active_round_id UUID,
    active_attempt_id UUID,
    UNIQUE (workspace_key, id)
);
ALTER TABLE workspaces ADD CONSTRAINT workspace_active_session_fk
    FOREIGN KEY (key, active_session_id) REFERENCES sessions(workspace_key, id);
CREATE TABLE challenges (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id),
    position INTEGER NOT NULL CHECK (position >= 0),
    approval_status TEXT NOT NULL CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    payload JSONB NOT NULL,
    UNIQUE (session_id, id),
    UNIQUE (session_id, position)
);
CREATE TABLE rounds (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id),
    state TEXT NOT NULL CHECK (state IN ('voting', 'resolved', 'cancelled')),
    closes_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    UNIQUE (session_id, id)
);
CREATE UNIQUE INDEX one_voting_round_per_session ON rounds(session_id) WHERE state = 'voting';
CREATE TABLE round_options (
    session_id UUID NOT NULL,
    round_id UUID NOT NULL,
    option_number SMALLINT NOT NULL CHECK (option_number BETWEEN 1 AND 3),
    challenge_id UUID NOT NULL,
    PRIMARY KEY (round_id, option_number),
    FOREIGN KEY (session_id, round_id) REFERENCES rounds(session_id, id),
    FOREIGN KEY (session_id, challenge_id) REFERENCES challenges(session_id, id)
);
CREATE TABLE votes (
    round_id UUID NOT NULL,
    twitch_user_id TEXT NOT NULL,
    option_number SMALLINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (round_id, twitch_user_id),
    FOREIGN KEY (round_id, option_number) REFERENCES round_options(round_id, option_number)
);
CREATE TABLE attempts (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    round_id UUID NOT NULL UNIQUE,
    challenge_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'completed', 'skipped', 'expired')),
    ends_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    UNIQUE (session_id, id),
    FOREIGN KEY (session_id, round_id) REFERENCES rounds(session_id, id),
    FOREIGN KEY (session_id, challenge_id) REFERENCES challenges(session_id, id)
);
CREATE UNIQUE INDEX one_running_attempt_per_session ON attempts(session_id) WHERE status IN ('running', 'paused', 'expired');
ALTER TABLE sessions ADD CONSTRAINT session_round_fk
    FOREIGN KEY (id, active_round_id) REFERENCES rounds(session_id, id);
ALTER TABLE sessions ADD CONSTRAINT session_attempt_fk
    FOREIGN KEY (id, active_attempt_id) REFERENCES attempts(session_id, id);
CREATE TABLE score_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id),
    round_id UUID NOT NULL,
    twitch_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    FOREIGN KEY (session_id, round_id) REFERENCES rounds(session_id, id)
);
CREATE TABLE activity_events (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id),
    occurred_at TIMESTAMPTZ NOT NULL,
    sequence BIGINT NOT NULL,
    payload JSONB NOT NULL
);
CREATE UNIQUE INDEX activity_session_sequence ON activity_events(session_id, sequence);
CREATE INDEX activity_session_time ON activity_events(session_id, occurred_at DESC, id);
CREATE INDEX score_events_session ON score_events(session_id);
CREATE INDEX sessions_owner ON sessions(creator_id);
