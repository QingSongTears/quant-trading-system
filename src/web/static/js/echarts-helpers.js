/*
 * echarts-helpers.js — ECharts 工厂函数 (2026-06-25)
 *
 * 替换 6+ 个页面里重复的 ECharts init 代码:
 *   - workbench.html, backtest_detail.html, compare.html
 *   - console.html, diagnose.html, ic.html
 *
 * 用法:
 *   const chart = QT.echarts.lineEquity(dom, dates, equity, benchmark, trades);
 *   chart.setOption(...);
 */
(function (global) {
  'use strict';
  global.QT = global.QT || {};
  const C = global.QT.COLOR;

  // ============== 渐变填充 (净值曲线 area) ==============
  function _gradientUp() {
    return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: 'rgba(79, 195, 247, 0.4)' },
      { offset: 1, color: 'rgba(79, 195, 247, 0.02)' },
    ]);
  }

  // ============== 净值曲线 + 买卖点 ==============
  function lineEquity(dom, dates, equity, benchmark, trades) {
    trades = trades || [];
    benchmark = benchmark || null;
    const series = [{
      name: '策略净值',
      type: 'line',
      data: equity,
      smooth: true,
      lineStyle: { color: C.ACCENT, width: 2 },
      areaStyle: { color: _gradientUp() },
      symbol: 'none',
    }];
    if (benchmark) {
      series.push({
        name: '基准',
        type: 'line',
        data: benchmark,
        smooth: true,
        lineStyle: { color: C.DIM, width: 1, type: 'dashed' },
        symbol: 'none',
      });
    }
    // 买卖点
    if (trades.length > 0) {
      const buyData = trades.filter(t => t.side === 'buy').map(t => ({
        name: 'B', value: [t.index, equity[t.index]], itemStyle: { color: C.UP },
      }));
      const sellData = trades.filter(t => t.side === 'sell').map(t => ({
        name: 'S', value: [t.index, equity[t.index]], itemStyle: { color: C.DOWN },
      }));
      series.push({
        name: '买入', type: 'scatter', data: buyData, symbolSize: 8,
      });
      series.push({
        name: '卖出', type: 'scatter', data: sellData, symbolSize: 8,
      });
    }
    return echarts.init(dom, null, { renderer: 'canvas' }).setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: series.map(s => s.name), textStyle: { color: C.TEXT } },
      grid: { left: 60, right: 20, top: 40, bottom: 50 },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: {
          color: C.DIM,
          interval: global.QT.smartDateInterval(dates.length),
        },
        axisLine: { lineStyle: { color: C.BORDER } },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: C.DIM, formatter: (v) => (v * 100).toFixed(1) + '%' },
        splitLine: { lineStyle: { color: C.BORDER, type: 'dashed' } },
      },
      series: series,
    });
  }

  // ============== 多策略对比 ==============
  function lineMultiStrategy(dom, dates, curves) {
    // curves: [{name, data, color?}, ...]
    const palette = [C.ACCENT, C.UP, C.DOWN, C.YELLOW, C.ORANGE, C.INDIGO];
    const series = curves.map((c, i) => ({
      name: c.name,
      type: 'line',
      data: c.data,
      smooth: true,
      lineStyle: { color: c.color || palette[i % palette.length], width: 2 },
      symbol: 'none',
    }));
    return echarts.init(dom).setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: series.map(s => s.name), textStyle: { color: C.TEXT } },
      grid: { left: 60, right: 20, top: 40, bottom: 50 },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: { color: C.DIM, interval: global.QT.smartDateInterval(dates.length) },
        axisLine: { lineStyle: { color: C.BORDER } },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: C.DIM, formatter: (v) => (v * 100).toFixed(0) + '%' },
        splitLine: { lineStyle: { color: C.BORDER, type: 'dashed' } },
      },
      series: series,
    });
  }

  // ============== 雷达图 (多维评分) ==============
  function radar(dom, indicators, values, name) {
    return echarts.init(dom).setOption({
      tooltip: {},
      radar: {
        indicator: indicators.map(i => ({ name: i, max: 100 })),
        axisName: { color: C.TEXT, fontSize: 12 },
        splitLine: { lineStyle: { color: C.BORDER } },
        splitArea: { areaStyle: { color: ['rgba(79, 195, 247, 0.05)', 'rgba(79, 195, 247, 0.1)'] } },
      },
      series: [{
        name: name || '评分',
        type: 'radar',
        data: [{ value: values, name: name || '当前',
          areaStyle: { color: 'rgba(79, 195, 247, 0.3)' },
          lineStyle: { color: C.ACCENT, width: 2 },
        }],
      }],
    });
  }

  // ============== K 线图 (OHLC) ==============
  function candlestick(dom, dates, ohlc, volume) {
    const series = [{
      name: 'K线', type: 'candlestick', data: ohlc,
    }];
    if (volume) {
      series.push({
        name: '成交量', type: 'bar', data: volume,
        xAxisIndex: 1, yAxisIndex: 1,
      });
    }
    return echarts.init(dom).setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: ['K线', '成交量'], textStyle: { color: C.TEXT } },
      grid: [
        { left: 60, right: 20, top: 30, height: '60%' },
        { left: 60, right: 20, top: '75%', height: '15%' },
      ],
      xAxis: [
        { type: 'category', data: dates, scale: true,
          axisLabel: { color: C.DIM, interval: global.QT.smartDateInterval(dates.length) },
          axisLine: { lineStyle: { color: C.BORDER } },
          splitLine: { show: false },
        },
        { type: 'category', data: dates, gridIndex: 1,
          axisLabel: { show: false }, axisLine: { show: false } },
      ],
      yAxis: [
        { scale: true, axisLabel: { color: C.DIM },
          splitLine: { lineStyle: { color: C.BORDER, type: 'dashed' } } },
        { scale: true, gridIndex: 1, axisLabel: { show: false },
          splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1] },
        { type: 'slider', xAxisIndex: [0, 1], textStyle: { color: C.DIM } },
      ],
      series: series,
    });
  }

  // ============== 暴露 ==============
  global.QT.echarts = {
    lineEquity: lineEquity,
    lineMultiStrategy: lineMultiStrategy,
    radar: radar,
    candlestick: candlestick,
  };
  console.log('[QT] echarts-helpers.js loaded');
})(window);
