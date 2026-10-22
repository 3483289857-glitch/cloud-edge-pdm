"""工业设备故障诊断与边缘预警系统 —— 核心业务串联

数据流：模拟采集 -> 差异化清洗（快/慢变量）-> 边缘端感知机初筛
        -> 云端多元回归残差检测（自适应阈值）-> KNN 故障溯源 -> 导出 JSON 供看板消费

对应"云边协同"的三段式：
    边缘（秒级、轻量）  感知机做第一道闸门，只把可疑窗口上传，省带宽
    云端（重计算）      回归残差 + KNN 检索历史故障库，做诊断与溯源
    应用（看板）        前端 ECharts 拉取 JSON，点击点位联动溯源卡片
"""
import json
import os

import numpy as np
import pandas as pd

from core.data_processor import IndustrialDataCleaner, TimeSeriesAnomalyDetector
from core.algorithms import IndustrialAlgorithms

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, 'frontend', 'data.json')

# 训练标签 -> 诊断建议（实际项目里应来自故障知识库/工单系统）
LABEL_MAP = {
    0: "正常工况（无故障特征）",
    1: "轴承外圈磨损 / 润滑不良",
}

FAULT_START, FAULT_END = 150, 160
TRAIN_END = 100          # 前 100 个点作为物理基线拟合的"正常工况"
FEATURE_WINDOW = 15      # 提取 RMS / 峭度 的窗口长度


def generate_mock_data(num_samples=200):
    """生成模拟工业时序数据"""
    np.random.seed(42)
    time = pd.date_range(start='2023-10-01', periods=num_samples, freq='1s')
    # 模拟温度(慢变量) 与 振动(快变量)
    temp = 70 + np.random.normal(0, 1, num_samples)
    vibration = 5 + np.random.normal(0, 0.5, num_samples)

    # 注入异常：第150-160个点发生故障（温度骤升，振动异常）
    temp[FAULT_START:FAULT_END] += 8
    vibration[FAULT_START:FAULT_END] += 4

    return pd.DataFrame({'timestamp': time, 'temperature': temp, 'vibration': vibration})


def build_training_set(seed=7):
    """构造历史训练集：正常工况 + 故障工况（特征 = [温度, 振动]）

    注意：特征量纲必须和线上真实数据一致，否则感知机永远输出同一个类。
    """
    rng = np.random.RandomState(seed)
    n_normal, n_fault = 100, 20

    X_normal = np.column_stack([
        rng.normal(70.0, 1.0, n_normal),   # 温度 ~70℃
        rng.normal(5.0, 0.5, n_normal),    # 振动 ~5mm/s
    ])
    X_fault = np.column_stack([
        rng.normal(78.0, 1.5, n_fault),    # 故障：温度抬升
        rng.normal(9.0, 1.0, n_fault),     # 故障：振动抬升
    ])

    X_train = np.vstack((X_normal, X_fault))
    y_train = np.hstack((np.zeros(n_normal), np.ones(n_fault)))
    return X_train, y_train


def rolling_features(series, window=FEATURE_WINDOW):
    """对整段信号逐点提取 RMS / 峭度（尾部窗口不足时复用最近窗口）"""
    values = np.asarray(series, dtype=float)
    algo = IndustrialAlgorithms()
    feats = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        feats.append(algo.extract_features(values[start:i + 1]))
    return np.array(feats)


