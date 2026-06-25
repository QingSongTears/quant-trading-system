/*
 * common.js — 前端公共 JS (2026-06-25)
 *
 * 提供:
 *   - 颜色常量 (A 股约定: 涨红跌绿)
 *   - fetch 拦截器 (Bearer token 注入)
 *   - smartDateInterval (智能 x 轴 label 间隔)
 *   - formatNumber / formatPct 工具
 *   - debounce / throttle
 *
 * 加载: <script src="/static/js/common.js"></script>
 *
 * 命名空间: window.QT (Quant Trading)
 */
(function (global) {
  'use strict';

  // ============== A 股颜色 ==============
  const COLOR = {
    UP: '#ef5350',      // 涨 (红)
    DOWN: '#4caf50',    // 跌 (绿)
    FLAT: '#888',
    ACCENT: '#4fc3f7',
    BG: '#0f1117',
    CARD: '#1a1d27',
    BORDER: '#2a2d3a',
    TEXT: '#e0e0e0',
    DIM: '#888',
  };

  // ============== fetch 拦截器 (Bearer token) ==============
  // 2026-06-25: 既挂 QT.fetch 命名空间, 又覆盖 window.fetch 让老代码 (window.fetch) 自动生效
  const _originalFetch = global.fetch ? global.fetch.bind(global) : null;
  function authedFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    // 优先从 meta 标签读 API key, fallback 到 localStorage
    const metaKey = document.querySelector('meta[name="api-key"]');
    const apiKey = metaKey ? metaKey.getAttribute('content')
                          : (localStorage.getItem('QUANT_API_KEY') || '');
    if (apiKey && !options.headers['Authorization']) {
      options.headers['Authorization'] = 'Bearer ' + apiKey;
    }
    if (_originalFetch) {
      return _originalFetch(url, options);
    }
    return Promise.reject(new Error('fetch 未定义'));
  }
  // 2026-06-25: 覆盖 window.fetch 让老 fetch('/api/...') 调用自动带 Bearer
  // (页面用 window.fetch 时不用改代码就能鉴权)
  global.QT = global.QT || {};
  global.QT.fetch = authedFetch;
  global.QT.COLOR = COLOR;
  global.fetch = authedFetch;

  // ============== smartDateInterval ==============
  // 净值曲线 / K 线图: 数据 > 100 时跳过中间 label, 避免拥挤
  function smartDateInterval(dataLen, maxLabels) {
    maxLabels = maxLabels || 8;
    if (dataLen <= maxLabels) return 0;
    return Math.ceil(dataLen / maxLabels);
  }
  global.QT.smartDateInterval = smartDateInterval;

  // ============== 数字格式化 ==============
  function formatNumber(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    digits = (digits === undefined) ? 2 : digits;
    return Number(n).toFixed(digits);
  }

  function formatPct(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    digits = (digits === undefined) ? 2 : digits;
    return Number(n).toFixed(digits) + '%';
  }

  function formatMoney(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    n = Number(n);
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + '万';
    return n.toFixed(2);
  }

  global.QT.format = {
    number: formatNumber,
    pct: formatPct,
    money: formatMoney,
  };

  // ============== debounce / throttle ==============
  function debounce(fn, ms) {
    let timer = null;
    return function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function throttle(fn, ms) {
    let last = 0;
    return function (...args) {
      const now = Date.now();
      if (now - last >= ms) {
        last = now;
        fn.apply(this, args);
      }
    };
  }

  global.QT.debounce = debounce;
  global.QT.throttle = throttle;

  // ============== 股票代码搜索 (调用 /api/data/search) ==============
  function searchStock(query) {
    if (!query || query.length < 2) return Promise.resolve([]);
    return QT.fetch(`/api/data/search?q=${encodeURIComponent(query)}`)
      .then(r => r.ok ? r.json() : [])
      .catch(() => []);
  }
  global.QT.searchStock = searchStock;

  console.log('[QT] common.js loaded');
})(window);
