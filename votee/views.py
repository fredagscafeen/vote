import datetime
import json
import time

from django import forms
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, TemplateView
from votee import models


class SingleElectionMixin:
    def get_election(self) -> models.Election:
        try:
            return models.Election.objects.get(slug=self.kwargs["election"])
        except models.Election.DoesNotExist:
            raise Http404


class SinglePollMixin(SingleElectionMixin):
    def get_poll(self) -> models.Poll:
        try:
            return models.Poll.objects.get(
                election__slug=self.kwargs["election"], slug=self.kwargs["poll"]
            )
        except models.models.Poll.DoesNotExist:
            raise Http404


class ElectionCreateForm(forms.Form):
    name = forms.CharField()
    polls = forms.CharField(
        required=False,
        widget=forms.Textarea,
    )

    def clean_polls(self):
        v = self.cleaned_data["polls"]
        if not v.strip():
            return [], []
        polls = []
        options = []
        if v.strip().startswith("{"):
            parsed = json.loads(v)
            for k, v in parsed.items():
                assert isinstance(k, str)
                assert isinstance(v, list)
                p = models.Poll(name=k, slug=slugify(k))
                polls.append(p)
                for n in v:
                    assert isinstance(n, str)
                    options.append(models.PollOption(poll=p, name=n))
        else:
            for line in v.splitlines():
                if not line.strip():
                    continue
                indented = line.lstrip() != line
                if indented:
                    if not polls:
                        raise Exception("Indented line without a leading poll name")
                    name = "" if line.strip() == "(blank)" else line.strip()
                    options.append(models.PollOption(poll=polls[-1], name=name))
                else:
                    if polls and (not options or options[-1].poll is not polls[-1]):
                        raise Exception("Poll with no options")
                    name = line.strip()
                    polls.append(models.Poll(name=name, slug=slugify(name)))
            if not polls:
                raise Exception("No polls")
            if not options or options[-1].poll is not polls[-1]:
                raise Exception("Poll with no options")
        return polls, options


class ElectionCreate(FormView):
    template_name = "votee/election_create.html"
    form_class = ElectionCreateForm

    def form_valid(self, form):
        if not self.request.user.is_superuser:
            form.add_error(None, "You must be a superuser to create a new election")
            return self.form_invalid(form)
        polls, options = form.cleaned_data["polls"]
        e = models.Election.objects.create(
            name=form.cleaned_data["name"],
            slug=slugify(form.cleaned_data["name"]),
        )
        for p in polls:
            p.election = e
            p.save()
        for o in options:
            o.poll = o.poll
            o.save()
        url = (
            reverse("election_admin", kwargs={"election": e.slug})
            + "?a="
            + e.get_admin_key()
        )
        return HttpResponseRedirect(url)


class ElectionDetail(TemplateView, SingleElectionMixin):
    template_name = "votee/election_detail.html"


