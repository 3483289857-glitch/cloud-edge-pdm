"""数据清洗（区分快慢变量）与连续性异常检测"""
import pandas as pd
import numpy as np
from scipy.signal import medfilt


class IndustrialDataCleaner:
    """工业时序数据清洗

    核心思想：不同物理量不能用同一套清洗策略。
    - 慢变量（温度、压力、液位）：惯性大、变化平缓，用窗口分箱 IQR 截断 + 边界平滑
    - 快变量（振动、电流、声音）：含高频冲击特征，中值滤波去噪但必须保住尖峰
    """

    def __init__(self, window_size=15):
        self.window_size = window_size

    def clean(self, df, time_col='timestamp', value_col='value', signal_type='slow'):
        df = df.copy().drop_duplicates(subset=[time_col], keep='first')
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col).sort_index()

        # 1. 缺失值处理（时序线性插值，两端用最近值兜底）
        df[value_col] = pd.to_numeric(df[value_col], errors='coerce')
        df[value_col] = df[value_col].interpolate(method='linear').ffill().bfill()

        # 2. 分道扬镳的清洗策略
        if signal_type == 'slow':
            df[value_col] = self._clean_slow(df[value_col].values)
        else:
            df[value_col] = self._clean_fast(df[value_col].values)

        return df.reset_index()

    def _clean_slow(self, values):
        """慢变量：滑动窗口分箱 IQR，超界值压到边界（Smooth），不直接丢弃"""
        n = len(values)
        half = self.window_size // 2
        cleaned = np.empty(n, dtype=float)

        for i in range(n):
            start_idx = max(0, i - half)
            end_idx = min(n, i + half + 1)
            window = values[start_idx:end_idx]

            q1, q3 = np.percentile(window, 25), np.percentile(window, 75)
            iqr = q3 - q1
            lb, ub = q1 - 1.5 * iqr, q3 + 1.5 * iqr

            val = values[i]
            cleaned[i] = lb if val < lb else (ub if val > ub else val)
        return cleaned

    def _clean_fast(self, values):
        """快变量：中值滤波去噪，但保留高频尖峰特征（真实冲击不能被抹掉）"""
        values = np.asarray(values, dtype=float)
        if len(values) < 3:
            return values.copy()

        filtered = medfilt(values, kernel_size=3)
        # 计算残差（原始 - 滤波后）：残差过大说明是真实冲击，不应被平滑
        residual = values - filtered
        threshold = np.std(residual) * 3
        # 保留超出阈值的冲击
        return np.where(np.abs(residual) > threshold, values, filtered)


class TimeSeriesAnomalyDetector:
    """时序连续性异常检测

    单点超阈值大概率是噪声，工业现场只认"连续 N 个点越界"这种持续性劣化。
    """

    def __init__(self, continuous_threshold=3, sigma=2.5):
        self.continuous_threshold = continuous_threshold
        self.sigma = sigma

    def detect_continuity_anomalies(self, df, value_col='value'):
        """关注时序连续性：连续 N 个点异常才报警，返回真实异常点"""
        return self.annotate(df, value_col=value_col).query('is_real_anomaly')

    def annotate(self, df, value_col='value'):
        """返回带判定标记的完整序列（供前端画标记用）"""
        df = df.copy()
        # 简单阈值判定单点异常（均值 + k*标准差）
        threshold = df[value_col].mean() + self.sigma * df[value_col].std()
        df['point_threshold'] = threshold
        df['is_point_anomaly'] = df[value_col] > threshold

        # 计算连续异常点数：布尔序列变化处切组，再按组求和
        df['anomaly_group'] = (df['is_point_anomaly'] != df['is_point_anomaly'].shift()).cumsum()
        df['anomaly_count'] = df.groupby('anomaly_group')['is_point_anomaly'].transform('sum')

        # 只有连续点数达标才视为真实异常
        df['is_real_anomaly'] = df['is_point_anomaly'] & (
            df['anomaly_count'] >= self.continuous_threshold
        )
        return df
