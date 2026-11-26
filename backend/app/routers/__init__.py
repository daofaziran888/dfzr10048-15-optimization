"""业务路由汇总：缺片核对为固定路径需先于通用资源路由注册，其余模块共用一套资源路由。"""
from __future__ import annotations

from app.routers import missing_scan, resource

ROUTERS = [missing_scan, resource]