class ElectionAdmin(FormView, SingleElectionMixin):
    template_name = "votee/election_admin.html"

    def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.election = self.get_election()
        key = self.request.GET.get("a") or ""
        if not self.election.validate_admin_key(key):
            if request.user.is_superuser:
                url = (
                    reverse("election_admin", kwargs={"election": self.election.slug})
                    + "?a="
                    + self.election.get_admin_key()
                )
                return HttpResponseRedirect(url)
            return HttpResponseForbidden("<h1>Invalid admin key</h1>")
        return super().dispatch(request, *args, **kwargs)

    def get_form(self) -> forms.Form:
        self.election = self.get_election()
        self.polls = self.election.polls()
        f = forms.Form(**self.get_form_kwargs())
        f.fields["name"] = forms.CharField(
            initial=self.election.name,
        )
        self.rows = []
        for i, p in enumerate(self.polls):
            prefix = f"p{p.id}_"
            f.fields[prefix + "order"] = forms.IntegerField(
                initial=i + 1,
            )
            ac = p.accepting_votes
            f.fields[prefix + "delete"] = forms.BooleanField(
                required=False,
                disabled=ac,
            )
            f.fields[prefix + "name"] = forms.CharField(
                initial=p.name,
                disabled=ac,
            )
            f.fields[prefix + "votes"] = forms.IntegerField(
                initial=1,
                disabled=ac,
            )
            self.rows.append(
                (
                    p,
                    prefix + "order",
                    prefix + "delete",
                    prefix + "name",
                    prefix + "votes",
                )
            )
        f.fields["new_polls"] = forms.CharField(widget=forms.Textarea, required=False)
        return f

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(election=self.election, **kwargs)
        form = context_data["form"]
        context_data["rows"] = [
            [form[k] for k in keys] + [p.get_admin_url()] for p, *keys in self.rows
        ]
        poll_export = "\n\n".join(
            "%s\n\n%s"
            % (
                poll.name,
                "\n".join("    %s" % (o.name or "(blank)") for o in poll.options()),
            )
            for poll in self.polls
        )
        context_data["poll_export"] = poll_export
        return context_data

    def form_invalid(self, form):
        print("Invalid")
        return super().form_invalid(form)

    def form_valid(self, form):
        print("Valid")
        new_order = []
        to_delete = []
        to_save = []
        for p, k_order, k_delete, k_name, k_votes in self.rows:
            ac = p.accepting_votes
            if not ac and form.cleaned_data[k_delete]:
                to_delete.append(p)
                continue
            new_order.append((form.cleaned_data[k_order], p))
            if not ac:
                continue
            if (
                p.votes_per_ballot != form.cleaned_data[k_votes]
                or p.name != form.cleaned_data[k_name]
            ):
                p.votes_per_ballot = form.cleaned_data[k_votes]
                p.name = form.cleaned_data[k_name]
                to_save.append(p)
        order_slugs = [p.slug for _, p in sorted(new_order)]
        for n in form.cleaned_data["new_polls"].splitlines():
            name = n.strip()
            if not name:
                continue
            slug = slugify(name)
            order_slugs.append(slug)
            p = models.Poll(
                election=self.election,
                name=name,
                slug=slug,
            )
            p.votes_per_ballot = 1
            p.accepting_votes = False
            p.number_of_ballots = 0
            to_save.append(p)
        for o in to_delete:
            o.delete()
        for o in to_save:
            o.save()
        self.election.poll_order = order_slugs
        self.election.name = form.cleaned_data["name"]
        self.election.save()

        url = (
            reverse("election_admin", kwargs={"election": self.election.slug})
            + "?a="
            + self.election.get_admin_key()
        )
        return HttpResponseRedirect(url)


