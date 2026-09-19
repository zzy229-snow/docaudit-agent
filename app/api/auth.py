"""RBAC 与对象级鉴权(PRD §2 角色 / §15 访问控制 / AC-09)。

模式由环境变量 ``AUTH_MODE`` 控制:

- ``off``(默认):本地演示与单元测试不校验身份,保持既有调用契约;
- ``enforce``:要求身份头,按角色白名单与对象级归属校验。

身份头(真实部署应替换为 SSO/JWT 校验,这里保留同样的接口形状):

| 头 | 说明 |
| --- | --- |
| ``X-User-Id`` | 用户标识(与任务 applicant 对应) |
| ``X-Role`` | 角色:APPLICANT / REVIEWER / POLICY_ADMIN / ADMIN / DEVELOPER |
| ``X-Department`` | 可选,部门(用于制度部门过滤与数据范围) |

角色权限(PRD §2):

| 角色 | 权限边界 |
| --- | --- |
| APPLICANT | 只能访问本人任务;越权访问返回 404 且不泄露任务是否存在(AC-09) |
| REVIEWER | 可访问授权范围内任务、修正字段、提交复核结论(留痕) |
| POLICY_ADMIN | 制度上传/发布/停用 |
| ADMIN | 全部权限 |
| DEVELOPER | 评测与轨迹查看 |

被拒绝的访问会写入任务审计事件(``ACCESS_DENIED``),用于事后追溯。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROLE_APPLICANT = "APPLICANT"
ROLE_REVIEWER = "REVIEWER"
ROLE_POLICY_ADMIN = "POLICY_ADMIN"
ROLE_ADMIN = "ADMIN"
ROLE_DEVELOPER = "DEVELOPER"

ALL_ROLES = frozenset({ROLE_APPLICANT, ROLE_REVIEWER, ROLE_POLICY_ADMIN, ROLE_ADMIN, ROLE_DEVELOPER})

ENV_AUTH_MODE = "AUTH_MODE"
MODE_OFF = "off"
MODE_ENFORCE = "enforce"

#: 免鉴权路径(健康检查与接口文档)
PUBLIC_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

#: 路径前缀 -> 允许的角色(未匹配到规则时按"任意已认证角色"处理)
PATH_RULES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"^/api/v1/policies"), frozenset({ROLE_POLICY_ADMIN, ROLE_ADMIN})),
    (re.compile(r"^/api/v1/evaluations"), frozenset({ROLE_DEVELOPER, ROLE_ADMIN})),
    (re.compile(r"^/api/v1/review-items/.+/decision$"), frozenset({ROLE_REVIEWER, ROLE_ADMIN})),
)

#: 任务级路径:提取 task_id 做对象级鉴权
_TASK_PATH_RE = re.compile(r"^/api/v1/audit-tasks/(?P<task_id>[^/]+)")


@dataclass(frozen=True)
class User:
    user_id: str
    role: str
    department: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def auth_mode() -> str:
    mode = os.environ.get(ENV_AUTH_MODE, MODE_OFF).strip().lower()
    return mode if mode in {MODE_OFF, MODE_ENFORCE} else MODE_OFF


def user_from_headers(headers) -> User | None:
    user_id = (headers.get("X-User-Id") or "").strip()
    role = (headers.get("X-Role") or "").strip().upper()
    department = (headers.get("X-Department") or "").strip()
    if not user_id or role not in ALL_ROLES:
        return None
    return User(user_id=user_id, role=role, department=department)


def required_roles(path: str, method: str) -> frozenset[str] | None:
    for pattern, roles in PATH_RULES:
        if pattern.match(path):
            return roles
    return None


def task_id_from_path(path: str) -> str | None:
    match = _TASK_PATH_RE.match(path)
    return match.group("task_id") if match else None


def _deny(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def install_auth(app: FastAPI) -> None:
    """挂载鉴权中间件(AUTH_MODE=enforce 时生效)。"""

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        path = request.url.path
        if auth_mode() != MODE_ENFORCE or path in PUBLIC_PATHS or not path.startswith("/api/"):
            request.state.user = None
            return await call_next(request)

        user = user_from_headers(request.headers)
        if user is None:
            return _deny(401, "缺少有效的 X-User-Id / X-Role 身份头")

        allowed = required_roles(path, request.method)
        if allowed is not None and user.role not in allowed:
            return _deny(403, f"角色 {user.role} 无权访问该接口")

        task_id = task_id_from_path(path)
        if task_id and user.role == ROLE_APPLICANT:
            from app.api.store import task_store

            task = task_store.get_task(task_id)
            if task is None:
                return _deny(404, "Audit task not found")
            if task.applicant and task.applicant != user.user_id:
                _log_denied_access(task_id, user)
                # AC-09:不泄露任务是否存在
                return _deny(404, "Audit task not found")

        request.state.user = user
        return await call_next(request)


def _log_denied_access(task_id: str, user: User) -> None:
    from app.api.store import task_store

    try:
        task_store.log_event(
            task_id,
            "ACCESS_DENIED",
            f"用户 {user.user_id}（{user.role}）尝试访问非本人任务",
        )
    except Exception:  # noqa: BLE001 — 审计写失败不应改变鉴权结果
        pass
