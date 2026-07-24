"""Repository 层异常定义。"""


class RepoError(Exception):
    """Repository 层基础异常。"""
    pass


class ProjectNotFoundError(RepoError):
    """项目不存在的异常。"""
    pass


class ConfigCorruptedError(RepoError):
    """配置文件损坏的异常。"""
    pass


class AtomicWriteError(RepoError):
    """原子写入失败的异常。"""
    pass
