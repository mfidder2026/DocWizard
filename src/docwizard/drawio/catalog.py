"""Type catalogues per notation, with alias tables for fuzzy matching of LLM output."""

from __future__ import annotations

import re
import unicodedata

NOTATIONS = ("archimate", "c4", "generic")

# --------------------------------------------------------------------------- ArchiMate
# canonical type -> (layer, draw.io appType, draw.io archiType or None)
# appType/archiType values verified against jgraph/drawio Sidebar-ArchiMate3.js and
# shapes/mxArchiMate3.js (dev branch).
ARCHIMATE_TYPES: dict[str, tuple[str, str, str | None]] = {
    "business-actor": ("business", "actor", "square"),
    "business-role": ("business", "role", "square"),
    "business-collaboration": ("business", "collab", "square"),
    "business-interface": ("business", "interface", "square"),
    "business-process": ("business", "proc", "rounded"),
    "business-function": ("business", "func", "rounded"),
    "business-interaction": ("business", "interaction", "rounded"),
    "business-event": ("business", "event", "rounded"),
    "business-service": ("business", "serv", "rounded"),
    "business-object": ("business", "passive", "square"),
    "contract": ("business", "contract", "square"),
    "representation": ("business", "representation", "square"),
    "product": ("business", "product", "square"),
    "application-component": ("application", "comp", "square"),
    "application-collaboration": ("application", "collab", "square"),
    "application-interface": ("application", "interface", "square"),
    "application-function": ("application", "func", "rounded"),
    "application-interaction": ("application", "interaction", "rounded"),
    "application-process": ("application", "proc", "rounded"),
    "application-event": ("application", "event", "rounded"),
    "application-service": ("application", "serv", "rounded"),
    "data-object": ("application", "passive", "square"),
    "node": ("technology", "node", "square"),
    "device": ("technology", "device", None),
    "system-software": ("technology", "sysSw", "square"),
    "technology-collaboration": ("technology", "collab", "square"),
    "technology-interface": ("technology", "interface", "square"),
    "technology-function": ("technology", "func", "rounded"),
    "technology-process": ("technology", "proc", "rounded"),
    "technology-interaction": ("technology", "interaction", "rounded"),
    "technology-event": ("technology", "event", "rounded"),
    "technology-service": ("technology", "serv", "rounded"),
    "artifact": ("technology", "artifact", "square"),
    "communication-network": ("technology", "netw", "square"),
    "path": ("technology", "path", "square"),
    "equipment": ("technology", "equipment", "square"),
    "facility": ("technology", "facility", "square"),
    "stakeholder": ("motivation", "role", "oct"),
    "driver": ("motivation", "driver", "oct"),
    "assessment": ("motivation", "assess", "oct"),
    "goal": ("motivation", "goal", "oct"),
    "outcome": ("motivation", "outcome", "oct"),
    "principle": ("motivation", "principle", "oct"),
    "requirement": ("motivation", "requirement", "oct"),
    "constraint": ("motivation", "constraint", "oct"),
    "meaning": ("motivation", "meaning", "oct"),
    "value": ("motivation", "amValue", "oct"),
    "capability": ("strategy", "capability", "rounded"),
    "resource": ("strategy", "resource", "square"),
    "value-stream": ("strategy", "valueStream", "rounded"),
    "course-of-action": ("strategy", "course", "rounded"),
    "work-package": ("implementation", "workPackage", "rounded"),
    "deliverable": ("implementation", "deliverable", None),
    "implementation-event": ("implementation", "event", "rounded"),
    "plateau": ("implementation", "plateau", None),
    "gap": ("implementation", "gap", None),
    "grouping": ("other", "grouping", "square"),
    "location": ("other", "location", "square"),
}

# Display order of ArchiMate layers in a layered view (top to bottom).
ARCHIMATE_LAYER_ORDER = ("motivation", "strategy", "business", "application", "technology", "implementation", "other")

