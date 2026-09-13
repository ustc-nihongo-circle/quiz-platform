import ipaddress

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def trusted_proxy_configuration(app_configs, **kwargs):
    try:
        for cidr in settings.QUIZ_TRUSTED_PROXY_CIDRS:
            network = ipaddress.ip_network(cidr)
            if network.prefixlen == 0:
                raise ValueError
    except ValueError:
        return [Error("QUIZ_TRUSTED_PROXY_CIDRS must contain explicit proxy networks.",
                      id="quiz.E001")]
    return []
