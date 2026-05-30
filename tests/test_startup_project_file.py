import os
import tempfile

from modules.app import PhoneticsApp


def test_启动参数接受带引号工程路径():
    fd, path = tempfile.mkstemp(suffix=".teproj")
    os.close(fd)
    try:
        found = PhoneticsApp._find_startup_project_file([f'"{path}"'])
        assert found == os.path.normpath(path)
    finally:
        os.remove(path)


def test_启动参数规范化_windows_file_uri():
    files = PhoneticsApp._normalize_startup_files(["file:///C:/Users/Sager/Desktop/project.teproj"])
    assert files == [os.path.normpath("C:/Users/Sager/Desktop/project.teproj")]