ARCHIMATE_LAYER_LABELS = {
    "nl": {
        "motivation": "Motivatie",
        "strategy": "Strategie",
        "business": "Bedrijfslaag",
        "application": "Applicatielaag",
        "technology": "Technologielaag",
        "implementation": "Implementatie en migratie",
        "other": "Overig",
    },
    "en": {
        "motivation": "Motivation",
        "strategy": "Strategy",
        "business": "Business layer",
        "application": "Application layer",
        "technology": "Technology layer",
        "implementation": "Implementation & migration",
        "other": "Other",
    },
}

# Keywords that identify which ArchiMate layer a (layer) group label refers to.
LAYER_KEYWORDS = {
    "business": ("business", "bedrijf", "organisatie", "proces"),
    "application": ("applicat", "application", "informatie", "software", "app"),
    "technology": ("technolog", "infra", "platform", "techniek", "hosting"),
    "motivation": ("motivat", "doel", "principe"),
    "strategy": ("strateg",),
    "implementation": ("implement", "migrat", "project", "transitie"),
}

_ARCHIMATE_ALIASES: dict[str, list[str]] = {
    "business-actor": ["actor", "bedrijfsactor", "persoon", "person", "user", "gebruiker", "burger", "klant",
                       "medewerker", "organisatie", "organization", "organisation", "afdeling", "ondernemer",
                       "inwoner", "citizen", "customer", "employee", "externalperson"],
    "business-role": ["role", "rol", "bedrijfsrol"],
    "business-collaboration": ["bedrijfssamenwerking", "collaboration", "samenwerking"],
    "business-interface": ["bedrijfsinterface", "loket", "kanaal", "channel"],
    "business-process": ["process", "proces", "bedrijfsproces", "werkproces", "workflow"],
    "business-function": ["function", "functie", "bedrijfsfunctie"],
    "business-event": ["event", "gebeurtenis", "bedrijfsgebeurtenis"],
    "business-service": ["bedrijfsdienst", "bedrijfsservice", "dienst", "productdienst"],
    "business-object": ["bedrijfsobject", "object", "informatieobject", "document", "dossier", "formulier"],
    "contract": ["overeenkomst"],
    "product": ["produkt"],
    "application-component": ["app", "application", "applicatie", "applicatiecomponent", "component", "system",
                              "systeem", "software", "module", "microservice", "webapp", "portal", "portaal",
                              "frontend", "backend", "softwaresystem", "container", "controller", "externalsystem", "external",
                              "extern", "saas", "registratie", "basisregistratie", "mobile", "mobileapp",
                              "browser", "website"],
    "application-collaboration": ["applicatiesamenwerking"],
    "application-interface": ["interface", "api", "koppelvlak", "endpoint", "applicatieinterface", "rest", "soap",
                              "gateway", "apigateway", "koppeling"],
    "application-function": ["applicatiefunctie"],
    "application-process": ["applicatieproces"],
    "application-event": ["applicatiegebeurtenis"],
    "application-service": ["applicatiedienst", "applicatieservice", "service", "appservice"],
    "data-object": ["data", "gegevens", "gegevensobject", "entity", "entiteit", "table", "tabel", "record",
                    "dataset", "gegevensverzameling", "informatie"],
    "node": ["server", "host", "vm", "virtualmachine", "cluster", "knooppunt", "kubernetes", "k8s", "platform",
             "omgeving", "environment", "cloud"],
    "device": ["apparaat", "hardware", "laptop", "pc", "werkplek", "smartphone", "telefoon", "phone"],
    "system-software": ["systeemsoftware", "database", "db", "dbms", "os", "operatingsystem", "middleware",
                        "rdbms", "postgres", "postgresql", "oracle", "sqlserver", "mysql", "webserver",
                        "applicationserver", "runtime", "broker", "queue", "messagebroker", "kafka", "esb",
                        "servicebus", "datastore", "storage", "opslag", "eventbus", "eventhub", "messagebus",
                        "cache", "redis"],
    "technology-service": ["technologiedienst", "technologieservice", "infrastructuurdienst", "infraservice",
                           "infrastructureservice", "platformservice", "techservice"],
    "artifact": ["artefact", "file", "bestand", "jar", "deployment", "image", "script"],
    "communication-network": ["network", "netwerk", "lan", "wan", "internet", "diginetwerk", "vpn", "firewall"],
    "path": ["pad", "communicationpath", "verbinding"],
    "stakeholder": ["belanghebbende"],
    "driver": ["aanleiding", "drijfveer"],
    "assessment": ["beoordeling", "analyse"],
    "goal": ["doel", "doelstelling", "objective"],
    "outcome": ["uitkomst", "resultaat", "effect"],
    "principle": ["principe", "uitgangspunt"],
    "requirement": ["eis", "vereiste", "wens"],
    "constraint": ["beperking", "randvoorwaarde"],
    "capability": ["bekwaamheid", "capaciteit"],
    "work-package": ["werkpakket", "project", "programma", "workpackage"],
    "deliverable": ["opleverproduct", "oplevering"],
    "plateau": ["fase", "stage", "transitiearchitectuur"],
    "gap": ["kloof"],
    "grouping": ["groep", "groepering", "group"],
    "location": ["locatie", "site", "datacenter", "rekencentrum", "vestiging"],
}

