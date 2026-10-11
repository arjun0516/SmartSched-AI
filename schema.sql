-- =========================================================
-- SmartSched AI - DBMS Setup
-- Run in MySQL Workbench or from VS Code using MySQL.
-- WARNING: This resets the database and deletes existing data.
-- =========================================================

DROP DATABASE IF EXISTS smartsched_ai;

CREATE DATABASE smartsched_ai
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE smartsched_ai;

-- =========================================================
-- 1. PROCESSES TABLE
-- =========================================================

CREATE TABLE processes (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    pid BIGINT NOT NULL UNIQUE,
    arrival_ms DECIMAL(12,2) NOT NULL,
    ram_mb DECIMAL(10,2) NOT NULL,
    priority TINYINT NOT NULL,
    max_allowed_burst_ms DECIMAL(12,2) NOT NULL,
    task_deadline_ms DECIMAL(12,2) NOT NULL,
    io_ratio_pct DECIMAL(5,2) NOT NULL,
    max_wait_ms DECIMAL(12,2) NOT NULL,
    disk_quota_mb DECIMAL(12,2) NOT NULL,
    disk_usage_mb DECIMAL(12,2) NOT NULL,
    predicted_burst_ms DECIMAL(12,2) NOT NULL,
    actual_burst_ms DECIMAL(12,2),
    waiting_time_ms DECIMAL(12,2),
    assigned_core TINYINT,
    mlfq_queue TINYINT,
    status VARCHAR(40) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_ram
        CHECK (ram_mb > 0 AND ram_mb <= 2048),
    CONSTRAINT chk_priority
        CHECK (priority BETWEEN 1 AND 5),
    CONSTRAINT chk_burst
        CHECK (max_allowed_burst_ms > 0),
    CONSTRAINT chk_deadline
        CHECK (task_deadline_ms > 0),
    CONSTRAINT chk_io
        CHECK (io_ratio_pct BETWEEN 0 AND 100),
    CONSTRAINT chk_wait
        CHECK (max_wait_ms > 0),
    CONSTRAINT chk_disk
        CHECK (disk_quota_mb > 0 AND disk_usage_mb >= 0)
) ENGINE=InnoDB;

-- =========================================================
-- 2. MACHINE LEARNING PREDICTIONS TABLE
-- =========================================================

CREATE TABLE ml_burst_predictions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    pid BIGINT NOT NULL,
    predicted_burst_ms DECIMAL(12,2) NOT NULL,
    actual_burst_ms DECIMAL(12,2),
    model_name VARCHAR(80) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_prediction_pid (pid)
) ENGINE=InnoDB;

-- =========================================================
-- 3. TRANSACTION LOGS TABLE
-- =========================================================

CREATE TABLE process_transaction_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    pid BIGINT NOT NULL,
    outcome ENUM('COMMIT','ROLLBACK') NOT NULL,
    error_code VARCHAR(64),
    message VARCHAR(255) NOT NULL,
    request_json JSON,
    result_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_log_pid (pid)
) ENGINE=InnoDB;

-- =========================================================
-- 4. SAVEPOINT / PROCESS AUDIT TABLE
-- =========================================================

