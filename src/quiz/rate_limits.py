"""Shared admission control; no client identifiers are stored or logged."""

import ipaddress
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .models import RateLimitBucket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Policy:
    namespace: str
    capacity: int
    refill: int
    seconds: int


BROWSER_REGISTRATION = Policy("registration_browser", 8, 8, 60)
IDENTIFIER_REGISTRATION = Policy("registration_identifier", 6, 6, 60)
IP_NEW_IDENTITY = Policy("new_identity_ip", 100, 120, 60)
ACTIVITY_NEW_IDENTITY = Policy("new_identity_activity", 200, 1000, 3600)
PARTICIPANT_NEW_ATTEMPT = Policy("new_attempt_participant", 10, 1, 60)
ACTIVITY_NEW_ATTEMPT = Policy("new_attempt_activity", 200, 120, 60)


class RateLimited(Exception):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Request rate limit exceeded.")


class RateLimitUnavailable(Exception):
    pass


def client_ip(request) -> str:
    """Only a configured proxy may assert X-Real-IP; external headers are ignored."""
    try:
        peer = ipaddress.ip_address(request.META.get("REMOTE_ADDR", "127.0.0.1"))
        trusted = any(
            peer in ipaddress.ip_network(cidr) for cidr in settings.QUIZ_TRUSTED_PROXY_CIDRS
        )
        address = ipaddress.ip_address(request.META.get("HTTP_X_REAL_IP", "")) if trusted else peer
    except ValueError as error:
        raise RateLimitUnavailable from error
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def bucket_key(policy, scope, subject) -> str:
    from .identity import IdentityProtector

    return IdentityProtector.from_environment().digest(
        str(subject), activity_id=scope, field=f"rate-limit/v1/{policy.namespace}"
    )


def _postgres_consume(keyed, now):
    """One ordered UPSERT for the successful path, holding all row locks to commit."""
    table = connection.ops.quote_name(RateLimitBucket._meta.db_table)
    values = ",".join(["(%s,%s,%s::numeric,%s::numeric,%s::numeric,%s::timestamptz)"] * len(keyed))
    params = [
        value
        for key, policy in keyed
        for value in (key, policy.namespace, policy.capacity, policy.refill, policy.seconds, now)
    ]
    sql = f"""
        WITH incoming(key, namespace, capacity, refill, seconds, stamp) AS (VALUES {values})
        INSERT INTO {table} AS bucket (key, namespace, tokens, updated_at, expires_at)
        SELECT key, namespace, capacity - 1, stamp, stamp + interval '1 hour'
        FROM incoming ORDER BY key
        ON CONFLICT (key) DO UPDATE SET
          tokens = trunc(least(
            (SELECT capacity FROM incoming WHERE incoming.key = bucket.key),
            bucket.tokens + greatest(extract(epoch FROM
              (EXCLUDED.updated_at - bucket.updated_at)), 0)
              * (SELECT refill FROM incoming WHERE incoming.key = bucket.key)
              / (SELECT seconds FROM incoming WHERE incoming.key = bucket.key)
          ), 6) - 1,
          updated_at = greatest(bucket.updated_at, EXCLUDED.updated_at),
          expires_at = greatest(bucket.updated_at, EXCLUDED.updated_at) + interval '1 hour'
        RETURNING key, tokens
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        balances = dict(cursor.fetchall())
        blocked = []
        wait = 0
        for key, policy in keyed:
            if balances[key] < 0:
                blocked.append(policy.namespace)
                wait = max(
                    wait,
                    int(
                        (-balances[key] * policy.seconds / policy.refill).to_integral_value(
                            rounding=ROUND_CEILING
                        )
                    ),
                )
        if wait:
            # Negative balances never commit: restore every debit before releasing
            # the locks, retaining only the refill and last-access timestamp.
            placeholders = ",".join(["%s"] * len(keyed))
            cursor.execute(
                f"UPDATE {table} SET tokens = tokens + 1 WHERE key IN ({placeholders})",
                [key for key, _ in keyed],
            )
    return wait, blocked


def consume_limits(limits, *, now=None) -> None:
    """Atomically debit all buckets, or none, using a stable row-lock order.

    Call outside the business transaction for request limits, and inside it
    for new-record quotas. Only the latter must roll back with failed writes.
    """
    now = now or timezone.now()
    keyed = sorted(
        (bucket_key(policy, scope, subject), policy) for policy, scope, subject in limits
    )
    retry_after = 0
    blocked = []
    try:
        with transaction.atomic(savepoint=False):
            if connection.vendor == "postgresql":
                retry_after, blocked = _postgres_consume(keyed, now)
            else:
                retry_after, blocked = _portable_consume(keyed, now)
    except DatabaseError as error:
        raise RateLimitUnavailable from error
    if retry_after:
        logger.warning("rate_limited policies=%s retry_after=%s", ",".join(blocked), retry_after)
        raise RateLimited(retry_after)


def _portable_consume(keyed, now):
    retry_after = 0
    blocked = []
    # Insert missing rows together; per-bucket get_or_create adds several
    # round trips and savepoints to every registration under contention.
    RateLimitBucket.objects.bulk_create(
        [
            RateLimitBucket(
                key=key,
                namespace=policy.namespace,
                tokens=policy.capacity,
                updated_at=now,
                expires_at=now + timedelta(hours=1),
            )
            for key, policy in keyed
        ],
        ignore_conflicts=True,
    )
    locked = {
        bucket.key: bucket
        for bucket in RateLimitBucket.objects.select_for_update()
        .filter(key__in=[key for key, _ in keyed])
        .order_by("key")
    }
    # An idle bucket may be removed between INSERT and SELECT by cleanup.
    # Fail closed; a subsequent request can recreate it without losing data.
    if len(locked) != len(keyed):
        raise RateLimitUnavailable
    rows = []
    for key, policy in keyed:
        bucket = locked[key]
        effective_now = max(now, bucket.updated_at)
        elapsed = Decimal(str((effective_now - bucket.updated_at).total_seconds()))
        rate = Decimal(policy.refill) / policy.seconds
        tokens = min(Decimal(policy.capacity), bucket.tokens + elapsed * rate)
        tokens = tokens.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if tokens < 1:
            retry_after = max(
                retry_after,
                int(((1 - tokens) / rate).to_integral_value(rounding=ROUND_CEILING)),
            )
            blocked.append(policy.namespace)
        rows.append((bucket, tokens, effective_now))
    for bucket, tokens, effective_now in rows:
        bucket.tokens = tokens if retry_after else tokens - 1
        bucket.updated_at = effective_now
        bucket.expires_at = effective_now + timedelta(hours=1)
    RateLimitBucket.objects.bulk_update(
        [row[0] for row in rows], ("tokens", "updated_at", "expires_at")
    )
    return retry_after, blocked


def prune_buckets(*, now=None, batch_size=1000, max_batches=10) -> int:
    now = now or timezone.now()
    removed = 0
    for _ in range(max_batches):
        keys = list(
            RateLimitBucket.objects.filter(expires_at__lte=now)
            .order_by("expires_at", "key")
            .values_list("key", flat=True)[:batch_size]
        )
        if not keys:
            break
        # Recheck expiry so a concurrent refresh cannot be deleted by an old scan.
        count, _ = RateLimitBucket.objects.filter(key__in=keys, expires_at__lte=now).delete()
        removed += count
    return removed
