import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .compat import StrEnum
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


class PolicyError(ValueError):
    """A visibility or binding the policy refuses; the message names the reason for the operator."""


@dataclass(frozen=True, slots=True)
class SubscriptionPolicy:
    visibility: Visibility = Visibility()
    bound: frozenset[Subject] = frozenset()


@dataclass(frozen=True, slots=True)
class Policy:
    visibility: Mapping[SubscriptionId, Visibility] = field(default_factory=dict)
    bindings: Mapping[SubscriptionId, frozenset[Subject]] = field(default_factory=dict)
    version: int = 0


SUBJECTS_METADATA_FIELD = "agentek_subjects"
SUBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def subject_token(subject: Subject) -> str:
    return f"{subject.kind.value}:{subject.id}"


def parse_subject(token: str) -> Subject:
    kind, separator, identifier = token.partition(":")
    try:
        subject_kind = SubjectKind(kind)
    except ValueError:
        raise PolicyError(f"unknown subject kind in {token!r}") from None
    if not separator or not SUBJECT_ID_PATTERN.match(identifier):
        raise PolicyError(f"invalid subject {token!r}")
    return Subject(subject_kind, identifier)


def validate_subscription_policy(policy: SubscriptionPolicy) -> None:
    """Rejects an empty `only` list and a binding the visibility contradicts."""
    visibility, bound = policy.visibility, policy.bound
    match visibility.kind:
        case VisibilityKind.ALL:
            if visibility.subjects:
                raise PolicyError("visibility 'all' takes no subjects")
        case VisibilityKind.ONLY:
            if not visibility.subjects:
                raise PolicyError("visibility 'only' needs at least one subject")
            outside = bound - visibility.subjects
            if outside:
                raise PolicyError(
                    f"binding to {_names(outside)} contradicts visibility 'only'"
                )
        case VisibilityKind.ALL_EXCEPT:
            excluded = bound & visibility.subjects
            if excluded:
                raise PolicyError(
                    f"binding to {_names(excluded)} contradicts visibility 'all_except'"
                )


def _names(subjects: frozenset[Subject]) -> str:
    return ", ".join(sorted(subject_token(subject) for subject in subjects))


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
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
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

    The narrowest binding level that has any bound subscription defines the own tier, minus the subscriptions
    whose visibility excludes the key; subscriptions bound to anyone else are never eligible. Shared subscriptions are unbound and visible to the key.
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
            return frozenset(
                sub_id
                for sub_id in bound
                if is_visible(policy.visibility.get(sub_id, Visibility()), subjects)
            )
    return frozenset()
