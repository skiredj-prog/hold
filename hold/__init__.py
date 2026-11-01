"""HOLD — habilitation d'agent prêt-à-porter.

Le profil dit ce que l'agent peut faire seul ; le plancher garantit ce qui ne peut pas arriver ;
l'hésitation transforme un résultat inconnu en discernement plutôt qu'en devinette ;
le journal rend chaque décision vérifiable et rejouable.
"""

from .discern import LlamaDiscerner, ReferenceDiscerner
from .floor import INVARIANTS
from .journal import CORE_VERSION, ENVELOPE_VERSION, verify_chain
from .outcome import Outcome, ToolResult
from .profile import Profile, dump_profile, load_profile, revise, sign, signature_valid
from .relay.base import ConsoleRelay, SimulatedRelay
from .runtime import Habilitation, Mission
from .store import Store

__version__ = "0.1.0"

__all__ = [
    "CORE_VERSION",
    "ENVELOPE_VERSION",
    "INVARIANTS",
    "ConsoleRelay",
    "Habilitation",
    "LlamaDiscerner",
    "Mission",
    "Outcome",
    "Profile",
    "ReferenceDiscerner",
    "SimulatedRelay",
    "Store",
    "ToolResult",
    "dump_profile",
    "load_profile",
    "revise",
    "sign",
    "signature_valid",
    "verify_chain",
]
