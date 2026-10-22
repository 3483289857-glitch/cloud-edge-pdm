"""算法库：感知机初筛 / 降维KNN溯源 / 自适应多元回归残差检测"""
import numpy as np
from sklearn.linear_model import LinearRegression, Perceptron
from sklearn.neighbors import KNeighborsClassifier
from sklearn.decomposition import PCA
from scipy.stats import kurtosis


class IndustrialAlgorithms:
    def __init__(self, n_components=2):
        # 边缘端：感知机，线性可分场景下极轻量，适合跑在网关/PLC 侧
        self.perceptron = Perceptron(max_iter=1000, eta0=0.01, random_state=42)
        # 云端：KNN 前加入 PCA 降维，防止维度灾难（高维特征下距离度量失效）
        self.n_components = n_components
        self.pca = None
        self.knn = KNeighborsClassifier(n_neighbors=3, metric='euclidean')
        # 物理基线回归 + 自适应阈值
        self.regression = LinearRegression()
        self.residual_threshold = None
        # 训练集标签字典（用于把 KNN 命中的索引翻译成人话）
        self.label_map = {}

    # ---------- 特征工程 ----------
    def extract_features(self, signal_window):
        """提取时域特征：均方根(RMS)、峭度(Kurtosis)

        振动信号做故障诊断的经典组合：
        RMS 反映能量大小（磨损/不平衡），峭度反映冲击成分（点蚀/剥落）。
        """
        signal_window = np.asarray(signal_window, dtype=float)
        rms = np.sqrt(np.mean(np.square(signal_window)))
        kurt = kurtosis(signal_window, fisher=True)
        return [float(rms), float(kurt)]

    def extract_features_batch(self, signal_matrix):
        """对二维窗口矩阵逐行提特征，返回 (n_samples, 2)"""
        return np.array([self.extract_features(row) for row in signal_matrix])

    # ---------- 1. 感知机（边缘端快速初筛） ----------
    def train_perceptron(self, X_train, y_train):
        self.perceptron.fit(np.asarray(X_train), np.asarray(y_train))
        return self

    def predict_perceptron(self, X_test):
        return self.perceptron.predict(np.asarray(X_test))

    # ---------- 2. KNN（云端多工况溯源检索） ----------
    def train_knn(self, X_train, y_train, label_map=None):
        X_train = np.asarray(X_train, dtype=float)
        # 降维维度不能超过原始特征维度
        n_comp = max(1, min(self.n_components, X_train.shape[1]))
        self.pca = PCA(n_components=n_comp)
        X_reduced = self.pca.fit_transform(X_train)
        self.knn.fit(X_reduced, np.asarray(y_train))
        if label_map is not None:
            self.label_map = label_map
        return self

    def retrieve_similar_faults(self, X_current, n_neighbors=3):
        """返回 (距离数组, 索引数组, 命中标签)，用于前端溯源展示"""
        X_current = np.asarray(X_current, dtype=float)
        X_reduced = self.pca.transform(X_current)
        k = min(n_neighbors, len(self.knn.classes_) and self.knn.n_samples_fit_)
        distances, indices = self.knn.kneighbors(X_reduced, n_neighbors=k)
        labels = self.knn.predict(X_reduced)
        return distances[0], indices[0], labels[0]

    # ---------- 3. 多元线性回归（物理残差 + 自适应阈值） ----------
    def fit_physics_baseline(self, X_physics, y_target):
        X_physics = np.asarray(X_physics, dtype=float)
        y_target = np.asarray(y_target, dtype=float).ravel()
        self.regression.fit(X_physics, y_target)
        self.update_threshold(X_physics, y_target)
        return self

    def update_threshold(self, X_recent, y_recent):
        """引入滑动窗口自学习，动态更新阈值，解决概念漂移

        工况缓慢变化（环境温度、负载、季节）会让固定阈值失效，
        用近期残差的 3 倍标准差作为阈值可以跟着设备一起"漂"。
        """
        X_recent = np.asarray(X_recent, dtype=float)
        y_recent = np.asarray(y_recent, dtype=float).ravel()
        residuals = y_recent - self.regression.predict(X_recent)
        self.residual_threshold = max(float(np.std(residuals) * 3), 1e-5)
        return self.residual_threshold

    def detect_residual_anomaly(self, X_current_physics, y_current_target):
        y_pred = self.regression.predict(np.asarray(X_current_physics, dtype=float))
        residual = abs(float(np.asarray(y_current_target, dtype=float).ravel()[0]) - float(y_pred[0]))
        is_anomaly = bool(residual > self.residual_threshold)
        return is_anomaly, float(residual), float(self.residual_threshold)