# --------------------------------------------------------------------------- C4
C4_TYPES = ("person", "external-person", "software-system", "external-system", "container", "component",
            "database", "queue", "browser", "mobile")

_C4_ALIASES: dict[str, list[str]] = {
    "person": ["persoon", "user", "gebruiker", "actor", "businessactor", "burger", "klant", "medewerker",
               "role", "rol", "stakeholder", "businessrole", "customer", "employee", "citizen"],
    "external-person": ["externalperson", "externeperson", "externeuser"],
    "software-system": ["system", "softwaresystem", "systeem", "softwaresysteem"],
    "external-system": ["external", "externalsystem", "externsysteem", "extern", "thirdparty", "saas", "partner",
                        "externalservice", "legacy", "basisregistratie"],
    "container": ["app", "application", "applicatie", "webapp", "spa", "frontend", "backend", "api", "service",
                  "microservice", "applicationcomponent", "applicatiecomponent", "module", "server",
                  "webserver", "gateway", "apigateway", "function", "lambda", "worker", "job", "applicationservice",
                  "node", "portal", "portaal", "website"],
    "component": ["class", "klasse", "onderdeel", "controller", "repository"],
    "database": ["db", "datastore", "store", "dataobject", "opslag", "storage", "postgres", "postgresql", "sql",
                 "mysql", "oracle", "rdbms", "nosql", "mongodb", "cache", "redis", "data", "gegevens", "systemsoftware",
                 "blob", "bucket", "s3", "filestore"],
    "queue": ["messagequeue", "broker", "kafka", "topic", "bus", "eventbus", "messagebus", "mq", "rabbitmq",
              "servicebus", "messagebroker", "eventhub", "stream"],
    "browser": ["webbrowser", "webclient", "client"],
    "mobile": ["mobileapp", "smartphone", "phone", "ios", "android", "device"],
}

# --------------------------------------------------------------------------- generic
GENERIC_TYPES = ("app", "service", "api", "database", "queue", "storage", "user", "external", "server", "cloud",
                 "firewall", "gateway", "mobile", "browser")

