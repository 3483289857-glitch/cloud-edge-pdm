/* ============================================================
   工业设备预警看板 · ECharts 渲染与事件联动
   数据来源：main.py 推理后导出的 frontend/data.json
   ============================================================ */
const COLOR = {
    green: '#00ff88',
    red: '#ff3366',
    amber: '#ffb020',
    cyan: '#38bdf8',
    axis: '#8b98a9',
    split: 'rgba(255,255,255,0.06)'
};

const chartMain = echarts.init(document.getElementById('realtime-chart'));
const chartResidual = echarts.init(document.getElementById('residual-chart'));

let chartData = [];      // 原始记录（点击联动取这里）
let timeData = [];
let vibData = [];
let tempData = [];
let residualData = [];
let thresholdData = [];
let alarmIndexes = [];

const baseAxisLabel = { color: COLOR.axis, fontSize: 10, fontFamily: 'Consolas' };
const baseSplitLine = { lineStyle: { color: COLOR.split, type: 'dashed' } };
const baseAxisLine = { lineStyle: { color: 'rgba(255,255,255,0.15)' } };

/* ---------------- 1. 拉取数据 ---------------- */
fetch('./data.json')
    .then(res => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    })
    .then(data => {
        if (!Array.isArray(data) || data.length === 0) throw new Error('数据为空');
        buildSeries(data);
        renderMainChart();
        renderResidualChart();
        bindClick(chartMain);
        bindClick(chartResidual);
        document.getElementById('meta-info').innerText =
            `数据点 ${data.length} | 告警 ${alarmIndexes.length} | 采样 1s`;
        // 默认聚焦第一个告警点，没有告警则聚焦最后一个点（不弹提示框，避免遮挡曲线）
        focusPoint(alarmIndexes.length ? alarmIndexes[0] : data.length - 1, false);
    })
    .catch(err => {
        showError(err);
    });

function buildSeries(data) {
    chartData = data;
    timeData = data.map(d => d.time.split(' ')[1]);   // 只取时分秒
    vibData = data.map(d => d.vibration);
    tempData = data.map(d => d.temperature);
    residualData = data.map(d => d.residual);
    thresholdData = data.map(d => d.residual_threshold);
    alarmIndexes = data
        .map((d, i) => (d.perceptron_alarm === 1 || d.is_reg_anomaly || d.continuity_alarm) ? i : -1)
        .filter(i => i >= 0);
}

/* ---------------- 2. 主图：振动 + 温度 + 异常标记 ---------------- */
function renderMainChart() {
    const alarmPoints = alarmIndexes.map(i => ({
        coord: [timeData[i], vibData[i]],
        value: '⚠'
    }));

    // 疑似故障区间：把连续告警点合并成一段 markArea
    const faultAreas = buildAlarmAreas();

    const option = {
        backgroundColor: 'transparent',
        animationDuration: 600,
        grid: { left: 52, right: 56, top: 34, bottom: 32 },
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(13,17,23,0.94)',
            borderColor: '#2a3240',
            textStyle: { color: '#e6edf3', fontSize: 12 },
            axisPointer: { type: 'line', lineStyle: { color: 'rgba(0,255,136,0.35)' } },
            formatter: function (params) {
                const idx = params[0].dataIndex;
                const d = chartData[idx];
                if (!d) return '';
                const flag = (d.perceptron_alarm === 1 || d.is_reg_anomaly)
                    ? '<span style="color:#ff3366">⚠ 异常</span>'
                    : '<span style="color:#00ff88">正常</span>';
                return `<b>${d.time}</b><br/>` +
                    `振动：${d.vibration} mm/s<br/>` +
                    `温度：${d.temperature} ℃<br/>` +
                    `残差：${d.residual} / 阈值 ${d.residual_threshold}<br/>` +
                    `判定：${flag}` +
                    (d.trace ? `<br/>溯源：${d.trace.suggestion}` : '');
            }
        },
        legend: {
            data: ['实际振动', '设备温度'],
            right: 20, top: 4,
            textStyle: { color: COLOR.axis, fontSize: 11 },
            itemWidth: 14, itemHeight: 8
        },
        xAxis: {
            type: 'category',
            data: timeData,
            boundaryGap: false,
            axisLabel: { ...baseAxisLabel, interval: Math.floor(timeData.length / 10) },
            axisLine: baseAxisLine,
            axisTick: { show: false }
        },
        yAxis: [
            {
                type: 'value',
                name: 'mm/s',
                nameTextStyle: { color: COLOR.axis, fontSize: 10 },
                axisLabel: baseAxisLabel,
                axisLine: { show: false },
                splitLine: baseSplitLine
            },
            {
                type: 'value',
                name: '℃',
                nameTextStyle: { color: COLOR.axis, fontSize: 10 },
                axisLabel: baseAxisLabel,
                axisLine: { show: false },
                splitLine: { show: false }
            }
        ],
        series: [
            {
                name: '实际振动',
                type: 'line',
                data: vibData,
                smooth: true,
                symbol: 'none',
                lineStyle: { color: COLOR.green, width: 2 },
                itemStyle: { color: COLOR.green },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(0,255,136,0.22)' },
                        { offset: 1, color: 'rgba(0,255,136,0.01)' }
                    ])
                },
                markArea: {
                    silent: true,
                    itemStyle: { color: 'rgba(255,51,102,0.07)' },
                    label: { show: true, position: 'insideTop', color: COLOR.red, fontSize: 10 },
                    data: faultAreas
                },
                markPoint: {
                    symbol: 'pin',
                    symbolSize: 26,
                    itemStyle: { color: COLOR.red, borderColor: '#fff', borderWidth: 1 },
                    label: { color: '#fff', fontSize: 10 },
                    data: alarmPoints
                }
            },
            {
                name: '设备温度',
                type: 'line',
                yAxisIndex: 1,
                data: tempData,
                smooth: true,
                symbol: 'none',
                lineStyle: { color: COLOR.amber, width: 1.6, type: 'dotted' },
                itemStyle: { color: COLOR.amber }
            }
        ]
    };
    chartMain.setOption(option);
}

