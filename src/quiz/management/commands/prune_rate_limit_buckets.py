from django.core.management.base import BaseCommand

from quiz.rate_limits import prune_buckets


class Command(BaseCommand):
    help = "Remove idle admission-control buckets in bounded batches."

    def handle(self, *args, **options):
        self.stdout.write(f"removed={prune_buckets()}")
