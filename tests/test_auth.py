"""
auth 单测 — Web 层认证与 class_path 白名单

覆盖:
- check_bearer_token 7 个边界(None/空/非Bearer/缺token/空格/错token/正确)
- get_api_key 进程内稳定 + env 覆盖
- safe_import_strategy 白名单 + 标识符校验
- require_api_key Flask hook 行为(用 mock request)
"""
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.web.auth import (
    ALLOWED_STRATEGY_PREFIXES,
    check_bearer_token,
    get_api_key,
    safe_import_strategy,
)


# ============================================================
# check_bearer_token — framework-agnostic 核心
# ============================================================


class TestCheckBearerToken:
    def test_none_rejected(self):
        assert check_bearer_token(None) is False

    def test_empty_rejected(self):
        assert check_bearer_token("") is False

    def test_wrong_scheme_rejected(self):
        """Basic / Digest / 不带 scheme 都应拒绝"""
        assert check_bearer_token("Basic dXNlcjpwYXNz") is False
        assert check_bearer_token("Digest foo") is False
        assert check_bearer_token("BearerEvil") is False  # 没空格,当整 token 处理

    def test_bearer_without_token_rejected(self):
        assert check_bearer_token("Bearer") is False
        assert check_bearer_token("Bearer ") is False
        assert check_bearer_token("Bearer  ") is False  # 多空格

    def test_wrong_token_rejected(self, monkeypatch):
        monkeypatch.setenv("QUANT_API_KEY", "right-key")
        assert check_bearer_token("Bearer wrong-key") is False

    def test_correct_token_accepted(self, monkeypatch):
        monkeypatch.setenv("QUANT_API_KEY", "right-key")
        assert check_bearer_token("Bearer right-key") is True

    def test_case_insensitive_scheme(self, monkeypatch):
        """HTTP 规范说 scheme 大小写不敏感"""
        monkeypatch.setenv("QUANT_API_KEY", "k")
        assert check_bearer_token("bearer k") is True
        assert check_bearer_token("BEARER k") is True
        assert check_bearer_token("BeArEr k") is True

    def test_timing_attack_resistance(self, monkeypatch):
        """secrets.compare_digest 是常量时间,这里只验调用没抛异常"""
        monkeypatch.setenv("QUANT_API_KEY", "a" * 32)
        # 不同长度的 token 也应该被正确处理
        assert check_bearer_token("Bearer " + "a" * 31) is False
        assert check_bearer_token("Bearer " + "a" * 32) is True
        assert check_bearer_token("Bearer " + "a" * 33) is False


# ============================================================
# get_api_key — 进程内稳定 + env 覆盖
# ============================================================


class TestGetApiKey:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("QUANT_API_KEY", "explicit-key")
        # reload 模块使 _api_key_cache 重置
        import importlib
        import src.web.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.get_api_key() == "explicit-key"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("QUANT_API_KEY", "  key-with-spaces  ")
        import importlib
        import src.web.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.get_api_key() == "key-with-spaces"

    def test_empty_env_falls_back_to_ephemeral(self, monkeypatch):
        monkeypatch.setenv("QUANT_API_KEY", "")
        import importlib
        import src.web.auth as auth_mod
        importlib.reload(auth_mod)
        k = auth_mod.get_api_key()
        assert k != ""
        assert len(k) >= 32  # token_urlsafe(32) 至少 43 字符

    def test_stable_within_process(self, monkeypatch):
        """进程内多次调用应返回相同 key"""
        monkeypatch.delenv("QUANT_API_KEY", raising=False)
        import importlib
        import src.web.auth as auth_mod
        importlib.reload(auth_mod)
        k1 = auth_mod.get_api_key()
        k2 = auth_mod.get_api_key()
        k3 = auth_mod.get_api_key()
        assert k1 == k2 == k3


# ============================================================
# safe_import_strategy — 白名单
# ============================================================


