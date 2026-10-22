-- ============================================================
-- 工业设备智能预警系统 · MySQL 8.0 数据库设计
-- 设计要点：冷热分离（热表只留7天，冷表按月分表）+ 联合索引 + 溯源视图
-- ============================================================
CREATE DATABASE IF NOT EXISTS industrial_anomaly
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE industrial_anomaly;

-- ------------------------------------------------------------
-- 1. 设备主表
-- ------------------------------------------------------------
CREATE TABLE machine_info (
    machine_id    INT PRIMARY KEY AUTO_INCREMENT,
    machine_name  VARCHAR(50)  NOT NULL,
    location      VARCHAR(100),
    model         VARCHAR(50)  COMMENT '设备型号',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    KEY idx_name (machine_name)
) ENGINE=InnoDB COMMENT='设备台账';

-- ------------------------------------------------------------
-- 2.【热数据表】只存最近 7 天，服务实时看板
--    联合索引 (machine_id, record_time) 覆盖"按设备+时间范围"的典型查询
--    写入极高频 -> 只建必要索引，避免写放大
-- ------------------------------------------------------------
CREATE TABLE sensor_data_hot (
    data_id        BIGINT PRIMARY KEY AUTO_INCREMENT,
    machine_id     INT      NOT NULL,
    record_time    DATETIME NOT NULL,
    rms_value      FLOAT    NOT NULL COMMENT '振动均方根',
    kurtosis_value FLOAT    COMMENT '振动峭度',
    temperature    FLOAT    COMMENT '温度(慢变量)',
    is_anomaly     TINYINT(1) DEFAULT 0,
    KEY idx_machine_time_hot (machine_id, record_time),
    KEY idx_time_hot (record_time),
    CONSTRAINT fk_hot_machine FOREIGN KEY (machine_id) REFERENCES machine_info(machine_id)
) ENGINE=InnoDB COMMENT='传感器热数据(近7天)';

-- ------------------------------------------------------------
-- 3.【告警日志表】溯源视图依赖它，字段与算法输出一一对应
-- ------------------------------------------------------------
CREATE TABLE anomaly_log (
    alarm_id      BIGINT PRIMARY KEY AUTO_INCREMENT,
    machine_id    INT      NOT NULL,
    alarm_time    DATETIME NOT NULL,
    anomaly_type  VARCHAR(30) NOT NULL COMMENT 'perceptron / residual / continuity',
    anomaly_value FLOAT       COMMENT '触发时的残差或特征值',
    threshold     FLOAT       COMMENT '触发时的动态阈值',
    knn_distance  FLOAT       COMMENT 'KNN 最近邻欧氏距离',
    description   VARCHAR(255) COMMENT 'KNN 溯源诊断建议',
    status        TINYINT DEFAULT 0 COMMENT '0未处理 1处理中 2已闭环',
    KEY idx_machine_alarm_time (machine_id, alarm_time),
    KEY idx_status (status),
    CONSTRAINT fk_alarm_machine FOREIGN KEY (machine_id) REFERENCES machine_info(machine_id)
) ENGINE=InnoDB COMMENT='告警与溯源记录';

-- ------------------------------------------------------------
-- 4.【冷数据表】按月分表，存历史数据用于离线训练/回溯
--    命名规范 sensor_data_cold_YYYYMM，由定时任务建表+迁移
-- ------------------------------------------------------------
CREATE TABLE sensor_data_cold_202310 (
    data_id        BIGINT PRIMARY KEY AUTO_INCREMENT,
    machine_id     INT      NOT NULL,
    record_time    DATETIME NOT NULL,
    rms_value      FLOAT    NOT NULL,
    kurtosis_value FLOAT,
    temperature    FLOAT,
    KEY idx_machine_time_cold (machine_id, record_time)
) ENGINE=InnoDB COMMENT='冷数据 2023-10';

CREATE TABLE sensor_data_cold_202311 LIKE sensor_data_cold_202310;

-- 冷表也可用原生分区（按天 RANGE 分区，便于 DROP PARTITION 快速过期）
-- CREATE TABLE sensor_data_cold_partitioned (...) 
-- PARTITION BY RANGE (TO_DAYS(record_time)) (
--     PARTITION p20231001 VALUES LESS THAN (TO_DAYS('2023-10-02')),
--     PARTITION p20231002 VALUES LESS THAN (TO_DAYS('2023-10-03')),
--     PARTITION pmax       VALUES LESS THAN MAXVALUE
-- );

-- ------------------------------------------------------------
-- 5. 告警溯源视图（供前端面板直接查询，只扫热区数据）
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_anomaly_trace AS
SELECT
    m.machine_name,
    a.alarm_time,
    a.anomaly_type,
    a.anomaly_value,
    a.threshold,
    a.knn_distance,
    a.description AS knn_suggestion,
    a.status
FROM anomaly_log a
JOIN machine_info m ON a.machine_id = m.machine_id
WHERE a.alarm_time >= NOW() - INTERVAL 7 DAY;

-- ------------------------------------------------------------
-- 6. 冷热迁移：每天把 7 天前的热数据搬到当月冷表后删除
--    （生产环境建议用事件调度器或独立的归档 Job，分批 LIMIT 删除避免锁表）
-- ------------------------------------------------------------
DELIMITER $$
CREATE PROCEDURE sp_archive_hot_to_cold(IN p_cold_table VARCHAR(64), IN p_days INT)
BEGIN
    SET @sql = CONCAT(
        'INSERT INTO ', p_cold_table,
        ' (machine_id, record_time, rms_value, kurtosis_value, temperature) ',
        'SELECT machine_id, record_time, rms_value, kurtosis_value, temperature ',
        'FROM sensor_data_hot WHERE record_time < NOW() - INTERVAL ', p_days, ' DAY'
    );
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    DELETE FROM sensor_data_hot
    WHERE record_time < NOW() - INTERVAL p_days DAY
    LIMIT 50000;   -- 分批删除，避免大事务
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 7. 慢查询排查（面试常问）
-- ------------------------------------------------------------
-- EXPLAIN SELECT * FROM sensor_data_hot
-- WHERE machine_id = 1 AND record_time BETWEEN '2023-10-01' AND '2023-10-02';
-- 期望：type = range，key = idx_machine_time_hot，Extra 不含 Using filesort
--
-- 反例：WHERE DATE(record_time) = '2023-10-01' 会让索引失效（函数作用于索引列），
--       应改写为范围查询 record_time >= '2023-10-01' AND record_time < '2023-10-02'。
