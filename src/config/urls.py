from django.conf import settings
from django.contrib import admin
from django.http import Http404
from django.urls import include, path
from django.views.static import serve


def debug_media(request, path):
    """Serve imported question images only from the local debug server."""
    if not settings.DEBUG:
        raise Http404
    return serve(request, path, document_root=settings.MEDIA_ROOT, show_indexes=False)

urlpatterns = [
    path("media/<path:path>", debug_media),
    path("ops/", include("quiz.ops_urls")),
    path("", include("quiz.urls")),
]

if settings.DEBUG:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
