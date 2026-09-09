"""Literature discovery and ingestion services."""

from science_buddy.services.literature.crossref import CrossrefProvider
from science_buddy.services.literature.europe_pmc import EuropePmcProvider
from science_buddy.services.literature.openalex import OpenAlexProvider
from science_buddy.services.literature.pubmed import PubMedProvider

__all__ = [
	"CrossrefProvider",
	"EuropePmcProvider",
	"OpenAlexProvider",
	"PubMedProvider",
]
