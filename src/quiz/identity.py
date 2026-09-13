import base64
import hashlib
import hmac
import json
import os
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

import phonenumbers
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    ActivityEdition,
    ActivityStatus,
    AdminAuditLog,
    Participant,
    ParticipantIdentity,
)


class IdentityConfigurationError(ImproperlyConfigured):
    pass


class IdentityDecryptionError(ValueError):
    pass


class ParticipantRecoveryRequired(ValueError):
    pass


@dataclass(frozen=True)
class RegistrationResult:
    participant: Participant
    created: bool


@dataclass(frozen=True)
class RegistrationInput:
    display_name: str
    identifier: str
    contact: str


def validate_registration(*, display_name, identifier, contact) -> RegistrationInput:
    """Bound untrusted inputs before normalization, hashing or encryption."""
    values = {"display_name": display_name, "identifier": identifier, "contact": contact}
    raw_limits = {"display_name": 400, "identifier": 128, "contact": 512}
    limits = {"display_name": 100, "identifier": 64, "contact": 254}
    labels = {"display_name": "显示名", "identifier": "学号或工号", "contact": "联系方式"}
    errors = {}
    for field, value in values.items():
        if not isinstance(value, str):
            errors[field] = [f"{labels[field]}必须是文本。"]
        elif len(value) > raw_limits[field]:
            errors[field] = [f"{labels[field]}输入过长。"]
        elif "\x00" in value or any(unicodedata.category(c) == "Cs" for c in value):
            errors[field] = [f"{labels[field]}含有无效字符。"]
    if errors:
        raise ValidationError(errors)
    normalizers = {
        "display_name": lambda value: unicodedata.normalize("NFKC", value).strip(),
        "identifier": normalize_identifier,
        "contact": normalize_contact,
    }
    for field, normalize in normalizers.items():
        try:
            values[field] = normalize(values[field])
        except ValidationError:
            errors[field] = [f"请输入有效的{labels[field]}。"]
            continue
        if not values[field]:
            errors[field] = [f"{labels[field]}不能为空。"]
        elif len(values[field]) > limits[field]:
            errors[field] = [f"{labels[field]}最多 {limits[field]} 个字符。"]
    if errors:
        raise ValidationError(errors)
    return RegistrationInput(**values)


def normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValidationError("Identifier must be non-empty and contain no whitespace.")
    return normalized


