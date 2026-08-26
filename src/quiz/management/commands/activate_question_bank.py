from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from quiz.models import ActivityEdition, QuestionBankVersion
from quiz.services import ActivityMustBePaused, activate_question_bank


class Command(BaseCommand):
    help = "Activate a finalized question-bank version for an activity edition."

    def add_arguments(self, parser) -> None:
        parser.add_argument("activity_slug")
        parser.add_argument("bank_version")
        parser.add_argument("--actor", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--legacy-exception", action="store_true")

    def handle(self, *args, **options) -> None:
        user_model = get_user_model()
        try:
            activity = ActivityEdition.objects.get(slug=options["activity_slug"])
            bank = QuestionBankVersion.objects.get(version_code=options["bank_version"])
            actor = user_model.objects.get(username=options["actor"])
            activation = activate_question_bank(
                activity=activity,
                bank=bank,
                actor=actor,
                reason=options["reason"],
                legacy_exception=options["legacy_exception"],
            )
        except (ActivityEdition.DoesNotExist, QuestionBankVersion.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        except user_model.DoesNotExist as error:
            raise CommandError("The approving administrator does not exist.") from error
        except ActivityMustBePaused as error:
            raise CommandError(str(error)) from error
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"activated activity={activation.activity.slug} "
                f"bank={activation.bank.version_code} "
                f"legacy_exception={activation.legacy_exception}"
            )
        )
