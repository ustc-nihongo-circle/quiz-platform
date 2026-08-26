from django.core.management.base import BaseCommand, CommandError

from quiz.identity import IdentityProtector
from quiz.models import ParticipantIdentity


class Command(BaseCommand):
    help = "Re-encrypt participant identity fields with the configured active key."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options) -> None:
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("batch-size must be positive")
        protector = IdentityProtector.from_environment()
        queryset = ParticipantIdentity.objects.order_by("participant_id")
        scanned = 0
        rotated = 0
        for identity in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            needs_rotation = any(
                envelope.get("key_id") != protector.active_key_id
                for envelope in (identity.identifier_envelope, identity.contact_envelope)
            )
            if not needs_rotation:
                continue
            rotated += 1
            if not options["dry_run"]:
                protector.rotate_identity(identity)
        mode = "would_rotate" if options["dry_run"] else "rotated"
        self.stdout.write(
            self.style.SUCCESS(
                f"identity key rotation scanned={scanned} {mode}={rotated} "
                f"active_key_id={protector.active_key_id}"
            )
        )
