from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update one non-superuser account for the on-site quiz console."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")

    def handle(self, *args, **options) -> None:
        username = options["username"].strip()
        if not username:
            raise CommandError("username must not be empty")
        user_model = get_user_model()
        operator, created = user_model.objects.get_or_create(
            username=username,
            defaults={"is_staff": True, "is_superuser": False},
        )
        if operator.is_superuser:
            raise CommandError("Refusing to repurpose a superuser as a quiz operator.")
        if not operator.is_staff:
            operator.is_staff = True
            operator.save(update_fields=("is_staff",))
        if created:
            operator.set_unusable_password()
            operator.save(update_fields=("password",))
        permission = Permission.objects.get(
            content_type__app_label="quiz",
            codename="operate_quiz",
        )
        operator.user_permissions.add(permission)
        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} quiz operator {username}; set a password with changepassword"
            )
        )
