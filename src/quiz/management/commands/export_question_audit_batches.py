from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from quiz.question_banks import QuestionBankValidationError, export_question_audit_batches


def _private_path(value: str) -> Path:
    path = Path(value).resolve()
    if "_private" not in path.parts:
        raise CommandError("question audit output must be below an _private directory")
    return path


class Command(BaseCommand):
    help = "Export private category/pool batches for evidence and answer review."

    def add_arguments(self, parser) -> None:
        parser.add_argument("package_dir")
        parser.add_argument("output_dir")

    def handle(self, *args, **options) -> None:
        output = _private_path(options["output_dir"])
        try:
            batches = export_question_audit_batches(options["package_dir"], output)
        except (FileExistsError, QuestionBankValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"exported batches={len(batches)} output={output}")
        )
