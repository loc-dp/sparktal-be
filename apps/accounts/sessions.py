from importlib import import_module

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.utils import timezone

from apps.accounts.models import User


def end_sessions(user: User) -> int:
    """Delete every live session signed in as `user`, and return how many there were.

    Switching an account off is not enough by itself: Django treats the session as anonymous
    while the user is inactive but keeps it, so switching the account back on would bring it
    back. Django keeps no index from a user to their sessions, so this decodes every unexpired
    session in the session table: for a rare staff action such as locking an account, never per
    request. Works with the `db` and `cached_db` session engines.
    """
    store = import_module(settings.SESSION_ENGINE).SessionStore
    live = store.get_model_class().objects.filter(expire_date__gt=timezone.now())
    keys = [
        session.session_key
        for session in live.iterator()
        if session.get_decoded().get(SESSION_KEY) == str(user.pk)
    ]
    for key in keys:
        store(session_key=key).delete()
    return len(keys)
