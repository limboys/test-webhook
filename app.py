#!/usr/bin/python3
# -*- coding: utf-8 -*-

import asyncio
from contextlib import asynccontextmanager

import sentry_sdk
import stripe
from dutils.database.url import make_url
from dutils.log import logger
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles
from tortoise import Tortoise

from src import dao
from src.common.constants import PROTOTYPE_COVER_STATIC_ROUTE
from src.common.util import log as log_util
from src.common.util import pool as pool_util
from src.common.util.moderation import Moderation
from src.conf import Conf_Manager
from src.dao.es.conn import ESManager
from src.grpc.mgx_callback_server import start_callback_grpc_server
from src.http.fastapi.forword.proxy_azure import add_azure_proxy_router
from src.http.fastapi.forword.proxy_serper import add_serper_proxy_router
from src.http.fastapi.forword.proxy_unsplash import add_unsplash_proxy_router
from src.http.fastapi.handler.base import register_exception_handler
from src.http.fastapi.middleware import register_middlewares
from src.http.fastapi.router import api_router
from src.http.fastapi.router.callback import add_callback_router
from src.http.fastapi.router.swagger import add_swagger_router
from src.http.fastapi.router.websocket import add_socketio_router
from src.tiktoken import init_tiktoken

_grpc_servers = []
_mgx_queue_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="MetaGPT X",
    middleware=register_middlewares(),  # web中间件
    exception_handlers=register_exception_handler(),  # 错误处理
)


async def init_setup():
    """初始化项目配置"""

    log_util.setup_logging()

    pool_util.set_default_executor()
    await asyncio.gather(dao.init_orm(), dao.init_lock(), dao.init_cache(), dao.init_es(), init_tiktoken())
    stripe.api_key = Conf_Manager.stripe.secret_key

    log_util.setup_tortoise_orm_logging_debug()
    _try_init_sentry()


def _try_init_sentry():
    if Conf_Manager.sentry.enable:
        sentry_sdk.init(**Conf_Manager.sentry.model_dump(exclude={"enable"}))


async def startup():
    """项目启动时准备环境"""

    await init_setup()

    # 加载路由
    app.include_router(api_router, prefix="/api")
    add_callback_router(app)

    if Conf_Manager.forward.serper:
        add_serper_proxy_router(app, **Conf_Manager.forward.serper)

    if Conf_Manager.forward.azure:
        add_azure_proxy_router(app, **Conf_Manager.forward.azure)

    if Conf_Manager.forward.unsplash:
        add_unsplash_proxy_router(app, **Conf_Manager.forward.unsplash)

    mgx_callback_grpc_conf = Conf_Manager.grpc.mgx_callback
    if mgx_callback_grpc_conf.enable:
        _grpc_servers.append(await start_callback_grpc_server(mgx_callback_grpc_conf.host, mgx_callback_grpc_conf.port))

    # app.mount(
    #     "/static",
    #     StaticFiles(directory=Conf_Manager.storage.static_dir, check_dir=Conf_Manager.storage.check_static),
    #     name="static",
    # )

    app.mount(
        PROTOTYPE_COVER_STATIC_ROUTE,
        StaticFiles(**Conf_Manager.storage.static.template_cover.model_dump()),
        name="static",
    )

    # socketio路由要在最后

    redis_url = make_url("redis", **Conf_Manager.redis.model_dump(exclude=("db",)), name=Conf_Manager.redis.db)
    add_socketio_router(app, redis_url=redis_url, **Conf_Manager.server.socketio.model_dump())
    if Conf_Manager.server.develop:
        add_swagger_router(app)

    await Moderation.init_keyword_processor()

    if Conf_Manager.server.mgx_queue:
        # 本地模式
        from src.script.mgx_queue import start as start_mgx_queue

        global _mgx_queue_task
        _mgx_queue_task = asyncio.create_task(start_mgx_queue(**Conf_Manager.mgx_queue.server))

    logger.info("fastapi startup success")


async def shutdown():
    # 关闭定时任务
    # 关闭orm
    await asyncio.gather(
        Tortoise.close_connections(),
        ESManager.close(),
    )
    for i in _grpc_servers:
        await i.stop(True)

    if _mgx_queue_task:
        _mgx_queue_task.cancel()
    logger.info("app shutdown")