class PollDetail(FormView, SinglePollMixin):
    template_name = "votee/poll_detail.html"

    def get_form(self) -> forms.Form:
        self.poll = self.get_poll()
        key = self.request.GET.get("s")
        if key is not None:
            self.ballot_index = self.poll.validate_ballot(key)
            # If "key" was invalid, ballot_index is simply None
        else:
            self.ballot_index = None
        self.key_error = bool(key and self.ballot_index is None)
        self.already_voted = (
            self.ballot_index is not None
            and models.UsedBallot.objects.filter(
                poll=self.poll, ballot_index=self.ballot_index
            ).exists()
        )
        self.can_vote = self.ballot_index is not None
        self.options = self.poll.options()
        f = forms.Form(**self.get_form_kwargs())
        for i in range(1, self.poll.votes_per_ballot + 1):
            choices = [("0", "---")] + [(str(o.id), str(o)) for o in self.options]
            f.fields["option%s" % i] = forms.ChoiceField(
                choices=choices, disabled=not self.can_vote
            )
        return f

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        s = self.poll.settings
        voting_interval = s["voting_interval"]
        if voting_interval:
            next_vote = s["voting_start"] - time.time()
            if next_vote < 0:
                next_vote %= voting_interval
        else:
            next_vote = 0
        context_data.update(
            just_voted=bool(self.request.GET.get("voted")),
            options=self.options,
            ballot_index=None if self.ballot_index is None else self.ballot_index + 1,
            already_voted=self.already_voted,
            poll=self.poll,
            ac=s["accepting_votes"],
            next_vote=next_vote,
            voting_interval=voting_interval,
        )
        return context_data

    def form_valid(self, form):
        if self.already_voted:
            form.add_error(None, "You have already voted in this poll")
            return self.form_invalid(form)
        if not self.can_vote:
            form.add_error(None, "Your voting key is not valid")
            return self.form_invalid(form)
        if not self.poll.accepting_votes:
            form.add_error(
                None, "Sorry, but the poll closed before we received your vote!"
            )
            return self.form_invalid(form)
        assert self.ballot_index is not None
        options = {str(o.id): o for o in self.poll.options()}
        chosen_option_ids = [
            form.cleaned_data["option%s" % i]
            for i in range(1, self.poll.votes_per_ballot + 1)
        ]
        chosen_options = [options.get(i) for i in chosen_option_ids]
        missing_options = any(o is None for o in chosen_options)
        if missing_options:
            form.add_error(None, "Please fill out the entire form")
            return self.form_invalid(form)
        non_blank_options = [o for o in chosen_options if o.name != ""]
        dupes = len(non_blank_options) - len(set(non_blank_options))
        if dupes:
            form.add_error(None, "You cannot vote for the same option more than once")
            return self.form_invalid(form)
        models.use_ballot(self.poll, self.ballot_index, chosen_options)
        url = (
            reverse(
                "poll_detail",
                kwargs={"election": self.get_election().slug, "poll": self.poll.slug},
            )
            + "?voted=1"
        )
        return HttpResponseRedirect(url)


