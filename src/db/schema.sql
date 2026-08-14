-- Healthcare call center PoC schema (SQLite)

PRAGMA foreign_keys = ON;

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_datetime TEXT NOT NULL DEFAULT (datetime('now')),
    process_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    value_before TEXT,
    value_after TEXT,
    operator_name TEXT
);

-- Hospital information
CREATE TABLE IF NOT EXISTS hospital (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    region TEXT,
    rating REAL DEFAULT 0.0,
    address TEXT,
    internal_number TEXT
);

-- Doctor information
CREATE TABLE IF NOT EXISTS doctor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    gender TEXT,
    age INTEGER,
    expertise TEXT NOT NULL,
    hospital_id INTEGER,
    hospital_name TEXT,
    region TEXT,
    language TEXT,
    internal_number TEXT,
    rating REAL DEFAULT 0.0,
    score REAL DEFAULT 0.0,
    personality TEXT,
    available_slots TEXT,  -- JSON array of ISO datetime strings
    register_date TEXT,
    leave_date TEXT,
    FOREIGN KEY (hospital_id) REFERENCES hospital(id)
);

-- Keywords and alias list
CREATE TABLE IF NOT EXISTS keywords_alias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE(keyword, alias)
);

-- Operator information
CREATE TABLE IF NOT EXISTS operator (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    gender TEXT,
    role TEXT NOT NULL DEFAULT 'operator',
    employee_id TEXT UNIQUE,
    phone_number TEXT,
    join_date TEXT,
    leave_date TEXT
);

-- Contact log
CREATE TABLE IF NOT EXISTS contact_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL DEFAULT (datetime('now')),
    end_time TEXT,
    phone_number TEXT,
    keywords TEXT,
    doctor_name TEXT,
    is_accurate INTEGER,
    csat REAL,
    token_cost INTEGER DEFAULT 0
);

-- Indexes for search performance
CREATE INDEX IF NOT EXISTS idx_doctor_expertise ON doctor(expertise);
CREATE INDEX IF NOT EXISTS idx_doctor_region ON doctor(region);
CREATE INDEX IF NOT EXISTS idx_doctor_gender ON doctor(gender);
CREATE INDEX IF NOT EXISTS idx_doctor_name ON doctor(name);
CREATE INDEX IF NOT EXISTS idx_doctor_hospital ON doctor(hospital_name);
CREATE INDEX IF NOT EXISTS idx_doctor_leave ON doctor(leave_date);
CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON keywords_alias(keyword);
CREATE INDEX IF NOT EXISTS idx_keywords_alias ON keywords_alias(alias);
CREATE INDEX IF NOT EXISTS idx_hospital_region ON hospital(region);
CREATE INDEX IF NOT EXISTS idx_hospital_name ON hospital(name);
CREATE INDEX IF NOT EXISTS idx_audit_datetime ON audit_log(audit_datetime);
CREATE INDEX IF NOT EXISTS idx_contact_start ON contact_log(start_time);

-- Full-text search (SQLite FTS5) for semantic-ish keyword matching
CREATE VIRTUAL TABLE IF NOT EXISTS doctor_fts USING fts5(
    name,
    expertise,
    region,
    personality,
    hospital_name,
    language,
    content='doctor',
    content_rowid='id'
);

-- Keep FTS in sync
CREATE TRIGGER IF NOT EXISTS doctor_ai AFTER INSERT ON doctor BEGIN
    INSERT INTO doctor_fts(rowid, name, expertise, region, personality, hospital_name, language)
    VALUES (new.id, new.name, new.expertise, new.region, new.personality, new.hospital_name, new.language);
END;

CREATE TRIGGER IF NOT EXISTS doctor_ad AFTER DELETE ON doctor BEGIN
    INSERT INTO doctor_fts(doctor_fts, rowid, name, expertise, region, personality, hospital_name, language)
    VALUES ('delete', old.id, old.name, old.expertise, old.region, old.personality, old.hospital_name, old.language);
END;

CREATE TRIGGER IF NOT EXISTS doctor_au AFTER UPDATE ON doctor BEGIN
    INSERT INTO doctor_fts(doctor_fts, rowid, name, expertise, region, personality, hospital_name, language)
    VALUES ('delete', old.id, old.name, old.expertise, old.region, old.personality, old.hospital_name, old.language);
    INSERT INTO doctor_fts(rowid, name, expertise, region, personality, hospital_name, language)
    VALUES (new.id, new.name, new.expertise, new.region, new.personality, new.hospital_name, new.language);
END;
