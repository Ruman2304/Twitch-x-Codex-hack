CREATE TABLE event_inbox (
    workspace_key TEXT NOT NULL REFERENCES workspaces(key),
    provider_event_id TEXT NOT NULL,
    chat_message_id TEXT NOT NULL,
    session_id UUID NOT NULL REFERENCES sessions(id),
    received_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('counted','ignored')),
    PRIMARY KEY (workspace_key, provider_event_id),
    UNIQUE (workspace_key, chat_message_id)
);
CREATE INDEX event_inbox_received ON event_inbox(received_at);
CREATE TABLE eventsub_runtime (
    workspace_key TEXT PRIMARY KEY REFERENCES workspaces(key),
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
