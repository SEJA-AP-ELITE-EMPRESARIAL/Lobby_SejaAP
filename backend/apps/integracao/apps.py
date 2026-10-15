import logging
import sys

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger("lobby.integracao")


class IntegracaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integracao"
    verbose_name = "Integração com o Conecta"

    def ready(self):
        # Na subida, uma linha dizendo se a integração está ligada e, se não,
        # por quê (ADR-002 §4.6). Sem rede aqui: só a configuração. A conferência
        # do cache de verdade acontece no `cache.add` do `jti`, a cada chamada.
        if "test" in sys.argv:
            return
        from apps.contas.autenticacao_conecta import kids_aceitos, motivo_de_desligada

        motivo = motivo_de_desligada()
        if not motivo:
            logger.info("integração com o Conecta ligada; kids aceitos: %s", ", ".join(kids_aceitos()))
        elif settings.DEBUG:
            logger.info("integração com o Conecta desligada: %s", motivo)
        else:
            logger.error("integração com o Conecta DESLIGADA: %s", motivo)