class TestSafeImportStrategy:
    def test_accepts_whitelisted_path(self):
        """白名单 src.strategies.* 接受"""
        cls = safe_import_strategy("src.strategies.ma_cross.MaCrossStrategy")
        # 类名可能不同(具体看实际代码),但应是 class
        assert isinstance(cls, type)
        # 类应该能识别"策略"接口(继承 BaseStrategy)
        # 不强断言,允许重构

    def test_rejects_os_system(self):
        with pytest.raises(ValueError, match="不在白名单"):
            safe_import_strategy("os.system")

    def test_rejects_subprocess(self):
        with pytest.raises(ValueError, match="不在白名单"):
            safe_import_strategy("subprocess.Popen")

    def test_rejects_arbitrary_src(self):
        """src.web.app 之类不应在白名单"""
        with pytest.raises(ValueError, match="不在白名单"):
            safe_import_strategy("src.web.app.create_app")

    def test_rejects_builtins_eval(self):
        with pytest.raises(ValueError, match="不在白名单"):
            safe_import_strategy("builtins.eval")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="必须是字符串"):
            safe_import_strategy(None)
        with pytest.raises(ValueError, match="必须是字符串"):
            safe_import_strategy(123)
        with pytest.raises(ValueError, match="必须是字符串"):
            safe_import_strategy(["a", "b"])

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="必须是字符串"):
            safe_import_strategy("")

    def test_rejects_injection_in_class_name(self):
        """类名注入 (e.g. 'eval; rm -rf /') 应被标识符检查拒掉"""
        with pytest.raises(ValueError, match="类名不合法"):
            safe_import_strategy("src.strategies.x.Class; rm -rf /")

    def test_rejects_injection_in_module_path(self):
        """模块路径段含特殊字符应拒绝"""
        with pytest.raises(ValueError, match="模块路径段不合法"):
            safe_import_strategy("src.strategies.foo-bar.Baz")
        with pytest.raises(ValueError, match="模块路径段不合法"):
            safe_import_strategy("src.strategies.123foo.Baz")

    def test_rejects_class_name_starting_with_digit(self):
        with pytest.raises(ValueError, match="类名不合法"):
            safe_import_strategy("src.strategies.x.123notid")

    def test_rejects_missing_class_in_module(self):
        """白名单通过但类不存在的报错应明确"""
        with pytest.raises(ValueError, match="不存在"):
            safe_import_strategy("src.strategies.ma_cross.NonExistentClass_xyz")

    def test_rejects_module_not_found(self):
        """白名单通过但模块不存在的报错应明确"""
        with pytest.raises(ValueError, match="无法 import"):
            safe_import_strategy("src.strategies.nonexistent_xyz_module.Foo")

    def test_rejects_module_with_no_dot(self):
        with pytest.raises(ValueError, match="格式错误"):
            safe_import_strategy("NoDotsHere")
        with pytest.raises(ValueError, match="格式错误"):
            safe_import_strategy("onlyone.class")

    def test_rejects_trailing_dot(self):
        """'src.x.' 末尾点 - rpartition 后 class_name 为空"""
        with pytest.raises(ValueError, match="格式错误"):
            safe_import_strategy("src.x.")

    def test_loaded_object_must_be_class(self, monkeypatch):
        """getattr 拿到函数/变量应拒绝"""
        # 用一个确实存在但不是类的对象
        import src.config
        # get_config 是函数,不是类
        with pytest.raises(ValueError, match="不是类"):
            safe_import_strategy("src.config.get_config")

    def test_whitelist_prefixes(self):
        """白名单前缀应至少包含 src.strategies. 和 src.models."""
        assert "src.strategies." in ALLOWED_STRATEGY_PREFIXES
        assert "src.models." in ALLOWED_STRATEGY_PREFIXES
        # 不应包含危险前缀
        assert "os." not in ALLOWED_STRATEGY_PREFIXES
        assert "subprocess." not in ALLOWED_STRATEGY_PREFIXES
        assert "builtins." not in ALLOWED_STRATEGY_PREFIXES


# ============================================================
# 回归保护:防止有人去掉白名单
# ============================================================


class TestNoWhitelistRegression:
    """防止未来回退到无白名单的 importlib.import_module"""

    def test_api_py_uses_safe_import(self):
        """src/web/routes/api.py 应全部用 safe_import_strategy,没有裸 importlib"""
        path = _PROJECT_ROOT / "src" / "web" / "routes" / "api.py"
        src = path.read_text(encoding="utf-8")
        # 文件中不应该有 'import importlib'
        assert "import importlib" not in src
        # 应该有 safe_import_strategy 的调用
        assert src.count("safe_import_strategy(") >= 4
