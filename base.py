#!/usr/bin/python3
# -*- coding: utf-8 -*-
from http import HTTPStatus

from fastapi import Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.common.constants import API_ADMIN_LIST, API_AUTH_WHITE_LIST, API_USER_ACTIVTE_WHITE_LIST, AUTH_API_KEY_PREFIX
from src.common.enum.user import UserRole, UserStatus
from src.common.enum.web import ErrorCodeEnum
from src.common.util import web
from src.common.util.context import CUR_USER
from src.service.user import user_service

_api_auth_white_list = set(API_AUTH_WHITE_LIST)
_api_user_activte_white_list = set(API_USER_ACTIVTE_WHITE_LIST)
_api_admin_list = set(API_ADMIN_LIST)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件
    """

    @staticmethod
    def _auth_error(msg="", status_code=HTTPStatus.UNAUTHORIZED):
        """中间件如果使用raise AuthorizationErr()会有堆栈报错日志，需返回JSONResponse才不会"""
        return JSONResponse(
            status_code=status_code,
            content=web.fail_api_resp_with_err_enum(ErrorCodeEnum.AUTHORIZATION_ERR, err_msg=msg),
        )

    @staticmethod
    def _forbidden_error(msg="", status_code=HTTPStatus.FORBIDDEN):
        """中间件如果使用raise ForbiddenError()会有堆栈报错日志，需返回JSONResponse才不会"""
        return JSONResponse(
            status_code=status_code,
            content=web.fail_api_resp_with_err_enum(ErrorCodeEnum.FORBIDDEN_ERR, err_msg=msg),
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if not path.startswith("/api/v1/"):
            return await call_next(request)

        request_key = (request.method, path)
        if request_key in _api_auth_white_list or path.startswith("/api/v1/public/"):
            return await call_next(request)

        token = request.headers.get("Authorization") or request.query_params.get("token")
        if not token:
            return self._auth_error()
        if token.startswith(AUTH_API_KEY_PREFIX):
            token = token[len(AUTH_API_KEY_PREFIX) :]

        user = await user_service.jwt_auth_verify(token)
        if not user:
            return self._auth_error()

        # 管理员路由
        if (path.startswith("/api/v1/admin/") or request_key in _api_admin_list) and user.role is not UserRole.ADMIIN:
            return self._forbidden_error()

        # 未激活用户
        if user.status != UserStatus.ACTIVE and request_key not in _api_user_activte_white_list:
            return self._forbidden_error()

        # 把用户对象存到请求上下文中
        CUR_USER.set(user)
        request.scope["user"] = user

        return await call_next(request)


def register_middlewares():
    """注册中间件"""
    return [
        Middleware(AuthMiddleware),
    ]
