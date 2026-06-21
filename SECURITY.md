# 安全策略

> 最后更新: 2026-06-21

## 支持的版本

| 版本 | 支持状态 |
|------|---------|
| develop (最新) | ✅ 积极维护 |
| main (主分支) | ✅ 安全更新 |
| 其他分支 | ⚠️ 自行评估 |

## 报告漏洞

**请勿在 GitHub Issue 公开披露安全漏洞。**

### 推荐渠道 (按优先级)

1. **GitHub Security Advisories**(首选)
   - 仓库 → Security → Advisories → New draft security advisory
   - https://github.com/QingSongTears/quant-trading-system/security/advisories/new

2. **Email**(备选)
   - 发邮件给维护者:见仓库 `CODEOWNERS` 或最近 commit 作者邮箱

### 报告内容

请包含:
- 漏洞类型(命令注入 / 反序列化 / XSS / ...)
- 受影响的文件:行号
- 复现步骤
- 潜在影响(数据泄露 / RCE / DoS / ...)
- 建议的修复方案(可选)

### 响应时间承诺

| 阶段 | 时间 |
|------|------|
| 初次确认 | 收到报告后 7 天内 |
| 严重漏洞修复 | 30 天内 |
| 中等漏洞修复 | 90 天内 |
| 低危漏洞 | 下一版本 |

## 已知已修复漏洞

### 2026-06-21 安全加固批次

| 编号 | 严重度 | 描述 | 修复 commit |
|------|--------|------|-------------|
| P0-1 | Critical | `westock.py` 命令注入 (subprocess shell=True + 字符串拼接) | `ef98b2d` |
| P0-2 | Critical | FastAPI:5050 完全无认证,监听 0.0.0.0 | `61b7d8a` |
| P0-2b | Critical | Flask param_server.py:8081 同样无认证 | `17bad42` |
| P0-3 | Critical | `xgb_scaler.pkl` pickle 反序列化 RCE | `d26580f` |
| P1-3 | High | `class_path` 任意 importlib 模块加载 | `61b7d8a` |

详见 commit log `git log --grep security`。

## 安全最佳实践 (部署)

### Web 服务

- **必设** `QUANT_API_KEY` 环境变量(不能依赖临时生成的 key)
  ```bash
  export QUANT_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
  ```
- **默认绑定 127.0.0.1**,公网访问用反向代理 (nginx) + HTTPS
- **必设** `QUANT_ALLOWED_HOSTS` 限制 host header
  ```bash
  export QUANT_ALLOWED_HOSTS="quant.example.com,api.example.com"
  ```

### 依赖管理

- `requirements.txt` 已 SHA-256 hash 锁定,安装必须用 `--require-hashes`
  ```bash
  pip install --require-hashes -r requirements.txt
  ```
- 重新生成: `pip-compile --generate-hashes -o requirements.txt requirements.in`
- 定期跑 `pip-audit`:
  ```bash
  pip-audit --strict -r requirements.txt
  ```

### 数据库

- 不要把 `database/quant.db` commit 到 git(.gitignore 已覆盖)
- 生产环境用 `--require-hashes` 装的 SQLAlchemy + WAL 模式
- 定期备份 `quant.db`(WAL 模式下有 `-wal` 和 `-shm` 三个文件)

### 配置文件

- `config/config.yaml` 不应包含密钥/Token
- 如必须,改用环境变量,在 `config.py` 中读取

## 安全相关文件

- `src/web/auth.py` — 认证 + class_path 白名单
- `src/data/westock.py` — 命令注入防护
- `src/data/xgb_scaler.py` — JSON 序列化(替代 pickle)
- `requirements.txt` — Hash 锁定的依赖
- `.github/dependabot.yml` — 自动依赖更新
- `.github/workflows/ci.yml` — pip-audit 安全扫描

## 致谢

报告漏洞并协助修复的贡献者将在 `CREDITS.md` 中署名(如适用)。
