-- Frozen initial migration; new changes belong in later revisions.
CREATE TABLE entity (
    id VARCHAR(36) NOT NULL,
    workspace VARCHAR(16) NOT NULL,
    kind VARCHAR(40) NOT NULL,
    external_key VARCHAR(500),
    data JSONB NOT NULL,
    version INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT entity_scope_id UNIQUE (workspace, id),
    CONSTRAINT entity_natural_key UNIQUE (workspace, kind, external_key)
);

CREATE INDEX entity_list ON entity (workspace, kind, created_at);

CREATE UNIQUE INDEX single_confirmed_match ON entity (workspace, (CAST(data ->> 'listing_id' AS VARCHAR))) WHERE kind = 'matches' AND CAST((data ->> 'state') AS VARCHAR) = 'MATCHED';

CREATE INDEX entity_data ON entity USING gin (data);

CREATE TABLE audit_event (
    id VARCHAR(36) NOT NULL,
    workspace VARCHAR(16) NOT NULL,
    entity_id VARCHAR(36) NOT NULL,
    action VARCHAR(80) NOT NULL,
    actor VARCHAR(100) NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE job (
    id VARCHAR(36) NOT NULL,
    workspace VARCHAR(16) NOT NULL,
    kind VARCHAR(40) NOT NULL,
    idem_key VARCHAR(160) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL,
    attempts INTEGER NOT NULL,
    progress INTEGER NOT NULL,
    lease_token VARCHAR(36),
    lease_until TIMESTAMP WITH TIME ZONE,
    run_after TIMESTAMP WITH TIME ZONE NOT NULL,
    result JSONB,
    error VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (idem_key)
);

CREATE INDEX job_claim ON job (status, run_after);

CREATE TABLE login_session (
    token_hash VARCHAR(64) NOT NULL,
    csrf VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked BOOLEAN NOT NULL,
    PRIMARY KEY (token_hash)
);

CREATE TABLE entity_link (
    workspace VARCHAR(16) NOT NULL,
    from_id VARCHAR(36) NOT NULL,
    field VARCHAR(80) NOT NULL,
    to_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (workspace, from_id, field, to_id),
    FOREIGN KEY(workspace, from_id) REFERENCES entity (workspace, id),
    FOREIGN KEY(workspace, to_id) REFERENCES entity (workspace, id)
);

CREATE INDEX link_target ON entity_link (workspace, to_id, field);

CREATE TABLE entity_revision (
    id VARCHAR(36) NOT NULL,
    workspace VARCHAR(16) NOT NULL,
    entity_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    data JSONB NOT NULL,
    actor VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(workspace, entity_id) REFERENCES entity (workspace, id),
    CONSTRAINT revision_version UNIQUE (entity_id, version)
);

CREATE TABLE import_batch (
    id VARCHAR(36) NOT NULL,
    workspace VARCHAR(16) NOT NULL,
    source_id VARCHAR(36) NOT NULL,
    kind VARCHAR(40) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    path VARCHAR(500) NOT NULL,
    options JSONB NOT NULL,
    status VARCHAR(20) NOT NULL,
    mapping JSONB NOT NULL,
    import_key VARCHAR(64),
    accepted INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    unchanged INTEGER NOT NULL,
    errors_path VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(workspace, source_id) REFERENCES entity (workspace, id),
    UNIQUE (import_key)
);