def normalize_contact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if "@" in normalized:
        local, separator, domain = normalized.rpartition("@")
        if not separator or not local or not domain:
            raise ValidationError("Enter a valid email address.")
        email = f"{local}@{domain.casefold()}"
        validate_email(email)
        return email

    try:
        number = phonenumbers.parse(normalized, "CN")
    except phonenumbers.NumberParseException as error:
        raise ValidationError("Enter a valid phone number.") from error
    if not phonenumbers.is_valid_number(number):
        raise ValidationError("Enter a valid phone number.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class IdentityProtector:
    ENVELOPE_VERSION = 1

    def __init__(
        self,
        *,
        keys: Mapping[str, bytes],
        active_key_id: str,
        hmac_key: bytes,
    ) -> None:
        self._keys = dict(keys)
        self.active_key_id = active_key_id
        self._hmac_key = hmac_key
        if active_key_id not in self._keys:
            raise IdentityConfigurationError("The active identity key is not configured.")
        if any(len(key) != 32 for key in self._keys.values()):
            raise IdentityConfigurationError("Every identity encryption key must be 32 bytes.")
        if len(hmac_key) < 32:
            raise IdentityConfigurationError("The identity HMAC key must be at least 32 bytes.")

    @classmethod
    def from_environment(cls) -> "IdentityProtector":
        try:
            raw_keys = json.loads(os.environ["QUIZ_IDENTITY_KEYS"])
            active_key_id = os.environ["QUIZ_IDENTITY_ACTIVE_KEY_ID"]
            hmac_key = base64.b64decode(os.environ["QUIZ_IDENTITY_HMAC_KEY"], validate=True)
            keys = {
                key_id: base64.b64decode(encoded_key, validate=True)
                for key_id, encoded_key in raw_keys.items()
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise IdentityConfigurationError(
                "Identity keys must be provided through valid environment variables."
            ) from error
        return cls(keys=keys, active_key_id=active_key_id, hmac_key=hmac_key)

    def encrypt(self, plaintext: str, *, context: str) -> dict[str, int | str]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            context.encode("utf-8"),
        )
        return {
            "v": self.ENVELOPE_VERSION,
            "key_id": self.active_key_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def decrypt(self, envelope: Mapping[str, object], *, context: str) -> str:
        try:
            if envelope["v"] != self.ENVELOPE_VERSION:
                raise IdentityDecryptionError("Unsupported identity envelope version.")
            key_id = str(envelope["key_id"])
            key = self._keys[key_id]
            nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
            ciphertext = base64.b64decode(str(envelope["ciphertext"]), validate=True)
            plaintext = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                context.encode("utf-8"),
            )
        except (KeyError, TypeError, ValueError, InvalidTag) as error:
            raise IdentityDecryptionError(
                "Identity ciphertext could not be authenticated."
            ) from error
        return plaintext.decode("utf-8")

    def digest(self, value: str, *, activity_id: object, field: str) -> str:
        message = f"v1\0{activity_id}\0{field}\0{value}".encode()
        return hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest()

    def rotate_identity(self, identity: ParticipantIdentity) -> bool:
        if (
            identity.identifier_envelope.get("key_id") == self.active_key_id
            and identity.contact_envelope.get("key_id") == self.active_key_id
        ):
            return False
        participant_id = identity.participant_id
        identity.identifier_envelope = self.encrypt(
            self.decrypt(
                identity.identifier_envelope,
                context=f"participant:{participant_id}:identifier",
            ),
            context=f"participant:{participant_id}:identifier",
        )
        identity.contact_envelope = self.encrypt(
            self.decrypt(
                identity.contact_envelope,
                context=f"participant:{participant_id}:contact",
            ),
            context=f"participant:{participant_id}:contact",
        )
        identity.save(update_fields=("identifier_envelope", "contact_envelope", "updated_at"))
        return True


def _result_for_existing(
    identity: ParticipantIdentity,
    *,
    contact_digest: str,
) -> RegistrationResult:
    if not hmac.compare_digest(identity.contact_digest, contact_digest):
        raise ParticipantRecoveryRequired("The supplied identity details do not match.")
    return RegistrationResult(participant=identity.participant, created=False)


def register_or_resume(
    *,
    activity,
    display_name: str,
    identifier: str,
    contact: str,
    protector: IdentityProtector | None = None,
    source_ip: str | None = None,
) -> RegistrationResult:
    validated = validate_registration(
        display_name=display_name, identifier=identifier, contact=contact
    )
    protector = protector or IdentityProtector.from_environment()
    normalized_display_name = validated.display_name
    normalized_identifier = validated.identifier
    normalized_contact = validated.contact
    identifier_digest = protector.digest(
        normalized_identifier,
        activity_id=activity.pk,
        field="identifier",
    )
    contact_digest = protector.digest(
        normalized_contact,
        activity_id=activity.pk,
        field="contact",
    )

    with transaction.atomic():
        activity = ActivityEdition.objects.select_for_update().get(pk=activity.pk)
        existing = (
            ParticipantIdentity.objects.select_related("participant")
            .select_for_update()
            .filter(activity=activity, identifier_digest=identifier_digest)
            .first()
        )
        if existing:
            return _result_for_existing(
                existing,
                contact_digest=contact_digest,
            )
        if activity.status != ActivityStatus.OPEN:
            raise ParticipantRecoveryRequired(
                "Only an existing participant can enter while the activity is not open."
            )

        try:
            with transaction.atomic():
                from .rate_limits import ACTIVITY_NEW_IDENTITY, IP_NEW_IDENTITY, consume_limits

                limits = [(ACTIVITY_NEW_IDENTITY, activity.pk, "activity")]
                if source_ip is not None:
                    limits.append((IP_NEW_IDENTITY, activity.pk, source_ip))
                consume_limits(limits)
                participant = Participant.objects.create(activity=activity)
                ParticipantIdentity.objects.create(
                    participant=participant,
                    activity=activity,
                    display_name=normalized_display_name,
                    contact_type="email" if "@" in normalized_contact else "phone",
                    identifier_envelope=protector.encrypt(
                        normalized_identifier,
                        context=f"participant:{participant.pk}:identifier",
                    ),
                    contact_envelope=protector.encrypt(
                        normalized_contact,
                        context=f"participant:{participant.pk}:contact",
                    ),
                    identifier_digest=identifier_digest,
                    contact_digest=contact_digest,
                )
        except IntegrityError:
            existing = ParticipantIdentity.objects.select_related("participant").get(
                activity=activity,
                identifier_digest=identifier_digest,
            )
            return _result_for_existing(
                existing,
                contact_digest=contact_digest,
            )
    return RegistrationResult(participant=participant, created=True)


@transaction.atomic
def deidentify_participant(*, participant: Participant, reason: str, actor=None) -> bool:
    reason = reason.strip()
    if not reason:
        raise ValidationError("A de-identification reason is required.")
    locked = Participant.objects.select_for_update().get(pk=participant.pk)
    deleted, _ = ParticipantIdentity.objects.filter(participant=locked).delete()
    if not deleted:
        return False
    locked.anonymized_at = timezone.now()
    locked.save(update_fields=("anonymized_at",))
    AdminAuditLog.objects.create(
        actor=actor,
        activity=locked.activity,
        participant=locked,
        action="participant_deidentified",
        reason=reason,
    )
    return True
