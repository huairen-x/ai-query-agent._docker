"""pytest 全局配置：在导入被测模块之前固定环境变量。

模块级单例（GLOBAL_DB_MANAGER / GLOBAL_*_CACHE / GLOBAL_HEADROOM）与数据目录
在导入期求值，因此环境变量必须在任何业务模块被 import 之前设置好。
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="ai-query-test-")
os.environ["AGENT_DATA_DIR"] = _TMP
os.environ["CACHE_CLEANUP_INTERVAL_SEC"] = "0"
os.environ["HEADROOM_ENABLED"] = "false"
os.environ["HEADROOM_REPORT_ENABLED"] = "false"
os.environ["MCP_MAX_BODY_BYTES"] = "4096"
