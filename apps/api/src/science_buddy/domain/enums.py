from enum import StrEnum


class AssetSource(StrEnum):
    PUBMED = "pubmed"
    EUROPE_PMC = "europe_pmc"
    OPENALEX = "openalex"
    CROSSREF = "crossref"
    USER_PDF = "user_pdf"


class EvidenceDepth(StrEnum):
    METADATA_ONLY = "metadata_only"
    ABSTRACT = "abstract"
    FULLTEXT_XML = "fulltext_xml"
    USER_PDF = "user_pdf"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    REJECTED = "rejected"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INDIRECT = "indirect"
