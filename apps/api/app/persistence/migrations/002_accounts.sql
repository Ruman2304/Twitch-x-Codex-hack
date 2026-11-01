CREATE TABLE auth_namespaces (
    key TEXT PRIMARY KEY
);
CREATE TABLE twitch_users (
    namespace TEXT NOT NULL REFERENCES auth_namespaces(key),
    twitch_user_id TEXT NOT NULL,
    profile JSONB NOT NULL,
    PRIMARY KEY (namespace, twitch_user_id)
);
CREATE TABLE oauth_connections (
    namespace TEXT NOT NULL,
    twitch_user_id TEXT NOT NULL,
    access_cipher TEXT,
    refresh_cipher TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    validated_at TIMESTAMPTZ,
    validation_process TEXT,
    retry_after TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('connected','reconnect_required','disconnected')),
    PRIMARY KEY (namespace, twitch_user_id),
    FOREIGN KEY (namespace, twitch_user_id) REFERENCES twitch_users(namespace, twitch_user_id)
);
CREATE TABLE browser_sessions (
    namespace TEXT NOT NULL,
    session_hash CHAR(64) NOT NULL,
    twitch_user_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, session_hash),
    FOREIGN KEY (namespace, twitch_user_id) REFERENCES twitch_users(namespace, twitch_user_id)
);
CREATE INDEX browser_session_expiry ON browser_sessions(expires_at);
CREATE TABLE oauth_states (
    namespace TEXT NOT NULL REFERENCES auth_namespaces(key),
    state_hash CHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, state_hash)
);
CREATE TABLE token_revocations (
    namespace TEXT NOT NULL REFERENCES auth_namespaces(key),
    id UUID NOT NULL,
    twitch_user_id TEXT NOT NULL,
    token_cipher TEXT NOT NULL,
    next_attempt TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, id)
);