_GENERIC_ALIASES: dict[str, list[str]] = {
    "app": ["application", "applicatie", "webapp", "frontend", "backend", "system", "systeem", "component",
            "applicationcomponent", "applicatiecomponent", "container", "softwaresystem", "module", "portal",
            "portaal", "website"],
    "service": ["microservice", "function", "lambda", "worker", "job", "process", "proces", "dienst",
                "applicationservice", "technologyservice", "businessservice", "applicatiedienst"],
    "api": ["interface", "endpoint", "rest", "graphql", "koppelvlak", "applicationinterface", "soap", "grpc"],
    "database": ["db", "dataobject", "sql", "postgres", "postgresql", "mysql", "oracle", "rdbms", "nosql",
                 "mongodb", "cache", "redis", "data", "gegevens", "datastore", "systemsoftware"],
    "queue": ["broker", "kafka", "topic", "bus", "eventbus", "messagebus", "mq", "rabbitmq", "servicebus",
              "eventhub", "stream", "messagequeue", "messagebroker"],
    "storage": ["blob", "s3", "bucket", "filestore", "opslag", "bestand", "file", "artifact", "objectstorage",
                "datalake", "fileshare", "disk"],
    "user": ["person", "persoon", "actor", "gebruiker", "burger", "klant", "medewerker", "businessactor", "role",
             "rol", "customer", "employee", "citizen", "stakeholder"],
    "external": ["externalsystem", "extern", "thirdparty", "saas", "partner", "externalservice", "legacy"],
    "server": ["vm", "host", "node", "machine", "cluster", "kubernetes", "k8s", "virtualmachine", "compute",
               "device"],
    "cloud": ["internet", "azure", "aws", "gcp", "network", "communicationnetwork", "netwerk", "cloudplatform"],
    "firewall": ["waf", "fw"],
    "gateway": ["apigateway", "loadbalancer", "lb", "proxy", "reverseproxy", "ingress", "cdn", "router"],
    "mobile": ["mobileapp", "smartphone", "phone", "ios", "android", "telefoon"],
    "browser": ["webbrowser", "spa", "webclient", "client"],
}

DEFAULT_NODE_TYPE = {"archimate": "application-component", "c4": "container", "generic": "app"}

# --------------------------------------------------------------------------- relationships
ARCHIMATE_RELATIONS = ("serving", "realization", "assignment", "access", "flow", "triggering", "composition",
                       "aggregation", "association", "influence", "specialization")
SIMPLE_RELATIONS = ("default", "async", "bidirectional", "dependency", "association")

_ARCHIMATE_REL_ALIASES: dict[str, list[str]] = {
    "serving": ["serves", "serve", "uses", "usedby", "use", "bedient", "bediening", "gebruikt", "dient",
                "calls", "roeptaan", "default", "sync", "dependency"],
    "realization": ["realizes", "realises", "realisation", "realisatie", "realiseert", "implements",
                    "implementeert", "realize"],
    "assignment": ["assigned", "assign", "toewijzing", "wijsttoe", "performs", "voertuit", "deployedon",
                   "runs", "runson", "draait", "draaitop", "hosts", "host", "deploys"],
    "access": ["accesses", "reads", "read", "writes", "write", "leest", "schrijft", "toegang", "readwrite",
               "crud", "raadpleegt", "muteert", "opslaan", "stores"],
    "flow": ["flows", "dataflow", "stroom", "sends", "send", "stuurt", "levert", "transfer", "informatiestroom",
             "gegevensstroom", "async", "message", "bericht", "publishes", "stuurtdoor"],
    "triggering": ["triggers", "trigger", "starts", "start", "initiates", "triggert", "volgt", "next"],
    "composition": ["composed", "composedof", "bestaatuit", "compositie", "consistsof", "contains", "bevat",
                    "partof", "onderdeelvan"],
    "aggregation": ["aggregates", "aggregatie", "groups", "has", "heeft", "groepeert"],
    "association": ["associated", "associatie", "relates", "related", "link", "connect", "connected", "verbonden",
                    "relatie", "bidirectional", "line"],
    "influence": ["influences", "invloed", "beinvloedt", "affects"],
    "specialization": ["specializes", "specialisatie", "isa", "kindof", "subtype", "specialisation"],
}

