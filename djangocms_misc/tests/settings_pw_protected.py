from .settings import *  # noqa


MIDDLEWARE += [  # noqa: F405
    "djangocms_misc.basic.middleware.PasswordProtectedMiddleware",
]
