from handlers.admin import router as admin_router
from handlers.analytics import router as analytics_router
from handlers.info import router as info_router
from handlers.locks import router as locks_router
from handlers.moderation import router as moderation_router
from handlers.protection import router as protection_router
from handlers.settings import router as settings_router
from handlers.welcome import router as welcome_router

__all__ = [
    "admin_router",
    "analytics_router",
    "info_router",
    "locks_router",
    "moderation_router",
    "protection_router",
    "settings_router",
    "welcome_router",
]
