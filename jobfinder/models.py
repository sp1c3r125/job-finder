from dataclasses import dataclass, field


@dataclass
class Job:
    source: str            # greenhouse | lever | ashby | himalayas | remoteok | wwr
    source_id: str
    company: str
    title: str
    url: str
    posted: str            # ISO 8601 UTC, "" if unknown
    location: str          # raw location text
    remote: bool | None    # True / False / None if unknown
    description: str       # plain text
    also_seen_on: list = field(default_factory=list)
    undated: bool = False  # True: the source showed no posting date; `posted` is the first-seen date (research)
    expires: str = ""      # ISO 8601 UTC; "" if the source gives none (Himalayas gives expiryDate)