CREATE TABLE system_savepoints (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    pid BIGINT NOT NULL,
    savepoint_name VARCHAR(80) NOT NULL,
    detail JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- =========================================================
-- 5. SYSTEM TELEMETRY AUDIT TABLE
-- =========================================================

CREATE TABLE system_telemetry_audit (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_type VARCHAR(80) NOT NULL,
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- =========================================================
-- 6. STORED PROCEDURE: ACID + ML TRANSACTION
-- =========================================================

DELIMITER $$

CREATE PROCEDURE sp_execute_acid_ml_transaction(
    IN p_pid BIGINT,
    IN p_arrival DECIMAL(12,2),
    IN p_ram DECIMAL(10,2),
    IN p_priority TINYINT,
    IN p_max_burst DECIMAL(12,2),
    IN p_deadline DECIMAL(12,2),
    IN p_io DECIMAL(5,2),
    IN p_max_wait DECIMAL(12,2),
    IN p_disk_quota DECIMAL(12,2),
    IN p_disk_usage DECIMAL(12,2),
    IN p_predicted DECIMAL(12,2),
    IN p_actual DECIMAL(12,2),
    IN p_waiting DECIMAL(12,2),
    IN p_core TINYINT,
    IN p_queue TINYINT,
    IN p_model VARCHAR(80),
    IN p_status VARCHAR(40),
    IN p_error VARCHAR(64),
    IN p_message VARCHAR(255),
    IN p_request JSON,
    IN p_result JSON
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    IF p_error IS NULL OR p_error = '' THEN

        INSERT INTO processes (
            pid, arrival_ms, ram_mb, priority,
            max_allowed_burst_ms, task_deadline_ms,
            io_ratio_pct, max_wait_ms,
            disk_quota_mb, disk_usage_mb,
            predicted_burst_ms, actual_burst_ms,
            waiting_time_ms, assigned_core, mlfq_queue, status
        )
        VALUES (
            p_pid, p_arrival, p_ram, p_priority,
            p_max_burst, p_deadline, p_io, p_max_wait,
            p_disk_quota, p_disk_usage,
            p_predicted, p_actual, p_waiting,
            p_core, p_queue, p_status
        );

        INSERT INTO ml_burst_predictions (
            pid, predicted_burst_ms, actual_burst_ms, model_name
        )
        VALUES (
            p_pid, p_predicted, p_actual, p_model
        );

        INSERT INTO system_savepoints (
            pid, savepoint_name, detail
        )
        VALUES (
            p_pid, 'PROCESS_COMMIT', p_result
        );

        INSERT INTO process_transaction_logs (
            pid, outcome, error_code, message,
            request_json, result_json
        )
        VALUES (
            p_pid, 'COMMIT', NULL, p_message,
            p_request, p_result
        );

    ELSE

        INSERT INTO process_transaction_logs (
            pid, outcome, error_code, message,
            request_json, result_json
        )
        VALUES (
            p_pid, 'ROLLBACK', p_error, p_message,
            p_request, p_result
        );

        INSERT INTO system_telemetry_audit (
            event_type, details
        )
        VALUES (
            'ROLLBACK',
            JSON_OBJECT(
                'pid', p_pid,
                'error_code', p_error,
                'message', p_message
            )
        );

    END IF;

    COMMIT;
END$$

DELIMITER ;

-- =========================================================
-- 7. VIEW: CORE UTILIZATION
-- =========================================================

CREATE VIEW view_core_utilization AS
SELECT
    assigned_core,
    COUNT(*) AS process_count,
    SUM(COALESCE(actual_burst_ms, predicted_burst_ms))
        AS total_burst_ms
FROM processes
WHERE assigned_core IS NOT NULL
GROUP BY assigned_core;

-- =========================================================
-- 8. VIEW: ML PREDICTION ACCURACY
-- =========================================================

CREATE VIEW view_ml_prediction_accuracy AS
SELECT
    model_name,
    COUNT(*) AS prediction_count,
    AVG(ABS(predicted_burst_ms - actual_burst_ms))
        AS mean_absolute_error_ms
FROM ml_burst_predictions
WHERE actual_burst_ms IS NOT NULL
GROUP BY model_name;

-- =========================================================
-- 9. VIEW: RECENT TRANSACTIONS
-- =========================================================

CREATE VIEW view_recent_transactions AS
SELECT
    id,
    pid,
    outcome,
    error_code,
    message,
    created_at
FROM process_transaction_logs;

-- =========================================================
-- 10. VIEW: SYSTEM PERFORMANCE SUMMARY
-- =========================================================

CREATE VIEW view_system_performance_summary AS
SELECT
    COUNT(*) AS total_processes,
    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END)
        AS completed_processes,
    AVG(waiting_time_ms) AS average_waiting_time_ms,
    AVG(predicted_burst_ms) AS average_predicted_burst_ms
FROM processes;

-- =========================================================
-- 11. TEST THE STORED PROCEDURE
-- =========================================================

CALL sp_execute_acid_ml_transaction(
    1001,                         -- pid
    0.00,                         -- arrival
    512.00,                       -- RAM
    2,                            -- priority
    1000.00,                      -- max burst
    5000.00,                      -- deadline
    20.00,                        -- I/O ratio
    2000.00,                      -- max wait
    1000.00,                      -- disk quota
    100.00,                       -- disk usage
    800.00,                       -- predicted burst
    750.00,                       -- actual burst
    50.00,                        -- waiting time
    1,                            -- assigned core
    1,                            -- MLFQ queue
    'test_model',                 -- model name
    'COMPLETED',                  -- status
    NULL,                         -- no error
    'Test process committed',
    JSON_OBJECT('source', 'sql_test'),
    JSON_OBJECT('result', 'success')
);

-- =========================================================
-- 12. VERIFY THE DATABASE
-- =========================================================

SHOW TABLES;

DESCRIBE processes;

SELECT * FROM processes;
SELECT * FROM ml_burst_predictions;
SELECT * FROM process_transaction_logs;
SELECT * FROM system_savepoints;
SELECT * FROM system_telemetry_audit;

SELECT * FROM view_core_utilization;
SELECT * FROM view_ml_prediction_accuracy;
SELECT * FROM view_recent_transactions
ORDER BY created_at DESC;
SELECT * FROM view_system_performance_summary;