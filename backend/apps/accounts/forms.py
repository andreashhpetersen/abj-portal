"""
Forms the admin needs that are not a model form.

Currently just the register upload, which exists because the people who fetch
the export from INNA are board members rather than anyone with a shell on the
server.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .csvsource import MAX_BYTES, RegisterFileError, read_bytes


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
