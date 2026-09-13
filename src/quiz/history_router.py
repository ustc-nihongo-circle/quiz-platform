from django.core.exceptions import PermissionDenied


class HistoryRouter:
    """The historical database is an existing, read-only data source."""

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return False if db == "history" else None

    def db_for_write(self, model, **hints):
        instance = hints.get("instance")
        if instance is not None and instance._state.db == "history":
            raise PermissionDenied("测试历史记录只读。")
        return None

    def allow_relation(self, obj1, obj2, **hints):
        databases = {obj1._state.db, obj2._state.db}
        if "history" in databases and len(databases) > 1:
            return False
        return None
