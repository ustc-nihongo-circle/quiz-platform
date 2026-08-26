from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from quiz.models import ActivityEdition, ActivityStatus
from quiz.services import transition_activity


class Command(BaseCommand):
    help = "Apply an audited activity status transition."

    def add_arguments(self, parser) -> None:
        parser.add_argument("activity_slug")
        parser.add_argument("status", choices=ActivityStatus.values)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **options) -> None:
        user_model = get_user_model()
        try:
            activity = ActivityEdition.objects.get(slug=options["activity_slug"])
            actor = user_model.objects.get(username=options["actor"])
            activity = transition_activity(
                activity=activity,
                next_status=options["status"],
                actor=actor,
                reason=options["reason"],
            )
        except ActivityEdition.DoesNotExist as error:
            raise CommandError("The activity edition does not exist.") from error
        except user_model.DoesNotExist as error:
            raise CommandError("The administrator does not exist.") from error
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        self.stdout.write(
            self.style.SUCCESS(f"activity={activity.slug} status={activity.status}")
        )
