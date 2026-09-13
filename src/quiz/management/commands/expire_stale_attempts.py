from django.core.management.base import BaseCommand, CommandError

from quiz.models import ActivityEdition
from quiz.services import expire_stale_attempts


class Command(BaseCommand):
    help = "Expire in-progress answer sessions started at least 30 minutes ago."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--activity", help="Limit expiry to one activity slug.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options) -> None:
        activity = None
        if options["activity"]:
            try:
                activity = ActivityEdition.objects.get(slug=options["activity"])
            except ActivityEdition.DoesNotExist as error:
                raise CommandError("The activity edition does not exist.") from error
        count = expire_stale_attempts(activity=activity, dry_run=options["dry_run"])
        label = "eligible" if options["dry_run"] else "expired"
        self.stdout.write(f"{label}={count} max_age_minutes=30")
