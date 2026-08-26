from django.core.management.base import BaseCommand, CommandError

from quiz.question_banks import QuestionBankValidationError, validate_question_bank


class Command(BaseCommand):
    help = "Validate a versioned question-bank directory without writing to the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("package_dir")

    def handle(self, *args, **options) -> None:
        try:
            bank = validate_question_bank(options["package_dir"])
        except QuestionBankValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"valid bank={bank.version} questions={len(bank.questions)} "
                f"rules={len(bank.sampling_rules)} hash={bank.content_hash}"
            )
        )
