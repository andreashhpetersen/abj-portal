"""
Forms the admin needs that are not a model form.

The register upload, which exists because the people who fetch the export from
INNA are board members rather than anyone with a shell on the server; and the
register-entry picker, which exists because a resident who mistypes their
number is still a resident.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .csvsource import MAX_BYTES, RegisterFileError, read_bytes
from .models import RegisterEntry
from .register import MAX_SEARCH_RESULTS, search_eligible


class RegisterUploadForm(forms.Form):
    """Upload an export of INNA's resident register.

    The file is read out of the request and never written anywhere. It is
    personal data about every resident in the association, so the less of it
    that exists the better: there is deliberately no upload directory, no
    "recent imports" list holding the original, and nothing to remember to
    delete afterwards. What survives the request is `RegisterEntry` rows, which
    is the point of uploading it.

    Reading the file is done here, in `clean_file`, rather than in the view, so
    that a wrong file comes back as an ordinary field error against the field
    the person used — next to the input, in Danish, with the form still filled
    in around it.
    """

    file = forms.FileField(
        label=_("Beboerudtræk (CSV)"),
        help_text=_(
            "Filen, du henter hos INNA. Kommasepareret eller semikolonsepareret, "
            "UTF-8 eller Windows-tegnsæt — portalen finder selv ud af det. "
            "Filen gemmes ikke på serveren."
        ),
    )
    dry_run = forms.BooleanField(
        label=_("Kun prøvekørsel"),
        required=False,
        initial=True,
        help_text=_(
            "Læs filen og vis, hvad importen ville gøre, uden at ændre noget. "
            "Slå den fra, og vælg filen igen, for at gennemføre importen."
        ),
    )

    def clean_file(self):
        """Turn the upload into `(header, rows)`, or an error beside the field.

        The size check comes first and is its own message: `read_bytes` would
        also refuse an enormous file, but only after Django had already accepted
        the whole upload, and the point of the limit is that it stays in memory.
        """
        upload = self.cleaned_data["file"]
        if upload.size > MAX_BYTES:
            raise forms.ValidationError(
                _("Filen er for stor (%(size)s). Beboerudtrækket fylder omkring 0,1 MB.")
                % {"size": f"{upload.size / 1024 / 1024:.1f} MB"}
            )
        try:
            return read_bytes(upload.read())
        except RegisterFileError as error:
            raise forms.ValidationError(str(error)) from error


class RegisterEntryChoiceField(forms.ModelChoiceField):
    """A register entry, described the way a board member would recognise it.

    `RegisterEntry.__str__` gives a name and an address, which is not enough to
    choose between the two people living in the same flat, or to check the
    choice against the rent statement lying in front of you. Both numbers
    belong in the label for that reason.
    """

    def label_from_instance(self, entry):
        return _("%(name)s — %(address)s (bolignr. %(unit)s, beboernr. %(number)s)") % {
            "name": entry.full_name or entry.alias or _("uden navn"),
            "address": entry.address or entry.raw_address,
            "unit": entry.unit_number or _("intet"),
            "number": entry.resident_number or _("intet"),
        }


class LinkRegisterEntryForm(forms.Form):
    """Find the register entry a signup request should have matched.

    Searching and choosing are one form on purpose. They are one act — a board
    member types a name, looks at what comes back, recognises the person or
    searches again — and splitting them over two pages would scroll the claim
    being checked off the top of the screen at the moment it is needed.

    Nothing here is validated against what the applicant wrote. Their number is
    what turned out to be wrong; the entry is what a person decided it meant,
    and the two disagreeing is the normal case rather than an error.
    """

    query = forms.CharField(
        label=_("Søg i beboerregistret"),
        required=False,
        help_text=_(
            "Navn, adresse, bolignr. eller beboernr. Flere ord indsnævrer søgningen, "
            "så »holm jægersborggade« kun finder Holm på Jægersborggade. "
            "Kun beboere, der må oprette en konto, kan vælges."
        ),
    )
    entry = RegisterEntryChoiceField(
        label=_("Beboer i registret"),
        queryset=RegisterEntry.objects.none(),
        widget=forms.RadioSelect,
        required=False,
        empty_label=None,
    )
    note = forms.CharField(
        label=_("Bemærkning"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "Gemmes under den automatiske note om, hvad kontoen blev knyttet til — "
            "skriv, hvordan du genkendte ansøgeren. Vises ikke for ansøgeren."
        ),
    )

    def __init__(self, *args, confirming=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.confirming = confirming
        query = self.data.get("query", "") if self.is_bound else self.initial.get("query", "")
        found = list(search_eligible(query).values_list("pk", flat=True)[: MAX_SEARCH_RESULTS + 1])
        self.result_count = len(found)
        self.truncated = self.result_count > MAX_SEARCH_RESULTS
        # Filtering on the ids rather than slicing the queryset: a sliced
        # queryset cannot be filtered again, and `ModelChoiceField` filters
        # this one every time it validates — so a slice here would turn
        # choosing an entry into a 500.
        self.fields["entry"].queryset = (
            RegisterEntry.objects.eligible()
            .filter(pk__in=found[:MAX_SEARCH_RESULTS])
            .select_related("building")
            .order_by("unit_number", "name_key")
        )

    def clean_entry(self):
        """Required when confirming, optional while searching.

        One form does both, so a plain `required=True` would greet a board
        member who has just typed a name with an error about not having chosen
        from a list that was empty when they submitted it.
        """
        entry = self.cleaned_data.get("entry")
        if self.confirming and entry is None:
            raise forms.ValidationError(
                _("Vælg den beboer i registret, som kontoen skal knyttes til.")
            )
        return entry
