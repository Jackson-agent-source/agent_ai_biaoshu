"""Agent 可以调用的工具集合。V1 只有两个：web_search 和 read_webpage。"""

from apps.agent.tools.read_webpage import read_webpage
from apps.agent.tools.web_search import web_search

__all__ = ["web_search", "read_webpage"]