/* ---------------- 3. 副图：残差 vs 动态阈值 ---------------- */
function renderResidualChart() {
    const option = {
        backgroundColor: 'transparent',
        animationDuration: 600,
        grid: { left: 52, right: 56, top: 26, bottom: 28 },
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(13,17,23,0.94)',
            borderColor: '#2a3240',
            textStyle: { color: '#e6edf3', fontSize: 12 },
            axisPointer: { type: 'line', lineStyle: { color: 'rgba(56,189,248,0.35)' } },
            formatter: function (params) {
                const idx = params[0].dataIndex;
                const d = chartData[idx];
                if (!d) return '';
                const ratio = d.residual_threshold
                    ? (d.residual / d.residual_threshold).toFixed(2) : '-';
                return `<b>${d.time}</b><br/>` +
                    `残差：${d.residual}<br/>` +
                    `阈值：${d.residual_threshold}<br/>` +
                    `残差/阈值：${ratio}`;
            }
        },
        xAxis: {
            type: 'category',
            data: timeData,
            boundaryGap: false,
            axisLabel: { ...baseAxisLabel, interval: Math.floor(timeData.length / 10) },
            axisLine: baseAxisLine,
            axisTick: { show: false }
        },
        yAxis: {
            type: 'value',
            name: '残差',
            nameTextStyle: { color: COLOR.axis, fontSize: 10 },
            axisLabel: baseAxisLabel,
            axisLine: { show: false },
            splitLine: baseSplitLine
        },
        series: [
            {
                name: '残差',
                type: 'line',
                data: residualData,
                smooth: true,
                symbol: 'none',
                lineStyle: { color: COLOR.cyan, width: 1.8 },
                itemStyle: { color: COLOR.cyan },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(56,189,248,0.25)' },
                        { offset: 1, color: 'rgba(56,189,248,0.01)' }
                    ])
                },
                // 超阈值的点染成红色，一眼看出越界
                markPoint: {
                    symbol: 'circle',
                    symbolSize: 8,
                    itemStyle: { color: COLOR.red },
                    label: { show: false },
                    data: alarmIndexes.map(i => ({
                        coord: [timeData[i], residualData[i]],
                        value: residualData[i]
                    }))
                }
            },
            {
                name: '动态阈值 3σ',
                type: 'line',
                data: thresholdData,
                smooth: false,
                symbol: 'none',
                lineStyle: { color: COLOR.red, width: 1.4, type: 'dashed' },
                itemStyle: { color: COLOR.red }
            }
        ]
    };
    chartResidual.setOption(option);
}

