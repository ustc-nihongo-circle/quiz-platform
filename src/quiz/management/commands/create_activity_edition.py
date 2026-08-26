from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from quiz.models import (
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    PoolQuota,
    QuestionBankVersion,
)
from quiz.services import activate_question_bank, transition_activity

LEGACY_CATEGORY_TITLES = {
    "history": "历史",
    "geography": "地理铁路",
    "yakyuu": "棒球",
    "horse": "赛马",
    "japanese": "日语知识",
    "ACG": "ACG",
    "mahjong": "麻将",
}


class Command(BaseCommand):
    help = "Create an activity edition, copy bank categories, and activate the bank."

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug")
        parser.add_argument("title")
        parser.add_argument("bank_version")
        parser.add_argument("--actor", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--question-count", type=int, default=15)
        parser.add_argument("--time-limit", type=int, default=300)
        parser.add_argument("--category-title", action="append", default=[])
        parser.add_argument("--legacy-exception", action="store_true")
        parser.add_argument("--open", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if options["question_count"] < 1 or options["time_limit"] < 1:
            raise CommandError("question-count and time-limit must be positive")
        title_overrides = dict(LEGACY_CATEGORY_TITLES)
        for value in options["category_title"]:
            if "=" not in value:
                raise CommandError("category-title must use CODE=TITLE")
            code, title = value.split("=", 1)
            if not code.strip() or not title.strip():
                raise CommandError("category-title must use non-empty CODE=TITLE")
            title_overrides[code.strip()] = title.strip()
        user_model = get_user_model()
        try:
            bank = QuestionBankVersion.objects.get(version_code=options["bank_version"])
            actor = user_model.objects.get(username=options["actor"])
        except QuestionBankVersion.DoesNotExist as error:
            raise CommandError("The question-bank version does not exist.") from error
        except user_model.DoesNotExist as error:
            raise CommandError("The approving administrator does not exist.") from error
        if ActivityEdition.objects.filter(slug=options["slug"]).exists():
            raise CommandError("The activity slug already exists.")
        activity = ActivityEdition.objects.create(
            slug=options["slug"],
            title=options["title"],
            default_question_count=options["question_count"],
            default_time_limit_seconds=options["time_limit"],
        )
        categories: dict[str, list] = {}
        for rule in bank.sampling_rules.all():
            categories.setdefault(rule.category_key, []).append(rule)
        if not categories:
            raise CommandError("The question-bank version has no sampling rules.")
        for display_order, (category_code, rules) in enumerate(categories.items(), start=1):
            total = sum(rule.default_quota for rule in rules)
            category = ActivityCategoryConfig.objects.create(
                activity=activity,
                category_key=category_code,
                title=title_overrides.get(category_code, category_code),
                question_count=(total if total != activity.default_question_count else None),
                display_order=display_order,
            )
            if category.question_count is not None:
                PoolQuota.objects.bulk_create(
                    [
                        PoolQuota(
                            category_config=category,
                            pool_key=rule.pool_key,
                            quota=rule.default_quota,
                            display_order=rule.display_order,
                        )
                        for rule in rules
                    ]
                )
        try:
            activate_question_bank(
                activity=activity,
                bank=bank,
                actor=actor,
                reason=options["reason"],
                legacy_exception=options["legacy_exception"],
            )
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        if options["open"]:
            activity = transition_activity(
                activity=activity,
                next_status=ActivityStatus.OPEN,
                actor=actor,
                reason="创建活动时开放入口",
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"created activity={activity.slug} categories={len(categories)} "
                f"status={activity.status}"
            )
        )