_SIMPLE_REL_ALIASES: dict[str, list[str]] = {
    "default": ["sync", "uses", "use", "calls", "call", "serving", "serves", "flow", "triggering", "http",
                "https", "rest", "request", "gebruikt", "roeptaan", "access", "reads", "writes", "realization",
                "assignment", "dataflow", "sends", "stuurt"],
    "async": ["asynchronous", "asynchroon", "event", "events", "message", "messaging", "publishes", "publish",
              "subscribes", "subscribe", "queue", "emits", "notify", "notifies"],
    "bidirectional": ["both", "twoway", "tweerichting", "bidi", "readwrite", "sync2"],
    "dependency": ["depends", "dependson", "afhankelijk", "specialization", "realizes", "influence"],
    "association": ["line", "link", "connected", "connect", "related", "relatie", "composition", "aggregation",
                    "none", "plain"],
}


def compact(value: object) -> str:
    """Lowercase, strip accents and every non-alphanumeric character."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _build_index(canonical: tuple[str, ...] | list[str], aliases: dict[str, list[str]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for name in canonical:
        index[compact(name)] = name
    for name, keys in aliases.items():
        for key in keys:
            index.setdefault(compact(key), name)
    return index


_TYPE_INDEX = {
    "archimate": _build_index(list(ARCHIMATE_TYPES), _ARCHIMATE_ALIASES),
    "c4": _build_index(C4_TYPES, _C4_ALIASES),
    "generic": _build_index(GENERIC_TYPES, _GENERIC_ALIASES),
}
_REL_INDEX = {
    "archimate": _build_index(ARCHIMATE_RELATIONS, _ARCHIMATE_REL_ALIASES),
    "simple": _build_index(SIMPLE_RELATIONS, _SIMPLE_REL_ALIASES),
}


def _fuzzy(raw: object, index: dict[str, str]) -> str | None:
    key = compact(raw)
    if not key:
        return None
    if key in index:
        return index[key]
    # Prefer the longest alias contained in the key ("bedrijfsproces2" -> business-process),
    # then the longest alias that contains the key ("comp" -> application-component).
    best: tuple[int, str] | None = None
    for alias, name in index.items():
        if len(alias) >= 3 and alias in key and (best is None or len(alias) > best[0]):
            best = (len(alias), name)
    if best:
        return best[1]
    if len(key) >= 3:
        for alias, name in sorted(index.items(), key=lambda kv: len(kv[0])):
            if alias.startswith(key):
                return name
    return None


def resolve_node_type(raw: object, notation: str) -> str:
    return _fuzzy(raw, _TYPE_INDEX[notation]) or DEFAULT_NODE_TYPE[notation]


def resolve_relation(raw: object, notation: str) -> str | None:
    """Map a free-form relationship name to a canonical one; None when unrecognised."""
    return _fuzzy(raw, _REL_INDEX["archimate" if notation == "archimate" else "simple"])


def access_mode(raw: object) -> str | None:
    """Derive ArchiMate access direction from a relation alias (reads/writes...)."""
    key = compact(raw)
    if key in {"readwrite", "crud", "muteert"}:
        return "readwrite"
    if key.startswith(("read", "lees", "leest", "raadpleeg")):
        return "read"
    if key.startswith(("write", "schrijf", "opslaan", "store")):
        return "write"
    return None


def detect_notation(type_names: list[str]) -> str:
    """Guess a notation from raw node type names when the spec does not say."""
    keys = [compact(t) for t in type_names if t]
    archi_specific = {compact(t) for t in ARCHIMATE_TYPES} - {"node", "path", "product", "value"}
    if any(k in archi_specific or k.startswith(("business", "application", "technology", "bedrijfs", "applicatie"))
           for k in keys):
        return "archimate"
    if any(k in {"softwaresystem", "externalsystem", "person", "container", "externalperson"} for k in keys):
        return "c4"
    return "generic"


def archimate_layer(node_type: str) -> str:
    return ARCHIMATE_TYPES.get(node_type, ("other", "", None))[0]
