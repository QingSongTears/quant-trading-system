"""
系统配置加载器
从 config/config.yaml 读取全局配置
"""
from __future__ import annotations
import os
import yaml
from pathlib import Path
from typing import Any


# 项目根目录 (src/config.py → ../ 即项目根)
PROJECT_ROOT = Path(__file__).parent.parent

# 配置文件路径
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
STRATEGIES_FILE = CONFIG_DIR / "strategies.yaml"


def load_config() -> dict[str, Any]:
    """加载全局配置"""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 解析相对路径为绝对路径
    if "database" in config and "path" in config["database"]:
        db_path = config["database"]["path"]
        if not os.path.isabs(db_path):
            config["database"]["path"] = str(PROJECT_ROOT / db_path)

    if "logging" in config and "file" in config["logging"]:
        log_file = config["logging"]["file"]
        if not os.path.isabs(log_file):
            config["logging"]["file"] = str(PROJECT_ROOT / log_file)

    return config


def load_strategies() -> dict[str, Any]:
    """加载策略注册表"""
    if not STRATEGIES_FILE.exists():
        return {"strategies": []}

    with open(STRATEGIES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_db_url(config: dict[str, Any]) -> str:
    """获取数据库连接 URL"""
    db_config = config.get("database", {})
    engine = db_config.get("engine", "sqlite")
    path = db_config.get("path", "database/quant.db")
    return f"sqlite:///{path}"


# 模块级配置缓存
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """获取全局配置（带缓存）"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
