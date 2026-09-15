#!/usr/bin/env python
"""Django 命令行入口。

初学者最常用的三条命令：
    python manage.py migrate        创建/更新数据库表
    python manage.py createsuperuser 创建管理员账号
    python manage.py runserver      启动开发服务器
"""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "无法导入 Django。请确认你已经激活虚拟环境，并且执行过：pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
