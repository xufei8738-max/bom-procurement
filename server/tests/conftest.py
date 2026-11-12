"""pytest 共享配置：所有测试用独立临时数据目录，互不污染真实库。"""
import os
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="bom-proc-test-")
os.environ["PROC_DATA_DIR"] = _TEST_DATA_DIR

from server import db  # noqa: E402  (import 依赖 PROC_DATA_DIR，必须先设 env)

db.init_db()
