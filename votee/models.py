import json
from typing import List, Optional, TypedDict

import votee.crypto
from django.core.validators import validate_unicode_slug
from django.db import models, transaction
from django.db.models import F
from django.urls import reverse


class ElectionSettings(TypedDict):
    poll_order: List[str]


class PollSettings(TypedDict):
    option_order: List[str]
    votes_per_ballot: int
    accepting_votes: bool
    voting_start: float
    voting_interval: float


class Election(models.Model):
    name = models.TextField()
    slug = models.SlugField(
        max_length=50, unique=True, validators=[validate_unicode_slug]
    )
    admin_secret = models.BinaryField(max_length=16, default=votee.crypto.rand128)
    settings_raw = models.TextField()

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse(
            "election_detail", {"election": self.election.slug, "poll": self.slug}
        )

    @property
    def settings(self) -> ElectionSettings:
        return {"poll_order": [], **json.loads(self.settings_raw or "{}")}

    @property
    def poll_order(self) -> List[str]:
        return list(self.settings.get("poll_order", ()))

    @poll_order.setter
    def poll_order(self, v: List[str]) -> None:
        self.settings_raw = json.dumps({**self.settings, "poll_order": v})

    def polls(self) -> List["Poll"]:
        poll_order = {s: i for i, s in enumerate(self.poll_order)}
        all_polls = list(Poll.objects.filter(election=self))
        all_polls.sort(key=lambda p: (p.slug not in poll_order, poll_order.get(p.slug)))
        return all_polls

    def get_admin_key(self) -> str:
        return votee.crypto.urlencode(votee.crypto.encrypt_int(self.admin_secret, 0))

    def validate_admin_key(self, k: str) -> bool:
        decoded = votee.crypto.urldecode(k)
        return (
            decoded is not None
            and votee.crypto.decrypt_int(self.admin_secret, decoded, 1) == 0
        )


class Poll(models.Model):
    election = models.ForeignKey(Election, models.CASCADE)
    name = models.TextField()
    slug = models.SlugField(max_length=50, validators=[validate_unicode_slug])
    settings_raw = models.TextField()
    ballot_secret = models.BinaryField(max_length=16, default=votee.crypto.rand128)
    number_of_ballots = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse(
            "poll_detail", kwargs={"election": self.election.slug, "poll": self.slug}
        )

    def get_admin_url(self) -> str:
        return (
            reverse(
                "poll_admin", kwargs={"election": self.election.slug, "poll": self.slug}
            )
            + "?a="
            + self.election.get_admin_key()
        )

    @property
    def settings(self) -> PollSettings:
        return {
            "option_order": [],
            "votes_per_ballot": 1,
            "accepting_votes": False,
            "voting_start": 0,
            "voting_interval": 0,
            **json.loads(self.settings_raw or "{}"),
        }

    @property
    def votes_per_ballot(self) -> int:
        return self.settings["votes_per_ballot"]

    @votes_per_ballot.setter
    def votes_per_ballot(self, v: int) -> None:
        self.settings_raw = json.dumps({**self.settings, "votes_per_ballot": v})

    @property
    def accepting_votes(self) -> bool:
        return self.settings["accepting_votes"]

    @accepting_votes.setter
    def accepting_votes(self, v: bool) -> None:
        self.settings_raw = json.dumps({**self.settings, "accepting_votes": v})

    @property
    def option_order(self) -> List[str]:
        return list(self.settings.get("option_order", ()))

    @option_order.setter
    def option_order(self, v: List[str]) -> None:
        self.settings_raw = json.dumps({**self.settings, "option_order": v})

    @property
    def voting_start(self) -> float:
        return list(self.settings.get("voting_start", ()))

    @voting_start.setter
    def voting_start(self, v: float) -> None:
        self.settings_raw = json.dumps({**self.settings, "voting_start": v})

    @property
    def voting_interval(self) -> float:
        return list(self.settings.get("voting_interval", ()))

    @voting_interval.setter
    def voting_interval(self, v: float) -> None:
        self.settings_raw = json.dumps({**self.settings, "voting_interval": v})

    def options(self) -> List["PollOption"]:
        option_order = {s: i for i, s in enumerate(self.option_order)}
        all_options = list(PollOption.objects.filter(poll=self))
        all_options.sort(
            key=lambda p: (p.id not in option_order, option_order.get(p.id))
        )
        return all_options

    def get_ballots(self, lo: int, hi: int) -> List[str]:
        return [
            votee.crypto.urlencode(votee.crypto.encrypt_int(self.ballot_secret, i))
            for i in range(lo, hi)
        ]

    def validate_ballot(self, k: str) -> Optional[int]:
        decoded = votee.crypto.urldecode(k)
        if decoded is None:
            return None
        return votee.crypto.decrypt_int(
            self.ballot_secret, decoded, self.number_of_ballots
        )

    class Meta:
        unique_together = [
            ("election", "slug"),
        ]


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, models.CASCADE)
    name = models.TextField(blank=True)
    count = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.name or "(blank)"


class UsedBallot(models.Model):
    poll = models.ForeignKey(Poll, models.CASCADE)
    ballot_index = models.IntegerField()

    def __repr__(self) -> str:
        return (
            f"<UsedBallot poll={repr(str(self.poll))} ballot_index={self.ballot_index}>"
        )

    class Meta:
        unique_together = [
            ("poll", "ballot_index"),
        ]


def use_ballot(p: Poll, i: int, options: List[PollOption]) -> bool:
    if UsedBallot.objects.filter(poll=p, ballot_index=i).exists():
        return False
    qs = PollOption.objects.filter(id__in=[o.id for o in options])
    with transaction.atomic():
        UsedBallot.objects.create(poll=p, ballot_index=i)
        qs.update(count=F("count") + 1)
    return True