class PollAdmin(FormView, SinglePollMixin):
    template_name = "votee/poll_admin.html"

    def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.poll = self.get_poll()
        key = self.request.GET.get("a") or ""
        if not self.poll.election.validate_admin_key(key):
            if request.user.is_superuser:
                base_url = reverse(
                    "poll_admin",
                    kwargs={
                        "election": self.poll.election.slug,
                        "poll": self.poll.slug,
                    },
                )
                url = base_url + "?a=" + self.poll.election.get_admin_key()
                return HttpResponseRedirect(url)
            return HttpResponseForbidden("<h1>Invalid admin key</h1>")
        return super().dispatch(request, *args, **kwargs)

    def get_form(self) -> forms.Form:
        f = forms.Form(**self.get_form_kwargs())
        s = self.poll.settings
        ac = s["accepting_votes"]
        if s["voting_start"]:
            self.voting_start = timezone.make_aware(
                datetime.datetime.utcfromtimestamp(s["voting_start"]),
                timezone=timezone.utc,
            )
            next_vote = self.voting_start - timezone.now().replace(microsecond=0)
            self.first_vote = (
                "(in %s)" % next_vote
                if next_vote.total_seconds() > 0
                else "(%s ago)" % (-next_vote)
            )
        else:
            self.voting_start = ""
            self.first_vote = ""
        voting_interval = s["voting_interval"]
        self.options = self.poll.options()
        any_votes = any(bool(o.count) for o in self.options)
        f.fields["name"] = forms.CharField(
            initial=self.poll.name,
        )
        f.fields["votes"] = forms.IntegerField(
            initial=self.poll.votes_per_ballot,
        )
        f.fields["ac"] = forms.BooleanField(
            initial=ac,
            required=False,
        )
        f.fields["next_vote"] = forms.FloatField(
            required=False,
        )
        f.fields["voting_interval"] = forms.FloatField(
            initial=voting_interval or None,
            required=False,
        )
        blank_options = [o for o in self.options if not o.name]
        f.fields["blank"] = forms.BooleanField(
            initial=bool(blank_options),
            required=False,
        )
        self.rows = []
        for i, o in enumerate(self.options):
            if not o.name:
                continue
            prefix = f"o{o.id}_"
            f.fields[prefix + "order"] = forms.IntegerField(
                initial=len(self.rows) + 1,
            )
            f.fields[prefix + "delete"] = forms.BooleanField(
                required=False,
                disabled=ac or any_votes,
            )
            f.fields[prefix + "name"] = forms.CharField(
                initial=o.name,
                disabled=ac or any_votes,
            )
            self.rows.append((o, prefix + "order", prefix + "delete", prefix + "name"))
        f.fields["new_options"] = forms.CharField(widget=forms.Textarea, required=False)
        f.fields["ballots"] = forms.IntegerField(
            initial=self.poll.number_of_ballots,
            min_value=0,
        )
        return f

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        form = context_data["form"]
        rows = [[form[k] for k in keys] for o, *keys in self.rows]
        reverse_args = {"election": self.poll.election.slug, "poll": self.poll.slug}
        url = (
            reverse(
                "poll_admin",
                kwargs=reverse_args,
            )
            + "?a="
            + self.poll.election.get_admin_key()
            + "&results=1"
        )
        ballot_url = reverse("poll_detail", kwargs=reverse_args) + "?s="
        ballots = [
            ballot_url + b
            for b in self.poll.get_ballots(0, self.poll.number_of_ballots)
        ]
        used_ballots = models.UsedBallot.objects.filter(poll=self.poll).count()
        vote_count = sum(o.count for o in self.options)
        context_data.update(
            poll=self.poll,
            rows=rows,
            options=self.options,
            vote_count=vote_count,
            used_ballots=used_ballots,
            ballots=ballots,
            show_results=bool(self.request.GET.get("results")),
            show_results_link=url,
            voting_start=self.voting_start,
            first_vote=self.first_vote,
        )
        return context_data

    def form_valid(self, form):
        new_order = []
        to_delete = []
        to_save = []
        ac = self.poll.accepting_votes
        for o in self.options:
            if o.name:
                continue
            if new_order or not form.cleaned_data["blank"]:
                # Keep 0 blanks if "blank" is not checked,
                # and keep 1 blank if "blank" is checked.
                to_delete.append(o)
                continue
            # Insert blank as the first option
            new_order.append((float("-inf"), o))
            break
        if form.cleaned_data["blank"] and not new_order:
            b = models.PollOption(poll=self.poll, name="")
            to_save.append(b)
            new_order.append((float("-inf"), b))
        for o, k_order, k_delete, k_name in self.rows:
            if not ac and form.cleaned_data[k_delete]:
                to_delete.append(o)
                continue
            new_order.append((form.cleaned_data[k_order], o))
            if not ac:
                continue
            if o.name != form.cleaned_data[k_name]:
                o.name = form.cleaned_data[k_name]
                to_save.append(o)
        order = [o for _, o in sorted(new_order)]
        for n in form.cleaned_data["new_options"].splitlines():
            name = n.strip()
            if not name:
                continue
            o = models.PollOption(
                poll=self.poll,
                name=name,
            )
            order.append(o)
            to_save.append(o)
        for o in to_delete:
            o.delete()
        for o in to_save:
            o.save()
        self.poll.accepting_votes = form.cleaned_data["ac"]
        self.poll.option_order = [o.id for o in order]
        self.poll.votes_per_ballot = form.cleaned_data["votes"]
        self.poll.number_of_ballots = form.cleaned_data["ballots"]
        self.poll.name = form.cleaned_data["name"]
        if form.cleaned_data["next_vote"]:
            self.poll.voting_start = round(time.time() + form.cleaned_data["next_vote"])
        self.poll.voting_interval = form.cleaned_data["voting_interval"] or 0
        self.poll.save()

        url = (
            reverse(
                "poll_admin",
                kwargs={"election": self.poll.election.slug, "poll": self.poll.slug},
            )
            + "?a="
            + self.poll.election.get_admin_key()
        )
        return HttpResponseRedirect(url)