def main():
    print("--- 启动工业设备智能预警系统 ---")
    df = generate_mock_data()

    # ---------- 1. 数据清洗（差分策略） ----------
    cleaner = IndustrialDataCleaner(window_size=15)
    df_temp_clean = cleaner.clean(df.copy(), value_col='temperature', signal_type='slow')
    df_vib_clean = cleaner.clean(df.copy(), value_col='vibration', signal_type='fast')
    print(f"[清洗] 温度(慢变量) IQR 截断完成 | 振动(快变量) 中值滤波+尖峰保留完成")

    # ---------- 2. 连续性异常检测（单点噪声不算，连续N点才报警） ----------
    detector = TimeSeriesAnomalyDetector(continuous_threshold=3, sigma=2.5)
    temp_annotated = detector.annotate(df_temp_clean, value_col='temperature')
    continuity_hits = temp_annotated[temp_annotated['is_real_anomaly']]
    continuity_set = set(pd.to_datetime(continuity_hits['timestamp']))
    print(f"[连续性检测] 温度连续越界点 {len(continuity_hits)} 个（单点越界 "
          f"{int(temp_annotated['is_point_anomaly'].sum())} 个，已过滤噪声）")

    # ---------- 3. 算法初始化与训练 ----------
    algo = IndustrialAlgorithms(n_components=2)
    X_train, y_train = build_training_set()
    algo.train_perceptron(X_train, y_train)
    algo.train_knn(X_train, y_train, label_map=LABEL_MAP)
    print(f"[训练] 感知机/ KNN 训练完成，样本 {len(X_train)} 条（正常100 / 故障20）")

    # 拟合物理基线（假设: 温度 = f(振动) + 基线偏置）
    X_physics = df_vib_clean[['vibration']].values[:TRAIN_END]
    y_physics = df_temp_clean[['temperature']].values[:TRAIN_END]
    algo.fit_physics_baseline(X_physics, y_physics)
    print(f"[基线] 温度-振动回归拟合完成，初始自适应阈值 = {algo.residual_threshold:.4f}")

    # ---------- 4. 实时流转与推理（模拟数据流解析） ----------
    vib_feats = rolling_features(df_vib_clean['vibration'].values)
    results_for_frontend = []
    alarm_count = 0
    recent_residuals = []

    for i in range(TRAIN_END, len(df)):
        current_vib = float(df_vib_clean['vibration'].iloc[i])
        current_temp = float(df_temp_clean['temperature'].iloc[i])
        time_str = str(df['timestamp'].iloc[i])

        # --- 边缘端：感知机初筛（特征 [温度, 振动]） ---
        X_current = np.array([[current_temp, current_vib]])
        perceptron_result = int(algo.predict_perceptron(X_current)[0])

        # --- 云端：多元回归残差检测 ---
        is_reg_anomaly, residual, threshold = algo.detect_residual_anomaly(
            np.array([[current_vib]]), np.array([current_temp])
        )
        recent_residuals.append(residual)

        # --- 自适应阈值：每 30 点用"干净"的近期残差重估一次，跟随工况漂移 ---
        # 前提：当前点正常，且窗口内所有残差都在阈值内（窗口必须干净）
        # 否则会把故障学成正常工况（自适应阈值最典型的坑）
        if len(recent_residuals) >= 30 and not is_reg_anomaly and perceptron_result == 0:
            window = np.array(recent_residuals[-30:])
            if window.max() <= threshold:
                algo.update_threshold(
                    df_vib_clean[['vibration']].values[i - 29:i + 1],
                    df_temp_clean[['temperature']].values[i - 29:i + 1],
                )
                threshold = algo.residual_threshold

        # --- 只有异常才上云做 KNN 溯源（云边协同：省带宽） ---
        trace_info = None
        if perceptron_result == 1 or is_reg_anomaly:
            alarm_count += 1
            distances, indices, label = algo.retrieve_similar_faults(X_current)
            trace_info = {
                "nearest_fault_id": int(indices[0]),
                "distance": round(float(distances[0]), 4),
                "label": int(label),
                "suggestion": LABEL_MAP.get(int(label), "未知故障模式"),
                "top3_ids": [int(x) for x in indices],
            }

        rms, kurt = vib_feats[i]
        results_for_frontend.append({
            "index": i,
            "time": time_str,
            "vibration": round(current_vib, 3),
            "temperature": round(current_temp, 3),
            "rms": round(float(rms), 4),
            "kurtosis": round(float(kurt), 4),
            "perceptron_alarm": perceptron_result,
            "residual": round(residual, 3),
            "residual_threshold": round(threshold, 3),
            "is_reg_anomaly": bool(is_reg_anomaly),
            "continuity_alarm": bool(pd.Timestamp(df['timestamp'].iloc[i]) in continuity_set),
            "trace": trace_info,
        })

    # ---------- 5. 导出结果供前端读取 ----------
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results_for_frontend, f, ensure_ascii=False, indent=2)

    # ---------- 6. 控制台简报 ----------
    first_alarm = next((r for r in results_for_frontend
                        if r['perceptron_alarm'] or r['is_reg_anomaly']), None)
    print(f"[推理] 共处理 {len(results_for_frontend)} 个点，触发告警 {alarm_count} 个")
    if first_alarm:
        print(f"[告警] 首次异常 @ {first_alarm['time']} "
              f"| 残差 {first_alarm['residual']} > 阈值 {first_alarm['residual_threshold']} "
              f"| 溯源: {first_alarm['trace']['suggestion'] if first_alarm['trace'] else '-'}")
    print(f"--- 数据推理完成，已生成 {os.path.relpath(OUT_PATH, BASE_DIR)} ---")
    print("提示：看板需通过 HTTP 打开（浏览器限制 file:// 下 fetch 本地 JSON），"
          "在项目根目录执行  python -m http.server 8000  后访问 "
          "http://localhost:8000/frontend/index.html")


if __name__ == "__main__":
    main()
