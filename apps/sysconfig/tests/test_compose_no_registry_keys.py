"""Đối chứng compose ↔ registry: compose KHÔNG được inject khóa đã chuyển vào DB.

Nếu một overlay còn dòng `KEY: ${KEY:-default}` cho khóa registry thì container luôn
thấy env đó → guard assert_no_registry_env_leftovers không bao giờ qua được (review
cuối 2026-09-21 F1). Test đọc thẳng 3 file YAML nên thêm khóa vào registry mà quên
gỡ compose cũng đỏ.
"""

from pathlib import Path

import pytest
import yaml

from apps.sysconfig import registry

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.staging.yml", "docker-compose.local.yml")

# Khóa đã bỏ khỏi env vì chuyển vào bảng riêng (BestExpressAccount, CnvConnection) hoặc
# tự suy ra (KIOTVIET_WEBHOOK_SECRET_B64) — compose cũng không được inject nữa.
LEGACY_REMOVED = frozenset({
    "KIOTVIET_WEBHOOK_SECRET_B64",
    "BEST_EXPRESS_USERNAME", "BEST_EXPRESS_PASSWORD", "BEST_EXPRESS_EMS_KEY", "BEST_EXPRESS_ENVIRONMENT",
    "CNV_SSO_CLIENT_ID", "CNV_SSO_CLIENT_SECRET",
})


def _env_keys(env) -> set[str]:
    """`environment:` có 2 dạng: mapping {K: V} hoặc list ["K=V", "K"]."""
    if env is None:
        return set()
    if isinstance(env, dict):
        return {str(k) for k in env}
    if isinstance(env, list):
        return {str(item).split("=", 1)[0] for item in env}
    raise AssertionError(f"environment có dạng lạ: {type(env).__name__}")


def _collect_env_keys(doc: dict) -> dict[str, set[str]]:
    """Trả về {tên khối: set khóa} cho mọi khối environment: x-* anchor + từng service."""
    found: dict[str, set[str]] = {}
    for name, value in doc.items():
        if str(name).startswith("x-") and isinstance(value, dict):
            # Anchor env là mapping phẳng (x-backend-env); anchor khác (x-backend-build) bỏ qua
            # trừ khi nó có `environment` bên trong.
            if "environment" in value:
                found[name] = _env_keys(value["environment"])
            elif all(not isinstance(v, (dict, list)) for v in value.values()):
                found[name] = {str(k) for k in value}
    for svc, spec in (doc.get("services") or {}).items():
        if isinstance(spec, dict) and "environment" in spec:
            found[f"services.{svc}"] = _env_keys(spec["environment"])
    return found


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_compose_does_not_inject_registry_or_legacy_keys(filename):
    path = REPO_ROOT / filename
    assert path.exists(), path
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    blocks = _collect_env_keys(doc)
    assert "x-backend-env" in blocks, f"{filename}: không thấy anchor x-backend-env"
    forbidden = set(registry.BY_KEY) | LEGACY_REMOVED
    offenders = {
        block: sorted(keys & forbidden) for block, keys in blocks.items() if keys & forbidden
    }
    assert not offenders, f"{filename} còn inject khóa đã chuyển vào DB: {offenders}"


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_compose_passes_through_allow_env_keys_flag(filename):
    """Cửa thoát deploy chuyển đổi phải tới được container (anchor là whitelist)."""
    doc = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
    assert "SYSCONFIG_ALLOW_ENV_KEYS" in _collect_env_keys(doc)["x-backend-env"], filename
