from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_role_groups"), ("otp_email", "0006_add_timestamps")]
    operations = [
        migrations.CreateModel(
            name="SignInCodeDevice",
            fields=[],
            options={"proxy": True, "indexes": [], "constraints": []},
            bases=("otp_email.emaildevice",),
        ),
    ]
