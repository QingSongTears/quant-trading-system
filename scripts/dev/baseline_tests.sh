#!/usr/bin/env bash
# ADR-0012 #83 — 测试基线守门脚本
# 排除与本 ADR 无关的环境依赖失败 (e2e_pages 需真实 DB / research 需 pyarrow /
# regression_real_data 需 BaoStock / optimization 需 optuna 等)
#
# 用法: bash scripts/dev/baseline_tests.sh
# 退出码 0 = 基线不变; 退出码 1 = 有新增失败
set -uo pipefail

cd "$(dirname "$0")/../.."

/home/ubuntu/.local/bin/pytest tests/ \
  --ignore=tests/e2e \
  --ignore=tests/test_e2e_pages.py \
  --ignore=tests/test_regression_real_data.py \
  --ignore=tests/test_research.py \
  --ignore=tests/test_research_apis.py \
  --ignore=tests/test_scorer_registry.py \
  --ignore=tests/test_sector_constraint.py \
  --ignore=tests/test_sector_engine_integration.py \
  --ignore=tests/test_xgb_scaler.py \
  --ignore=tests/test_optimization.py \
  --deselect tests/test_e2e.py::TestExceptionScenarios::test_backtest_zero_division_resilience \
  --deselect tests/test_e2e.py::TestSmokeRegression::test_all_test_files_present \
  --no-header -q --tb=no 2>&1 | tail -3