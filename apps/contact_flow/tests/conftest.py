import pytest

from apps.contact_flow.cache_utils import bump_config_cache_version


@pytest.fixture(autouse=True)
def _isolate_config_cache():
    """Cache danh mục config là versioned-bust theo ĐƯỜNG GHI API; fixture test
    dựng Stage/Result bằng ORM thẳng nên không bump — bump đầu mỗi test để test
    không đọc nhầm cache của test trước (cùng lớp bẫy REDIS_CACHE_DB dùng chung).
    """
    bump_config_cache_version()
