from django.core.management.base import BaseCommand, CommandError

from quiz.question_banks import (
    QuestionBankImportConflict,
    QuestionBankValidationError,
    import_question_bank,
)


class Command(BaseCommand):
    help = "Validate, atomically import, and finalize a question-bank directory."

    def add_arguments(self, parser) -> None:
        parser.add_argument("package_dir")

    def handle(self, *args, **options) -> None:
        try:
            result = import_question_bank(options["package_dir"])
        except (QuestionBankValidationError, QuestionBankImportConflict) as exc:
            raise CommandError(str(exc)) from exc
        action = "imported" if result.created else "already imported"
        self.stdout.write(self.style.SUCCESS(f"{action}: {result.version.version_code}"))