/* ---------------- 4. 事件联动：点击点位 -> 刷新侧栏 ---------------- */
function bindClick(chart) {
    chart.on('click', function (params) {
        if (params.componentType === 'series' && params.dataIndex !== undefined) {
            focusPoint(params.dataIndex, true);
        }
    });
}

function focusPoint(index, showTip = true) {
    const d = chartData[index];
    if (!d) return;

    const isAlarm = d.perceptron_alarm === 1 || d.is_reg_anomaly || d.continuity_alarm;

    // 感知机判定
    const elPerceptron = document.getElementById('status-perceptron');
    elPerceptron.innerText = d.perceptron_alarm === 1 ? '⚠️ 异常' : '正常';
    elPerceptron.parentElement.classList.toggle('alarm', d.perceptron_alarm === 1);

    // 回归残差
    const elResidual = document.getElementById('status-residual');
    elResidual.innerText = d.residual.toFixed(3);
    elResidual.parentElement.classList.toggle('alarm', d.is_reg_anomaly);

    // 阈值
    document.getElementById('status-threshold').innerText = d.residual_threshold.toFixed(3);

    // 连续性检测
    const elCont = document.getElementById('status-continuity');
    elCont.innerText = d.continuity_alarm ? '⚠️ 连续越界' : '正常';
    elCont.parentElement.classList.toggle('alarm', d.continuity_alarm);

    // 时域特征
    document.getElementById('feat-rms').innerText = d.rms !== undefined ? d.rms.toFixed(3) : '-';
    document.getElementById('feat-kurt').innerText = d.kurtosis !== undefined ? d.kurtosis.toFixed(3) : '-';

    // KNN 溯源卡片：只有真异常且有溯源结果才展示
    const panel = document.getElementById('trace-panel');
    if (isAlarm && d.trace) {
        panel.classList.remove('hidden');
        document.getElementById('trace-id').innerText = '#' + d.trace.nearest_fault_id;
        document.getElementById('trace-distance').innerText = d.trace.distance;
        document.getElementById('trace-suggestion').innerText = d.trace.suggestion;
        document.getElementById('trace-time').innerText = d.time;
    } else {
        panel.classList.add('hidden');
    }

    // 同步两图的十字准星位置（仅在用户点击时弹出提示框）
    if (showTip) {
        [chartMain, chartResidual].forEach(c => {
            c.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: index });
        });
    }
}

/* ---------------- 5. 辅助函数 ---------------- */
function buildAlarmAreas() {
    const areas = [];
    let start = null;
    for (let i = 0; i < chartData.length; i++) {
        const bad = chartData[i].perceptron_alarm === 1 || chartData[i].is_reg_anomaly;
        if (bad && start === null) start = i;
        if (!bad && start !== null) {
            if (i - start >= 2) {
                areas.push([
                    { xAxis: timeData[start], name: '疑似故障区间' },
                    { xAxis: timeData[i - 1] }
                ]);
            }
            start = null;
        }
    }
    if (start !== null && chartData.length - start >= 2) {
        areas.push([
            { xAxis: timeData[start], name: '疑似故障区间' },
            { xAxis: timeData[chartData.length - 1] }
        ]);
    }
    return areas;
}

function showError(err) {
    const msg = `
        <div style="height:100%;display:flex;flex-direction:column;align-items:center;
                    justify-content:center;gap:10px;color:#8b98a9;font-size:13px;line-height:1.9;">
            <div style="color:#ff3366;font-size:15px;">⚠ 未读取到 data.json</div>
            <div>${err.message}</div>
            <div style="font-family:Consolas,monospace;font-size:12px;color:#e6edf3;">
                1) 先在项目根目录执行：python main.py<br/>
                2) 再启动本地服务：python -m http.server 8000<br/>
                3) 访问：http://localhost:8000/frontend/index.html
            </div>
            <div style="font-size:11px;">浏览器不允许 file:// 协议下 fetch 本地 JSON</div>
        </div>`;
    document.getElementById('realtime-chart').innerHTML = msg;
}

/* ---------------- 6. 自适应 ---------------- */
window.addEventListener('resize', () => {
    chartMain.resize();
    chartResidual.resize();
});
