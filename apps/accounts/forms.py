from django import forms
from django.contrib.auth import password_validation


class LoginForm(forms.Form):
    identifier = forms.CharField(label="Email or Employee ID", max_length=254)
    password = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False)


class CodeForm(forms.Form):
    code = forms.CharField(
        label="Verification code",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "pattern": "[0-9]{6}"}),
    )


class NewPasswordForm(forms.Form):
    current_password = forms.CharField(label="Current password", widget=forms.PasswordInput, strip=False)
    new_password1 = forms.CharField(label="New password", widget=forms.PasswordInput, strip=False)
    new_password2 = forms.CharField(label="Confirm new password", widget=forms.PasswordInput, strip=False)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].help_text = password_validation.password_validators_help_text_html()

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("Your current password is incorrect.")
        return value

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("new_password1"), data.get("new_password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("new_password2", "The two passwords do not match.")
            elif p1 == data.get("current_password"):
                self.add_error("new_password1", "Choose a password different from the current one.")
            else:
                try:
                    password_validation.validate_password(p1, self.user)
                except forms.ValidationError as exc:
                    self.add_error("new_password1", exc)
        return data
