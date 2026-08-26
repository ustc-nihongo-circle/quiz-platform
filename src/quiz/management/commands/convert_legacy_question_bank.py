from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from quiz.question_banks import QuestionBankValidationError, convert_legacy_question_bank


def _private_path(value: str) -> Path:
    path = Path(value).resolve()
    if "_private" not in path.parts:
        raise CommandError("legacy conversion output must be below an _private directory")
    return path


class Command(BaseCommand):
    help = "Convert the 2024 workbook to a private, pending-review v1 bank package."

    def add_arguments(self, parser) -> None:
        parser.add_argument("source_workbook")
        parser.add_argument("output_dir")
        parser.add_argument("--assets-source-dir")
        parser.add_argument("--bank-version", default="legacy-2024-converted")

    def handle(self, *args, **options) -> None:
        output = _private_path(options["output_dir"])
        try:
            report = convert_legacy_question_bank(
                options["source_workbook"],
                output,
                assets_source_dir=options["assets_source_dir"],
                version=options["bank_version"],
            )
        except (FileNotFoundError, FileExistsError, QuestionBankValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"converted questions={report.question_count} assets={report.asset_count} "
                f"warnings={len(report.warnings)} output={report.package_dir}"
            )
        )
