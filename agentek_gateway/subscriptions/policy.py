from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .model import SubscriptionId


class SubjectKind(StrEnum):
    EMPLOYEE = "employee"
    SPACE = "space"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class Subject:
    kind: SubjectKind
    id: str


class VisibilityKind(StrEnum):
    ALL = "all"
    ALL_EXCEPT = "all_except"
    ONLY = "only"


@dataclass(frozen=True, slots=True)
class Visibility:
    kind: VisibilityKind = VisibilityKind.ALL
    subjects: frozenset[Subject] = frozenset()


@dataclass(frozen=True, slots=True)
class KeySubjects:
    employee: Subject | None = None
    space: Subject | None = None
    service: Subject | None = None

    @property
    def narrow(self) -> frozenset[Subject]:
        return frozenset(subject for subject in (self.space, self.service) if subject)

    @property
    def broad(self) -> frozenset[Subject]:
        return frozenset(subject for subject in (self.employee,) if subject)

    @property
    def all(self) -> frozenset[Subject]:
        return self.narrow | self.broad


@dataclass(frozen=True, slots=True)
class Policy:
    visibility: Mapping[SubscriptionId, Visibility] = field(default_factory=dict)
    bindings: Mapping[SubscriptionId, frozenset[Subject]] = field(default_factory=dict)
    version: int = 0


SUBJECTS_METADATA_FIELD = "agentek_subjects"


def key_subjects_from_metadata(key_metadata: object) -> KeySubjects | None:
    if not isinstance(key_metadata, Mapping):
        return None
    labels = key_metadata.get(SUBJECTS_METADATA_FIELD)
    if not isinstance(labels, Mapping):
        return None
    subjects = KeySubjects(
        employee=_subject(labels, SubjectKind.EMPLOYEE),
        space=_subject(labels, SubjectKind.SPACE),
        service=_subject(labels, SubjectKind.SERVICE),
    )
    return subjects if subjects.all else None


def _subject(labels: Mapping[object, object], kind: SubjectKind) -> Subject | None:
    value = labels.get(kind.value)
    if isinstance(value, (str, int)) and str(value):
        return Subject(kind, str(value))
    return None


def is_visible(visibility: Visibility, subjects: KeySubjects | None) -> bool:
    match visibility.kind:
        case VisibilityKind.ALL:
            return True
        case VisibilityKind.ALL_EXCEPT:
            return subjects is not None and not (subjects.all & visibility.subjects)
        case VisibilityKind.ONLY:
            return subjects is not None and bool(subjects.all & visibility.subjects)


def eligible_tiers(
    policy: Policy,
    subscription_ids: frozenset[SubscriptionId],
    subjects: KeySubjects | None,
) -> tuple[frozenset[SubscriptionId], frozenset[SubscriptionId]]:
    """Returns (own bound subscriptions, shared subscriptions) the key may use, before state checks.

    The narrowest binding level that has any bound subscription defines the own tier; subscriptions bound to
    anyone else are never eligible. Shared subscriptions are unbound and visible to the key.
    """
    own = _own_bound(policy, subscription_ids, subjects)
    bound_anywhere = frozenset(
        sub_id for sub_id in subscription_ids if policy.bindings.get(sub_id)
    )
    shared = frozenset(
        sub_id
        for sub_id in subscription_ids - bound_anywhere
        if is_visible(policy.visibility.get(sub_id, Visibility()), subjects)
    )
    return own, shared


def _own_bound(
    policy: Policy,
    subscription_ids: frozenset[SubscriptionId],
    subjects: KeySubjects | None,
) -> frozenset[SubscriptionId]:
    if subjects is None:
        return frozenset()
    for level in (subjects.narrow, subjects.broad):
        bound = frozenset(
            sub_id
            for sub_id in subscription_ids
            if policy.bindings.get(sub_id, frozenset()) & level
        )
        if bound:
            return bound
    return frozenset()
