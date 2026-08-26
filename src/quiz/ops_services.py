import csv
import uuid

from django.core.exceptions import ValidationError

from .identity import IdentityProtector, normalize_contact, normalize_identifier
from .models import ActivityEdition, AdminAuditLog, CategoryHighScore, Participant, QuizAttempt

PARTICIPANT_PAGE_SIZE = 50


class CsvEcho:
    def write(self, value: str) -> str:
        return value


def safe_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def mask_identifier(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def mask_contact(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if value.startswith("+86") and len(value) >= 7:
        return f"+86{'*' * (len(value) - 7)}{value[-4:]}"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def search_participants(
    *,
    activity: ActivityEdition,
    display_name: str = "",
    identifier: str = "",
    contact: str = "",
    participant_id: str = "",
) -> list[dict[str, object]]:
    identities = (
        Participant.objects.filter(activity=activity, identity__isnull=False)
        .select_related("identity")
        .order_by("identity__display_name", "created_at", "pk")
    )
    protector = IdentityProtector.from_environment()
    if display_name.strip():
        identities = identities.filter(identity__display_name__icontains=display_name.strip())
    if identifier.strip():
        identifier_digest = protector.digest(
            normalize_identifier(identifier),
            activity_id=activity.pk,
            field="identifier",
        )
        identities = identities.filter(identity__identifier_digest=identifier_digest)
    if contact.strip():
        contact_digest = protector.digest(
            normalize_contact(contact),
            activity_id=activity.pk,
            field="contact",
        )
        identities = identities.filter(identity__contact_digest=contact_digest)
    if participant_id.strip():
        try:
            normalized_participant_id = uuid.UUID(participant_id.strip())
        except ValueError:
            return []
        identities = identities.filter(pk=normalized_participant_id)
    rows = []
    for participant in identities[:PARTICIPANT_PAGE_SIZE]:
        identity = participant.identity
        identifier = protector.decrypt(
            identity.identifier_envelope,
            context=f"participant:{participant.pk}:identifier",
        )
        contact = protector.decrypt(
            identity.contact_envelope,
            context=f"participant:{participant.pk}:contact",
        )
        rows.append(
            {
                "id": str(participant.pk),
                "display_name": identity.display_name,
                "identifier": mask_identifier(identifier),
                "contact": mask_contact(contact),
                "contact_type": identity.contact_type,
                "anonymized": False,
            }
        )
    return rows


def reveal_participant_identities(
    *,
    activity: ActivityEdition,
    participant_ids: list[str],
    fields: list[str],
    actor,
) -> dict[str, dict[str, str]]:
    requested_fields = sorted(set(fields) & {"identifier", "contact"})
    if not requested_fields:
        raise ValidationError("至少选择一个身份字段。")
    unique_ids = list(dict.fromkeys(participant_ids))
    if not unique_ids or len(unique_ids) > PARTICIPANT_PAGE_SIZE:
        raise ValidationError("一次只能查看当前页的参与者身份字段。")
    participants = list(
        Participant.objects.filter(
            activity=activity,
            pk__in=unique_ids,
            identity__isnull=False,
        )
        .select_related("identity")
        .order_by("pk")
    )
    protector = IdentityProtector.from_environment()
    result: dict[str, dict[str, str]] = {}
    for participant in participants:
        identity = participant.identity
        row: dict[str, str] = {}
        if "identifier" in requested_fields:
            row["identifier"] = protector.decrypt(
                identity.identifier_envelope,
                context=f"participant:{participant.pk}:identifier",
            )
        if "contact" in requested_fields:
            row["contact"] = protector.decrypt(
                identity.contact_envelope,
                context=f"participant:{participant.pk}:contact",
            )
        result[str(participant.pk)] = row
    AdminAuditLog.objects.create(
        actor=actor,
        activity=activity,
        action="participant_identity_revealed",
        reason="管理员在当前页面切换完整身份字段。",
        metadata={"fields": requested_fields, "row_count": len(result)},
    )
    return result


def identity_export_rows(
    *,
    activity: ActivityEdition,
    actor,
    reason: str,
):
    reason = reason.strip()
    if not reason:
        raise ValidationError("导出身份资料必须填写用途说明。")
    participants = (
        Participant.objects.filter(activity=activity, identity__isnull=False)
        .select_related("identity")
        .order_by("created_at", "pk")
    )
    row_count = participants.count()
    AdminAuditLog.objects.create(
        actor=actor,
        activity=activity,
        action="participant_identity_exported",
        reason=reason,
        metadata={"row_count": row_count},
    )
    protector = IdentityProtector.from_environment()
    writer = csv.writer(CsvEcho(), lineterminator="\r\n")

    def rows():
        yield "\ufeff"
        yield writer.writerow(
            ("participant_id", "display_name", "identifier", "contact_type", "contact")
        )
        for participant in participants.iterator(chunk_size=100):
            identity = participant.identity
            identifier = protector.decrypt(
                identity.identifier_envelope,
                context=f"participant:{participant.pk}:identifier",
            )
            contact = protector.decrypt(
                identity.contact_envelope,
                context=f"participant:{participant.pk}:contact",
            )
            yield writer.writerow(
                tuple(
                    safe_csv_cell(value)
                    for value in (
                        participant.pk,
                        identity.display_name,
                        identifier,
                        identity.contact_type,
                        contact,
                    )
                )
            )

    return rows()


def statistics_export_rows(*, activity: ActivityEdition, actor):
    attempts = (
        QuizAttempt.objects.filter(activity=activity)
        .select_related("category_config")
        .order_by("started_at", "pk")
    )
    row_count = attempts.count()
    high_scores = {
        (participant_id, category_id): score
        for participant_id, category_id, score in CategoryHighScore.objects.filter(
            activity=activity
        ).values_list("participant_id", "category_config_id", "score")
    }
    AdminAuditLog.objects.create(
        actor=actor,
        activity=activity,
        action="participant_statistics_exported",
        reason="管理员导出无身份答题统计。",
        metadata={"row_count": row_count},
    )
    writer = csv.writer(CsvEcho(), lineterminator="\r\n")

    def rows():
        yield "\ufeff"
        yield writer.writerow(
            (
                "participant_id",
                "attempt_id",
                "category",
                "status",
                "score",
                "question_count",
                "started_at",
                "submitted_at",
                "category_high_score",
            )
        )
        for attempt in attempts.iterator(chunk_size=100):
            yield writer.writerow(
                (
                    attempt.participant_id,
                    attempt.pk,
                    attempt.category_config.category_key,
                    attempt.status,
                    attempt.score,
                    attempt.question_count,
                    attempt.started_at.isoformat(),
                    attempt.submitted_at.isoformat() if attempt.submitted_at else "",
                    high_scores.get(
                        (attempt.participant_id, attempt.category_config_id),
                        "",
                    ),
                )
            )

    return rows()


def leaderboard_rows(*, activity: ActivityEdition, category_key: str) -> list[dict[str, object]]:
    scores = (
        CategoryHighScore.objects.filter(
            activity=activity,
            category_config__category_key=category_key,
        )
        .select_related("participant__identity")
        .order_by("-score", "achieved_at", "pk")
    )
    rows = []
    rank = 0
    previous_score = None
    for score in scores:
        if score.score != previous_score:
            rank += 1
            previous_score = score.score
        try:
            display_name = score.participant.identity.display_name
        except Participant.identity.RelatedObjectDoesNotExist:
            display_name = "已去身份化"
        rows.append(
            {
                "rank": rank,
                "participant_id": str(score.participant_id),
                "display_name": display_name,
                "score": score.score,
                "achieved_at": score.achieved_at.isoformat().replace("+00:00", "Z"),
            }
        )
    return rows
