"""legislink -- extract, resolve and link UK legislation citations.

    >>> from legislink import resolve_text
    >>> result = resolve_text("See section 6 of the Human Rights Act 1998.")
    >>> result.citations[0].url
    'https://www.legislation.gov.uk/ukpga/1998/42/section/6'
"""

from .extract import extract
from .models import (
    BASE,
    CanonicalWork,
    Citation,
    DocumentResult,
    Provision,
    Version,
    WorkRef,
    WORK_TYPES,
)
from .normalize import normalize_title
from .pipeline import annotate, build_index, default_resolver, resolve_text
from .registry import Registry
from .resolve import Resolver

__version__ = "0.1.0"

__all__ = [
    "BASE",
    "CanonicalWork",
    "Citation",
    "DocumentResult",
    "Provision",
    "Registry",
    "Resolver",
    "Version",
    "WORK_TYPES",
    "WorkRef",
    "__version__",
    "annotate",
    "build_index",
    "default_resolver",
    "extract",
    "normalize_title",
    "resolve_text",
]
