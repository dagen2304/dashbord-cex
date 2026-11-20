"""
Mode démonstration — DÉSACTIVÉ par défaut (variable DEMO_DATA=true pour l'activer).

Crée des équipements fictifs dont l'adresse se termine par `.demo.invalid`
(domaine réservé, RFC 2606 : il ne peut correspondre à aucun vrai équipement)
et rafraîchit leurs données chaque minute en passant par le même pipeline que
les vraies collectes. La collecte réelle ignore toujours ces équipements, et
l'interface les signale par un badge « Démo ».

À n'activer que sur un environnement de test ou de formation.
"""

DEMO_SUFFIX = ".demo.invalid"


def is_demo_host(hostname: str | None) -> bool:
    return bool(hostname) and hostname.lower().endswith(DEMO_SUFFIX)
