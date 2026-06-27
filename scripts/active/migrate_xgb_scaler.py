"""
一次性迁移脚本: data/xgb_scaler.pkl → data/xgb_scaler.json
==========================================================

背景
----
旧训练脚本 (train_xgb_model.py / train_xgb_v4.py) 用 pickle 保存 StandardScaler,
加载时存在反序列化 RCE 风险 (P0-3)。现已统一改为 JSON 格式
(src/data/xgb_scaler.py),加载路径不再触碰 pickle。

本脚本用于把已存在的 .pkl 数据无损迁移为 .json,只依赖 numpy。

⚠️ 安全提示
----------
本脚本会调用 pickle.load() 读取 data/xgb_scaler.pkl。
仅在你确认该文件来自本项目自己的 train_xgb_*.py 训练产物时运行;
**不要**对来源不明的 .pkl 运行此脚本,或运行前先校验文件哈希。

完整修复见 src/data/xgb_scaler.py — 那是 param_server 的加载路径,
本迁移完成后系统将只读 .json。

用法
----
    pip install numpy
    python scripts/migrate_xgb_scaler.py
"""
import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
PKL_PATH = PROJECT_ROOT / "data" / "xgb_scaler.pkl"
JSON_PATH = PROJECT_ROOT / "data" / "xgb_scaler.json"
EXPECTED_SHA256 = None  # 可选:首次迁移后填入,后续运行会校验


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def migrate():
    if not PKL_PATH.exists():
        print(f"❌ 源文件不存在: {PKL_PATH}")
        print("   (如果是从头训练,请直接运行 train_xgb_*.py,会自动生成 .json)")
        return 1

    if JSON_PATH.exists():
        print(f"⚠️ 目标已存在,跳过: {JSON_PATH}")
        print("   如需重新迁移,先删除该文件再重跑。")
        return 0

    # 可选 SHA-256 校验
    digest = _sha256(PKL_PATH)
    print(f"📋 源文件 SHA-256: {digest}")
    if EXPECTED_SHA256:
        if digest != EXPECTED_SHA256:
            print(f"❌ SHA-256 校验失败!")
            print(f"   期望: {EXPECTED_SHA256}")
            print(f"   实际: {digest}")
            print(f"   拒绝迁移,请确认文件来源")
            return 1
        print("   ✅ SHA-256 校验通过")

    print(f"📥 读取 {PKL_PATH} ...")
    # pickle.load 需要 import sklearn 才能实例化 StandardScaler
    # 如果环境没有 sklearn,给清晰的指引而不是栈错误
    try:
        with open(PKL_PATH, "rb") as f:
            obj = pickle.load(f)
    except ModuleNotFoundError as e:
        missing = e.name or "unknown"
        print(f"❌ 读取 pickle 失败: 缺模块 {missing!r}")
        print()
        print("本脚本需要 sklearn 才能反序列化 StandardScaler 对象。")
        print("解决方式(任选其一):")
        print(f"  1. pip install scikit-learn && python {Path(__file__).name}")
        print(f"  2. 直接重新训练:python scripts/train_xgb_model.py "
              f"(会自动写 .json,无需 .pkl)")
        print(f"  3. 跳过 XGBoost,使用系统 fallback (旧逻辑回归模型)")
        return 1

    # obj 应该是 {'scaler': StandardScaler, 'feature_names': [...]}
    if not isinstance(obj, dict) or "scaler" not in obj or "feature_names" not in obj:
        print(f"❌ pickle 内容不符合预期,得到: {type(obj).__name__}")
        if isinstance(obj, dict):
            print(f"   keys: {list(obj.keys())}")
        return 1

    scaler = obj["scaler"]
    feature_names = list(obj["feature_names"])

    mean = np.asarray(scaler.mean_, dtype=float)
    scale = np.asarray(scaler.scale_, dtype=float)
    n_features = int(getattr(scaler, "n_features_in_", len(mean)))

    if mean.shape != (n_features,):
        print(f"❌ mean 长度 {mean.shape[0]} != n_features {n_features}")
        return 1
    if scale.shape != (n_features,):
        print(f"❌ scale 长度 {scale.shape[0]} != n_features {n_features}")
        return 1
    if len(feature_names) != n_features:
        print(f"❌ feature_names 长度 {len(feature_names)} != n_features {n_features}")
        return 1

    data = {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "n_features": n_features,
        "feature_names": list(feature_names),
    }

    print(f"💾 写入 {JSON_PATH} ...")
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ 迁移完成: {n_features} 个特征")
    print(f"   mean 范围: [{mean.min():.4f}, {mean.max():.4f}]")
    print(f"   scale 范围: [{scale.min():.4f}, {scale.max():.4f}]")
    print()
    print("后续步骤:")
    print(f"  1. 删除 pkl: rm {PKL_PATH.name}")
    print(f"  2. 启动 param_server 验证: python scripts/param_server.py")
    print()
    print("ℹ️  建议把上面的 SHA-256 填入 EXPECTED_SHA256,"
          "后续运行会自动校验文件未被篡改。")
    return 0


if __name__ == "__main__":
    sys.exit(migrate())
